"""ACE-Step 1.5 smoke-generation driver (AI-DJ-P3-ACE-001, S1).

Constructs ``AceStepHandler`` + ``LLMHandler`` and emits ONE instrumental
Vinahouse BGM WAV via ``acestep.inference.generate_music``. This is the
lane's first deliverable: it proves end-to-end generation BEFORE the full
Option-Y remix PoC runs.

Checkpoints resolve via ``ACESTEP_CHECKPOINTS_DIR`` (set here to
``F:/ace-checkpoints`` unless overridden by ``--checkpoints``), so no model
files are copied into the worktree. The LLM handler is constructed but NOT
initialized: with ``thinking=False`` and no CoT flags, ``generate_music``
takes the pure DiT path and never touches the LM (verified in
``acestep/inference.py`` — ``use_lm`` requires ``llm_handler.llm_initialized``).

Run:
    F:/ace-venv/Scripts/python.exe scripts/ace_poc_generate.py
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_CHECKPOINTS = "F:/ace-checkpoints"
DEFAULT_OUT = "evidence/ace_poc_sample.wav"
DEFAULT_CAPTION = (
    "Vinahouse remix instrumental, Vietnamese dance style, four-on-the-floor "
    "punchy kick, driving offbeat bassline, bright synth stabs and supersaw "
    "lead, energetic club groove, clean modern mix"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ace_poc_generate", description=__doc__)
    p.add_argument("--out", default=DEFAULT_OUT, help="output WAV path")
    p.add_argument("--bpm", type=int, default=130, help="target BPM (30-300)")
    p.add_argument("--key", default="B minor", help="keyscale, e.g. 'B minor'")
    p.add_argument("--duration", type=float, default=30.0, help="seconds (<=30 for S1)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--caption", default=DEFAULT_CAPTION)
    p.add_argument(
        "--checkpoints",
        default=os.environ.get("ACESTEP_CHECKPOINTS_DIR", DEFAULT_CHECKPOINTS),
        help="ACE-Step checkpoints dir (exported as ACESTEP_CHECKPOINTS_DIR)",
    )
    p.add_argument(
        "--project-root",
        default="evidence/ace_poc_work",
        help="handler project root (its .cache sidecars land here, not repo root)",
    )
    p.add_argument("--config", default="acestep-v15-turbo", help="DiT config name")
    p.add_argument("--device", default="cuda")
    p.add_argument("--steps", type=int, default=8, help="diffusion steps (turbo: 8)")
    p.add_argument("--json", default=None, help="optional JSON status output path")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.duration > 30.0:
        print(
            json.dumps({"error": "S1 smoke gen must be <=30s", "duration": args.duration}),
            file=sys.stderr,
        )
        return 2

    # Resolve the model dir BEFORE importing acestep — initialize_service and
    # model_downloader read ACESTEP_CHECKPOINTS_DIR at call time. The handler
    # also resolves .cache/ under ACESTEP_PROJECT_ROOT at __init__ time, so
    # point that at the evidence work dir too (never the repo root).
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = args.checkpoints
    os.environ["ACESTEP_PROJECT_ROOT"] = str(Path(args.project_root).resolve())
    ckpt = Path(args.checkpoints)
    if not ckpt.is_dir():
        print(json.dumps({"error": f"checkpoints dir missing: {ckpt}"}), file=sys.stderr)
        return 2

    # Lazy import via importlib — this driver only runs under F:/ace-venv.
    try:
        h = importlib.import_module("acestep.handler")
        inf = importlib.import_module("acestep.inference")
        li = importlib.import_module("acestep.llm_inference")
    except ImportError as exc:
        print(
            json.dumps({"error": f"acestep not importable here ({exc}) — run with F:/ace-venv"}),
            file=sys.stderr,
        )
        return 2

    dit = h.AceStepHandler()
    status, _ok = dit.initialize_service(
        project_root=str(Path(args.project_root).resolve()),
        config_path=args.config,
        device=args.device,
    )
    print(f"[initialize_service] {status}", file=sys.stderr)

    # LLMHandler stays uninitialized on purpose (see module docstring).
    llm = li.LLMHandler()

    params = inf.GenerationParams(
        task_type="text2music",
        caption=args.caption,
        instrumental=True,
        bpm=args.bpm,
        keyscale=args.key,
        timesignature="4",
        duration=args.duration,
        seed=args.seed,
        inference_steps=args.steps,
        thinking=False,
        use_cot_metas=False,
        use_cot_caption=False,
        use_cot_language=False,
    )
    config = inf.GenerationConfig(
        batch_size=1,
        use_random_seed=False,
        seeds=[args.seed],
        audio_format="wav",
    )

    out_dir = Path(args.out).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    res = inf.generate_music(dit, llm, params, config, save_dir=str(out_dir))
    if not res.success or not res.audios:
        print(
            json.dumps({"error": "generation failed", "detail": res.error or res.status_message}),
            file=sys.stderr,
        )
        return 1

    src = Path(res.audios[0]["path"])
    dst = Path(args.out)
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)

    report = {
        "written_to": str(dst),
        "source_artifact": str(src),
        "params": {
            "task_type": params.task_type,
            "caption": params.caption,
            "bpm": params.bpm,
            "keyscale": params.keyscale,
            "duration": params.duration,
            "seed": params.seed,
            "inference_steps": params.inference_steps,
            "instrumental": params.instrumental,
        },
        "checkpoints": str(ckpt),
        "size_bytes": dst.stat().st_size,
    }
    if args.json:
        Path(args.json).write_bytes(
            (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
