#!/usr/bin/env python3
"""E2E timing probe: synthetic click track -> FFmpeg rubberband -> measured deviation.

This is the *ground-truth* timing gate. It does not trust any engine-reported
number: the target beat interval is known before processing (the click track is
generated at an exact BPM and stretched by an exact tempo factor), so the only
unknown is how accurate the stretch actually is.

Usage:
    python scripts/timing_probe.py --bpm 120 --tempo 1.08 --out evidence/timing_probe.json

Exit code 0 = deviation within contract; 1 = outside contract; 2 = ffmpeg missing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj.timing import (
    MASTER_SR,
    measure_stretch_deviation,
    synthesize_click_track,
    write_wav_pcm16,
)


def stretch_with_rubberband(src: Path, dst: Path, tempo: float) -> None:
    """Time-stretch `src` to `dst` using FFmpeg's rubberband filter."""
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-af",
        f"rubberband=tempo={tempo!r}",
        "-ar",
        str(MASTER_SR),
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--tempo", type=float, default=1.08)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--out", type=Path, default=Path("evidence/timing_probe.json"))
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(json.dumps({"skipped": "ffmpeg not found on PATH", "gate": "G2a"}))
        return 2

    target_bpm = args.bpm * args.tempo
    target_ibi_ms = 60000.0 / target_bpm

    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        src = td / "click_src.wav"
        dst = td / "click_stretched.wav"
        y, sr, truth = synthesize_click_track(args.bpm, args.duration)
        write_wav_pcm16(src, y, sr)
        stretch_with_rubberband(src, dst, args.tempo)
        stats = measure_stretch_deviation(dst, target_bpm)

    report = {
        "gate": "G2a",
        "probe": "synthetic_click_track_rubberband",
        "bpm_source": args.bpm,
        "tempo_factor": args.tempo,
        "target_bpm": round(target_bpm, 6),
        "target_ibi_ms": round(target_ibi_ms, 3),
        "ffmpeg": ffmpeg,
        **stats,
        "ground_truth_beats": int(truth.size),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if stats["within_threshold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
