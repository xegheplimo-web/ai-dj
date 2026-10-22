"""Verified-artifact playback gate (DJ-P2-CONTINUITY-002, Flag #2 = C).

The pipeline is PLAN -> RENDER -> VERIFY -> PLAY. ``plan`` is a fast
simulation that prunes infeasible candidates — it does NOT prove the master
will pass D12; it only predicts feasibility to reduce wasted renders. The
verifier reads the real audio and its verdict is the gate for playback:

* ``verify`` writes a small verdict file next to the master WAV
  (``<name>.verify.json``) recording the verdict plus the SHA-256 of BOTH
  the plan.json and the WAV it measured.
* ``play`` refuses any master whose verdict file is missing, unreadable, not
  PASS, or bound to different bytes — changing either artifact invalidates
  the old PASS.

No double render: the verifier is a gate on the playback process, not on the
planner.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

VERDICT_SUFFIX = ".verify.json"


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verdict_path(master_wav: str | Path) -> Path:
    """Sidecar verdict file travelling with the master WAV."""
    return Path(master_wav).with_suffix(VERDICT_SUFFIX)


def artifact_binding(plan_path: str | Path, master_wav: str | Path) -> dict:
    """SHA-256 binding between the verified plan and the verified master."""
    master_wav = Path(master_wav)
    return {
        "plan_json": str(Path(plan_path)),
        "plan_sha256": sha256_file(plan_path),
        "master_wav": str(master_wav),
        "master_sha256": sha256_file(master_wav) if master_wav.exists() else None,
    }


def write_verdict(
    report: dict,
    *,
    plan_path: str | Path,
    master_wav: str | Path,
    report_path: str | Path | None = None,
) -> dict:
    """Bind ``report`` to its artifacts; write the report and verdict sidecar.

    Returns the binding dict (also attached to ``report['artifact_binding']``).
    The sidecar is the machine token ``play`` gates on; it is only written when
    the master file exists on disk.
    """
    binding = artifact_binding(plan_path, master_wav)
    report["artifact_binding"] = binding
    if report_path:
        rp = Path(report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if Path(master_wav).exists():
        doc = {
            "verdict": report.get("verdict"),
            "pass": report.get("pass"),
            "verify_report": str(report_path) if report_path else None,
            "verified_by": "ai_dj.verify",
            **binding,
        }
        verdict_path(master_wav).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return binding


class PlaybackRefused(RuntimeError):
    """Playback gate refusal — carries a structured payload for the CLI."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        context: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.payload = {
            "error": "PLAYBACK_REFUSED",
            "reason": reason,
            "message": message,
            **(context or {}),
        }


def require_play_gate(master_wav: str | Path) -> dict:
    """Return the stored verdict for ``master_wav``, or refuse playback.

    A master reaches the audio path only with a verdict file that is PASS and
    bound to the exact bytes of both the current WAV and the current plan.json
    — anything else fails closed with ``PlaybackRefused``.
    """
    master_wav = Path(master_wav)
    vp = verdict_path(master_wav)
    if not vp.exists():
        raise PlaybackRefused(
            f"no verify verdict for {master_wav} — run `verify` on this master before playing it",
            reason="verify_verdict_missing",
            context={"verdict_file": str(vp)},
        )
    try:
        doc = json.loads(vp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlaybackRefused(
            f"verify verdict {vp} is unreadable: {exc}",
            reason="verify_verdict_corrupt",
            context={"verdict_file": str(vp)},
        ) from exc
    if doc.get("verdict") != "PASS" or doc.get("pass") is not True:
        raise PlaybackRefused(
            f"verify verdict for {master_wav} is {doc.get('verdict')}, not PASS — "
            "playback is blocked until a render verifies clean",
            reason="verify_verdict_not_pass",
            context={"verdict": doc.get("verdict"), "verdict_file": str(vp)},
        )
    if not doc.get("master_sha256") or not doc.get("plan_sha256"):
        raise PlaybackRefused(
            f"verify verdict {vp} lacks the plan/WAV SHA-256 binding — "
            "re-run `verify` so the verdict is bound to the artifacts",
            reason="verify_verdict_unbound",
            context={"verdict_file": str(vp)},
        )
    if sha256_file(master_wav) != doc["master_sha256"]:
        raise PlaybackRefused(
            f"{master_wav} changed since it was verified — the old PASS is "
            "invalid; re-run `verify` on the current file",
            reason="verify_master_changed",
            context={"verdict_file": str(vp)},
        )
    plan_json = doc.get("plan_json")
    if not plan_json or not Path(plan_json).exists():
        raise PlaybackRefused(
            f"plan.json recorded in {vp} is gone — the verified plan/master "
            "pair cannot be re-checked; re-run `verify`",
            reason="verify_plan_missing",
            context={"plan_json": plan_json, "verdict_file": str(vp)},
        )
    if sha256_file(plan_json) != doc["plan_sha256"]:
        raise PlaybackRefused(
            f"{plan_json} changed since the master was verified — the old PASS "
            "is invalid; re-render and re-verify",
            reason="verify_plan_changed",
            context={"plan_json": plan_json, "verdict_file": str(vp)},
        )
    return doc
