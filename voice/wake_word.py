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
import wave
import glob
import threading
import subprocess
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
    return subprocess.Popen(
        ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
         "-c", "1", "-t", "raw", "-q", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


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

# Gap between repeats. Long enough to be a repeating alarm rather than a wall of
# noise, short enough that the silence never reads as "it stopped".
ALARM_REPEAT_SEC = 8.0

# The fallback tone, for when the network or the TTS quota is gone.
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


def alarm_label():
    """The label of the ringing alarm, or "" — the flag holds "<id> <label>"."""
    try:
        with open(ALARM_FILE) as f:
            parts = f.read().strip().split(" ", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    except Exception:
        return ""


def alarm_audio(label):
    """The audio for this ring, fetched once and reused for every repeat.

    Once, not per repeat: re-synthesising every eight seconds would spend the
    daily TTS quota inside a couple of minutes and then start failing partway
    through — the alarm would get quieter the longer nobody answered it, which
    is precisely backwards.
    """
    text = f"{label}." if label else "Wake up."
    try:
        r = requests.post(SPEECH_URL, json={"text": text}, timeout=30)
        if r.status_code == 200 and r.content:
            log(f"alarm audio: {len(r.content)} bytes of speech")
            return r.content
        log(f"alarm speech -> {r.status_code}: {r.text[:120]} — falling back to the beep")
    except Exception as e:
        log(f"alarm speech failed ({e}) — falling back to the beep")
    return beep_wav()


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
    audio = None
    last = 0.0
    while True:
        try:
            if not os.path.exists(ALARM_FILE):
                # Dropped between repeats: forget the audio, so the next ring
                # fetches against its own label rather than the last one's.
                audio = None
                if os.path.exists(REMINDER_FILE):
                    speak_reminder()
                    continue
                time.sleep(0.5)
                continue

            if audio is None:
                audio = alarm_audio(alarm_label())
                last = 0.0                      # ring now, then on the gap

            if time.monotonic() - last >= ALARM_REPEAT_SEC:
                last = time.monotonic()
                # A failure here is usually the voice loop holding the speaker
                # mid-answer. Not worth handling: the next repeat is eight
                # seconds away and that answer will be over by then.
                _play(audio)
        except Exception as e:
            log(f"alarm loop: {e}")
            time.sleep(1.0)
        time.sleep(0.25)


def save_wav(frames):
    RECORDINGS_DIR.mkdir(exist_ok=True)
    path = RECORDINGS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.concatenate(frames).tobytes())
    return path


def record(proc, preroll):
    """Record until silence or MAX_RECORD_SEC, reusing the detection stream.

    Reopening the device here would race the still-closing capture handle and
    swallow the first fraction of a second of the question.
    """
    frames = list(preroll)
    gate = SilenceGate()
    max_frames = int(MAX_RECORD_SEC / FRAME_SEC)

    for _ in range(max_frames):
        frame = read_frame(proc)
        if frame is None:
            break
        frames.append(frame)
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
    last_fire = 0.0
    last_near_miss = 0.0

    # Rings alarms main.py has decided are due. Its own thread because it spends
    # most of its life blocked in aplay, and the detection loop below cannot
    # afford to wait on that — a blocked read backs up arecord's pipe and every
    # wake word after it lands late.
    threading.Thread(target=alarm_watch_loop, daemon=True).start()

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
                raise SystemExit(f"arecord stopped on {MIC_DEVICE}")
            preroll.append(frame)

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
                    oww.reset()
                    frames_seen = 0
                    started = time.monotonic()
                    continue

            score = oww.predict(frame).get(label, 0.0)
            if score < DETECTION_THRESHOLD:
                # A miss is otherwise invisible, which makes "it only hears one
                # tone" impossible to act on: a 0.45 means the threshold is too
                # high, a 0.05 means the model does not know that delivery at
                # all and no threshold will help. Rate-limited so a noisy room
                # cannot flood the journal.
                if score >= NEAR_MISS_SCORE and time.monotonic() - last_near_miss > 1.0:
                    last_near_miss = time.monotonic()
                    log(f"near miss: {score:.3f}")
                continue
            if time.monotonic() - last_fire < COOLDOWN_SEC:
                continue

            last_fire = time.monotonic()
            log(f"wake word detected, confidence: {score:.3f}")

            # Fire-and-forget so a slow Railway round trip does not eat the
            # start of the question.
            threading.Thread(target=notify_listening, daemon=True).start()

            frames = record(proc, preroll)
            path = save_wav(frames)
            log(f"saved {path}")
            # Off-thread: transcription plus Claude runs to tens of seconds, and
            # blocking here would back up arecord's pipe and make the next wake
            # word land late — the same lag the drift check below watches for.
            threading.Thread(target=send_audio, args=(path,), daemon=True).start()

            # Clear the model's internal audio buffer, otherwise the wake word
            # still sitting in it re-fires the moment we resume.
            preroll.clear()
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
                raise SystemExit(f"arecord stopped on {MIC_DEVICE}")
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

    # The flag file carries "<id> <label>", and every part of it is optional
    # except the id — a label with spaces must survive intact.
    import tempfile
    global ALARM_FILE
    _real_flag, tmpdir = ALARM_FILE, tempfile.mkdtemp()
    ALARM_FILE = str(Path(tmpdir) / "desky_alarm")
    try:
        assert alarm_label() == "", "missing flag file should read as no label"
        for written, want in (("1 WAKE UP", "WAKE UP"), ("2 CHECK THE OVEN", "CHECK THE OVEN"),
                              ("3", ""), ("4  ", ""), ("5 x", "x")):
            Path(ALARM_FILE).write_text(written)
            assert alarm_label() == want, f"{written!r} -> {alarm_label()!r}, want {want!r}"
    finally:
        ALARM_FILE = _real_flag

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
