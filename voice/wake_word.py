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
# ElevenLabs key stays on the server.
SPEECH_URL = BACKEND + "/voice/speech"

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


def notify_listening():
    """Tell the server the wake word fired. Never fatal — a dead network
    should not stop us recording the question."""
    try:
        requests.post(LISTENING_URL, timeout=2)
    except Exception as e:
        log(f"POST /voice/listening failed: {e}")


def send_audio(path):
    """Upload the recording; the server transcribes it and answers from there.

    The reply only arrives once Whisper and Claude have both run, so the timeout
    is generous — but the screen does not wait on it. Byte moves to 'thinking'
    the moment the server has the transcript, over SSE.
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

    # Speaking is a bonus on top of the answer, which is already on screen via
    # SSE. A failure here must not look like a failure of the whole exchange.
    try:
        answer = r.json().get("answer", "")
    except Exception:
        answer = ""
    if answer:
        speak(answer)


def speak(text):
    """Say the answer out loud.

    The server returns WAV, header and all, so aplay reads the sample rate off
    the stream — the Pi never has to agree with the server about a format.
    """
    try:
        r = requests.post(SPEECH_URL, json={"text": text}, timeout=60)
        if r.status_code != 200:
            log(f"POST /voice/speech -> {r.status_code}: {r.text[:160]}")
            return
        # Playback runs while the loop keeps listening, so Desky can be
        # interrupted by another wake word mid-sentence. The mic will also hear
        # this audio; harmless today because we only record after a trigger.
        p = subprocess.run(["aplay", "-D", SPEAKER_DEVICE, "-q", "-"],
                           input=r.content, stderr=subprocess.PIPE)
        if p.returncode != 0:
            log(f"aplay failed: {p.stderr.decode(errors='replace')[:160]}")
        else:
            log(f"spoke {len(r.content)} bytes")
    except Exception as e:
        log(f"speak failed: {e}")


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
            if frames_seen % 250 == 0:  # ~20s of audio
                lag = (time.monotonic() - started) - frames_seen * FRAME_SEC
                if lag > 1.0:
                    log(f"behind live audio by {lag:.1f}s — detection will be late")

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
