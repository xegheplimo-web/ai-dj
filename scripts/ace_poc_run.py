"""Run the full Option-Y remix PoC on the chosen Bolero (AI-DJ-P3-ACE-001).

Chosen input (see ``evidence/ace_poc_plan.json`` for the binding): the
rights-cleared Bolero ``Đam Mê (Cover Duy)`` — cached 99.38 BPM / B minor
(key confidence 0.90, the strongest key estimate in the 88-110 BPM band),
eligible, 321 s. The ``Gõ Cửa`` 96->192 doubling case (D29/F3) is avoided.

Run with the ACE-Step venv so acestep + demucs + torch are all importable:
    F:/ace-venv/Scripts/python.exe scripts/ace_poc_run.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj.ace_remixer import DEFAULT_CAPTION, run_poc
from ai_dj.verdict import sha256_file

WORKTREE = Path(__file__).resolve().parent.parent
EVIDENCE = WORKTREE / "evidence"
WORK = EVIDENCE / "ace_poc_work"
PLAN_PATH = EVIDENCE / "ace_poc_plan.json"

INPUT_PATH = Path(r"F:\DỮ LIỆU\ok\Đam Mê (Cover Duy) .wav")


def build_plan() -> dict:
    return {
        "card": "AI-DJ-P3-ACE-001",
        "architecture": "Option Y: demucs vocal stem + ACE-Step 1.5 generated Vinahouse BGM (D30)",
        "plan_path": str(PLAN_PATH),
        "input": {
            "recording_id": "rec-1d80838e4a40f4cb",
            "title": "Đam Mê (Cover Duy)",
            "path": str(INPUT_PATH),
            "sha256": sha256_file(INPUT_PATH),
            "cached_bpm": 99.38,
            "cached_key": "B minor",
            "excerpt_start_s": 56.0,
            "excerpt_s": 44.0,
            "excerpt_note": "intro_end_s=54.0 -> excerpt starts inside verse",
        },
        "stem": {"tool": "demucs", "model": "htdemucs", "two_stems": "vocals"},
        "target_bpm": 130.0,
        "bgm_duration_s": 44.0,
        "seed": 42,
        "caption": DEFAULT_CAPTION,
        "checkpoints": "F:/ace-checkpoints",
        # ACE-Step writes .cache/ under project_root — keep it inside the
        # gitignored evidence work dir, never at the repo root (S6 hygiene).
        "project_root": str(WORK),
    }


def main() -> int:
    if not INPUT_PATH.exists():
        print(json.dumps({"error": f"input missing: {INPUT_PATH}"}), file=sys.stderr)
        return 2
    plan = build_plan()
    report = run_poc(plan, work_dir=WORK, evidence_dir=EVIDENCE)
    summary = {
        "verdict": report["verdict"],
        "checks": {k: v["pass"] for k, v in report["checks"].items()},
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
