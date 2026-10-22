"""Tests for the P0 engine: corpus identity, analyzer, planner, renderer."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj.analysis import analyzer, corpus
from ai_dj.cli import main as cli_main
from ai_dj.planner import energy_arc, key_distance, plan_set, write_plan
from ai_dj.render import render_plan
from ai_dj.timing import MASTER_SR, synthesize_click_track, write_wav_pcm16
from ai_dj.verify import synthetic_alignment_check, verify_master


def _beat_times(bpm: float, duration_s: float) -> list[float]:
    step = 60.0 / bpm
    n = int(duration_s / step)
    return [round(i * step, 5) for i in range(n)]


def _record(
    rec_id: str,
    work_id: str,
    bpm: float,
    key: str,
    energy: float,
    *,
    duration_s: float = 200.0,
    conf: float = 0.9,
    **overrides,
) -> dict:
    beats = _beat_times(bpm, duration_s)
    rec = {
        "recording_id": rec_id,
        "work_id": work_id,
        "title": rec_id,
        "path": f"/fake/{rec_id}.wav",
        "status": "ok",
        "duration_s": duration_s,
        "bpm_raw": bpm * 2,
        "bpm_corrected": bpm,
        "bpm_ibi": bpm,
        "octave_diagnosis": "raw_double_time",
        "median_ibi_s": 60.0 / bpm,
        "ibi_cv": 0.02,
        "tempo_confidence": conf,
        "n_beats": len(beats),
        "beat_times_s": beats,
        "downbeat_times_s": beats[::4],
        "key": key,
        "key_confidence": 0.8,
        "energy_mean": energy,
        "rms_db": -20.0,
        "intro_end_s": 5.0,
        "outro_start_s": duration_s - 12.0,
        "silence_run_ratio": 0.0,
        "longest_silence_run_s": 0.0,
        "eligible": True,
        "exclusion_reason": None,
    }
    rec.update(overrides)
    return rec


class TestWorkTitleNormalization(unittest.TestCase):
    def test_cover_variants_group_to_same_work(self) -> None:
        a = corpus.normalize_work_title("ĐOẠN TUYỆT - COVER -DUY ")
        b = corpus.normalize_work_title("ĐOẠN TUYỆT (DUY COVER)")
        self.assertEqual(a, b)
        self.assertEqual(a, "doan tuyet")

    def test_real_corpus_names(self) -> None:
        cases = {
            "PHIM BA NGƯỜI (DUY COVER)": "phim ba nguoi",
            "PHIM BA NGƯỜI - COVER - DUY": "phim ba nguoi",
            "Mẹ Hiền Ơi - (Duy-Cover)": "me hien oi",
            "Gió Về Miền Xuôi (Duy Cover)(1)": "gio ve mien xuoi",
            "Thế Sự -. Cover Duywav": "the su",
            "vô thường và buông xả(1)": "vo thuong va buong xa",
            "Chuyện tình cô lái đò-Bolero(Cover Duy)": "chuyen tinh co lai do",
        }
        for stem, expected in cases.items():
            self.assertEqual(corpus.normalize_work_title(stem), expected, stem)

    def test_distinct_sessions_stay_distinct(self) -> None:
        a = corpus.normalize_work_title("Untitled Session 19_mixdown")
        b = corpus.normalize_work_title("Untitled Session 32_mixdown")
        self.assertNotEqual(a, b)
        self.assertIn("19", a)

    def test_work_id_is_deterministic(self) -> None:
        t = corpus.normalize_work_title("Đam Mê (Cover Duy)")
        self.assertEqual(corpus.work_id_for_title(t), corpus.work_id_for_title(t))
        self.assertTrue(corpus.work_id_for_title(t).startswith("work-"))

    def test_scan_unicode_dir_and_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "nhạc ổn"
            root.mkdir()
            y, sr, _ = synthesize_click_track(120.0, 1.0)
            write_wav_pcm16(root / "Bài Hát - Cover Duy.wav", y, sr)
            write_wav_pcm16(root / "Bài Hát (Duy Cover).wav", y, sr)
            (root / "bỏ qua.mp4").write_bytes(b"not audio")
            refs = corpus.scan_corpus(root)
            self.assertEqual(len(refs), 2)
            self.assertEqual(refs[0].work_id, refs[1].work_id)
            self.assertEqual(refs[0].recording_id, refs[1].recording_id)
            dup = corpus.duplicate_report(refs)
            self.assertEqual(dup["recording_count"], 1)
            self.assertEqual(dup["work_count"], 1)


class TestOctaveResolution(unittest.TestCase):
    def test_double_time_correction(self) -> None:
        bpm, diag = analyzer.resolve_octave(136.0, 68.0)
        self.assertEqual(bpm, 68.0)
        self.assertEqual(diag, "raw_double_time")

    def test_half_time_correction(self) -> None:
        bpm, diag = analyzer.resolve_octave(50.0, 100.0)
        self.assertEqual(bpm, 100.0)
        self.assertEqual(diag, "raw_half_time")

    def test_agreement(self) -> None:
        bpm, diag = analyzer.resolve_octave(121.0, 120.0)
        self.assertEqual(bpm, 120.0)
        self.assertEqual(diag, "agree")

    def test_missing_ibi_falls_back_to_raw(self) -> None:
        bpm, diag = analyzer.resolve_octave(100.0, float("nan"))
        self.assertEqual(bpm, 100.0)
        self.assertEqual(diag, "ibi_unavailable")


class TestKeyDistance(unittest.TestCase):
    def test_same_key(self) -> None:
        self.assertEqual(key_distance("A minor", "A minor"), 0)

    def test_relative_major_minor(self) -> None:
        self.assertEqual(key_distance("A minor", "C major"), 1)

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(key_distance("UNKNOWN", "C major"))
        self.assertIsNone(key_distance("A minor", "UNKNOWN"))


class TestEnergyArc(unittest.TestCase):
    def test_shape_and_bounds(self) -> None:
        arc = energy_arc(6, 0.2, 0.8)
        self.assertEqual(len(arc), 6)
        self.assertTrue(all(0.2 <= e <= 0.8 for e in arc))
        self.assertEqual(arc, energy_arc(6, 0.2, 0.8))


class TestPlanner(unittest.TestCase):
    def _records(self) -> list[dict]:
        return [
            _record("rec-1", "work-1", 100.0, "A minor", 0.30),
            _record("rec-2", "work-2", 103.0, "C major", 0.45),
            _record("rec-3", "work-3", 130.0, "F# minor", 0.60),
            _record("rec-4", "work-4", 105.0, "G major", 0.55),
            _record("rec-5", "work-5", 98.0, "A minor", 0.70),
            _record("rec-6", "work-6", 140.0, "D minor", 0.80),
            # same work as rec-1, weaker recording -> must be deduped
            _record("rec-1b", "work-1", 99.0, "A minor", 0.31, conf=0.4),
        ]

    def test_deterministic_plan_byte_identical(self) -> None:
        records = self._records()
        p1 = plan_set(records, n_tracks=6, set_name="mini", seed=0)
        p2 = plan_set(list(reversed(records)), n_tracks=6, set_name="mini", seed=0)
        self.assertEqual(
            json.dumps(p1, ensure_ascii=False, sort_keys=True),
            json.dumps(p2, ensure_ascii=False, sort_keys=True),
        )
        with tempfile.TemporaryDirectory() as tmp:
            f1 = write_plan(p1, Path(tmp) / "p1.json")
            f2 = write_plan(p2, Path(tmp) / "p2.json")
            self.assertEqual(f1.read_bytes(), f2.read_bytes())

    def test_dedup_one_recording_per_work(self) -> None:
        plan = plan_set(self._records(), n_tracks=6, set_name="mini")
        works = [t["work_id"] for t in plan["tracks"]]
        self.assertEqual(len(works), len(set(works)))
        ids = [t["recording_id"] for t in plan["tracks"]]
        self.assertIn("rec-1", ids)
        self.assertNotIn("rec-1b", ids)

    def test_incompatible_pair_is_not_beatmatched(self) -> None:
        records = [
            _record("rec-a", "work-a", 100.0, "A minor", 0.4),
            _record("rec-b", "work-b", 130.0, "F# minor", 0.6),
        ]
        plan = plan_set(records, n_tracks=2, set_name="t")
        tr = plan["transitions"][0]
        self.assertIn(tr["type"], ("cut", "echo_out", "crossfade"))
        self.assertNotEqual(tr["type"], "beat_crossfade")
        self.assertTrue(tr["reasons"])

    def test_compatible_pair_beatmatches(self) -> None:
        records = [
            _record("rec-a", "work-a", 100.0, "A minor", 0.4),
            _record("rec-b", "work-b", 103.0, "C major", 0.5),
        ]
        plan = plan_set(records, n_tracks=2, set_name="t")
        tr = plan["transitions"][0]
        self.assertEqual(tr["type"], "beat_crossfade")
        self.assertAlmostEqual(plan["tracks"][1]["tempo_stretch"], 100.0 / 103.0, places=4)
        self.assertGreater(tr["confidence"], 0.4)

    def test_unknown_key_blocks_beatmatch(self) -> None:
        records = [
            _record("rec-a", "work-a", 100.0, "UNKNOWN", 0.4),
            _record("rec-b", "work-b", 101.0, "C major", 0.5),
        ]
        plan = plan_set(records, n_tracks=2, set_name="t")
        self.assertNotEqual(plan["transitions"][0]["type"], "beat_crossfade")

    def test_error_records_are_ineligible(self) -> None:
        records = self._records()
        for r in records:
            r["status"] = "error"
            r["error"] = "decode failed"
        with self.assertRaises(ValueError):
            plan_set(records, n_tracks=6, set_name="t")


class TestAnalyzerEligibility(unittest.TestCase):
    """D10: long contiguous silent runs mark a file as not-a-song."""

    def test_long_silent_gap_is_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clicks, sr, _ = synthesize_click_track(120.0, 10.0)
            gap = np.zeros(int(6.0 * sr), dtype=np.float32)
            tail, _, _ = synthesize_click_track(120.0, 10.0)
            y = np.concatenate([clicks, gap, tail])
            wav = Path(tmp) / "session.wav"
            write_wav_pcm16(wav, y, sr)
            rec = analyzer.analyze_track(wav)
            self.assertIn("silence_run_ratio", rec)
            self.assertFalse(rec["eligible"])
            self.assertGreater(rec["silence_run_ratio"], 0.02)
            self.assertGreaterEqual(rec["longest_silence_run_s"], 5.9)
            self.assertIn("silence_run_ratio", rec["exclusion_reason"])

    def test_active_track_stays_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            y, sr, _ = synthesize_click_track(120.0, 12.0)
            wav = Path(tmp) / "song.wav"
            write_wav_pcm16(wav, y, sr)
            rec = analyzer.analyze_track(wav)
            self.assertTrue(rec["eligible"])
            self.assertIsNone(rec["exclusion_reason"])
            self.assertLessEqual(rec["silence_run_ratio"], 0.02)

    def test_short_silent_runs_do_not_count(self) -> None:
        # Click tracks are near-silent between beats: ~0.49 s runs, all under
        # the 2 s run threshold -> ratio stays 0 and the track is eligible.
        with tempfile.TemporaryDirectory() as tmp:
            y, sr, _ = synthesize_click_track(120.0, 12.0)
            wav = Path(tmp) / "clicks.wav"
            write_wav_pcm16(wav, y, sr)
            rec = analyzer.analyze_track(wav)
            self.assertTrue(rec["eligible"])
            self.assertEqual(rec["silence_run_ratio"], 0.0)


class TestPlannerEligibility(unittest.TestCase):
    """The planner must never select a track without an eligibility verdict."""

    def _records(self) -> list[dict]:
        return [
            _record("rec-a", "work-a", 100.0, "A minor", 0.30),
            _record("rec-b", "work-b", 103.0, "C major", 0.45),
            _record(
                "rec-c",
                "work-c",
                130.0,
                "F# minor",
                0.60,
                eligible=False,
                silence_run_ratio=0.319,
                longest_silence_run_s=46.8,
                exclusion_reason="silence_run_ratio 0.319 > 0.02",
            ),
        ]

    def test_ineligible_track_is_excluded(self) -> None:
        plan = plan_set(self._records(), n_tracks=3, set_name="t")
        ids = [t["recording_id"] for t in plan["tracks"]]
        self.assertNotIn("rec-c", ids)
        self.assertEqual(len(ids), 2)
        excluded = plan["eligibility"]["excluded"]
        self.assertEqual([e["recording_id"] for e in excluded], ["rec-c"])
        self.assertIn("silence_run_ratio", excluded[0]["exclusion_reason"])
        self.assertFalse(plan["eligibility"]["include_ineligible"])

    def test_missing_eligibility_flag_fails_closed(self) -> None:
        # v1 cache rows predate the rule: no verdict means not selectable.
        records = self._records()
        del records[2]["eligible"]
        plan = plan_set(records, n_tracks=3, set_name="t")
        self.assertNotIn("rec-c", [t["recording_id"] for t in plan["tracks"]])

    def test_include_ineligible_restores_selection(self) -> None:
        plan = plan_set(self._records(), n_tracks=3, set_name="t", include_ineligible=True)
        ids = [t["recording_id"] for t in plan["tracks"]]
        self.assertIn("rec-c", ids)
        self.assertEqual(len(ids), 3)
        self.assertEqual(plan["eligibility"]["excluded"], [])
        self.assertEqual([o["recording_id"] for o in plan["eligibility"]["overridden"]], ["rec-c"])
        self.assertTrue(plan["eligibility"]["include_ineligible"])

    def test_real_cache_never_selects_ineligible(self) -> None:
        cache_path = Path(__file__).resolve().parent.parent / "analysis_cache.sqlite"
        if not cache_path.exists():
            self.skipTest("real analysis cache not present")
        cache = analyzer.AnalysisCache(cache_path)
        try:
            records = [
                r
                for r in cache.all_records()
                if r.get("analysis_version") == analyzer.ANALYSIS_VERSION
            ]
        finally:
            cache.close()
        if not records:
            self.skipTest("cache holds no current-version records")
        ineligible = {
            r["recording_id"]
            for r in records
            if r.get("status") == "ok" and r.get("eligible") is not True
        }
        plan = plan_set(records, n_tracks=6, set_name="cache-check")
        ids = {t["recording_id"] for t in plan["tracks"]}
        self.assertTrue(ids.isdisjoint(ineligible), f"ineligible selected: {ids & ineligible}")
        by_id = {r["recording_id"]: r for r in records}
        for t in plan["tracks"]:
            self.assertIs(by_id[t["recording_id"]]["eligible"], True)


class TestAnalyzerIdempotent(unittest.TestCase):
    def test_click_track_analysis_repeats_identically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "click.wav"
            y, sr, _ = synthesize_click_track(100.0, 8.0)
            write_wav_pcm16(wav, y, sr)
            digest = hashlib.sha256(wav.read_bytes()).hexdigest()
            ref = corpus.TrackRef(
                path=str(wav),
                relpath=wav.name,
                title=wav.stem,
                recording_id="rec-" + digest[:16],
                work_id="work-test",
                normalized_title="click",
                format="wav",
                size_bytes=wav.stat().st_size,
                sha256=digest,
            )
            cache = Path(tmp) / "a.sqlite"
            r1 = analyzer.analyze_corpus([ref], cache_path=cache)
            r2 = analyzer.analyze_corpus([ref], cache_path=cache)
            self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True))
            self.assertEqual(r1[0]["status"], "ok")
            self.assertAlmostEqual(r1[0]["bpm_corrected"], 100.0, delta=5.0)


def _synth_plan(tmp: Path, bpm_a: float, bpm_b: float) -> dict:
    """Minimal two-click-track plan with one beat_crossfade."""
    dur_s, overlap_s = 16.0, 4.0
    stretch = bpm_a / bpm_b
    ya, _, _ = synthesize_click_track(bpm_a, dur_s)
    yb, _, _ = synthesize_click_track(bpm_b, dur_s)
    pa, pb = tmp / "a.wav", tmp / "b.wav"
    write_wav_pcm16(pa, ya, MASTER_SR)
    write_wav_pcm16(pb, yb, MASTER_SR)
    n = int(dur_s * MASTER_SR)
    d = int(overlap_s * MASTER_SR)
    n_b = round(n / stretch)
    return {
        "plan_version": 1,
        "set_name": "synth",
        "master_sr": MASTER_SR,
        "tracks": [
            {
                "index": 0,
                "recording_id": "rec-a",
                "path": str(pa),
                "cue_in_samples": 0,
                "cue_out_samples": n,
                "segment_master_samples": n,
                "tempo_stretch": 1.0,
                "gain_db": -6.0,
                "median_ibi_s": 60.0 / bpm_a,
                "beats_in_segment_s": _beat_times(bpm_a, dur_s),
                "master_start_sample": 0,
            },
            {
                "index": 1,
                "recording_id": "rec-b",
                "path": str(pb),
                "cue_in_samples": 0,
                "cue_out_samples": n,
                "segment_master_samples": n_b,
                "tempo_stretch": stretch,
                "gain_db": -6.0,
                "median_ibi_s": 60.0 / bpm_b,
                "beats_in_segment_s": _beat_times(bpm_b, dur_s),
                "master_start_sample": n - d,
            },
        ],
        "transitions": [
            {
                "index": 0,
                "type": "beat_crossfade",
                "duration_samples": d,
                "overlap_start_sample": n - d,
                "overlap_end_sample": n,
                "target_bpm": bpm_a,
            }
        ],
        "timeline": {
            "total_samples": n - d + n_b,
            "total_duration_s": (n - d + n_b) / MASTER_SR,
        },
    }


def _cut_plan(tmp: Path) -> dict:
    """Two click tracks hard-spliced; no stretch, no overlap window."""
    plan = _synth_plan(tmp, 100.0, 100.0)
    plan["transitions"][0]["type"] = "cut"
    plan["transitions"][0]["duration_samples"] = 0
    plan["transitions"][0]["overlap_start_sample"] = plan["tracks"][0]["cue_out_samples"]
    plan["transitions"][0]["overlap_end_sample"] = plan["tracks"][0]["cue_out_samples"]
    plan["tracks"][1]["tempo_stretch"] = 1.0
    plan["tracks"][1]["master_start_sample"] = plan["tracks"][0]["cue_out_samples"]
    plan["tracks"][1]["segment_master_samples"] = plan["tracks"][1]["cue_out_samples"]
    total = plan["tracks"][0]["cue_out_samples"] + plan["tracks"][1]["cue_out_samples"]
    plan["timeline"] = {
        "total_samples": total,
        "total_duration_s": total / MASTER_SR,
    }
    return plan


class TestRenderer(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_cut_render_is_deterministic(self) -> None:
        """No-stretch render must produce byte-identical WAVs."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = _cut_plan(Path(tmp))
            w1 = render_plan(plan, Path(tmp) / "m1.wav")
            render_plan(plan, Path(tmp) / "m2.wav")
            self.assertEqual(
                hashlib.sha256((Path(tmp) / "m1.wav").read_bytes()).hexdigest(),
                hashlib.sha256((Path(tmp) / "m2.wav").read_bytes()).hexdigest(),
            )
            self.assertEqual(w1["clipping_count"], 0)
            self.assertLessEqual(w1["true_peak_dbtp"], -1.0)
            self.assertAlmostEqual(
                w1["total_samples_actual"], plan["timeline"]["total_samples"], delta=1024
            )

    def test_render_without_ffmpeg_is_not_fatal(self) -> None:
        """D11: missing ffmpeg downgrades the true-peak check, never raises."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = _cut_plan(Path(tmp))
            with mock.patch("shutil.which", return_value=None):
                report = render_plan(plan, Path(tmp) / "m.wav")
            self.assertIsNone(report["true_peak_dbtp"])
            self.assertIsNone(report["integrated_lufs"])
            self.assertIn("unavailable", report["true_peak_measured_by"])
            self.assertIn("ffmpeg", report["true_peak_measured_by"])
            self.assertEqual(report["clipping_count"], 0)
            self.assertIn("sample-peak", report["peak_protection"])
            self.assertTrue((Path(tmp) / "m.wav").exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_beat_aligned_overlap_within_contract(self) -> None:
        """Stretched overlap IBI must sit within the 20 ms contract."""
        result = synthetic_alignment_check(bpm_a=100.0, bpm_b=104.0)
        self.assertTrue(
            result["within_threshold"],
            f"deviation {result['deviation_ms']:.2f} ms exceeds contract",
        )
        self.assertGreater(result["detected_beats_in_overlap"], 5)


def _tone_wav(
    path: Path,
    duration_s: float,
    gaps: list[tuple[float, float]] = (),
    regions: list[tuple[float, float, float | None]] = (),
    mode: str = "same",
) -> Path:
    """Continuous stereo tone at MASTER_SR with optional edited spans.

    ``gaps``: zeroed [start, end) spans (digital silence).
    ``regions``: (start, end, rms_dbfs) — a sine at RMS ``dbfs`` fills the span
    (``None`` means digital zeros). ``mode``: ``same`` L=R; ``antiphase``
    R=-L; ``dead_right`` R=0.
    """
    n = int(duration_s * MASTER_SR)
    t = np.arange(n, dtype=np.float64) / MASTER_SR
    tone = 0.5 * np.sin(2.0 * np.pi * 220.0 * t)
    right = -tone if mode == "antiphase" else tone.copy()
    if mode == "dead_right":
        right[:] = 0.0
    data = np.stack([tone, right], axis=1).astype(np.float32)
    for start_s, end_s in gaps:
        data[int(start_s * MASTER_SR) : int(end_s * MASTER_SR)] = 0.0
    for start_s, end_s, dbfs in regions:
        i0, i1 = int(start_s * MASTER_SR), int(end_s * MASTER_SR)
        if dbfs is None:
            data[i0:i1] = 0.0
        else:
            tt = np.arange(i1 - i0, dtype=np.float64) / MASTER_SR
            amp = np.sqrt(2.0) * 10.0 ** (dbfs / 20.0)
            data[i0:i1] = (amp * np.sin(2.0 * np.pi * 220.0 * tt))[:, None]
    sf.write(str(path), data, MASTER_SR, subtype="PCM_24")
    return path


def _verify_plan(duration_s: float, transitions: list[dict] | None = None) -> dict:
    """Minimal plan whose timeline matches a rendered-tone fixture."""
    return {
        "plan_version": 1,
        "set_name": "verify-fixture",
        "master_sr": MASTER_SR,
        "tracks": [],
        "transitions": transitions or [],
        "timeline": {
            "total_samples": int(duration_s * MASTER_SR),
            "total_duration_s": duration_s,
        },
    }


class TestVerifier(unittest.TestCase):
    """D12: silence_ok gates the whole master; D13: unmeasurable -> INCONCLUSIVE."""

    def test_silent_hole_outside_transitions_fails(self) -> None:
        """Regression DJ-P0-006: a 3 s hole inside track content must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = _verify_plan(
                15.0,
                transitions=[
                    {
                        "index": 0,
                        "type": "crossfade",
                        "duration_samples": int(2.0 * MASTER_SR),
                        "overlap_start_sample": int(2.0 * MASTER_SR),
                        "overlap_end_sample": int(4.0 * MASTER_SR),
                    }
                ],
            )
            wav = _tone_wav(Path(tmp) / "m.wav", 15.0, gaps=[(6.0, 9.0)])
            report = verify_master(plan, wav)
            self.assertIs(report["pass"], False)
            self.assertEqual(report["verdict"], "FAIL")
            sil = report["checks"]["silence"]
            self.assertFalse(sil["silence_ok"])
            self.assertGreaterEqual(sil["max_silent_run_ms"], 2900.0)
            self.assertEqual(sil["worst_region_s"], [6.0, 9.0])
            # A digital-silence hole trips both tiers (DJ-P0-006b):
            self.assertFalse(sil["digital_silence"]["ok"])
            self.assertFalse(sil["low_level"]["ok"])
            self.assertEqual(sil["digital_silence"]["worst_region_s"], [6.0, 9.0])
            self.assertEqual(sil["low_level"]["worst_region_s"], [6.0, 9.0])
            # Transition-window silence stays reported, as diagnostics only:
            self.assertEqual(sil["transition_windows"]["max_silent_run_in_transitions_ms"], 0.0)
            self.assertIsNone(sil["transition_windows"]["worst_region_s"])
            self.assertGreater(sil["silent_hops_total"], 0)

    def test_declared_gap_and_edge_fades_are_deliberate(self) -> None:
        """Silence in the renderer's fade edges or plan-declared gaps is exempt."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = _verify_plan(15.0)
            plan["allowed_silent_gaps_s"] = [[6.0, 9.0]]
            wav = _tone_wav(
                Path(tmp) / "m.wav",
                15.0,
                gaps=[(0.0, 0.05), (6.0, 9.0), (13.0, 15.0)],
            )
            sil = verify_master(plan, wav)["checks"]["silence"]
            self.assertTrue(sil["silence_ok"])
            self.assertEqual(sil["max_silent_run_ms"], 0.0)
            self.assertIsNone(sil["worst_region_s"])
            self.assertIn([6.0, 9.0], sil["allowed_silent_regions_s"])

    def test_low_level_near_silence_fails(self) -> None:
        """DJ-P0-006b tier 2: 2 s at ~-60 dBFS fails though it is not digital."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = _tone_wav(Path(tmp) / "m.wav", 15.0, regions=[(6.0, 8.0, -60.5)])
            report = verify_master(_verify_plan(15.0), wav)
            self.assertIs(report["pass"], False)
            self.assertEqual(report["verdict"], "FAIL")
            sil = report["checks"]["silence"]
            self.assertFalse(sil["silence_ok"])
            self.assertFalse(sil["low_level"]["ok"])
            # far above -90 dBFS: the digital-silence tier stays clean
            self.assertTrue(sil["digital_silence"]["ok"])
            self.assertAlmostEqual(sil["low_level"]["max_run_s"], 2.0, places=2)
            self.assertEqual(sil["low_level"]["worst_region_s"], [6.0, 8.0])
            self.assertIn([6.0, 8.0], sil["low_level"]["violating_runs_s"])
            # 3 s short-term loudness is reported as a diagnostic: the quiet
            # 2 s stretch drags the minimum window well below program level.
            self.assertGreaterEqual(sil["short_term_3s"]["min_dbfs"], -16.0)
            self.assertLessEqual(sil["short_term_3s"]["min_dbfs"], -12.0)
            self.assertGreaterEqual(sil["short_term_3s"]["at_s"], 4.5)
            self.assertLessEqual(sil["short_term_3s"]["at_s"], 6.5)

    def test_min_run_boundaries_are_inclusive(self) -> None:
        """Rule C: >= is inclusive — exactly 0.5 s / 2.0 s runs must fail."""
        with tempfile.TemporaryDirectory() as tmp:
            plan = _verify_plan(15.0)
            exact_digital = _tone_wav(Path(tmp) / "d.wav", 15.0, gaps=[(6.0, 6.5)])
            sil = verify_master(plan, exact_digital)["checks"]["silence"]
            self.assertFalse(sil["digital_silence"]["ok"])
            self.assertAlmostEqual(sil["digital_silence"]["max_run_s"], 0.5, places=2)
            self.assertEqual(sil["digital_silence"]["violating_runs_s"], [[6.0, 6.5]])
            self.assertTrue(sil["low_level"]["ok"])  # 0.5 s < 2.0 s

            exact_low = _tone_wav(Path(tmp) / "l.wav", 15.0, regions=[(6.0, 8.0, -60.5)])
            sil = verify_master(plan, exact_low)["checks"]["silence"]
            self.assertFalse(sil["low_level"]["ok"])
            self.assertAlmostEqual(sil["low_level"]["max_run_s"], 2.0, places=2)
            self.assertEqual(sil["low_level"]["violating_runs_s"], [[6.0, 8.0]])
            self.assertTrue(sil["digital_silence"]["ok"])

            under = _tone_wav(
                Path(tmp) / "u.wav",
                15.0,
                gaps=[(6.0, 6.45)],
                regions=[(8.0, 9.95, -60.5)],
            )
            sil = verify_master(plan, under)["checks"]["silence"]
            self.assertTrue(sil["silence_ok"])
            self.assertAlmostEqual(sil["digital_silence"]["max_run_s"], 0.45, places=2)
            self.assertAlmostEqual(sil["low_level"]["max_run_s"], 1.95, places=2)

    def test_antiphase_stereo_is_not_silence(self) -> None:
        """Rule A: L=-R cancels in a mono mixdown but both channels carry audio."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = _tone_wav(Path(tmp) / "m.wav", 15.0, mode="antiphase")
            sil = verify_master(_verify_plan(15.0), wav)["checks"]["silence"]
            self.assertTrue(sil["silence_ok"])
            self.assertEqual(sil["digital_silence"]["silent_blocks"], 0)
            self.assertEqual(sil["low_level"]["silent_blocks"], 0)
            self.assertEqual(sil["dead_channels"], [])
            self.assertEqual(len(sil["channel_rms_dbfs"]), 2)
            self.assertAlmostEqual(sil["channel_rms_dbfs"][0], sil["channel_rms_dbfs"][1], places=2)

    def test_dead_channel_is_flagged(self) -> None:
        """Per-channel RMS exposes a dead channel without failing the tier."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = _tone_wav(Path(tmp) / "m.wav", 15.0, mode="dead_right")
            sil = verify_master(_verify_plan(15.0), wav)["checks"]["silence"]
            self.assertEqual(sil["dead_channels"], [1])
            self.assertLessEqual(sil["channel_rms_dbfs"][1], -90.0)
            self.assertGreater(sil["channel_rms_dbfs"][0], -20.0)
            self.assertTrue(sil["silence_ok"])

    def test_missing_master_is_fail_not_usage_error(self) -> None:
        """Gap 2: a required artifact that is absent is FAIL, not exit-2 usage."""
        with tempfile.TemporaryDirectory() as tmp:
            report = verify_master(_verify_plan(15.0), Path(tmp) / "nope.wav")
            self.assertIs(report["pass"], False)
            self.assertEqual(report["verdict"], "FAIL")
            art = report["checks"]["artifact"]
            self.assertFalse(art["ok"])
            self.assertFalse(art["exists"])
            self.assertFalse(art["decodable"])
            self.assertIn("duration_matches_plan", report["unmeasured_checks"])

    def test_corrupt_master_is_fail(self) -> None:
        """Gap 2: an undecodable master file is FAIL with artifact detail."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.wav"
            bad.write_bytes(b"not a wav")
            report = verify_master(_verify_plan(15.0), bad)
            self.assertIs(report["pass"], False)
            self.assertEqual(report["verdict"], "FAIL")
            art = report["checks"]["artifact"]
            self.assertFalse(art["ok"])
            self.assertTrue(art["exists"])
            self.assertFalse(art["decodable"])
            self.assertTrue(art["error"])

    def test_no_ffmpeg_and_a_hole_still_fails(self) -> None:
        """FAIL is never masked by INCONCLUSIVE: measured hole beats unmeasured."""
        with tempfile.TemporaryDirectory() as tmp:
            holey = _tone_wav(Path(tmp) / "h.wav", 15.0, gaps=[(6.0, 9.0)])
            with mock.patch("shutil.which", return_value=None):
                report = verify_master(_verify_plan(15.0), holey)
            self.assertIs(report["pass"], False)
            self.assertEqual(report["verdict"], "FAIL")
            self.assertIn("true_peak", report["unmeasured_checks"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_clean_master_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = verify_master(_verify_plan(15.0), _tone_wav(Path(tmp) / "m.wav", 15.0))
            self.assertIs(report["pass"], True)
            self.assertEqual(report["verdict"], "PASS")
            self.assertEqual(report["unmeasured_checks"], [])
            self.assertTrue(report["checks"]["silence"]["silence_ok"])

    def test_unmeasured_checks_yield_inconclusive(self) -> None:
        """D13: without ffmpeg the true-peak check is unmeasured -> pass: null."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = _tone_wav(Path(tmp) / "m.wav", 15.0)
            with mock.patch("shutil.which", return_value=None):
                report = verify_master(_verify_plan(15.0), wav)
            self.assertIsNone(report["pass"])
            self.assertEqual(report["verdict"], "INCONCLUSIVE")
            self.assertIsNone(report["checks"]["true_peak"]["dbtp"])
            self.assertIsNone(report["checks"]["true_peak"]["ok"])
            self.assertIn("true_peak", report["unmeasured_checks"])

    def test_verify_cli_exit_codes(self) -> None:
        """0 = PASS, 1 = FAIL (incl. missing/corrupt artifact), 3 = INCONCLUSIVE,
        2 = usage/environment error with no verdict emitted."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(_verify_plan(15.0)), encoding="utf-8")
            out = Path(tmp) / "v.json"

            def run(*argv: str) -> tuple[int, dict | None]:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    code = cli_main(list(argv))
                try:
                    return code, json.loads(buf.getvalue())
                except json.JSONDecodeError:
                    return code, None

            # Missing master: required artifact absent -> FAIL report, exit 1.
            code, rep = run(
                "verify",
                "--plan",
                str(plan_path),
                "--master",
                str(Path(tmp) / "nope.wav"),
                "--out",
                str(out),
            )
            self.assertEqual(code, 1)
            self.assertIs(rep["pass"], False)
            self.assertEqual(rep["verdict"], "FAIL")
            self.assertFalse(rep["checks"]["artifact"]["ok"])

            # Corrupt master: required artifact undecodable -> FAIL, exit 1.
            bad = Path(tmp) / "bad.wav"
            bad.write_bytes(b"not a wav")
            code, rep = run(
                "verify", "--plan", str(plan_path), "--master", str(bad), "--out", str(out)
            )
            self.assertEqual(code, 1)
            self.assertIs(rep["pass"], False)
            self.assertEqual(rep["verdict"], "FAIL")
            self.assertFalse(rep["checks"]["artifact"]["ok"])

            holey = _tone_wav(Path(tmp) / "hole.wav", 15.0, gaps=[(6.0, 9.0)])
            code, rep = run(
                "verify", "--plan", str(plan_path), "--master", str(holey), "--out", str(out)
            )
            self.assertEqual(code, 1)
            self.assertIs(rep["pass"], False)
            self.assertEqual(rep["verdict"], "FAIL")

            # No ffmpeg but a measured hole: FAIL is never masked -> exit 1.
            with mock.patch("shutil.which", return_value=None):
                code, rep = run(
                    "verify",
                    "--plan",
                    str(plan_path),
                    "--master",
                    str(holey),
                    "--out",
                    str(out),
                )
            self.assertEqual(code, 1)
            self.assertEqual(rep["verdict"], "FAIL")

            # No ffmpeg on a clean master: unmeasured required check -> exit 3.
            clean = _tone_wav(Path(tmp) / "clean.wav", 15.0)
            with mock.patch("shutil.which", return_value=None):
                code, rep = run(
                    "verify",
                    "--plan",
                    str(plan_path),
                    "--master",
                    str(clean),
                    "--out",
                    str(out),
                )
            self.assertEqual(code, 3)
            self.assertIsNone(rep["pass"])
            self.assertEqual(rep["verdict"], "INCONCLUSIVE")

            # Unreadable plan: usage error -> exit 2, no verdict emitted.
            code, rep = run(
                "verify",
                "--plan",
                str(Path(tmp) / "no-plan.json"),
                "--master",
                str(clean),
                "--out",
                str(Path(tmp) / "v2.json"),
            )
            self.assertEqual(code, 2)
            self.assertIn("error", rep)
            self.assertNotIn("verdict", rep)

            # Argparse usage error: exit 2, no verdict JSON on stdout.
            buf = io.StringIO()
            with (
                contextlib.redirect_stdout(buf),
                contextlib.redirect_stderr(buf),
                self.assertRaises(SystemExit) as cm,
            ):
                cli_main(["verify", "--plan", str(plan_path)])
            self.assertEqual(cm.exception.code, 2)
            self.assertNotIn('"verdict"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
