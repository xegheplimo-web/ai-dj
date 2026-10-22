"""Tests for the Unified Audio Continuity Contract (DJ-P2-CONTINUITY-001)."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj import continuity, verify
from ai_dj.analysis import analyzer
from ai_dj.analysis.corpus import TrackRef
from ai_dj.cli import main as cli_main
from ai_dj.planner import PlanInfeasible, plan_set
from ai_dj.timing import MASTER_SR, synthesize_click_track, write_wav_pcm16


def _stereo_blocks(dur_s: float, silent_spans=(), level: float = -20.0) -> list[float]:
    """Per-50ms-block dBFS for a synthetic record; spans in source seconds."""
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


def _write_stereo_wav(path: Path, y: np.ndarray, sr: int = MASTER_SR) -> None:
    sf.write(str(path), np.asarray(y, dtype=np.float32), sr, subtype="PCM_16")


def _tone_then_silence(
    dur_s: float, silent_spans, sr: int = MASTER_SR, level: float = 0.2
) -> np.ndarray:
    t = np.arange(int(dur_s * sr)) / sr
    sig = level * np.sin(2 * np.pi * 220.0 * t)
    stereo = np.stack([sig, sig], axis=1)
    for a, z in silent_spans:
        stereo[int(a * sr) : int(z * sr)] = 0.0
    return stereo


class TestContinuityMeasurement(unittest.TestCase):
    def test_block_rms_matches_verify(self) -> None:
        """The contract metric must equal verify.py's own block math (G3)."""
        rng = np.random.default_rng(7)
        data = (0.1 * rng.standard_normal((48000, 2))).astype(np.float32)
        data[12000:24000] = 0.0
        ours = continuity.block_rms_dbfs(data, 48000)
        theirs, _ms, _blk = verify._block_rms_dbfs(data, 48000)
        np.testing.assert_array_equal(ours, theirs)
        self.assertEqual(continuity.BLOCK_S, verify.SILENCE_BLOCK_S)
        self.assertEqual(continuity.DIGITAL_DBFS, verify.DIGITAL_SILENCE_DBFS)
        self.assertEqual(continuity.DIGITAL_MIN_RUN_S, verify.DIGITAL_SILENCE_MIN_RUN_S)
        self.assertEqual(continuity.LOW_LEVEL_DBFS, verify.LOW_LEVEL_DBFS)
        self.assertEqual(continuity.LOW_LEVEL_MIN_RUN_S, verify.LOW_LEVEL_MIN_RUN_S)

    def test_silent_runs_matches_verify(self) -> None:
        mask = np.array([False, True, True, False, True, False])
        self.assertEqual(continuity.silent_runs(mask), verify._silent_runs(mask))

    def test_analyzer_emits_continuity_fields(self) -> None:
        """Fresh analyze of one track emits the full contract payload (G1)."""
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "t.wav"
            _write_stereo_wav(wav, _tone_then_silence(60.0, []))
            rec = analyzer.analyze_track(wav)
        self.assertEqual(rec["continuity_version"], continuity.CONTINUITY_VERSION)
        self.assertIsInstance(rec["continuity_version"], int)
        self.assertEqual(rec["block_ms"], 50)
        self.assertEqual(rec["channels"], "all_channels_rms")
        self.assertEqual(rec["continuity_sr"], MASTER_SR)
        si = rec["silence_intervals"]
        self.assertEqual(si["digital_minus90"], [])
        self.assertEqual(si["low_level_minus60"], [])
        op = rec["opener"]
        self.assertIsNotNone(op["window_s"])
        self.assertIsNotNone(op["max_low_level_run_s"])
        self.assertIsNotNone(op["audibility_ok"])
        self.assertTrue(op["audibility_ok"])
        self.assertEqual(len(rec["block_rms_dbfs"]), int(60.0 / 0.05))

    def test_analyzer_flags_silent_source(self) -> None:
        """A dead stretch lands in silence_intervals and makes the honest
        opener verdict False when the default cue sits inside it."""
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "t.wav"
            _write_stereo_wav(wav, _tone_then_silence(60.0, [(40.0, 44.0)]))
            rec = analyzer.analyze_track(wav)
        si = rec["silence_intervals"]
        self.assertIn([40.0, 44.0], [list(r) for r in si["digital_minus90"]])
        self.assertIn([40.0, 44.0], [list(r) for r in si["low_level_minus60"]])
        # opener verdict must be honestly derived from the measured window:
        # recompute it independently from the stored per-block dBFS.
        op = rec["opener"]
        w1 = min(op["cue_in_s"] + op["window_s"], rec["duration_s"])
        b0 = math.floor(op["cue_in_s"] / continuity.BLOCK_S)
        b1 = min(len(rec["block_rms_dbfs"]), math.ceil(w1 / continuity.BLOCK_S))
        seg = np.asarray(rec["block_rms_dbfs"])[b0:b1]
        expected = not any(
            (e - s) * continuity.BLOCK_S >= continuity.LOW_LEVEL_MIN_RUN_S
            for s, e in continuity.silent_runs(seg <= continuity.LOW_LEVEL_DBFS)
        ) and not any(
            (e - s) * continuity.BLOCK_S >= continuity.DIGITAL_MIN_RUN_S
            for s, e in continuity.silent_runs(seg <= continuity.DIGITAL_DBFS)
        )
        self.assertEqual(op["audibility_ok"], expected)
        self.assertFalse(op["audibility_ok"])  # cue landed inside the silence

    def test_stale_cache_is_reanalyzed(self) -> None:
        """A payload without continuity_version is a cache miss (G2)."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            wav = Path(td) / "t.wav"
            y, sr, _ = synthesize_click_track(120.0, 8.0)
            write_wav_pcm16(wav, y, sr)
            ref = TrackRef(
                path=str(wav),
                relpath="t.wav",
                title="t",
                recording_id="rec-stale",
                work_id="work-stale",
                normalized_title="t",
                format="wav",
                size_bytes=wav.stat().st_size,
                sha256="0" * 64,
            )
            cache = analyzer.AnalysisCache(db)
            # simulate a pre-contract row: ok status, no continuity fields
            legacy = {
                "recording_id": "rec-stale",
                "path": str(wav),
                "status": "ok",
                "duration_s": 8.0,
                "eligible": True,
            }
            cache.put(legacy)
            # put() writes current ANALYSIS_VERSION; push the row back to v2
            # to simulate the pre-contract cache shape.
            cache._conn.execute(
                "UPDATE tracks SET analysis_version = 2 WHERE recording_id = 'rec-stale'"
            )
            cache._conn.commit()
            self.assertIsNone(cache.get("rec-stale"), "v2 payload must be stale")
            # also stale at current version if payload lacks the version
            cache._conn.execute(
                "UPDATE tracks SET analysis_version = ? WHERE recording_id = 'rec-stale'",
                (analyzer.ANALYSIS_VERSION,),
            )
            cache._conn.commit()
            self.assertIsNone(cache.get("rec-stale"), "payload without version is stale")
            cache.close()
            out = analyzer.analyze_corpus([ref], cache_path=db)
            self.assertEqual(out[0]["continuity_version"], continuity.CONTINUITY_VERSION)
            cache2 = analyzer.AnalysisCache(db)
            self.assertIsNotNone(cache2.get("rec-stale"))
            cache2.close()


class TestSegmentAssessment(unittest.TestCase):
    def test_interior_run_forces_cue_trim(self) -> None:
        rec = _record("rec-x", "w", 100.0, "A minor", 0.5, silent_spans=[(60.0, 66.0)])
        a = continuity.assess_segment(rec, 6.0, 17.0, 0.0)
        self.assertEqual(a["status"], "adjusted")
        self.assertLessEqual(a["cue_out"], 59.95)
        self.assertFalse(a["fatal"])

    def test_coverable_head_run_is_kept(self) -> None:
        # silence inside [cue_in, cue_in+head_cov) is optimistically covered
        # by the incoming crossfade — kept, cue unchanged; the post-order
        # sim is the exact check on whether the neighbour really covers.
        rec = _record("rec-x", "w", 100.0, "A minor", 0.5, silent_spans=[(5.0, 9.0)])
        a = continuity.assess_segment(rec, 6.0, 17.0, 0.0)
        self.assertEqual(a["status"], "ok")
        self.assertTrue(a["violations"])  # the run is recorded, just covered
        self.assertFalse(a["fatal"])

    def test_uncoverable_run_is_infeasible(self) -> None:
        rec = _record(
            "rec-x",
            "w",
            100.0,
            "A minor",
            0.5,
            silent_spans=[(30.0, 80.0), (82.0, 115.0)],
        )
        a = continuity.assess_segment(rec, 6.0, 17.0, 0.0)
        self.assertEqual(a["status"], "infeasible")
        self.assertTrue(a["fatal"])

    def test_no_downbeats_means_no_cue(self) -> None:
        rec = _record(
            "rec-x",
            "w",
            100.0,
            "A minor",
            0.5,
            silent_spans=[(30.0, 80.0)],
            downbeat_times_s=[],
            beat_times_s=[],
        )
        a = continuity.assess_segment(rec, 6.0, 17.0, 0.0)
        self.assertEqual(a["status"], "infeasible")
        self.assertEqual(a["reason"], "no_valid_cue")

    def test_gain_can_create_violation(self) -> None:
        # -84 dBFS source at gain -12 lands at -96 on the master: a 1s run is
        # a digital-silence violation only after the planned gain is applied.
        blocks = _stereo_blocks(120.0, [(60.0, 61.5)], level=-20.0)
        b = np.array(blocks)
        b[int(60.0 / 0.05) : int(61.5 / 0.05)] = -84.0
        rec = _record("rec-x", "w", 100.0, "A minor", 0.5)
        rec["block_rms_dbfs"] = [float(v) for v in b]
        a_quiet = continuity.assess_segment(rec, 0.0, 0.0, 0.0)
        self.assertEqual(a_quiet["status"], "ok")
        a_gained = continuity.assess_segment(rec, 0.0, 0.0, -12.0)
        self.assertNotEqual(a_gained["status"], "ok")


class TestPlannerHardConstraint(unittest.TestCase):
    def test_violating_track_is_pruned(self) -> None:
        # rec-2 has an uncoverable interior run; the greedy must skip it.
        records = _records()
        records[1] = _record(
            "rec-2",
            "work-2",
            103.0,
            "C major",
            0.45,
            silent_spans=[(30.0, 80.0), (82.0, 115.0)],
        )
        plan = plan_set(records, n_tracks=3, set_name="t")
        ids = [t["recording_id"] for t in plan["tracks"]]
        self.assertNotIn("rec-2", ids)

    def test_anchor_cue_adjusted_to_exclude_silence(self) -> None:
        # Anchor has a 6s dead stretch mid-track + dead tail; a legal cue
        # trims cue_out before it — no mid-lyric cut, no injected audio.
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
        self.assertLessEqual(anchor["cue_out_s"], 59.95)

    def test_infeasible_anchor_raises_structured(self) -> None:
        records = _records()
        records[2] = _record(
            "rec-3",
            "work-3",
            130.0,
            "F# minor",
            0.60,
            silent_spans=[(10.0, 100.0)],
            downbeat_times_s=[],
            beat_times_s=[],
        )
        with self.assertRaises(PlanInfeasible) as ctx:
            plan_set(records, n_tracks=3, set_name="t", include_ids=["rec-3"])
        exc = ctx.exception
        self.assertEqual(exc.payload["error"], "PLAN_INFEASIBLE")
        self.assertIn(exc.reason, ("anchor_infeasible", "anchor_no_feasible_plan"))
        self.assertTrue(exc.payload["violations"])

    def test_invalid_anchor_is_plan_infeasible(self) -> None:
        with self.assertRaises(PlanInfeasible) as ctx:
            plan_set(_records(), include_ids=["rec-nope"])
        self.assertEqual(ctx.exception.reason, "anchor_not_found")

    def test_cli_exit_code_for_infeasible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "c.sqlite"
            cache = analyzer.AnalysisCache(db)
            for r in _records():
                r["path"] = f"/fake/{r['recording_id']}.wav"
                cache.put(r)
            bad = _record(
                "rec-bad",
                "work-bad",
                120.0,
                "C major",
                0.5,
                silent_spans=[(10.0, 100.0)],
                downbeat_times_s=[],
                beat_times_s=[],
            )
            cache.put(bad)
            cache.close()
            code = cli_main(
                [
                    "plan",
                    "--cache",
                    str(db),
                    "--out",
                    str(Path(td) / "p.json"),
                    "--include-track",
                    "rec-bad",
                ]
            )
            self.assertEqual(code, 4)

    def test_unmeasured_records_never_pruned(self) -> None:
        """Legacy payloads without continuity data pass through untouched."""
        records = _records(with_continuity=False)
        plan = plan_set(records, n_tracks=3, set_name="t")
        self.assertEqual(len(plan["tracks"]), 3)

    def test_opener_with_dead_start_gets_cued_past_silence(self) -> None:
        # rec-2 is silent for its first 12 source seconds; if it opens the
        # set its cue must move past the dead stretch — a silent opener is
        # never legal, and an audible one must not be faked either.
        records = _records()
        records[1] = _record(
            "rec-2",
            "work-2",
            103.0,
            "C major",
            0.30,  # lowest energy: greedy would open with it
            silent_spans=[(0.0, 12.0)],
            intro_end_s=0.0,
        )
        plan = plan_set(records, n_tracks=3, set_name="t")
        first = plan["tracks"][0]
        if first["recording_id"] == "rec-2":
            self.assertGreaterEqual(first["cue_in_s"], 12.0)


class TestMasterSimulation(unittest.TestCase):
    def _plan(self, silent_spans_b=()) -> dict:
        recs = _records()
        recs[1] = _record("rec-2", "work-2", 103.0, "C major", 0.45, silent_spans=silent_spans_b)
        by_id = {r["recording_id"]: r for r in recs}
        chosen = recs[:3]
        cues = [continuity.default_cue_points(r) for r in chosen]
        tracks = []
        cursor = 0
        for i, (r, (ci, co)) in enumerate(zip(chosen, cues, strict=True)):
            seg = round((co - ci) * MASTER_SR)
            tracks.append(
                {
                    "index": i,
                    "recording_id": r["recording_id"],
                    "cue_in_samples": round(ci * MASTER_SR),
                    "segment_master_samples": seg,
                    "segment_source_samples": seg,
                    "tempo_stretch": 1.0,
                    "gain_db": 0.0,
                    "master_start_sample": cursor - (48000 * 6 if i else 0),
                }
            )
            cursor = tracks[-1]["master_start_sample"] + seg
        transitions = [
            {
                "index": i,
                "type": "crossfade",
                "duration_samples": 6 * MASTER_SR,
                "overlap_start_sample": tracks[i]["master_start_sample"]
                + tracks[i]["segment_master_samples"]
                - 6 * MASTER_SR,
                "overlap_end_sample": tracks[i]["master_start_sample"]
                + tracks[i]["segment_master_samples"],
            }
            for i in range(2)
        ]
        return {
            "master_sr": MASTER_SR,
            "tracks": tracks,
            "transitions": transitions,
        }, by_id

    def test_uncovered_interior_run_flagged(self) -> None:
        plan, by_id = self._plan(silent_spans_b=[(60.0, 63.0)])
        sim = continuity.find_master_violations(plan, by_id)
        self.assertTrue(sim["violations"])
        v = sim["violations"][0]
        self.assertEqual(v["tier"], "digital_minus90")
        self.assertIn("rec-2", v["present_recording_ids"])

    def test_covered_tail_run_is_clean(self) -> None:
        # rec-1 silent over [102,108] — the last 6 s of its played segment,
        # fully inside the incoming crossfade window where rec-2 is loud.
        plan, by_id = self._plan()
        rec1 = by_id["rec-1"]
        rec1["block_rms_dbfs"] = _stereo_blocks(120.0, [(102.0, 108.0)])
        sim = continuity.find_master_violations(plan, by_id)
        self.assertFalse(sim["violations"], sim["violations"])

    def test_silent_window_with_silent_neighbour_flagged(self) -> None:
        # rec-1 silent over its last 6 s AND rec-2's head silent over the
        # same window: nobody covers the dead stretch -> violation.
        plan, by_id = self._plan()
        by_id["rec-1"]["block_rms_dbfs"] = _stereo_blocks(120.0, [(102.0, 108.0)])
        by_id["rec-2"]["block_rms_dbfs"] = _stereo_blocks(120.0, [(4.8, 12.0)])
        sim = continuity.find_master_violations(plan, by_id)
        self.assertTrue(sim["violations"])
        self.assertTrue(any("rec-1" in v["present_recording_ids"] for v in sim["violations"]))

    def test_master_opener_report_on_rendered_wav(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "m.wav"
            y = _tone_then_silence(40.0, [(5.0, 9.0)])  # 4s digital gap at 5s
            _write_stereo_wav(wav, y)
            rep = continuity.master_opener_report({"tracks": []}, wav)
            self.assertFalse(rep["audibility_ok"])
            self.assertTrue(
                any(r[1] - r[0] >= 0.5 for r in rep["tiers"]["digital_minus90"]["violating_runs_s"])
            )
            wav2 = Path(td) / "m2.wav"
            _write_stereo_wav(wav2, _tone_then_silence(40.0, []))
            rep2 = continuity.master_opener_report({"tracks": []}, wav2)
            self.assertTrue(rep2["audibility_ok"])


if __name__ == "__main__":
    unittest.main()
