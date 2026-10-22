"""AI DJ command line: scan / analyze / plan / render / play / verify.

Every command prints a JSON result on stdout (or a JSON error on stderr).
Exit codes: 0 = success/PASS, 1 = gate/logic failure/FAIL, 3 = INCONCLUSIVE
(a required measurement is missing; verify only), 2 = usage/environment
errors (no verdict is ever emitted on a usage error).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_dj.analysis.analyzer import DEFAULT_CACHE, AnalysisCache, analyze_corpus
from ai_dj.analysis.corpus import TrackRef, duplicate_report, scan_corpus
from ai_dj.planner import load_plan, plan_set, write_plan
from ai_dj.player import play_master
from ai_dj.render import render_plan
from ai_dj.verify import verify_master

DEFAULT_EVIDENCE = Path("evidence")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _fail(obj: dict, code: int) -> int:
    print(json.dumps(obj, indent=2, ensure_ascii=False), file=sys.stderr)
    return code


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.dir)
    if not root.is_dir():
        return _fail({"error": f"not a directory: {root}"}, 2)
    refs = scan_corpus(root)
    dup = duplicate_report(refs)
    report = {
        "root": str(root),
        "file_count": dup["file_count"],
        "recording_count": dup["recording_count"],
        "work_count": dup["work_count"],
        "duplicate_recordings": dup["duplicate_recordings"],
        "works_with_multiple_recordings": dup["works_with_multiple_recordings"],
        "errors": [t.to_dict() for t in refs if t.status != "ok"],
        "tracks": [t.to_dict() for t in refs],
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report["written_to"] = str(out)
    _emit(report)
    return 0


def _load_refs(args: argparse.Namespace):
    if args.corpus:
        data = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
        known = {f.name for f in TrackRef.__dataclass_fields__.values()}
        return [TrackRef(**{k: v for k, v in t.items() if k in known}) for t in data["tracks"]]
    root = Path(args.dir)
    if not root.is_dir():
        return None
    return scan_corpus(root)


def cmd_analyze(args: argparse.Namespace) -> int:
    refs = _load_refs(args)
    if refs is None:
        return _fail({"error": f"not a directory: {args.dir}"}, 2)
    todo = [r for r in refs if r.status == "ok"]
    records = analyze_corpus(todo, cache_path=args.cache, force=args.force)
    ok = [r for r in records if r["status"] == "ok"]
    errs = [
        {"recording_id": r["recording_id"], "title": r.get("title"), "error": r["error"]}
        for r in records
        if r["status"] != "ok"
    ]
    _emit(
        {
            "cache": str(args.cache),
            "total": len(records),
            "analyzed_ok": len(ok),
            "errors": errs,
            "excluded": [
                {
                    "recording_id": r["recording_id"],
                    "title": r.get("title"),
                    "exclusion_reason": r.get("exclusion_reason"),
                }
                for r in ok
                if r.get("eligible") is not True
            ],
            "tracks": [
                {
                    "recording_id": r["recording_id"],
                    "title": r.get("title"),
                    "bpm_raw": r.get("bpm_raw"),
                    "bpm_corrected": r.get("bpm_corrected"),
                    "octave_diagnosis": r.get("octave_diagnosis"),
                    "key": r.get("key"),
                    "tempo_confidence": r.get("tempo_confidence"),
                    "duration_s": r.get("duration_s"),
                    "silence_run_ratio": r.get("silence_run_ratio"),
                    "eligible": r.get("eligible"),
                    "exclusion_reason": r.get("exclusion_reason"),
                }
                for r in ok
            ],
        }
    )
    return 0 if ok or not errs else 1


def cmd_plan(args: argparse.Namespace) -> int:
    cache = AnalysisCache(args.cache)
    try:
        records = cache.all_records()
    finally:
        cache.close()
    if not records:
        return _fail({"error": f"analysis cache is empty: {args.cache} — run analyze first"}, 2)
    if args.include_ineligible:
        flagged = [r for r in records if r.get("status") == "ok" and r.get("eligible") is not True]
        print(
            f"WARNING: --include-ineligible — {len(flagged)} track(s) that failed "
            "the eligibility rule are selectable again:",
            file=sys.stderr,
        )
        for r in flagged:
            print(f"  - {r.get('title')}: {r.get('exclusion_reason')}", file=sys.stderr)
    include_ids = [args.include_track] if args.include_track else None
    try:
        plan = plan_set(
            records,
            n_tracks=args.tracks,
            set_name=args.set_name,
            seed=args.seed,
            energy_target=args.energy_target,
            include_ineligible=args.include_ineligible,
            include_ids=include_ids,
        )
    except ValueError as exc:
        return _fail({"error": str(exc)}, 1)
    out = write_plan(plan, args.out)
    result = {
        "written_to": str(out),
        "set_name": plan["set_name"],
        "eligibility": plan["eligibility"],
        "track_count": plan["track_count"],
        "total_duration_s": plan["timeline"]["total_duration_s"],
        "tracks": [
            {
                "index": t["index"],
                "title": t["title"],
                "bpm": t["bpm"],
                "key": t["key"],
                "tempo_stretch": t["tempo_stretch"],
                "gain_db": t["gain_db"],
            }
            for t in plan["tracks"]
        ],
        "transitions": [
            {
                "index": t["index"],
                "type": t["type"],
                "duration_s": t["duration_s"],
                "confidence": t["confidence"],
                "reasons": t["reasons"],
            }
            for t in plan["transitions"]
        ],
    }
    if "planner" in plan:
        result["request"] = plan["request"]
        result["planner"] = plan["planner"]
        result["anchor_resolution"] = plan["anchor_resolution"]
    _emit(result)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
    except (OSError, json.JSONDecodeError) as exc:
        return _fail({"error": f"cannot read plan: {exc}"}, 2)
    try:
        report = render_plan(plan, args.out, report_path=args.report)
    except RuntimeError as exc:
        return _fail({"error": str(exc)}, 2)
    except Exception as exc:
        return _fail({"error": f"render failed: {type(exc).__name__}: {exc}"}, 1)
    _emit(report)
    peak = report["true_peak_dbtp"]
    peak_ok = peak is None or peak <= report["peak_limit_dbtp"]
    # Real-music beat offsets are diagnostics (G2b), not render failures; the
    # hard 20 ms contract is gated by `verify` on synthetic ground truth.
    return 0 if report["clipping_count"] == 0 and peak_ok else 1


def cmd_play(args: argparse.Namespace) -> int:
    try:
        report = play_master(args.master, report_path=args.report, progress=args.progress)
    except FileNotFoundError as exc:
        return _fail({"error": str(exc)}, 2)
    except Exception as exc:
        return _fail({"error": f"playback failed: {type(exc).__name__}: {exc}"}, 1)
    _emit(report)
    return 0 if report["underflows"] == 0 else 1


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
    except (OSError, json.JSONDecodeError) as exc:
        return _fail({"error": f"cannot read plan: {exc}"}, 2)
    try:
        report = verify_master(plan, args.master, out_path=args.out)
    except Exception as exc:
        return _fail({"error": f"verify failed: {type(exc).__name__}: {exc}"}, 2)
    _emit(report)
    if report["pass"] is None:
        return 3
    return 0 if report["pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai_dj", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan a music directory into a corpus report")
    s.add_argument("--dir", required=True, help="music library root (WAV/MP3, recursive)")
    s.add_argument("--out", default=str(DEFAULT_EVIDENCE / "corpus_report.json"))
    s.set_defaults(func=cmd_scan)

    a = sub.add_parser("analyze", help="analyze corpus tracks into the sqlite cache")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir", help="music library root (rescanned)")
    src.add_argument("--corpus", help="existing corpus_report.json")
    a.add_argument("--cache", default=DEFAULT_CACHE)
    a.add_argument("--force", action="store_true", help="recompute even when cached")
    a.set_defaults(func=cmd_analyze, dir=None, corpus=None)

    pl = sub.add_parser("plan", help="build a deterministic set plan from the cache")
    pl.add_argument("--cache", default=DEFAULT_CACHE)
    pl.add_argument("--out", default=str(DEFAULT_EVIDENCE / "plan.json"))
    pl.add_argument("--set-name", default="mini")
    pl.add_argument("--tracks", type=int, default=6)
    pl.add_argument("--seed", type=int, default=0)
    pl.add_argument("--energy-target", type=float, default=None)
    pl.add_argument(
        "--include-ineligible",
        action="store_true",
        help="allow tracks that failed the eligibility rule (escape hatch)",
    )
    pl.add_argument(
        "--include-track",
        metavar="RECORDING_ID",
        default=None,
        help="guarantee this recording_id appears in the plan (anchor-aware planning)",
    )
    pl.set_defaults(func=cmd_plan)

    r = sub.add_parser("render", help="render plan.json to a master WAV")
    r.add_argument("--plan", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--report", default=str(DEFAULT_EVIDENCE / "render_report.json"))
    r.set_defaults(func=cmd_render)

    py = sub.add_parser("play", help="play the master WAV via sounddevice")
    py.add_argument("--master", required=True)
    py.add_argument("--report", default=str(DEFAULT_EVIDENCE / "playback_report.json"))
    py.add_argument("--progress", action="store_true", help="print playback progress")
    py.set_defaults(func=cmd_play)

    v = sub.add_parser("verify", help="independently verify the master against the plan")
    v.add_argument("--plan", required=True)
    v.add_argument("--master", required=True)
    v.add_argument("--out", default=str(DEFAULT_EVIDENCE / "verification.json"))
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return _fail({"error": "interrupted"}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
