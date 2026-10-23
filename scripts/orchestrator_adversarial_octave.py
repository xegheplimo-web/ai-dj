#!/usr/bin/env python3
"""Adversarial probe for the octave resolver (DJ-P2-OCTAVE-001).

Devin's fix resolves the four half-time T1 failures by DOUBLING the detected tempo.
The mirror risk is that it now doubles genuinely SLOW music, or that its 128 BPM
tempo prior biases the choice. The agent's T1 range started at 88 BPM, so the low
end was never tested.

This probe asks: can a correct slow tempo be distinguished from a wrongly-halved
fast one? Ground truth is known before synthesis in every case.

Usage:
    PYTHONPATH=<repo> python adversarial_octave.py --out <path>.json
Exit 0 = no adversarial failure, 1 = failure found.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from orchestrator_musical_synthetic_benchmark import render_pattern  # noqa: E402

from ai_dj.analysis.analyzer import analyze_track  # noqa: E402
from ai_dj.timing import MASTER_SR, synthesize_click_track, write_wav_pcm16  # noqa: E402

TOL = 0.02  # 2%


def row(name, truth, payload, note=""):
    rep = payload.get("bpm_corrected")
    det = payload.get("bpm_detected")
    rel = abs(rep - truth) / truth * 100.0 if rep else None
    q = payload.get("beat_quality") or {}
    return {
        "case": name,
        "truth_bpm": truth,
        "bpm_detected": det,
        "bpm_corrected": rep,
        "rel_error_pct": round(rel, 4) if rel is not None else None,
        "ok": bool(rel is not None and rel <= TOL * 100),
        "status": q.get("status"),
        "octave_ambiguous": q.get("octave_ambiguous"),
        "octave_diagnosis": payload.get("octave_diagnosis"),
        "confidence": payload.get("tempo_confidence"),
        "note": note,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("adversarial_octave.json"))
    args = ap.parse_args()

    rows = []
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)

        # A. SLOW clicks -- must NOT be doubled. This is the mirror of the half-time bug.
        for bpm in (60.0, 66.0, 72.0, 78.0, 84.0, 90.0, 96.0):
            wav = td / f"slow_{bpm:.1f}.wav"
            y, sr, _ = synthesize_click_track(bpm, 25.0)
            write_wav_pcm16(wav, y, sr)
            rows.append(
                row(f"slow_click_{bpm:.1f}", bpm, analyze_track(str(wav)), "must not double")
            )

        # B. SLOW drum patterns -- same question on musical material.
        for bpm in (70.0, 80.0, 90.0):
            wav = td / f"slow_drum_{bpm:.0f}.wav"
            y, _truth = render_pattern(bpm, 25.0)
            write_wav_pcm16(wav, y, MASTER_SR)
            rows.append(
                row(f"slow_drum_{bpm:.0f}", bpm, analyze_track(str(wav)), "must not double")
            )

        # C. The four half-time cases the fix targets -- must be correct.
        for bpm in (168.0, 174.0, 178.0, 180.0):
            wav = td / f"fast_{bpm:.0f}.wav"
            y, sr, _ = synthesize_click_track(bpm, 20.0)
            write_wav_pcm16(wav, y, sr)
            rows.append(
                row(
                    f"fast_click_{bpm:.0f}", bpm, analyze_track(str(wav)), "the D26 B1 target cases"
                )
            )

        # D. Same tempo, different transient shape -- does the answer stay stable?
        for bpm in (120.0, 140.0):
            wav = td / f"drum_{bpm:.0f}.wav"
            y, _truth = render_pattern(bpm, 25.0)
            write_wav_pcm16(wav, y, MASTER_SR)
            rows.append(row(f"drum_{bpm:.0f}", bpm, analyze_track(str(wav)), "control"))

    bad = [r for r in rows if not r["ok"]]
    doubled_slow = [r for r in rows if r["case"].startswith("slow_") and r["ok"] is False]
    amb = [r for r in rows if r.get("octave_ambiguous")]

    out = {
        "probe": "adversarial_octave",
        "tolerance_pct": TOL * 100,
        "n": len(rows),
        "n_bad": len(bad),
        "n_slow_falsely_doubled": len(doubled_slow),
        "n_ambiguous": len(amb),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"{'case':22} {'truth':>8} {'detected':>10} {'corrected':>10} {'err%':>8} "
        f"{'status':>8} {'ambig':>6} {'conf':>5} {'ok':>5}"
    )
    for r in rows:
        print(
            f"{r['case']:22} {r['truth_bpm']:8.1f} {r['bpm_detected']!s:>10} "
            f"{r['bpm_corrected']!s:>10} {r['rel_error_pct']!s:>8} "
            f"{r['status']!s:>8} {r['octave_ambiguous']!s:>6} "
            f"{r['confidence']!s:>5} {r['ok']!s:>5}"
        )

    print(
        f"\ncases={out['n']}  bad={out['n_bad']}  "
        f"slow_falsely_doubled={out['n_slow_falsely_doubled']}  ambiguous={out['n_ambiguous']}"
    )
    if bad:
        print("\nFAILURES:")
        for r in bad:
            print(
                f"  {r['case']}: truth={r['truth_bpm']} -> {r['bpm_corrected']} "
                f"({r['rel_error_pct']}%) detected={r['bpm_detected']} "
                f"ambig={r['octave_ambiguous']} note={r['note']}"
            )
    print("\nwritten:", args.out)
    print("GATE:", "FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
