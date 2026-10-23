#!/usr/bin/env python3
"""T2 — Musical synthetic benchmark: robustness of tempo estimation.

D26 requires a three-tier known-answer benchmark. T1 (the rectangular-pulse click
track) only proves the algorithm works on one transient shape -- a real rectangular
pulse has a hard edge and a very different spectrum from a drum kit. T2 builds
musical synthetic patterns (kick / snare / hi-hat, backbeat, swung eighths) whose
beat positions are known BEFORE synthesis, so the analyzer is still measured against
a known answer -- never against its own output.

Ground truth is returned by the generator, not derived from the audio.

Usage:
    python scripts/musical_synthetic_benchmark.py --out evidence/t2_musical_benchmark.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj.timing import MASTER_SR, write_wav_pcm16

SR = MASTER_SR


def _env(n: int, attack: int, decay: float) -> np.ndarray:
    a = np.ones(max(1, attack), dtype=np.float32)
    d = np.exp(-np.arange(n - len(a), dtype=np.float32) / max(1.0, decay))
    return np.concatenate([a, d])[:n]


def kick(dur: float = 0.18, sr: int = SR) -> np.ndarray:
    """Swept sine -- low, percussive, the strongest downbeat cue."""
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float32) / sr
    f = 110.0 * np.exp(-t * 28.0) + 45.0
    return (np.sin(2 * np.pi * f * t) * _env(n, 8, 0.030)).astype(np.float32)


def snare(dur: float = 0.16, sr: int = SR) -> np.ndarray:
    """Noise burst + tonal body -- the backbeat cue."""
    n = int(dur * sr)
    rng = np.random.default_rng(1234)
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n, dtype=np.float32) / sr
    tone = np.sin(2 * np.pi * 190.0 * t).astype(np.float32)
    return ((0.7 * noise + 0.3 * tone) * _env(n, 4, 0.022)).astype(np.float32)


def hihat(dur: float = 0.045, sr: int = SR) -> np.ndarray:
    """Short high-passed noise -- subdivides the beat."""
    n = int(dur * sr)
    rng = np.random.default_rng(99)
    noise = rng.standard_normal(n).astype(np.float32)
    # crude high-pass: first difference emphasises the high band
    noise = np.diff(np.concatenate([[0.0], noise])).astype(np.float32)
    return (noise * _env(n, 2, 0.010)).astype(np.float32)


def render_pattern(
    bpm: float,
    duration_s: float,
    *,
    swing: float = 0.0,
    backbeat: bool = True,
    hats_on_eighths: bool = True,
    missing_every: int = 0,
    kick_every_beat: bool = False,
) -> tuple[np.ndarray, list[float]]:
    """Render a drum pattern; return (audio, ground_truth_beat_times).

    swing=0.0 -> straight eighths; swing=0.5 -> triplet feel (second eighth late).
    missing_every=N -> drop every Nth beat from the audio (ground truth still lists
    it, so a robust detector is expected to infer it or report low confidence).
    """
    ibi = 60.0 / bpm
    n = int(duration_s * SR)
    y = np.zeros(n, dtype=np.float32)
    k, s, h = kick(), snare(), hihat()

    truth: list[float] = []
    beat_i = 0
    t = 0.0
    while t < duration_s:
        truth.append(round(t, 9))
        drop = missing_every and (beat_i % missing_every == 0) and beat_i > 0
        bar_pos = beat_i % 4

        if not drop:
            if kick_every_beat or bar_pos in (0, 2):
                i = int(t * SR)
                j = min(n, i + len(k))
                y[i:j] += k[: j - i]
            if backbeat and bar_pos in (1, 3):
                i = int(t * SR)
                j = min(n, i + len(s))
                y[i:j] += s[: j - i]
        if hats_on_eighths and not drop:
            for frac in (0.0, 0.5 + swing * 0.5):
                ht = t + ibi * frac
                i = int(ht * SR)
                if i >= n:
                    continue
                j = min(n, i + len(h))
                y[i:j] += 0.35 * h[: j - i]

        beat_i += 1
        t += ibi

    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak * 0.85).astype(np.float32), truth


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("evidence/t2_musical_benchmark.json"))
    ap.add_argument("--duration", type=float, default=20.0)
    args = ap.parse_args()

    # Deferred on purpose: this import must follow the sys.path setup at module top.
    from ai_dj.analysis.analyzer import analyze_track  # noqa: PLC0415

    cases = [
        ("straight_120", 120.0, {}),
        ("straight_128", 128.0, {}),
        ("offladder_166_686", 166.686, {}),
        ("offladder_128_5", 128.5, {}),
        ("swing_120", 120.0, {"swing": 0.5}),
        (
            "half_time_120",
            120.0,
            {"backbeat": True, "kick_every_beat": False, "hats_on_eighths": False},
        ),
        ("four_on_floor_126", 126.0, {"kick_every_beat": True, "backbeat": False}),
        ("missing_every_4", 128.0, {"missing_every": 4}),
    ]

    rows = []
    with tempfile.TemporaryDirectory() as _tmpdir:
        td = Path(_tmpdir)
        for name, bpm, kw in cases:
            wav = td / f"{name}.wav"
            y, truth = render_pattern(bpm, args.duration, **kw)
            write_wav_pcm16(wav, y, SR)
            p = analyze_track(str(wav))
            rep = p.get("bpm_corrected")
            ratio = (rep / bpm) if rep else None
            rows.append(
                {
                    "case": name,
                    "truth_bpm": bpm,
                    "truth_n_beats": len(truth),
                    "reported_bpm": rep,
                    "reported_bpm_refined": p.get("bpm_refined"),
                    "reported_status": p.get("beat_grid_status") or p.get("status"),
                    "octave_ratio": round(ratio, 4) if ratio else None,
                    "abs_error_bpm": round(abs(rep - bpm), 4) if rep else None,
                    "rel_error_pct": round(abs(rep - bpm) / bpm * 100.0, 3) if rep else None,
                    "confidence": p.get("tempo_confidence"),
                    "kwargs": kw,
                }
            )

    bad = [r for r in rows if (r["rel_error_pct"] or 0) > 2.0]
    octave_bad = [r for r in rows if r["octave_ratio"] and abs(r["octave_ratio"] - 1.0) > 0.02]
    out = {
        "tier": "T2_musical_synthetic",
        "note": (
            "Ground truth beat times are produced by the generator BEFORE the audio is "
            "written; the analyzer never contributes to its own answer."
        ),
        "n_cases": len(rows),
        "n_error_gt_2pct": len(bad),
        "n_octave_wrong": len(octave_bad),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{'case':22} {'truth':>9} {'reported':>10} {'err%':>8} {'ratio':>7} {'conf':>5}")
    for r in rows:
        print(
            f"{r['case']:22} {r['truth_bpm']:9.3f} {r['reported_bpm']!s:>10} "
            f"{r['rel_error_pct']!s:>8} {r['octave_ratio']!s:>7} {r['confidence']!s:>5}"
        )
    print(
        f"\ncases={out['n_cases']}  err>2%={out['n_error_gt_2pct']}  "
        f"octave_wrong={out['n_octave_wrong']}"
    )
    print("written:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
