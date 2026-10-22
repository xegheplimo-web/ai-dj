"""Generate evidence/dj_p2_continuity002_*.json (DJ-P2-CONTINUITY-002).

Runs the real CLI (`python -m ai_dj.cli`) as subprocesses against seeded
analysis caches and real WAVs, capturing exit codes and emitted JSON. Also
runs the adversarial predictor harness from tests/test_predictor_adversarial.py.

Usage: python scripts/gen_continuity002_evidence.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_predictor_adversarial as adv  # noqa: E402

from ai_dj import continuity, verdict  # noqa: E402
from ai_dj.analysis import analyzer  # noqa: E402
from ai_dj.timing import MASTER_SR  # noqa: E402

PY = sys.executable
EVIDENCE = ROOT / "evidence"


def cli(*argv: str, cwd: Path | None = None) -> dict:
    proc = subprocess.run(
        [PY, "-m", "ai_dj.cli", *argv],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd or ROOT,
    )
    payload = None
    for text in (proc.stderr, proc.stdout):
        idx = text.find("{")
        if idx >= 0:
            try:
                payload = json.loads(text[idx:])
                break
            except json.JSONDecodeError:
                pass
    return {
        "argv": ["python", "-m", "ai_dj.cli", *argv],
        "exit_code": proc.returncode,
        "json": payload,
        "stderr_head": proc.stderr.strip().splitlines()[:4],
    }


def stereo_blocks(dur_s: float, silent_spans=(), level: float = -20.0) -> list[float]:
    n = int(dur_s / continuity.BLOCK_S)
    b = np.full(n, level, dtype=np.float64)
    for a, z in silent_spans:
        b[int(a / continuity.BLOCK_S) : int(z / continuity.BLOCK_S)] = -95.0
    return [round(float(v), 2) for v in b]


def record(
    meta: tuple,
    wav: Path,
    *,
    duration_s: float = 120.0,
    silent_spans=(),
    with_continuity: bool = True,
) -> dict:
    rec_id, work_id, bpm, key, energy = meta
    step = 60.0 / bpm
    beats = [round(i * step, 5) for i in range(int(duration_s / step))]
    rec = {
        "recording_id": rec_id,
        "work_id": work_id,
        "title": rec_id,
        "path": str(wav),
        "status": "ok",
        "duration_s": duration_s,
        "bpm_raw": bpm * 2,
        "bpm_corrected": bpm,
        "median_ibi_s": 60.0 / bpm,
        "tempo_confidence": 0.9,
        "key": key,
        "energy_mean": energy,
        "rms_db": -20.0,
        "intro_end_s": 5.0,
        "outro_start_s": duration_s - 12.0,
        "beat_times_s": beats,
        "downbeat_times_s": beats[::4],
        "eligible": True,
        "exclusion_reason": None,
    }
    if with_continuity:
        rec["continuity_version"] = continuity.CONTINUITY_VERSION
        rec["block_ms"] = int(continuity.BLOCK_S * 1000)
        rec["channels"] = "all_channels_rms"
        rec["continuity_sr"] = MASTER_SR
        rec["block_rms_dbfs"] = stereo_blocks(duration_s, silent_spans)
        rec["silence_intervals"] = {
            "digital_minus90": [[a, z] for a, z in silent_spans],
            "low_level_minus60": [[a, z] for a, z in silent_spans],
        }
        rec["opener"] = {"window_s": 30, "audibility_ok": True}
    return rec


def seed(db: Path, records: list[dict]) -> None:
    cache = analyzer.AnalysisCache(db)
    for r in records:
        cache.put(r)
    cache.close()


def tone(path: Path, dur_s: float, freq: float = 220.0) -> Path:
    n = int(dur_s * MASTER_SR)
    t = np.arange(n, dtype=np.float64) / MASTER_SR
    sig = 0.5 * np.sin(2.0 * np.pi * freq * t)
    sf.write(str(path), np.stack([sig, sig], 1).astype(np.float32), MASTER_SR, subtype="PCM_24")
    return path


def write(name: str, doc: dict) -> Path:
    p = EVIDENCE / name
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {p}")
    return p


META = [
    ("rec-1", "work-1", 100.0, "A minor", 0.30),
    ("rec-2", "work-2", 103.0, "C major", 0.45),
    ("rec-3", "work-3", 130.0, "F# minor", 0.60),
    ("rec-4", "work-4", 105.0, "G major", 0.55),
]
FREQS = {"rec-1": 220.0, "rec-2": 247.0, "rec-3": 262.0, "rec-4": 294.0}


def _c1_evidence(tmp: Path, wavs: dict, full: list[dict]) -> None:
    """H1+H2: plan on unmeasured/partially-measured caches -> exit 5 errors."""
    db = tmp / "all_unmeasured.sqlite"
    seed(db, [record(m, wavs[m[0]], with_continuity=False) for m in META])
    r_all = cli("plan", "--cache", str(db), "--out", str(tmp / "p_all.json"))

    db2 = tmp / "partial.sqlite"
    partial = list(full)
    for i in (1, 3):
        partial[i] = record(META[i], wavs[META[i][0]], with_continuity=False)
    seed(db2, partial)
    r_part = cli("plan", "--cache", str(db2), "--out", str(tmp / "p_part.json"))

    write(
        "dj_p2_continuity002_c1_fail_closed.json",
        {
            "card": "DJ-P2-CONTINUITY-002 / C1 (Flag #1 = A), gates H1+H2",
            "how_generated": (
                "real subprocess runs of `python -m ai_dj.cli plan` against "
                "seeded v3 caches missing the continuity payload"
            ),
            "corpus_unmeasured": r_all,
            "analysis_required": r_part,
            "plan_files_written": {
                "corpus_unmeasured": (tmp / "p_all.json").exists(),
                "analysis_required": (tmp / "p_part.json").exists(),
            },
            "note": (
                "exit 5 = data-not-analyzed; exit 4 remains PLAN_INFEASIBLE; "
                "no --allow-unmeasured waiver exists"
            ),
        },
    )


def _g8_evidence(tmp: Path, full: list[dict]) -> Path:
    """H3+H4: measured cache plans; two runs -> byte-identical plan.json."""
    db = tmp / "full.sqlite"
    seed(db, full)
    plan_a, plan_b = tmp / "plan_a.json", tmp / "plan_b.json"
    r_ok = cli("plan", "--cache", str(db), "--out", str(plan_a))
    r_ok2 = cli("plan", "--cache", str(db), "--out", str(plan_b))
    sha_a = verdict.sha256_file(plan_a) if plan_a.exists() else None
    sha_b = verdict.sha256_file(plan_b) if plan_b.exists() else None

    write(
        "dj_p2_continuity002_g8_determinism.json",
        {
            "card": "DJ-P2-CONTINUITY-002 / C2 + gates H3+H4",
            "how_generated": "two real `plan` runs on the same v3 snapshot/params/seed",
            "run_1": {**r_ok, "plan_sha256": sha_a},
            "run_2": {**r_ok2, "plan_sha256": sha_b},
            "byte_identical": sha_a == sha_b and sha_a is not None,
            "note": (
                "legacy v2 golden is archived at "
                "tests/fixtures/legacy_golden_plan_v2.json (reference only)"
            ),
        },
    )
    return plan_a


def _gate_track(i: int, rid: str, wav: Path, start_s: float, seg_s: float) -> dict:
    seg_n = int(seg_s * MASTER_SR)
    return {
        "index": i,
        "recording_id": rid,
        "path": str(wav),
        "cue_in_samples": 0,
        "cue_out_samples": seg_n,
        "segment_source_samples": seg_n,
        "segment_master_samples": seg_n,
        "tempo_stretch": 1.0,
        "gain_db": 0.0,
        "master_start_sample": int(start_s * MASTER_SR),
    }


def _gate_evidence(tmp: Path) -> None:
    """H5+H6: render -> verify PASS -> play gate; SHA-256 tamper invalidation.

    Plain crossfades (no stretch) keep duration exact; quiet tones keep
    true-peak under the -1 dBTP limit, so the render verifies PASS and the
    bound-verdict paths run for real.
    """
    a_w = tone(tmp / "gate_a.wav", 6.0, freq=200.0)
    b_w = tone(tmp / "gate_b.wav", 6.0, freq=250.0)
    for w in (a_w, b_w):  # headroom: crossfade sum must stay < -1 dBTP
        d, fsr = sf.read(str(w))
        sf.write(str(w), d * 0.4, fsr, subtype="PCM_24")
    gate_plan = {
        "plan_version": 1,
        "set_name": "gate-evidence",
        "master_sr": MASTER_SR,
        "allowed_silent_gaps_s": [],
        "tracks": [
            _gate_track(0, "gate-a", a_w, 0.0, 6.0),
            _gate_track(1, "gate-b", b_w, 4.0, 6.0),
        ],
        "transitions": [
            {
                "index": 0,
                "from_track": 0,
                "to_track": 1,
                "type": "crossfade",
                "duration_s": 2.0,
                "duration_samples": int(2.0 * MASTER_SR),
                "overlap_start_sample": int(4.0 * MASTER_SR),
                "overlap_end_sample": int(6.0 * MASTER_SR),
                "confidence": 1.0,
                "reasons": ["evidence"],
                "target_bpm": 100.0,
            }
        ],
        "timeline": {"total_samples": int(10.0 * MASTER_SR), "total_duration_s": 10.0},
    }
    plan_g = tmp / "gate_plan.json"
    plan_g.write_text(json.dumps(gate_plan, indent=2) + "\n", encoding="utf-8")
    master = tmp / "master.wav"
    r_render = cli(
        "render",
        "--plan",
        str(plan_g),
        "--out",
        str(master),
        "--report",
        str(tmp / "render_report.json"),
    )

    gate_cases: dict[str, dict] = {}
    play_report = tmp / "play_report.json"

    def play() -> dict:
        return cli("play", "--master", str(master), "--report", str(play_report))

    gate_cases["no_verdict"] = play()
    r_verify = cli(
        "verify",
        "--plan",
        str(plan_g),
        "--master",
        str(master),
        "--out",
        str(tmp / "verification.json"),
    )
    sidecar = verdict.verdict_path(master)
    verdict_doc = json.loads(sidecar.read_text()) if sidecar.exists() else None

    if verdict_doc and verdict_doc.get("verdict") == "PASS":
        # H6: one appended byte in plan.json -> old PASS invalidated via SHA-256
        orig_plan = plan_g.read_bytes()
        plan_g.write_bytes(orig_plan + b"\n")
        gate_cases["plan_changed_after_pass"] = play()
        plan_g.write_bytes(orig_plan)

        # H6: one flipped byte in the WAV -> old PASS invalidated via SHA-256
        data = bytearray(master.read_bytes())
        data[-1] ^= 0xFF
        master.write_bytes(bytes(data))
        gate_cases["master_changed_after_pass"] = play()
        data[-1] ^= 0xFF
        master.write_bytes(bytes(data))

        # bound PASS restored -> play proceeds past the gate (10 s tone)
        gate_cases["bound_pass_play"] = play()
    else:
        gate_cases["verify_unexpected"] = {
            "verdict": verdict_doc,
            "render_exit": r_render["exit_code"],
            "render_stderr": r_render["stderr_head"],
        }

    write(
        "dj_p2_continuity002_play_gate.json",
        {
            "card": "DJ-P2-CONTINUITY-002 / C3 (Flag #2 = C), gates H5+H6",
            "how_generated": (
                "real render + verify + play CLI runs on a minimal plan; "
                "tamper = one appended/flipped byte"
            ),
            "render": {"exit_code": r_render["exit_code"]},
            "verify": {
                "exit_code": r_verify["exit_code"],
                "verdict": (r_verify["json"] or {}).get("verdict"),
            },
            "verdict_sidecar": verdict_doc,
            "play_attempts": gate_cases,
            "note": (
                "PLAYBACK_REFUSED = the gate blocked it; any other play "
                "outcome after a bound PASS means the gate accepted "
                "(sounddevice may play a short tone or fail on device "
                "availability)"
            ),
        },
    )


def _adv_evidence(tmp: Path) -> None:
    """H7: adversarial predictor quantification against the real verifier."""
    results = adv.run_cases(tmp / "adv")
    summary: dict[str, int] = {}
    for r in results:
        summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1
    write(
        "dj_p2_continuity002_adversarial.json",
        {
            "card": "DJ-P2-CONTINUITY-002 / C4, gate H7",
            "how_generated": (
                "tests/test_predictor_adversarial.py run_cases() — predictor "
                "vs verify._silence_check on real renders"
            ),
            "summary": summary,
            "cases": results,
            "regression_fixtures": "tests/fixtures/predictor_divergence.json",
            "note": (
                "predictor is a feasibility bound, not proof of D12 PASS — "
                "every false_negative is a recorded regression fixture"
            ),
        },
    )


def main() -> None:
    EVIDENCE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        wavs = {m[0]: tone(tmp / f"{m[0]}.wav", 120.0, freq=FREQS[m[0]]) for m in META}
        full = [record(m, wavs[m[0]]) for m in META]
        _c1_evidence(tmp, wavs, full)
        _g8_evidence(tmp, full)
        _gate_evidence(tmp)
        _adv_evidence(tmp)


if __name__ == "__main__":
    main()
