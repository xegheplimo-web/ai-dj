"""Tests for DJ-P2-CONTINUITY-002 — Final Contract Hardening.

C1: fail-closed continuity completeness gate at CLI ``plan`` input (exit 5).
C2: G8-current — same v3 snapshot + params + seed -> byte-identical plan.json.
C3: verify-by-render gate — ``play`` refuses a master without a valid PASS
    verdict bound to the SHA-256 of both plan.json and the WAV.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj import continuity, player, verdict
from ai_dj.analysis import analyzer
from ai_dj.cli import main as cli_main
from ai_dj.planner import plan_set
from ai_dj.timing import MASTER_SR


def _stereo_blocks(dur_s: float, silent_spans=(), level: float = -20.0) -> list[float]:
    n = int(dur_s / continuity.BLOCK_S)
    b = np.full(n, level, dtype=np.float64)
    for a, z in silent_spans:
        b[int(a / continuity.BLOCK_S) : int(z / continuity.BLOCK_S)] = -95.0
    return [round(float(v), 2) for v in b]


def _record(
    rec_id: str,
    work_id: str,
    bpm: float,
    key: str,
    energy: float,
    *,
    duration_s: float = 120.0,
    conf: float = 0.9,
    silent_spans=(),
    with_continuity: bool = True,
    **overrides,
) -> dict:
    step = 60.0 / bpm
    beats = [round(i * step, 5) for i in range(int(duration_s / step))]
    rec = {
        "recording_id": rec_id,
        "work_id": work_id,
        "title": rec_id,
        "path": f"/fake/{rec_id}.wav",
        "status": "ok",
        "duration_s": duration_s,
        "bpm_raw": bpm * 2,
        "bpm_corrected": bpm,
        "median_ibi_s": 60.0 / bpm,
        "tempo_confidence": conf,
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
        rec["block_rms_dbfs"] = _stereo_blocks(duration_s, silent_spans)
        rec["silence_intervals"] = {
            "digital_minus90": [[a, z] for a, z in silent_spans],
            "low_level_minus60": [[a, z] for a, z in silent_spans],
        }
        rec["opener"] = {"window_s": 30, "audibility_ok": True}
    rec.update(overrides)
    return rec


def _records(**kw) -> list[dict]:
    return [
        _record("rec-1", "work-1", 100.0, "A minor", 0.30, **kw),
        _record("rec-2", "work-2", 103.0, "C major", 0.45, **kw),
        _record("rec-3", "work-3", 130.0, "F# minor", 0.60, **kw),
        _record("rec-4", "work-4", 105.0, "G major", 0.55, **kw),
    ]


def _seed_cache(db: Path, records: list[dict]) -> None:
    cache = analyzer.AnalysisCache(db)
    for r in records:
        cache.put(r)
    cache.close()


def _run_cli(*argv: str) -> tuple[int, dict | None, str]:
    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        code = cli_main(list(argv))
    combined = out_buf.getvalue() + err_buf.getvalue()
    for text in (err_buf.getvalue(), out_buf.getvalue()):
        try:
            return code, json.loads(text), combined
        except json.JSONDecodeError:
            pass
        idx = text.find("{")  # warning lines may precede the error JSON
        if idx >= 0:
            try:
                return code, json.loads(text[idx:]), combined
            except json.JSONDecodeError:
                pass
    return code, None, combined


class TestFailClosedPlanInput(unittest.TestCase):
    """C1 / Flag #1 = A: no continuity data -> no plan, exit 5, never a waiver."""

    def test_all_unmeasured_is_corpus_unmeasured(self) -> None:
        """Every usable record lacks the payload -> CORPUS_UNMEASURED, exit 5."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            _seed_cache(db, _records(with_continuity=False))
            out = Path(td) / "plan.json"
            code, rep, _ = _run_cli("plan", "--cache", str(db), "--out", str(out))
            self.assertEqual(code, 5)
            self.assertIsNotNone(rep)
            self.assertEqual(rep["error"], "CORPUS_UNMEASURED")
            self.assertEqual(rep["reason"], "missing_continuity_data")
            self.assertEqual(rep["analysis_version_required"], analyzer.ANALYSIS_VERSION)
            self.assertEqual(rep["continuity_version_required"], continuity.CONTINUITY_VERSION)
            self.assertEqual(rep["missing_recording_ids"], ["rec-1", "rec-2", "rec-3", "rec-4"])
            self.assertIn("message", rep)
            self.assertFalse(out.exists(), "no plan may be written on a data error")

    def test_partial_missing_is_analysis_required(self) -> None:
        """A subset lacking the payload -> ANALYSIS_REQUIRED + the exact ids."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            recs = _records()
            recs[1] = _record("rec-2", "work-2", 103.0, "C major", 0.45, with_continuity=False)
            recs[3] = _record("rec-4", "work-4", 105.0, "G major", 0.55, with_continuity=False)
            _seed_cache(db, recs)
            out = Path(td) / "plan.json"
            code, rep, _ = _run_cli("plan", "--cache", str(db), "--out", str(out))
            self.assertEqual(code, 5)
            self.assertEqual(rep["error"], "ANALYSIS_REQUIRED")
            self.assertEqual(rep["reason"], "missing_continuity_data")
            self.assertEqual(rep["missing_recording_ids"], ["rec-2", "rec-4"])
            self.assertEqual(rep["missing_count"], 2)
            self.assertFalse(out.exists())

    def test_measured_corpus_plans_normally(self) -> None:
        """H3: a fully measured cache still plans — exit 0, plan written."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            _seed_cache(db, _records())
            out = Path(td) / "plan.json"
            code, rep, _ = _run_cli("plan", "--cache", str(db), "--out", str(out))
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            self.assertEqual(rep["written_to"], str(out))

    def test_ineligible_unmeasured_does_not_block(self) -> None:
        """A D10-excluded track is never selectable: its missing payload must
        not hold back planning over the measured eligible pool."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            recs = _records()
            recs.append(
                _record(
                    "rec-x",
                    "work-x",
                    110.0,
                    "D minor",
                    0.5,
                    with_continuity=False,
                    eligible=False,
                    exclusion_reason="silence_run_ratio 0.5 > 0.02",
                )
            )
            _seed_cache(db, recs)
            code, rep, _ = _run_cli("plan", "--cache", str(db), "--out", str(Path(td) / "p.json"))
            self.assertEqual(code, 0, rep)

    def test_include_ineligible_makes_payload_required(self) -> None:
        """With the escape hatch the excluded track IS selectable -> its
        missing payload is now a hard data error, not a silent skip."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            recs = _records()
            recs.append(
                _record(
                    "rec-x",
                    "work-x",
                    110.0,
                    "D minor",
                    0.5,
                    with_continuity=False,
                    eligible=False,
                    exclusion_reason="silence_run_ratio 0.5 > 0.02",
                )
            )
            _seed_cache(db, recs)
            code, rep, _ = _run_cli(
                "plan",
                "--cache",
                str(db),
                "--out",
                str(Path(td) / "p.json"),
                "--include-ineligible",
            )
            self.assertEqual(code, 5)
            self.assertEqual(rep["error"], "ANALYSIS_REQUIRED")
            self.assertEqual(rep["missing_recording_ids"], ["rec-x"])

    def test_unmeasured_anchor_is_data_error_not_infeasible(self) -> None:
        """C1: missing data is never conflated with audio-infeasible tracks —
        an anchor without continuity data is ANALYSIS_REQUIRED (exit 5), not
        PLAN_INFEASIBLE (exit 4)."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            recs = _records()
            recs[2] = _record("rec-3", "work-3", 130.0, "F# minor", 0.60, with_continuity=False)
            _seed_cache(db, recs)
            code, rep, _ = _run_cli(
                "plan",
                "--cache",
                str(db),
                "--out",
                str(Path(td) / "p.json"),
                "--include-track",
                "rec-3",
            )
            self.assertEqual(code, 5)
            self.assertEqual(rep["error"], "ANALYSIS_REQUIRED")
            self.assertEqual(rep["missing_recording_ids"], ["rec-3"])

    def test_stale_v2_rows_are_unmeasured(self) -> None:
        """A pre-contract (v2) cache row fails closed even at ok status."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            cache = analyzer.AnalysisCache(db)
            for r in _records():
                cache.put(r)
            legacy = _record("rec-old", "work-old", 99.0, "E minor", 0.4)
            for k in ("continuity_version", "block_rms_dbfs", "silence_intervals", "opener"):
                legacy.pop(k, None)
            cache.put(legacy)
            cache._conn.execute(
                "UPDATE tracks SET analysis_version = 2 WHERE recording_id = 'rec-old'"
            )
            cache._conn.commit()
            cache.close()
            code, rep, _ = _run_cli("plan", "--cache", str(db), "--out", str(Path(td) / "p.json"))
            self.assertEqual(code, 5)
            self.assertEqual(rep["error"], "ANALYSIS_REQUIRED")
            self.assertEqual(rep["missing_recording_ids"], ["rec-old"])


class TestG8CurrentContract(unittest.TestCase):
    """C2: same v3 snapshot + same algorithm/params/seed -> byte-identical."""

    def test_plan_json_byte_identical_on_same_v3_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            _seed_cache(db, _records())  # v3-shaped records (continuity payload)
            p1 = Path(td) / "p1.json"
            p2 = Path(td) / "p2.json"
            code1, _, _ = _run_cli("plan", "--cache", str(db), "--out", str(p1))
            code2, _, _ = _run_cli("plan", "--cache", str(db), "--out", str(p2))
            self.assertEqual((code1, code2), (0, 0))
            self.assertEqual(
                hashlib.sha256(p1.read_bytes()).hexdigest(),
                hashlib.sha256(p2.read_bytes()).hexdigest(),
            )

    def test_legacy_v2_golden_is_archived_reference_only(self) -> None:
        """The pre-continuity golden is kept for comparison, not as a gate."""
        golden = Path(__file__).resolve().parent / "fixtures" / "legacy_golden_plan_v2.json"
        self.assertTrue(golden.exists(), "legacy golden must stay archived")
        archived = json.loads(golden.read_text(encoding="utf-8"))
        self.assertEqual(archived["set_name"], "anchor-test")
        # The G8-current gate is determinism on the v3 snapshot, not equality
        # with the archived v2 output.
        self.assertIn("tracks", archived)


class TestCueTrimReporting(unittest.TestCase):
    """D17: a cue moved to satisfy D12 must report what was dropped and why."""

    def test_adjusted_cue_carries_trim_report(self) -> None:
        records = _records()
        records[2] = _record(
            "rec-3",
            "work-3",
            130.0,
            "F# minor",
            0.60,
            silent_spans=[(60.0, 66.0), (108.0, 118.0)],
        )
        plan = plan_set(records, n_tracks=3, set_name="t", include_ids=["rec-3"])
        anchor = next(t for t in plan["tracks"] if t["recording_id"] == "rec-3")
        rep = anchor["cue_report"]
        self.assertEqual(rep["status"], "adjusted")
        self.assertEqual(rep["cue_in_s"], anchor["cue_in_s"])
        self.assertEqual(rep["cue_out_s"], anchor["cue_out_s"])
        self.assertGreater(rep["removed_total_s"], 0.0)
        self.assertEqual(rep["reason"], "uncovered_silence_run")
        self.assertAlmostEqual(
            rep["removed_total_s"],
            (rep["default_cue_out_s"] - rep["default_cue_in_s"])
            - (rep["cue_out_s"] - rep["cue_in_s"]),
            places=3,
        )

    def test_unadjusted_cue_has_no_trim_report(self) -> None:
        plan = plan_set(_records(), n_tracks=3, set_name="t")
        for t in plan["tracks"]:
            self.assertNotIn("cue_report", t)


def _tone(path: Path, dur_s: float, *, gaps=()) -> Path:
    n = int(dur_s * MASTER_SR)
    t = np.arange(n, dtype=np.float64) / MASTER_SR
    sig = 0.5 * np.sin(2.0 * np.pi * 220.0 * t)
    data = np.stack([sig, sig], axis=1).astype(np.float32)
    for a, z in gaps:
        data[int(a * MASTER_SR) : int(z * MASTER_SR)] = 0.0
    sf.write(str(path), data, MASTER_SR, subtype="PCM_24")
    return path


def _minimal_plan(duration_s: float) -> dict:
    return {
        "plan_version": 1,
        "set_name": "gate-fixture",
        "master_sr": MASTER_SR,
        "tracks": [],
        "transitions": [],
        "timeline": {
            "total_samples": int(duration_s * MASTER_SR),
            "total_duration_s": duration_s,
        },
    }


class TestVerifiedArtifactGate(unittest.TestCase):
    """C3 / Flag #2 = C: play accepts only a master with a bound PASS verdict."""

    def _make_master(self, td: str) -> tuple[Path, Path]:
        tmp = Path(td)
        plan_path = tmp / "plan.json"
        plan_path.write_text(json.dumps(_minimal_plan(15.0)), encoding="utf-8")
        wav = _tone(tmp / "m.wav", 15.0)
        return plan_path, wav

    def test_verify_writes_bound_verdict(self) -> None:
        """verify emits a sidecar verdict bound to SHA-256(plan.json + WAV)."""
        with tempfile.TemporaryDirectory() as td:
            plan_path, wav = self._make_master(td)
            code, rep, _ = _run_cli(
                "verify",
                "--plan",
                str(plan_path),
                "--master",
                str(wav),
                "--out",
                str(Path(td) / "v.json"),
            )
            self.assertIn(code, (0, 1, 3))  # verdict varies with ffmpeg presence
            side = verdict.verdict_path(wav)
            self.assertTrue(side.exists())
            doc = json.loads(side.read_text(encoding="utf-8"))
            self.assertEqual(doc["verdict"], rep["verdict"])
            self.assertEqual(doc["plan_sha256"], hashlib.sha256(plan_path.read_bytes()).hexdigest())
            self.assertEqual(doc["master_sha256"], hashlib.sha256(wav.read_bytes()).hexdigest())
            self.assertEqual(doc["plan_json"], str(plan_path))
            # binding also rides on the emitted report
            self.assertEqual(rep["artifact_binding"]["plan_sha256"], doc["plan_sha256"])
            self.assertEqual(rep["artifact_binding"]["master_sha256"], doc["master_sha256"])

    def test_play_refuses_master_without_verdict(self) -> None:
        """H5: no verdict file -> PLAYBACK_REFUSED, never playback."""
        with tempfile.TemporaryDirectory() as td:
            _plan, wav = self._make_master(td)
            code, rep, _ = _run_cli(
                "play",
                "--master",
                str(wav),
                "--report",
                str(Path(td) / "play_report.json"),
            )
            self.assertEqual(code, 1)
            self.assertEqual(rep["error"], "PLAYBACK_REFUSED")
            self.assertEqual(rep["reason"], "verify_verdict_missing")

    def _pass_verdict(self, plan_path: Path, wav: Path) -> dict:
        doc = {
            "verdict": "PASS",
            "pass": True,
            "plan_json": str(plan_path),
            "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "master_wav": str(wav),
            "master_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
        }
        verdict.verdict_path(wav).write_text(json.dumps(doc), encoding="utf-8")
        return doc

    def test_play_refuses_fail_and_inconclusive_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plan_path, wav = self._make_master(td)
            for bad in ("FAIL", "INCONCLUSIVE"):
                doc = self._pass_verdict(plan_path, wav)
                doc["verdict"] = bad
                doc["pass"] = False if bad == "FAIL" else None
                verdict.verdict_path(wav).write_text(json.dumps(doc), encoding="utf-8")
                code, rep, _ = _run_cli(
                    "play",
                    "--master",
                    str(wav),
                    "--report",
                    str(Path(td) / "play_report.json"),
                )
                self.assertEqual(code, 1)
                self.assertEqual(rep["error"], "PLAYBACK_REFUSED")
                self.assertEqual(rep["reason"], "verify_verdict_not_pass")
                self.assertEqual(rep["verdict"], bad)

    def test_play_refuses_when_master_changed(self) -> None:
        """H6: one flipped byte in the WAV invalidates the old PASS."""
        with tempfile.TemporaryDirectory() as td:
            plan_path, wav = self._make_master(td)
            self._pass_verdict(plan_path, wav)
            data = bytearray(wav.read_bytes())
            data[-1] ^= 0xFF
            wav.write_bytes(bytes(data))
            code, rep, _ = _run_cli(
                "play",
                "--master",
                str(wav),
                "--report",
                str(Path(td) / "play_report.json"),
            )
            self.assertEqual(code, 1)
            self.assertEqual(rep["reason"], "verify_master_changed")

    def test_play_refuses_when_plan_changed(self) -> None:
        """H6: re-writing plan.json invalidates the old PASS."""
        with tempfile.TemporaryDirectory() as td:
            plan_path, wav = self._make_master(td)
            self._pass_verdict(plan_path, wav)
            plan_path.write_text(json.dumps(_minimal_plan(16.0)), encoding="utf-8")
            code, rep, _ = _run_cli(
                "play",
                "--master",
                str(wav),
                "--report",
                str(Path(td) / "play_report.json"),
            )
            self.assertEqual(code, 1)
            self.assertEqual(rep["reason"], "verify_plan_changed")

    def test_play_refuses_when_plan_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plan_path, wav = self._make_master(td)
            self._pass_verdict(plan_path, wav)
            plan_path.unlink()
            code, rep, _ = _run_cli(
                "play",
                "--master",
                str(wav),
                "--report",
                str(Path(td) / "play_report.json"),
            )
            self.assertEqual(code, 1)
            self.assertEqual(rep["reason"], "verify_plan_missing")

    def test_play_accepts_bound_pass_verdict(self) -> None:
        """A valid bound PASS reaches the audio path (mocked sounddevice)."""
        with tempfile.TemporaryDirectory() as td:
            plan_path, wav = self._make_master(td)
            self._pass_verdict(plan_path, wav)

            class _FakeStream:
                device = 0
                active = False

                def start(self) -> None:
                    pass

                def stop(self) -> None:
                    pass

                def abort(self) -> None:
                    pass

                def close(self) -> None:
                    pass

            class _FakeSD:
                CallbackStop = type("CallbackStop", (Exception,), {})

                @staticmethod
                def OutputStream(**_kw):
                    return _FakeStream()

            with (
                mock.patch.object(player, "sd", _FakeSD),
                mock.patch.object(
                    player, "_make_reader", return_value=threading.Thread(daemon=True)
                ),
            ):
                code, rep, _ = _run_cli(
                    "play",
                    "--master",
                    str(wav),
                    "--report",
                    str(Path(td) / "play_report.json"),
                )
            self.assertNotEqual((rep or {}).get("error"), "PLAYBACK_REFUSED")
            self.assertEqual(code, 0, rep)
            self.assertEqual(rep["verify_gate"]["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
