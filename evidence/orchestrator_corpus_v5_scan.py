#!/usr/bin/env python3
"""Re-analyze the full corpus with the CURRENT analyzer and report the octave /
ambiguity / quality outcome. The v4 cache is stale by design under v5, so there is
no shipped v5 cache to read -- this builds the measurement.

Usage:
    PYTHONPATH=<repo> python corpus_v5_scan.py --repo <repo> --out <path>.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--cache", default="analysis_cache.sqlite")
    ap.add_argument("--out", type=Path, default=Path("corpus_v5_scan.json"))
    args = ap.parse_args()

    sys.path.insert(0, args.repo)
    from ai_dj.analysis.analyzer import analyze_track

    cache = Path(args.repo) / args.cache
    con = sqlite3.connect(cache)
    rows = list(con.execute("select recording_id, path, payload from tracks"))

    out_rows = []
    for i, (rid, path, payload) in enumerate(rows, 1):
        try:
            p = analyze_track(path)
            q = p.get("beat_quality") or {}
            out_rows.append({
                "recording_id": rid,
                "title": (json.loads(payload).get("title") if payload else None),
                "bpm_detected": p.get("bpm_detected"),
                "bpm_corrected": p.get("bpm_corrected"),
                "octave_ratio": (round(p["bpm_corrected"] / p["bpm_detected"], 4)
                                 if p.get("bpm_detected") and p.get("bpm_corrected") else None),
                "status": q.get("status"),
                "octave_ambiguous": q.get("octave_ambiguous"),
                "octave_level": (p.get("octave_diagnosis") or {}).get("selected_level")
                if isinstance(p.get("octave_diagnosis"), dict) else None,
                "octave_margin": (p.get("octave_diagnosis") or {}).get("margin")
                if isinstance(p.get("octave_diagnosis"), dict) else None,
                "confidence": p.get("tempo_confidence"),
                "residual_std_ms": q.get("residual_std_ms"),
                "beat_env_score": q.get("beat_env_score"),
                "reasons": q.get("reasons"),
            })
        except Exception as exc:
            out_rows.append({"recording_id": rid, "error": repr(exc)})
        print(f"[{i}/{len(rows)}] {rid} -> {out_rows[-1].get('bpm_corrected')} "
              f"{out_rows[-1].get('status')} ambig={out_rows[-1].get('octave_ambiguous')}",
              flush=True)

    ok = [r for r in out_rows if "error" not in r]
    amb = [r for r in ok if r.get("octave_ambiguous")]
    sus = [r for r in ok if r.get("status") == "suspect"]
    lvl = {}
    for r in ok:
        lvl[r.get("octave_level")] = lvl.get(r.get("octave_level"), 0) + 1
    ratios = {}
    for r in ok:
        ratios[r.get("octave_ratio")] = ratios.get(r.get("octave_ratio"), 0) + 1

    report = {
        "analyzer_scan": "corpus_v5",
        "n": len(out_rows),
        "n_ok": len(ok),
        "n_ambiguous": len(amb),
        "n_suspect": len(sus),
        "octave_level_histogram": lvl,
        "octave_ratio_histogram": ratios,
        "rows": out_rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== summary ===")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2, ensure_ascii=False))
    print("written:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
