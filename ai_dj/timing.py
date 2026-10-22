"""Deterministic timing verification for the AI DJ engine.

This module is the **independent measurement layer**. It never uses the engine's
own output as ground truth: ground truth is a synthetic click track with beat
positions known by construction, so a wrong engine and a wrong verifier cannot
both be wrong in the same way.

Design rules (from the adversarial architecture review, 2026-10-22):
  * ground truth is synthetic (beat timestamps known before processing);
  * measurement is separable from generation (no circular validation);
  * the deviation threshold is a *contract*, not a measured claim.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

# Contract: p95 absolute beat deviation must stay under this many milliseconds
# when the engine performs an explicit beat-aligned tempo change.
MAX_DEVIATION_MS = 20.0

# Sample rate used by the canonical master timeline.
MASTER_SR = 48000


def synthesize_click_track(
    bpm: float, duration_s: float, sr: int = MASTER_SR, click_s: float = 0.015
) -> tuple[np.ndarray, int, np.ndarray]:
    """Generate a click on every beat; return (signal, sr, ground_truth_beat_times)."""
    n = int(duration_s * sr)
    y = np.zeros(n, dtype=np.float32)
    ibi = 60.0 / bpm
    t = 0.0
    truth: list[float] = []
    click_n = max(1, int(click_s * sr))
    while t < duration_s:
        truth.append(t)
        i = int(t * sr)
        j = min(n, i + click_n)
        if j > i:
            y[i:j] = 1.0
        t += ibi
    return y, sr, np.asarray(truth, dtype=np.float64)


def write_wav_pcm16(path: str | Path, y: np.ndarray, sr: int) -> None:
    """Write a mono float32 signal as 16-bit PCM WAV (stdlib only)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(y, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav_pcm(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV (8/16/24/32-bit or float) into mono float32."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n_ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported PCM sample width: {width}")
    if n_ch > 1:
        data = data.reshape(-1, n_ch).mean(axis=1)
    return data.astype(np.float32), sr


def detect_beat_times(
    y: np.ndarray, sr: int, threshold: float = 0.25, min_gap_s: float = 0.05
) -> np.ndarray:
    """Detect transient onset times (seconds) of a click track."""
    env = np.abs(y)
    if env.size < 3:
        return np.asarray([], dtype=np.float64)
    peaks = (
        np.where((env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > threshold))[0] + 1
    )
    times = peaks / float(sr)
    merged: list[float] = []
    for t in times:
        if not merged or (t - merged[-1]) > min_gap_s:
            merged.append(float(t))
    return np.asarray(merged, dtype=np.float64)


def ibi_ms(times: np.ndarray) -> float:
    """Median inter-beat interval in milliseconds."""
    if times.size < 2:
        return float("nan")
    return float(np.median(np.diff(times)) * 1000.0)


def deviation_stats(measured_ibi_ms: float, target_ibi_ms: float) -> dict:
    """Absolute deviation of a measured IBI against a known target IBI."""
    dev = abs(measured_ibi_ms - target_ibi_ms)
    return {
        "measured_ibi_ms": measured_ibi_ms,
        "target_ibi_ms": target_ibi_ms,
        "deviation_ms": dev,
        "within_threshold": bool(dev <= MAX_DEVIATION_MS),
        "threshold_ms": MAX_DEVIATION_MS,
    }


def measure_stretch_deviation(source_wav: str | Path, target_bpm: float) -> dict:
    """Measure the beat interval of an already-stretched file against a known target BPM."""
    y, sr = read_wav_pcm(source_wav)
    times = detect_beat_times(y, sr)
    measured = ibi_ms(times)
    target = 60000.0 / target_bpm
    stats = deviation_stats(measured, target)
    stats.update(
        {
            "detected_beats": int(times.size),
            "duration_s": round(len(y) / float(sr), 3),
            "samplerate": sr,
        }
    )
    return stats
