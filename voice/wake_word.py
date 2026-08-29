#!/usr/bin/env python3
"""Phase 3: listen for "hey desky", then record the question that follows.

Runs forever under the desky-voice service. On detection it pokes the Railway
API so screen2 flips to Byte's listening state immediately, records until the
speaker stops, and drops a .wav in recordings/ for Phase 4 to transcribe.

No speech-to-text and no Claude here — this phase ends at the .wav.
"""

import io
import os
import sys
import time
import json
import wave
import glob
import threading
import subprocess
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import requests

# Sibling module. Sending a UDP packet has nothing to do with capturing audio,
# and keeping it out of here means it can be driven straight from the command
# line to test the bulb on its own — see light.py's docstring.
from light import apply_action

# --- tunables -------------------------------------------------------------
# ALSA capture device. plughw (not hw) so ALSA resamples the mic's native rate
# down to the 16 kHz openWakeWord needs — the raw hw: device rejects it.
#
# "mic" is a card *name*, not an index, pinned by /etc/udev/rules.d/85-desky-
# audio.rules matching the C-Media USB PnP's 08bb:2902. Card numbers move: the
# Pi Zero 2W has one USB port and everything hangs off a bus-powered hub that
# re-enumerates in arbitrary order after a replug or a brownout. This mic has
# been card 1 and card 2 on different boots; the name survives all of it.
MIC_DEVICE = os.environ.get("DESKY_MIC_DEVICE", "plughw:mic,0")

# The USB PnP mic reads quiet from arm's length, and openWakeWord was trained on
# normal-level speech, so a faint signal scores low and the wake word misses.
# Every frame is scaled on the way in, before detection, the silence gate or the
# saved .wav sees it.
#
# Now 1.0, i.e. off, and it should stay off while analog gain is available.
# Multiplying samples scales noise with signal and cannot improve SNR; analog
# gain before the ADC can. The mic's capture control is at 16/16 (+23.81 dB) as
# of 2026-07-29, and stacking 3x on top of that clipped real speech — the two
# recordings from 01:18 peaked at 32768 with 424 and 106 saturated samples,
# which distorts the melspectrogram openWakeWord scores on. Raise the analog
# knob first (`amixer -c mic sset Mic 16 && sudo alsactl store`); reach for this
# only if the mic is swapped for one with no capture control of its own.
MIC_GAIN = float(os.environ.get("DESKY_MIC_GAIN", "1.0"))

# openWakeWord score that counts as a hit. The custom hey_desky model is
# trained on a small sample set, so this is the first knob to turn if it
# either misses real triggers (lower) or fires at ordinary speech (raise).
DETECTION_THRESHOLD = 0.4

# Below the threshold but worth logging, so misses leave evidence. Not a second
# threshold — nothing fires off this, it only writes a line.
#
# 0.15 sits above room noise, so a line here means something speech-like was
# heard and rejected. Dropping it to 0.02 for a diagnostic run works, but every
# quiet frame then clears the bar and only the rate limit keeps the journal
# readable — put it back afterwards.
NEAR_MISS_SCORE = 0.15

# Near-miss clips are saved too — see the NEAR_MISS_* block below FRAME_SEC,
# which is where they live because the frame counts are derived from it.

# Ignore further hits right after one fires. Without this the tail of the
# wake word re-triggers the model while we are already recording.
COOLDOWN_SEC = 3.0

# Room for the pause plus a full question. 5.0 was tight once SILENCE_HOLD_SEC
# grew: the 01:18 recording spent 1.4s of its budget on the gap alone.
MAX_RECORD_SEC = 8.0

# int16 RMS below this counts as silence, in post-gain units. Measured from the
# 01:18 recordings at +23.81 dB analog: the room floor sits at 290-745 raw and
# speech at 1700+, so 1200 splits them with room on both sides. Still scaled by
# MIC_GAIN, since a gained-up room would otherwise never read as quiet and every
# recording would run to the cap.
# Re-check with `--calibrate` (which also prints post-gain) if the room moves.
SILENCE_RMS = 1200 * MIC_GAIN

# Do not gate predict() on frame loudness to save CPU. It was tried on
# 2026-07-30 at an RMS of 900 and no wake word fired again: real speech at
# arm's length sits under that floor, and openWakeWord scores a rolling ~1.3s
# melspectrogram window, so skipping frames also hands it audio stitched from
# either side of a gap. predict() measures 55 ms per 80 ms frame on this Pi —
# 0.69x realtime, headroom enough — and drift comes from contention with the
# display process, which drain() below handles without touching detection.

# How long the room must stay quiet before we call the question finished.
#
# 1.5s was too short, and it is the whole reason questions were being cut off.
# People say "hey desky", pause while the screen reacts, *then* ask — and that
# pause measured 1.4s, against a 1.5s window. One recording survived it by two
# frames; the next did not and captured the wake word alone. 2.5s clears a
# natural pause without leaving the recording hanging on a false trigger.
SILENCE_HOLD_SEC = 2.5

# How long a phone-triggered recording waits before the silence gate is allowed
# to end it. The wake word arrives mid-speech; a tap arrives before the sentence
# exists, and you still have to get the phone down and start talking. 3s plus
# the 2.5s hold is 5.5s of nothing before it gives up, comfortably inside
# MAX_RECORD_SEC — so the failure mode is a slightly long recording rather than
# an empty one.
REMOTE_LEAD_IN_SEC = 3.0

# The wake word only fires once it has been fully spoken, and people run their
# question straight into it. Keep a little audio from before the trigger.
PREROLL_SEC = 0.5

BACKEND = os.environ.get(
    "DESKY_BACKEND", "https://web-production-12607.up.railway.app").rstrip("/")
LISTENING_URL = BACKEND + "/voice/listening"
# The recording goes to the server, which holds the Groq and Anthropic keys and
# drives the rest of the exchange. The Pi keeps no secrets.
AUDIO_URL = BACKEND + "/voice/audio"
# Answer text goes back out and returns as spoken WAV. Same reasoning: the
# Groq key stays on the server.
SPEECH_URL = BACKEND + "/voice/speech"
# Reporting a failure the server cannot see for itself. It drives every other
# state transition, but it has no way to know whether a UDP packet reached the
# bulb — that happens out here, on a network the server is not on.
ERROR_URL = BACKEND + "/voice/error"

# Playback device, by the udev-pinned name rather than a card number — see
# MIC_DEVICE above for why numbers are not stable here.
SPEAKER_DEVICE = os.environ.get("DESKY_SPEAKER_DEVICE", "plughw:speaker,0")

# --- fixed by openWakeWord ------------------------------------------------
# The melspectrogram frontend assumes 16 kHz mono int16 in 80 ms frames.
SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_SEC = FRAME_SAMPLES / SAMPLE_RATE

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = SCRIPT_DIR / "recordings"

# --- near-miss capture ----------------------------------------------------
# A near miss is only actionable as audio. The score alone says the model was
# unsure; the clip says whether it was unsure about "hey desky" or about a chair
# scraping — and only the first kind is worth retraining on.
#
# Its own buffer rather than preroll's. That one holds 0.48s and feeds straight
# into the question recording, so growing it to cover the ~1.3s melspectrogram
# window openWakeWord actually scores would also push the wake word itself into
# every upload and leave Whisper transcribing it.
#
# Outside the repo on purpose: this is local training data, not code, and
# ~/display is a working tree that gets `git reset --hard` on every deploy.
NEAR_MISS_DIR = Path.home() / "wakeword-near-misses"
NEAR_MISS_SEC = 2.0
NEAR_MISS_FRAMES = int(NEAR_MISS_SEC / FRAME_SEC)

# Frames to keep buffering past the crossing before dumping. The score ramps as
# the word completes, so dumping the instant it clears NEAR_MISS_SCORE files the
# run-up and cuts off the half that matters. ~1s of tail centres the clip.
NEAR_MISS_TAIL_FRAMES = int(1.0 / FRAME_SEC)

# Gap between saved clips, deliberately >= NEAR_MISS_SEC. That inequality is the
# whole guard: two clips saved a buffer-length apart cannot share a sample, so
# they can neither duplicate each other in the training set nor collide on a
# filename, which is stamped to the second and would silently overwrite.
#
# Separate from the 1.0s rate limit on the near-miss log line. Those are
# different questions — how often to write a journal line, and how often to keep
# two seconds of audio — and tying them together is what makes a diagnostic run
# that lowers one quietly break the other.
NEAR_MISS_COOLDOWN_SEC = NEAR_MISS_SEC

# Ceiling on stored clips, oldest deleted first. 2s of 16 kHz mono is 64 KB, so
# the directory tops out around 32 MB — months of misses on a 29 GB card, and it
# cannot creep past that while nobody is looking.
NEAR_MISS_KEEP = 500


def log(msg):
    print(f"[wake] {msg}", flush=True)


def rms(frame):
    """Root-mean-square of an int16 frame, as a float."""
    # float64 first: int16 squares overflow.
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


class SilenceGate:
    """Decides when the speaker has stopped.

    Fed one frame at a time; returns True once SILENCE_HOLD_SEC of continuous
    quiet has passed. Any frame above the floor resets the run, so a pause
    mid-sentence does not cut the recording short.
    """

    def __init__(self, threshold=SILENCE_RMS, hold_sec=SILENCE_HOLD_SEC):
        self.threshold = threshold
        self.needed = max(1, int(round(hold_sec / FRAME_SEC)))
        self.quiet_run = 0

    def feed(self, frame):
        if rms(frame) < self.threshold:
            self.quiet_run += 1
        else:
            self.quiet_run = 0
        return self.quiet_run >= self.needed


def find_model():
    """ONNX, not the .tflite sitting next to it.

    The Pi runs Python 3.13 on aarch64 and tflite-runtime publishes no wheel
    for that combination, so openWakeWord's default tflite backend cannot load
    anything here. The .tflite is kept only as the training artifact.
    """
    matches = sorted(glob.glob(str(SCRIPT_DIR / "hey_desky*.onnx")))
    if not matches:
        raise SystemExit(f"no hey_desky*.onnx in {SCRIPT_DIR}")
    # Newest wins if a retrained model is dropped in alongside the old one.
    return matches[-1]


def open_mic():
    """Capture through arecord rather than PortAudio.

    PortAudio here only enumerates raw hw: devices, which refuse any rate but
    their native one (48000/44100) — and openWakeWord needs exactly 16 kHz.
    arecord's plug layer resamples for us, which is also one less dependency.
    """
    # arecord's stderr is kept, not discarded: when it exits, the line it wrote
    # IS the diagnosis. "No such file or directory" means the udev-pinned name
    # is gone, "Device or resource busy" means something else holds the mic,
    # "Invalid argument" means the format was refused — three different repairs
    # that all look identical once the message is dropped.
    #
    # A temp file rather than a PIPE. Nothing reads this until the process is
    # already dead, and an unread pipe that fills would block arecord itself —
    # trading a silent failure for a wedged one.
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-q", "-"],
        stdout=subprocess.PIPE,
        stderr=err,
    )
    proc.mic_stderr = err
    return proc


def mic_error(proc):
    """What arecord complained about before dying, or "" if it was silent."""
    err = getattr(proc, "mic_stderr", None)
    if err is None:
        return ""
    try:
        err.seek(0)
        return err.read().decode(errors="replace").strip().replace("\n", " | ")
    except (OSError, ValueError):
        # A closed or unseekable file is not worth losing the exit over — the
        # caller is already on its way out with a less specific message.
        return ""


def read_frame(proc):
    """One 80 ms frame, gain applied, or None if arecord died (mic unplugged)."""
    buf = proc.stdout.read(FRAME_BYTES)
    if buf is None or len(buf) < FRAME_BYTES:
        return None
    frame = np.frombuffer(buf, dtype=np.int16)
    if MIC_GAIN == 1.0:
        return frame
    # int32 before the multiply, then clip: scaling in int16 wraps a loud
    # sample to the opposite sign, which the model reads as noise.
    return np.clip(frame.astype(np.int32) * MIC_GAIN, -32768, 32767).astype(np.int16)


def drain(proc):
    """Throw away audio queued in arecord's pipe, returning the seconds dropped.

    Last resort when the loop has fallen behind anyway: stale frames are worse
    than no frames, because a late detection makes record() capture the pause
    after the wake word rather than the question that follows it.
    """
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    try:
        dropped = 0
        while True:
            buf = proc.stdout.read(FRAME_BYTES)
            if not buf:
                break
            dropped += len(buf)
    finally:
        os.set_blocking(fd, True)
    return dropped / (SAMPLE_RATE * 2)


def notify_listening():
    """Tell the server the wake word fired. Never fatal — a dead network
    should not stop us recording the question."""
    try:
        requests.post(LISTENING_URL, timeout=2)
    except Exception as e:
        log(f"POST /voice/listening failed: {e}")


# Set when the phone asks the desk to listen. An Event rather than a flag
# because the SSE thread sets it and the detection loop clears it, and this is
# the one piece of state those two share.
REMOTE_TRIGGER = threading.Event()

# The server stamps a nonce on each trigger, and that is what gets watched
# rather than the 'listening' state: the row is already listening by the second
# tap, so the state alone would never change again.
EVENTS_URL = BACKEND + "/events"


def remote_trigger_loop():
    """Watch the SSE stream for the phone's listen button.

    Its own connection rather than a hook into main.py's: that runs in a
    different process, and a pipe between them to carry one integer would be
    more moving parts than a second reader of a stream the server already
    fans out to everyone.
    """
    last = None
    while True:
        try:
            with requests.get(EVENTS_URL, stream=True, timeout=(5, 60)) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"status {resp.status_code}")
                for raw in resp.iter_lines(chunk_size=1, decode_unicode=True):
                    if not raw or raw.startswith(":") or not raw.startswith("data:"):
                        continue
                    try:
                        data = json.loads(raw[len("data:"):].strip() or "{}")
                    except ValueError:
                        continue
                    nonce = data.get("voice_trigger")
                    if nonce is None:
                        continue
                    # Every event on this stream is live. The hub is a plain
                    # fan-out with no history, so connecting replays nothing and
                    # there is no stale first nonce to skip — an earlier guard
                    # against one ate the first tap after every restart.
                    if nonce != last:
                        last = nonce
                        log("remote trigger from the app")
                        REMOTE_TRIGGER.set()
        except Exception as e:
            log(f"trigger stream: {e}")
        time.sleep(3)


def notify_error():
    """Put Byte in the error state for a failure only this end can see.

    Best-effort, like notify_listening: if this POST does not land, the screen
    keeps showing the answered face until the server's reaper clears it, which is
    cosmetic next to whatever already went wrong.
    """
    try:
        requests.post(ERROR_URL, timeout=2)
    except Exception as e:
        log(f"POST /voice/error failed: {e}")


def send_audio(path):
    """Upload the recording; the server transcribes it and answers from there.

    The reply only arrives once Whisper and Claude have both run, so the timeout
    is generous — but the screen does not wait on it. Byte moves to 'thinking'
    the moment the server has the transcript, over SSE.

    The reply may also carry an action, when the server decided the question was
    really a command. That rides the response body rather than the SSE stream
    because it is meant for this device alone: the SSE hub fans out to all three
    screens and any open browser, and a reconnecting client replaying a stale
    "lights off" is not a thing worth designing around.
    """
    try:
        with open(path, "rb") as f:
            r = requests.post(
                AUDIO_URL, files={"file": (path.name, f, "audio/wav")}, timeout=90)
        if r.status_code != 200:
            log(f"POST /voice/audio -> {r.status_code}: {r.text[:160]}")
            return
        log(f"answered: {r.text[:160]}")
    except Exception as e:
        log(f"POST /voice/audio failed: {e}")
        return

    try:
        body = r.json()
    except Exception:
        body = {}
    answer = body.get("answer", "")
    action = body.get("action")

    # Act first, then speak, so what Desky says matches what actually happened.
    # The bulb is the one thing in this exchange the server cannot verify — it is
    # not on this network — so the result of this call, and not the server's
    # optimism, decides what comes out of the speaker.
    if action:
        ok, reason, spoken = apply_action(action)
        if ok:
            log(f"action ok: {action}")
        else:
            log(f"action failed: {reason}")
            # The server already broadcast 'answered' before we got this reply,
            # so the screen is currently claiming success. This is the only way
            # it learns otherwise.
            notify_error()
            # Say what actually went wrong rather than one catch-all: an
            # unreachable bulb and a rejected command send you to different
            # places, and light.py is the only thing that knows which it was.
            answer = spoken

    # Speaking is a bonus on top of the answer, which is already on screen via
    # SSE. A failure here must not look like a failure of the whole exchange.
    if answer:
        speak(answer)


def _play(wav):
    """Push a complete WAV at the speaker. Returns whether it played.

    aplay reads the sample rate off the stream's own header, so the Pi never has
    to agree with the server about a format. Failure is reported rather than
    raised: both callers are on threads where an exception would take down more
    than the sound.
    """
    p = subprocess.run(["aplay", "-D", SPEAKER_DEVICE, "-q", "-"],
                       input=wav, stderr=subprocess.PIPE)
    if p.returncode != 0:
        log(f"aplay failed: {p.stderr.decode(errors='replace')[:160]}")
        return False
    return True


def speak(text):
    """Say the answer out loud."""
    try:
        r = requests.post(SPEECH_URL, json={"text": text}, timeout=60)
        if r.status_code != 200:
            log(f"POST /voice/speech -> {r.status_code}: {r.text[:160]}")
            return
        # Playback runs while the loop keeps listening, so Desky can be
        # interrupted by another wake word mid-sentence. The mic will also hear
        # this audio; harmless today because we only record after a trigger.
        if _play(r.content):
            log(f"spoke {len(r.content)} bytes")
    except Exception as e:
        log(f"speak failed: {e}")


# --- Alarms ---------------------------------------------------------------
# main.py decides when an alarm fires — it holds the schedule and watches the
# clock — but it has never touched audio, and this process owns the speaker. The
# handoff is the same /dev/shm flag file the encoder already uses for focus and
# brightness: its existence means "ringing", its removal means "stop". One
# writer each way, and no third thing opening the ALSA device.

ALARM_FILE = "/dev/shm/desky_alarm"

# The ring itself. Decoded from "alarm tune.mp3" on a dev machine rather than
# here —
#   afconvert -f WAVE -d LEI16@22050 -c 1 "alarm tune.mp3" voice/alarm.wav
# — because aplay takes WAV and nothing else, and putting an mp3 decoder in the
# one path that has to work at six in the morning buys nothing: the decode is
# identical every day, so it happens once, before the file is ever committed.
# Mono at 22 kHz is 1.8 MB for the 42s, and a small speaker on the end of a Pi
# has no use for the stereo 48 kHz the original carries.
ALARM_TUNE = SCRIPT_DIR / "alarm.wav"

# How much of the tune to hand aplay at a time. It drains its pipe at playback
# speed, so writing the whole thing in one go blocks for the full 42s and a
# dismissal goes unread until the pass ends. 8 KB is ~0.19s of audio here.
ALARM_CHUNK_BYTES = 8192

# Gap between repeats. Long enough to be a repeating alarm rather than a wall of
# noise, short enough that the silence never reads as "it stopped".
#
# Measured from the start of a pass, not its end, so the tune — which outruns
# this several times over — simply repeats without a gap. What it still paces is
# the beep fallback, which is over in 1.2s.
ALARM_REPEAT_SEC = 8.0

# The fallback, for a deploy or a checkout that lost the tune.
ALARM_BEEP_HZ = 880
ALARM_BEEP_SEC = 1.2
ALARM_BEEP_AMPLITUDE = 8000

_BEEP = None


def beep_wav():
    """A pulsed square wave, built here rather than shipped as a file.

    This is the whole reason the alarm cannot fail silently. The spoken wake
    message needs the network and Groq's TTS quota, and that quota is ~3600
    tokens a day — running dry is an ordinary Tuesday, not an outage. An alarm
    that says nothing because of it would be the worst bug this feature could
    have, so there is always something to fall back to that needs neither.
    """
    global _BEEP
    if _BEEP is None:
        t = np.arange(int(SAMPLE_RATE * ALARM_BEEP_SEC))
        # Square, not sine: it carries further through a small speaker, and the
        # harmonics are what make it sound like an alarm rather than a tone.
        tone = np.where(np.sin(2 * np.pi * ALARM_BEEP_HZ * t / SAMPLE_RATE) >= 0,
                        ALARM_BEEP_AMPLITUDE, -ALARM_BEEP_AMPLITUDE)
        # Chopped into bursts. A continuous tone is easy to stop hearing.
        pulses = ((t // (SAMPLE_RATE // 8)) % 2 == 0)
        samples = (tone * pulses).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(samples.tobytes())
        _BEEP = buf.getvalue()
    return _BEEP


_TUNE = None


def alarm_audio():
    """The ring, read once and reused for every repeat.

    Once, not per pass: the file is 1.8 MB and it is the same 1.8 MB every
    morning, so re-reading it between repeats would only add a disk hit to the
    gap. It is also read the first time an alarm rings rather than at import, so
    a service that has been up for a week does not hold it for nothing.

    Falls back to the generated beep when the file is missing. That fallback is
    the whole reason this cannot fail silently, and it is worth the four lines:
    a deploy that dropped the tune would otherwise wake nobody at all.
    """
    global _TUNE
    if _TUNE is None:
        try:
            _TUNE = ALARM_TUNE.read_bytes()
            log(f"alarm tune: {len(_TUNE)} bytes from {ALARM_TUNE.name}")
        except Exception as e:
            log(f"no alarm tune ({e}) — falling back to the beep")
            _TUNE = beep_wav()
    return _TUNE


def play_alarm(wav):
    """Play one pass of the ring, cut short the moment the alarm flag drops.

    _play() writes the whole buffer and waits, which was fine when a ring was a
    1.2s beep. A pass of the tune is 42s, and the flag is what a dismiss removes
    — playing to the end regardless would leave the room ringing for most of a
    minute after somebody had already switched it off.
    """
    p = subprocess.Popen(["aplay", "-D", SPEAKER_DEVICE, "-q", "-"],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    dismissed = False
    try:
        for i in range(0, len(wav), ALARM_CHUNK_BYTES):
            if not os.path.exists(ALARM_FILE):
                dismissed = True
                break
            p.stdin.write(wav[i:i + ALARM_CHUNK_BYTES])
        p.stdin.close()
    except (BrokenPipeError, OSError):
        # aplay died under us — the speaker was pulled, or the device is busy.
        # Whatever it wrote to stderr is reported below.
        pass

    # Either way aplay is still sitting on a second or so of buffered audio, so
    # a dismissal has to kill it rather than wait it out.
    while p.poll() is None:
        if not os.path.exists(ALARM_FILE):
            dismissed = True
            p.terminate()
            break
        time.sleep(0.25)
    p.wait()

    # A ring that never reaches the speaker is the one failure this feature
    # cannot absorb, so a real aplay error leaves a line. Being killed on a
    # dismiss is not one of those.
    if not dismissed and p.returncode != 0:
        log(f"aplay failed: {(p.stderr.read() or b'').decode(errors='replace')[:160]}")


# A reminder is the other half of the same handoff: main.py writes the text, this
# speaks it. Twice rather than once, because one utterance is easy to miss if you
# are not at the desk — and twice is still nothing like an alarm, since it ends
# whether or not anybody heard it.
REMINDER_FILE = "/dev/shm/desky_reminder"
REMINDER_REPEATS = 2
REMINDER_GAP_SEC = 5.0


def speak_reminder():
    """Say the reminder twice, then drop the flag. Blocking, by design.

    Runs on the alarm watcher's thread, so a reminder cannot overlap a ring or
    another reminder — one speaker, one thing talking at a time.
    """
    try:
        with open(REMINDER_FILE) as f:
            text = f.read().strip()
    except Exception:
        text = ""

    if text:
        # Fetched once and replayed, for the same reason the alarm does it: the
        # daily TTS quota is small, and the second utterance is usually the one
        # actually heard.
        audio = None
        try:
            r = requests.post(SPEECH_URL, json={"text": f"Reminder: {text}."}, timeout=30)
            if r.status_code == 200 and r.content:
                audio = r.content
            else:
                log(f"reminder speech -> {r.status_code}: {r.text[:120]}")
        except Exception as e:
            log(f"reminder speech failed: {e}")

        if audio:
            for i in range(REMINDER_REPEATS):
                if i:
                    time.sleep(REMINDER_GAP_SEC)
                _play(audio)
            log(f"spoke reminder twice: {text!r}")
        else:
            # No beep fallback here, unlike an alarm. A beep that means "you were
            # reminded of something, but not what" is worse than silence, and
            # main.py is still showing the text on screen — which is the honest
            # remainder of the feature.
            log(f"reminder {text!r} shown but not spoken")

    try:
        os.remove(REMINDER_FILE)
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"could not remove {REMINDER_FILE}: {e}")


def alarm_watch_loop():
    """Ring while the alarm flag is up, and speak reminders as they appear.

    Both live on this thread because both end in aplay and the speaker takes one
    at a time. An alarm wins: a reminder arriving mid-ring waits for the flag to
    drop, which is the right order — the ringing thing is the urgent one.
    """
    ringing = False
    while True:
        try:
            if not os.path.exists(ALARM_FILE):
                if ringing:
                    # The only line saying an alarm ended, and the pair of them
                    # is how the journal shows one fired at all — alarm_audio()
                    # logs once in the life of the process and nothing else here
                    # speaks up on a morning that went fine.
                    log("alarm stopped")
                    ringing = False
                if os.path.exists(REMINDER_FILE):
                    speak_reminder()
                    continue
                time.sleep(0.5)
                continue

            if not ringing:
                log("alarm ringing")
                ringing = True

            started = time.monotonic()
            # A failure here is usually the voice loop holding the speaker
            # mid-answer. Not worth handling: the next pass comes round shortly
            # and that answer will be over by then.
            play_alarm(alarm_audio())

            # Paced from the start of the pass, so this is a gap after the beep
            # and nothing at all after the tune, which has already outrun it.
            # Broken out of on a dismiss rather than slept through.
            while (time.monotonic() - started < ALARM_REPEAT_SEC
                    and os.path.exists(ALARM_FILE)):
                time.sleep(0.25)
        except Exception as e:
            log(f"alarm loop: {e}")
            time.sleep(1.0)


def save_wav(frames):
    RECORDINGS_DIR.mkdir(exist_ok=True)
    path = RECORDINGS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.concatenate(frames).tobytes())
    return path


def save_near_miss(frames, score):
    """Write one near-miss clip. Called on a thread — never from the loop.

    64 KB is nothing, but the loop's whole budget is 80 ms a frame with
    predict() already taking 55 of it. Off-thread means an SD card having a bad
    moment shows up as a late clip rather than a dropped wake word.
    """
    try:
        NEAR_MISS_DIR.mkdir(exist_ok=True)
        path = NEAR_MISS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{score:.3f}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(np.concatenate(frames).tobytes())

        # Oldest first, by the timestamp the name starts with. Non-recursive, so
        # clips already moved into verified/ are out of the cap's reach — a
        # reviewed batch cannot be thrown away by a noisy afternoon.
        old = sorted(NEAR_MISS_DIR.glob("*.wav"))[:-NEAR_MISS_KEEP]
        for p in old:
            # NEAR_MISS_COOLDOWN_SEC should mean only one of these runs at a
            # time, but losing a clip to a misleading "save failed" line if that
            # ever stops holding is not a trade worth making.
            p.unlink(missing_ok=True)
        if old:
            log(f"near-miss cap: dropped {len(old)} oldest")
    except Exception as e:
        log(f"near-miss save failed: {e}")


def record(proc, preroll, grace_sec=0.0):
    """Record until silence or MAX_RECORD_SEC, reusing the detection stream.

    Reopening the device here would race the still-closing capture handle and
    swallow the first fraction of a second of the question.

    grace_sec holds the silence gate shut for the first stretch of the
    recording. The wake word needs none of it — you have just spoken, so the
    gate starts from speech — but a button on a phone is pressed *before* the
    sentence exists, and without a lead-in the whole recording is 2.5s of a
    room going quiet.
    """
    frames = list(preroll)
    gate = SilenceGate()
    max_frames = int(MAX_RECORD_SEC / FRAME_SEC)
    grace_frames = int(grace_sec / FRAME_SEC)

    for i in range(max_frames):
        frame = read_frame(proc)
        if frame is None:
            break
        frames.append(frame)
        # Not fed rather than fed-and-ignored: the gate counts consecutive
        # quiet frames, so skipping them leaves the run at zero, which is what
        # "we are still waiting for you to start" means.
        if i < grace_frames:
            continue
        if gate.feed(frame):
            log("silence, stopping recording")
            break
    else:
        log(f"hit {MAX_RECORD_SEC}s cap, stopping recording")

    return frames


def listen_forever():
    from openwakeword.model import Model

    model_path = find_model()
    label = Path(model_path).stem
    log(f"loading {label}")
    # openwakeword 0.6.0 (the piwheels build for aarch64/py3.13) is ONNX-only:
    # the arg is wakeword_model_paths, and there is no inference_framework —
    # passing one falls through **kwargs into AudioFeatures and raises.
    oww = Model(wakeword_model_paths=[model_path])

    preroll = deque(maxlen=max(1, int(PREROLL_SEC / FRAME_SEC)))
    # Longer than preroll, and never fed into a recording: this one exists only
    # to be dumped when the model nearly fires.
    history = deque(maxlen=NEAR_MISS_FRAMES)
    last_fire = 0.0
    last_near_miss = 0.0
    last_capture = 0.0
    # Frames still owed to an armed capture, and the best score seen since it was
    # armed. pending == 0 means nothing is in flight.
    pending, peak = 0, 0.0

    # Rings alarms main.py has decided are due. Its own thread because it spends
    # most of its life blocked in aplay, and the detection loop below cannot
    # afford to wait on that — a blocked read backs up arecord's pipe and every
    # wake word after it lands late.
    threading.Thread(target=alarm_watch_loop, daemon=True).start()
    threading.Thread(target=remote_trigger_loop, daemon=True).start()

    proc = open_mic()
    log(f"listening on {MIC_DEVICE} (threshold {DETECTION_THRESHOLD}, gain {MIC_GAIN})")
    # Detection can only be as prompt as the loop is fast: if predict() takes
    # longer than the 80 ms of audio each frame holds, arecord's pipe backs up
    # and every trigger fires later than the last. Measured, not fixed — the
    # number says whether a late screen is this loop or the network.
    frames_seen = 0
    started = time.monotonic()
    try:
        while True:
            frame = read_frame(proc)
            if frame is None:
                # arecord exited — mic pulled, or the device is busy. Die and
                # let systemd's Restart=always retry until it comes back.
                raise SystemExit(
                    f"arecord stopped on {MIC_DEVICE}: "
                    f"{mic_error(proc) or 'no error output'}")
            preroll.append(frame)
            history.append(frame)

            frames_seen += 1
            if frames_seen % 25 == 0:  # ~2s of audio
                lag = (time.monotonic() - started) - frames_seen * FRAME_SEC
                if lag > 1.0:
                    # Catching up is impossible by definition — the pipe grew
                    # because we read slower than realtime. Drop the backlog so
                    # detection and the recording that follows it are live.
                    secs = drain(proc)
                    log(f"behind live audio by {lag:.1f}s — dropped {secs:.1f}s of stale audio")
                    preroll.clear()
                    # An armed capture cannot survive a drop: its buffer would be
                    # stitched from either side of the gap, which is a clip of
                    # something nobody said.
                    history.clear()
                    pending = 0
                    oww.reset()
                    frames_seen = 0
                    started = time.monotonic()
                    continue

            # A tap on the phone enters here rather than through the model, and
            # then takes exactly the same path: record until silence, upload,
            # answer. Checked before predict() because it costs nothing and the
            # tap should not wait on a frame that was going to miss anyway.
            remote = REMOTE_TRIGGER.is_set()
            if remote:
                REMOTE_TRIGGER.clear()
                last_fire = time.monotonic()
                log("remote trigger — recording")
                # No notify_listening: the endpoint that set this flag already
                # put the row in 'listening', and posting again would only
                # re-broadcast a state the screen is showing.
            else:
                score = oww.predict(frame).get(label, 0.0)

                # An armed capture keeps buffering past the trigger, tracking the
                # peak so the clip is filed under the best score the model gave
                # rather than the first one over the line.
                if pending:
                    peak = max(peak, score)
                    pending -= 1
                    if not pending:
                        # list() runs here, on this thread, before the worker
                        # exists — so the snapshot cannot be torn by the appends
                        # that follow it. read_frame allocates a fresh array per
                        # call, so nothing already in it is mutated either.
                        threading.Thread(target=save_near_miss,
                                         args=(list(history), peak),
                                         daemon=True).start()
                        last_capture = time.monotonic()

                if score < DETECTION_THRESHOLD:
                    # A miss is otherwise invisible, which makes "it only hears
                    # one tone" impossible to act on: a 0.45 means the threshold
                    # is too high, a 0.05 means the model does not know that
                    # delivery at all and no threshold will help. Rate-limited
                    # so a noisy room cannot flood the journal.
                    if score >= NEAR_MISS_SCORE and time.monotonic() - last_near_miss > 1.0:
                        last_near_miss = time.monotonic()
                        log(f"near miss: {score:.3f}")
                        # Arming is gated separately, and more slowly: a journal
                        # line costs nothing, two seconds of audio costs a file.
                        # Measured from the last dump, so consecutive clips are a
                        # full buffer apart and cannot share a sample.
                        if (not pending
                                and time.monotonic() - last_capture >= NEAR_MISS_COOLDOWN_SEC):
                            pending, peak = NEAR_MISS_TAIL_FRAMES, score
                    continue
                if time.monotonic() - last_fire < COOLDOWN_SEC:
                    continue

                last_fire = time.monotonic()
                # A real hit mid-capture is not a near miss. Drop the armed clip:
                # the question recording about to run covers this audio anyway,
                # and a "miss" the model actually caught would go into the
                # training set mislabelled, which is the one thing this whole
                # pipeline must not produce.
                pending = 0
                log(f"wake word detected, confidence: {score:.3f}")

                # Fire-and-forget so a slow Railway round trip does not eat the
                # start of the question.
                threading.Thread(target=notify_listening, daemon=True).start()

            frames = record(proc, preroll,
                            grace_sec=REMOTE_LEAD_IN_SEC if remote else 0.0)
            path = save_wav(frames)
            log(f"saved {path}")
            # Off-thread: transcription plus Claude runs to tens of seconds, and
            # blocking here would back up arecord's pipe and make the next wake
            # word land late — the same lag the drift check below watches for.
            threading.Thread(target=send_audio, args=(path,), daemon=True).start()

            # Clear the model's internal audio buffer, otherwise the wake word
            # still sitting in it re-fires the moment we resume.
            preroll.clear()
            # record() read frames straight off the pipe without passing them
            # through here, so what is left is from before the question — two
            # seconds either side of a gap that is now several seconds wide.
            history.clear()
            oww.reset()

            # Recording and saving consumed frames this counter never saw, so
            # restart the drift measurement rather than blame them on predict().
            frames_seen = 0
            started = time.monotonic()
    finally:
        proc.terminate()


def calibrate(seconds=10):
    """Print live RMS so SILENCE_RMS can be set against the real mic."""
    proc = open_mic()
    log(f"{seconds}s of RMS on {MIC_DEVICE} — speak for part of it, then stop")
    try:
        quiet, loud = [], []
        for _ in range(int(seconds / FRAME_SEC)):
            frame = read_frame(proc)
            if frame is None:
                raise SystemExit(
                    f"arecord stopped on {MIC_DEVICE}: "
                    f"{mic_error(proc) or 'no error output'}")
            v = rms(frame)
            (loud if v > SILENCE_RMS else quiet).append(v)
            print(f"  rms {v:8.1f}", flush=True)
        if quiet:
            print(f"quiet: n={len(quiet)} max={max(quiet):.1f}")
        if loud:
            print(f"loud:  n={len(loud)} min={min(loud):.1f} max={max(loud):.1f}")
        print(f"current SILENCE_RMS={SILENCE_RMS} — it should sit between them")
    finally:
        proc.terminate()


def selftest():
    """Runs without a mic, so it works on the dev machine too."""
    global MIC_GAIN  # the clip check below forces it on; restored in a finally

    quiet = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    loud = np.full(FRAME_SAMPLES, 3000, dtype=np.int16)
    # Clearly above the floor whatever MIC_GAIN is, so raising gain cannot
    # silently turn the gate's "speech" fixture into more silence.
    speech = np.full(FRAME_SAMPLES, min(int(SILENCE_RMS * 2), 30000), dtype=np.int16)

    assert rms(quiet) == 0.0
    assert abs(rms(loud) - 3000) < 1.0
    # Squaring int16 in-place would overflow and give a bogus small RMS.
    assert rms(np.full(FRAME_SAMPLES, 32000, dtype=np.int16)) > 31000

    gate = SilenceGate()
    assert gate.needed == 31, gate.needed  # 2.5s / 80ms
    # The gap between "hey desky" and the question measured 17 frames. The hold
    # window has to clear that comfortably or recordings stop before the ask.
    assert gate.needed > 17 + 8, "hold window too short for the wake-word pause"
    for i in range(gate.needed - 1):
        assert not gate.feed(quiet), f"stopped early at frame {i}"
    assert gate.feed(quiet), "did not stop after the hold window"

    # Speech mid-run must reset the countdown, not shorten it.
    gate = SilenceGate()
    for _ in range(gate.needed - 1):
        gate.feed(quiet)
    assert not gate.feed(speech)
    assert gate.quiet_run == 0
    for _ in range(gate.needed - 1):
        assert not gate.feed(quiet)
    assert gate.feed(quiet)

    # A frame at the measured ambient floor must still read as silence *after*
    # gain, otherwise recordings never stop early and always run to the cap.
    ambient = np.full(FRAME_SAMPLES, int(515 * MIC_GAIN), dtype=np.int16)
    assert not SilenceGate().feed(ambient)

    # The lead-in is the whole reason a phone-triggered ask records anything:
    # without it a tap captures 2.5s of a quiet room and Whisper hears nothing.
    class SilentProc:
        """A mic that only ever returns silence."""
        def __init__(self):
            self.reads = 0

        def read_frame(self):
            self.reads += 1
            return quiet

    def record_frames(grace_sec):
        mic = SilentProc()
        real_read = globals()["read_frame"]
        globals()["read_frame"] = lambda proc: proc.read_frame()
        try:
            return len(record(mic, [], grace_sec=grace_sec))
        finally:
            globals()["read_frame"] = real_read

    hold = SilenceGate().needed
    # No grace: silence alone ends it after exactly the hold window.
    assert record_frames(0.0) == hold, record_frames(0.0)
    # With one: the grace frames come first, then the same hold.
    lead = int(REMOTE_LEAD_IN_SEC / FRAME_SEC)
    assert record_frames(REMOTE_LEAD_IN_SEC) == lead + hold, record_frames(REMOTE_LEAD_IN_SEC)
    # And the whole of it still has to fit inside the cap, or the lead-in would
    # only ever produce recordings that ran to the ceiling.
    assert lead + hold < int(MAX_RECORD_SEC / FRAME_SEC), "lead-in overruns MAX_RECORD_SEC"

    # remote_trigger_loop parses the SSE stream with json, and its only failure
    # mode is silent: a NameError there is swallowed by the loop's catch-all, so
    # the phone's listen button does nothing while the journal repeats "trigger
    # stream: name 'json' is not defined" every three seconds. This is the check
    # that the import is still there.
    assert json.loads('{"voice_trigger": 1}').get("voice_trigger") == 1

    # read_frame must lift a quiet frame and clip a loud one rather than wrap it.
    class FakeProc:
        def __init__(self, frame):
            self.stdout = io.BytesIO(frame.tobytes())

    faint = np.full(FRAME_SAMPLES, 500, dtype=np.int16)
    assert abs(rms(read_frame(FakeProc(faint))) - 500 * MIC_GAIN) < 1.0
    # A short read (arecord died mid-frame) is None, not a truncated frame.
    assert read_frame(FakeProc(faint[:10])) is None

    # MIC_GAIN ships at 1.0, which skips the scaling path entirely, so force it
    # on to prove the clip still holds for anyone who turns the knob back up.
    original_gain, MIC_GAIN = MIC_GAIN, 3.0
    try:
        near_full = np.full(FRAME_SAMPLES, 20000, dtype=np.int16)
        out = read_frame(FakeProc(near_full))
        assert out.min() > 0, "gain wrapped a loud sample negative"
        assert out.max() <= 32767
        assert abs(rms(read_frame(FakeProc(faint))) - 1500) < 1.0
    finally:
        MIC_GAIN = original_gain

    # The alarm fallback has to be a WAV aplay will actually take, and it has to
    # be audible — a silent buffer would "work" everywhere except the one moment
    # it exists for.
    raw = beep_wav()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE", raw[:12]
    with wave.open(io.BytesIO(raw)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.getframerate() == SAMPLE_RATE
        beep = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert len(beep) == int(SAMPLE_RATE * ALARM_BEEP_SEC), len(beep)
    assert rms(beep) > 1000, f"beep is too quiet to wake anyone: rms {rms(beep):.0f}"
    assert beep.min() < 0 < beep.max(), "beep never leaves zero"
    # Pulsed, not continuous: there must be silence in there somewhere.
    assert (beep == 0).any(), "beep is a solid tone, not bursts"
    assert beep_wav() is raw, "beep rebuilt instead of being cached"

    # The tune has to be a WAV aplay will actually take. A conversion that quietly
    # produced something else — or an mp3 renamed .wav — fails at exactly one
    # moment, and it is the moment nobody is awake to debug it.
    import tempfile
    tune = alarm_audio()
    assert tune is not beep_wav(), f"{ALARM_TUNE} missing — the ring fell back to the beep"
    assert tune[:4] == b"RIFF" and tune[8:12] == b"WAVE", tune[:12]
    with wave.open(io.BytesIO(tune)) as w:
        assert w.getsampwidth() == 2, w.getsampwidth()
        seconds = w.getnframes() / w.getframerate()
    # Long enough to be a ring rather than a blip, and short enough that the
    # dismiss-mid-pass path below is what stops it rather than the file ending.
    assert seconds > 5, f"alarm tune is {seconds:.1f}s — too short to wake anyone"
    assert alarm_audio() is tune, "tune re-read instead of being cached"

    # A missing tune must still make a noise. This is the whole reason beep_wav()
    # survives, so it is worth proving rather than assuming.
    _real_tune, _real_cache = ALARM_TUNE, _TUNE
    globals()["ALARM_TUNE"] = Path(tempfile.mkdtemp()) / "gone.wav"
    globals()["_TUNE"] = None
    try:
        assert alarm_audio() is beep_wav(), "a missing tune left the alarm silent"
    finally:
        globals()["ALARM_TUNE"], globals()["_TUNE"] = _real_tune, _real_cache

    # A dismiss has to stop the noise now, not at the end of the pass. This is
    # the whole reason play_alarm feeds aplay in chunks instead of writing once
    # like _play(): 42s of ringing after somebody switched it off is worse than
    # the alarm never having gone off.
    flag = Path(tempfile.mkdtemp()) / "desky_alarm"
    flag.write_text("1 WAKE UP")

    class FakeAplay:
        """aplay that plays forever, and a flag dropped two chunks in."""
        returncode = None

        def __init__(self):
            self.written = 0
            self.stdin = self
            self.stderr = io.BytesIO()
            self.killed = False

        def write(self, buf):
            self.written += len(buf)
            if self.written >= 2 * ALARM_CHUNK_BYTES:
                flag.unlink(missing_ok=True)

        def close(self):
            pass

        def poll(self):
            return self.returncode

        def terminate(self):
            self.killed = True
            self.returncode = -15

        def wait(self):
            return self.returncode

    fake = FakeAplay()
    whole = 20 * ALARM_CHUNK_BYTES
    _real_flag, _real_sub = ALARM_FILE, subprocess
    globals()["ALARM_FILE"] = str(flag)
    globals()["subprocess"] = type("Stub", (), {"PIPE": None,
                                                "Popen": staticmethod(lambda *a, **kw: fake)})
    try:
        play_alarm(b"\0" * whole)
    finally:
        globals()["ALARM_FILE"], globals()["subprocess"] = _real_flag, _real_sub
    assert fake.written < whole, "wrote the whole tune despite the dismiss"
    assert fake.killed, "dismiss left aplay running on its buffered audio"

    # A reminder must not leave its flag behind, or main.py's screen state and
    # this process disagree about whether it has been said. The path worth
    # pinning is the one with no speech available: it still has to clean up, and
    # it must not fall back to the alarm's beep — a noise that means "you were
    # reminded of something, but not what" is worse than silence.
    #
    # Kept off the network and off the speaker: SPEECH_URL points at a closed
    # port so the request fails at once, and _play is stubbed because aplay does
    # not exist on a dev machine.
    _real = (REMINDER_FILE, SPEECH_URL, _play)
    played = []
    globals()["REMINDER_FILE"] = str(Path(tempfile.mkdtemp()) / "desky_reminder")
    globals()["SPEECH_URL"] = "http://127.0.0.1:1/refused"
    globals()["_play"] = lambda wav: played.append(wav) or True
    try:
        Path(REMINDER_FILE).write_text("do laundry")
        speak_reminder()
        assert not Path(REMINDER_FILE).exists(), "reminder flag survived being spoken"
        assert played == [], "a reminder with no speech still made a noise"
        # An empty flag is still consumed, or it would be retried forever.
        Path(REMINDER_FILE).write_text("")
        speak_reminder()
        assert not Path(REMINDER_FILE).exists(), "empty reminder flag was left behind"
        assert played == []
    finally:
        globals()["REMINDER_FILE"], globals()["SPEECH_URL"], globals()["_play"] = _real

    # Near-miss clips become positive training data for the next model, so a
    # clip that is silent, truncated or quietly overwritten is worse than no
    # clip at all — it gets reviewed by ear and folded in on trust.
    #
    # The centring is the part most likely to break silently: a tail as long as
    # the buffer means every clip ends exactly at the trigger and contains only
    # the run-up, which sounds like a clipped word and would be discarded by
    # hand one at a time forever.
    assert NEAR_MISS_TAIL_FRAMES < NEAR_MISS_FRAMES, \
        "tail outruns the buffer — clips would end before the trigger"
    # Two clips must never share audio, or the same utterance is folded in twice
    # and weighted double. Measured from the dump, which is TAIL after the arm.
    assert NEAR_MISS_COOLDOWN_SEC >= NEAR_MISS_SEC, \
        "capture cooldown shorter than the buffer — consecutive clips overlap"

    _real_dir, _real_keep = NEAR_MISS_DIR, NEAR_MISS_KEEP
    globals()["NEAR_MISS_DIR"] = Path(tempfile.mkdtemp()) / "near-misses"
    globals()["NEAR_MISS_KEEP"] = 3
    try:
        NEAR_MISS_DIR.mkdir(parents=True)
        # Reviewed clips live in a subdirectory and the cap must not reach them.
        (NEAR_MISS_DIR / "verified").mkdir()
        keeper = NEAR_MISS_DIR / "verified" / "20200101_000000_0.900.wav"
        keeper.write_bytes(b"kept")
        for n in range(5):
            (NEAR_MISS_DIR / f"20200101_00000{n}_0.200.wav").write_bytes(b"old")

        save_near_miss([speech, speech], 0.234)
        left = sorted(p.name for p in NEAR_MISS_DIR.glob("*.wav"))
        assert len(left) == NEAR_MISS_KEEP, left
        assert keeper.exists(), "the cap deleted a reviewed clip"
        # Newest survives and the oldest go, or the cap throws away the misses
        # worth looking at and keeps last month's.
        assert left[-1].endswith("_0.234.wav"), left
        assert not any(n.startswith("20200101_000000") for n in left), left

        with wave.open(str(NEAR_MISS_DIR / left[-1])) as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2
            assert w.getframerate() == SAMPLE_RATE, w.getframerate()
            clip = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        assert len(clip) == 2 * FRAME_SAMPLES, len(clip)
        assert rms(clip) > 0, "wrote a silent clip"
    finally:
        globals()["NEAR_MISS_DIR"], globals()["NEAR_MISS_KEEP"] = _real_dir, _real_keep

    assert Path(find_model()).exists()

    # The real question on the Pi is whether the ONNX model loads and scores at
    # all — that is what the tflite backend could not do. Needs no mic, so it
    # runs here too; skipped on machines without openwakeword installed.
    try:
        from openwakeword.model import Model
    except ImportError:
        print("selftest ok (openwakeword not installed, model load skipped)")
        return

    model_path = find_model()
    label = Path(model_path).stem
    oww = Model(wakeword_model_paths=[model_path])
    scores = oww.predict(quiet)
    assert label in scores, f"expected key {label!r}, got {list(scores)}"
    assert 0.0 <= scores[label] <= 1.0, scores[label]
    print(f"selftest ok (model loaded, silence scores {scores[label]:.4f})")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        selftest()
    elif arg == "--calibrate":
        calibrate()
    else:
        listen_forever()
