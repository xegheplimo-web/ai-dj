#!/usr/bin/env python3
"""Independent known-answer verification of the beat/tempo estimator.

D26 step 3: the ORCHESTRATOR re-runs the known-answer benchmark independently of
whatever the coding agent reported. Every ground truth here is decided BEFORE
analysis, so the analyzer never contributes to its own answer.

This file deliberately lives OUTSIDE the agent's worktree so the agent cannot
delete or modify it (an earlier run removed orchestrator-owned scripts).

Tiers
  T1  controlled synthetic   rectangular-pulse click track across the tempo range
  T2  musical synthetic      kick / snare / hi-hat patterns (backbeat, swing, four-on-floor)
  T2n negative cases         material with no periodic beat -- the analyzer must say so

Usage:
    PYTHONPATH=<repo> python independent_beat_verification.py --out <path>.json
Exit codes: 0 = all gates met, 1 = a gate failed, 2 = harness error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ai_dj.analysis.analyzer import ANALYSIS_SR, analyze_track  # noqa: E402
from ai_dj.timing import MASTER_SR, synthesize_click_track, write_wav_pcm16  # noqa: E402

from musical_synthetic_benchmark import render_pattern  # noqa: E402

FRAME_PERIOD = 512 / ANALYSIS_SR

T1_BPMS = [
    88.0, 92.0, 96.0, 100.0, 104.0, 108.0, 112.0, 116.0,
    120.0, 122.5, 124.0, 126.0, 128.0, 130.0, 132.0, 134.0, 136.0,
    138.0, 140.0, 142.0, 144.0, 146.0, 148.0, 150.0, 152.0, 154.0,
    156.0, 158.0, 160.0, 161.484, 162.0, 164.0, 166.0, 166.686, 168.0,
    170.0, 172.267, 174.0, 176.0, 178.0, 180.0,
]

T2_CASES = [
    ("straight_120", 120.0, {}),
    ("straight_128", 128.0, {}),
    ("offladder_166_686", 166.686, {}),
    ("offladder_128_5", 128.5, {}),
    ("swing_120", 120.0, {"swing": 0.5}),
    ("half_time_120", 120.0, {"backbeat": True, "kick_every_beat": False, "hats_on_eighths": False}),
    ("four_on_floor_126", 126.0, {"kick_every_beat": True, "backbeat": False}),
    ("missing_every_4", 128.0, {"missing_every": 4}),
]

TOL_PCT = 2.0


def _row(name, truth_bpm, payload, extra=None):
    rep = payload.get("bpm_corrected")
    ref = payload.get("bpm_refined")
    ratio = (rep / truth_bpm) if (rep and truth_bpm == truth_bpm) else None
    rel = (abs(rep - truth_bpm) / truth_bpm * 100.0) if (rep and truth_bpm == truth_bpm) else None
    row = {
        "case": name,
        "truth_bpm": truth_bpm,
        "reported_bpm_corrected": rep,
        "reported_bpm_refined": ref,
        "status": payload.get("beat_grid_status") or payload.get("status"),
        "octave_ratio": round(ratio, 4) if ratio else None,
        "rel_error_pct": round(rel, 3) if rel is not None else None,
        "ok": (rel is not None and rel <= TOL_PCT),
        "confidence": payload.get("tempo_confidence"),
        "beat_quality": payload.get("beat_quality"),
    }
    if extra:
        row.update(extra)
    return row


def run_t1(td: Path):
    rows = []
    for bpm in T1_BPMS:
        wav = td / f"t1_{bpm:.3f}.wav"
        y, sr, _ = synthesize_click_track(bpm, 20.0)
        write_wav_pcm16(wav, y, sr)
        p = analyze_track(str(wav))
        ibi = p.get("median_ibi_s")
        frames = ibi / FRAME_PERIOD if ibi else None
        rows.append(_row(f"click_{bpm:.3f}", bpm, p, {
            "ibi_frames": round(frames, 4) if frames else None,
            "ibi_is_integer_frames": (abs(frames - round(frames)) < 1e-4) if frames else None,
        }))
    return rows


def run_t2(td: Path):
    rows = []
    for name, bpm, kw in T2_CASES:
        wav = td / f"t2_{name}.wav"
        y, truth = render_pattern(bpm, 20.0, **kw)
        write_wav_pcm16(wav, y, MASTER_SR)
        p = analyze_track(str(wav))
        rows.append(_row(f"t2_{name}", bpm, p, {"kwargs": kw, "truth_n_beats": len(truth)}))
    return rows


def run_negative(td: Path):
    rows = []
    rng = np.random.default_rng(20261023)
    dur, sr = 20.0, MASTER_SR
    n = int(dur * sr)

    y = (rng.standard_normal(n) * 0.3).astype(np.float32)
    wav = td / "neg_white_noise.wav"
    write_wav_pcm16(wav, y, sr)
    rows.append(_row("neg_white_noise", float("nan"), analyze_track(str(wav)),
                     {"expected": "no confident tempo"}))

    t = np.arange(n, dtype=np.float32) / sr
    y = (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    wav = td / "neg_pure_tone.wav"
    write_wav_pcm16(wav, y, sr)
    rows.append(_row("neg_pure_tone", float("nan"), analyze_track(str(wav)),
                     {"expected": "no confident tempo"}))

    y = np.zeros(n, dtype=np.float32)
    for _ in range(120):
        i = int(rng.uniform(0, dur - 0.05) * sr)
        j = min(n, i + int(0.015 * sr))
        y[i:j] = 0.9
    wav = td / "neg_random_clicks.wav"
    write_wav_pcm16(wav, y, sr)
    rows.append(_row("neg_random_clicks", float("nan"), analyze_track(str(wav)),
                     {"expected": "no confident tempo"}))
    return rows


def summarize(rows, label):
    scored = [r for r in rows if r["rel_error_pct"] is not None]
    bad = [r for r in scored if not r["ok"]]
    octave_bad = [r for r in scored if r["octave_ratio"] and abs(r["octave_ratio"] - 1.0) > 0.02]
    hi_conf_bad = [r for r in bad if (r["confidence"] or 0) >= 0.5]
    return {
        "tier": label,
        "n": len(rows),
        "n_scored": len(scored),
        "n_error_gt_tol": len(bad),
        "n_octave_wrong": len(octave_bad),
        "n_high_confidence_but_wrong": len(hi_conf_bad),
        "max_rel_error_pct": round(max((r["rel_error_pct"] or 0) for r in scored), 3) if scored else None,
        "bad_cases": [r["case"] for r in bad],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("independent_beat_verification.json"))
    args = ap.parse_args()

    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            t1 = run_t1(td)
            t2 = run_t2(td)
            neg = run_negative(td)
    except Exception as exc:
        print(f"HARNESS ERROR: {exc!r}", file=sys.stderr)
        return 2

    report = {
        "harness": "independent_beat_verification",
        "run_by": "orchestrator (outside agent worktree)",
        "tolerance_pct": TOL_PCT,
        "legacy_frame_period_s": FRAME_PERIOD,
        "note": "Ground truth decided before analysis in every tier. The analyzer never supplies its own answer.",
        "T1": t1, "T2": t2, "T2_negative": neg,
        "summary": {"T1": summarize(t1, "T1_controlled_synthetic"),
                    "T2": summarize(t2, "T2_musical_synthetic")},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    for tier, rows in (("T1", t1), ("T2", t2)):
        print(f"\n=== {tier} ===")
        print(f"{'case':26} {'truth':>10} {'reported':>10} {'err%':>9} {'ratio':>7} {'conf':>5} {'ok':>5}")
        for r in rows:
            tv = "nan" if r["truth_bpm"] != r["truth_bpm"] else f"{r['truth_bpm']:.3f}"
            print(f"{r['case']:26} {tv:>10} {str(r['reported_bpm_corrected']):>10} "
                  f"{str(r['rel_error_pct']):>9} {str(r['octave_ratio']):>7} "
                  f"{str(r['confidence']):>5} {str(r['ok']):>5}")

    print("\n=== negative cases (expected: no confident tempo) ===")
    for r in neg:
        q = r.get("beat_quality") or {}
        st = q.get("status") or q.get("suspect") or r.get("status")
        print(f"{r['case']:26} reported={str(r['reported_bpm_corrected']):>9} "
              f"conf={r['confidence']} quality_status={st} env_ratio={q.get('beat_env_ratio')}")

    print("\n=== summary ===")
    print(json.dumps(report["summary"], indent=2))
    print("\nwritten:", args.out)

    s1, s2 = report["summary"]["T1"], report["summary"]["T2"]
    failed = bool(s1["n_error_gt_tol"] or s2["n_error_gt_tol"]
                  or s1["n_octave_wrong"] or s2["n_octave_wrong"])
    print("\nGATE:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
