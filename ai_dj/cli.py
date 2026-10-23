"""AI DJ command line: scan / analyze / plan / render / play / verify.

Every command prints a JSON result on stdout (or a JSON error on stderr).
Exit codes: 0 = success/PASS, 1 = gate/logic failure/FAIL (play only:
PLAYBACK_REFUSED when the verified-artifact gate blocks playback),
3 = INCONCLUSIVE (a required measurement is missing; verify only),
2 = usage/environment errors (no verdict is ever emitted on a usage error),
4 = PLAN_INFEASIBLE (plan only: no ordering satisfies the D12 continuity
hard constraint — structured violation ranges ride on the error JSON),
5 = data-not-analyzed (plan only: ANALYSIS_REQUIRED / CORPUS_UNMEASURED —
the corpus lacks the continuity payload; re-run analyze).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_dj.ace_remixer import DEFAULT_CAPTION, run_poc
from ai_dj.analysis.analyzer import DEFAULT_CACHE, AnalysisCache, analyze_corpus
from ai_dj.analysis.corpus import TrackRef, duplicate_report, scan_corpus
from ai_dj.planner import (
    PlanInfeasible,
    load_plan,
    plan_set,
    unmeasured_corpus_error,
    write_plan,
)
from ai_dj.player import play_master
from ai_dj.render import render_plan
from ai_dj.verdict import PlaybackRefused, sha256_file, write_verdict
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
        out.write_bytes((json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
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
    # C1 (Flag #1 = A, fail-closed): the selectable pool must carry the
    # continuity payload BEFORE beam search runs — missing data is a data
    # error (exit 5), never PLAN_INFEASIBLE, and there is no waiver flag.
    data_error = unmeasured_corpus_error(records, include_ineligible=args.include_ineligible)
    if data_error is not None:
        return _fail(data_error, 5)
    if args.anchor_last and not args.include_track:
        return _fail(
            {"error": "--anchor-last requires --include-track <recording_id>"},
            2,
        )
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
            anchor_last=args.anchor_last,
        )
    except PlanInfeasible as exc:
        return _fail(exc.payload, 4)
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
    except PlaybackRefused as exc:
        return _fail(exc.payload, 1)
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
        report = verify_master(plan, args.master)
        # C3 (Flag #2 = C): the verdict is bound to the SHA-256 of BOTH
        # plan.json and the WAV; the sidecar verdict is what `play` gates on.
        write_verdict(report, plan_path=args.plan, master_wav=args.master, report_path=args.out)
    except Exception as exc:
        return _fail({"error": f"verify failed: {type(exc).__name__}: {exc}"}, 2)
    _emit(report)
    if report["pass"] is None:
        return 3
    return 0 if report["pass"] else 1


def cmd_remix(args: argparse.Namespace) -> int:
    """Option-Y remix PoC (AI-DJ-P3-ACE-001): vocal stem + generated BGM."""
    src = Path(args.input)
    if not src.exists():
        return _fail({"error": f"input missing: {src}"}, 2)
    evidence_dir = Path(args.evidence)
    plan = {
        "card": "AI-DJ-P3-ACE-001",
        "architecture": "Option Y: demucs vocal stem + ACE-Step 1.5 generated BGM (D30)",
        "plan_path": str(evidence_dir / "ace_poc_plan.json"),
        "input": {
            "path": str(src),
            "sha256": sha256_file(src),
            "cached_bpm": args.cached_bpm,
            "cached_key": args.cached_key,
            "excerpt_start_s": args.excerpt_start,
            "excerpt_s": args.excerpt_s,
        },
        "stem": {"tool": "demucs", "model": "htdemucs", "two_stems": "vocals"},
        "target_bpm": args.bpm,
        "bgm_duration_s": args.bgm_duration,
        "seed": args.seed,
        "caption": args.caption or DEFAULT_CAPTION,
        "checkpoints": args.checkpoints,
        # ACE-Step drops .cache/ under project_root — keep it in the work dir.
        "project_root": args.work,
    }
    try:
        report = run_poc(plan, work_dir=Path(args.work), evidence_dir=evidence_dir)
    except RuntimeError as exc:
        return _fail({"error": str(exc)}, 1)
    _emit(
        {
            "verdict": report["verdict"],
            "checks": {k: v["pass"] for k, v in report["checks"].items()},
        }
    )
    return 0 if report["pass"] else 1


def _add_remix_parser(sub) -> None:
    rx = sub.add_parser(
        "remix",
        help="Option-Y remix PoC: stem-split -> ACE-Step BGM -> align -> mix -> verify",
    )
    rx.add_argument("--input", required=True, help="rights-cleared source WAV")
    rx.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    rx.add_argument("--work", default=str(DEFAULT_EVIDENCE / "ace_poc_work"))
    rx.add_argument("--bpm", type=float, default=130.0, help="target BGM BPM")
    rx.add_argument("--cached-bpm", type=float, default=None, help="prior BPM hint")
    rx.add_argument("--cached-key", default=None, help="key fallback if unmeasurable")
    rx.add_argument("--excerpt-start", type=float, default=56.0)
    rx.add_argument("--excerpt-s", type=float, default=44.0)
    rx.add_argument("--bgm-duration", type=float, default=44.0)
    rx.add_argument("--seed", type=int, default=42)
    rx.add_argument("--caption", default=None)
    rx.add_argument("--checkpoints", default="F:/ace-checkpoints")
    rx.set_defaults(func=cmd_remix)


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
    pl.add_argument(
        "--anchor-last",
        action="store_true",
        help=(
            "with --include-track: place the anchor at the END of the set and "
            "preserve its content — the cue is the content-maximal D12-legal "
            "placement (C1/G2B-001); infeasible -> PLAN_INFEASIBLE, never a "
            "silent cut"
        ),
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

    _add_remix_parser(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return _fail({"error": "interrupted"}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
