"""Tests for the refined beat grid (DJ-P2-BEAT-GRID-001).

The unit tests pin the estimator math and the v4 payload contract against
synthetic click tracks whose beat times are known by construction (D6:
ground truth exists before processing, never taken from the estimator).

``python tests/test_beatgrid.py --emit-evidence`` runs the corpus-scale
evidence pass: it re-analyzed cache (v4) vs the committed v3 cache, the term
ladder check, and the independent accuracy checks (synthetic ground truth and
the onset-landmark comparison on the real corpus). It writes
``evidence/beat_grid_before_after.json``, ``evidence/beat_grid_before_after.md``
and ``evidence/beat_grid_synthetic_validation.json`` — a deliberate,
reviewable act, never implicit from the test suite.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import librosa
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj import continuity
from ai_dj.analysis import analyzer, beatgrid
from ai_dj.analysis.analyzer import ANALYSIS_SR
from ai_dj.cli import main as cli_main
from ai_dj.timing import MASTER_SR, synthesize_click_track, write_wav_pcm16

ROOT = Path(__file__).resolve().parent.parent
LEGACY_FRAME_S = 512.0 / ANALYSIS_SR  # 23.22 ms — the v3 beat raster
FINE_FRAME_S = beatgrid.BEAT_HOP_LENGTH / ANALYSIS_SR  # 5.80 ms


def _analyzed_click(bpm: float, dur_s: float = 30.0, **kw) -> dict:
    with tempfile.TemporaryDirectory() as td:
        y, sr, _truth = synthesize_click_track(bpm, dur_s, **kw)
        wav = Path(td) / "click.wav"
        write_wav_pcm16(wav, y, sr)
        return analyzer.analyze_track(wav)


class TestFitBeatGrid(unittest.TestCase):
    def test_exact_line_recovers_slope_and_phase(self) -> None:
        times = 0.4177 * np.arange(200) + 0.137
        fit = beatgrid.fit_beat_grid(times)
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit["ibi_s"], 0.4177, places=9)
        self.assertAlmostEqual(fit["phase_s"], 0.137, places=9)
        self.assertLess(float(np.abs(fit["residuals_s"]).max()), 1e-9)

    def test_fit_ignores_per_beat_quantization(self) -> None:
        # Beats snapped to the fine frame raster still recover a continuous
        # IBI — the whole point of the regression.
        truth_ibi = 0.3714  # ~161.6 BPM: sits between frame multiples
        times = np.round((truth_ibi * np.arange(300) + 0.5) / FINE_FRAME_S) * FINE_FRAME_S
        fit = beatgrid.fit_beat_grid(times)
        self.assertAlmostEqual(fit["ibi_s"], truth_ibi, delta=FINE_FRAME_S / 10)
        self.assertNotAlmostEqual(
            fit["ibi_s"] / FINE_FRAME_S, round(fit["ibi_s"] / FINE_FRAME_S), places=6
        )

    def test_too_few_beats_returns_none(self) -> None:
        self.assertIsNone(beatgrid.fit_beat_grid(np.asarray([1.0])))
        self.assertIsNone(beatgrid.fit_beat_grid(np.asarray([])))

    def test_bpm_resolution_below_1_bpm(self) -> None:
        # The acceptance bound: < 1 BPM across 80-180 at realistic beat counts.
        for bpm in (80.0, 120.0, 160.0, 180.0):
            ibi = 60.0 / bpm
            for n in (30, 100, 400):
                res = beatgrid.bpm_resolution(ibi, n, FINE_FRAME_S)
                self.assertLess(res, 1.0, f"bpm={bpm} n={n}")
        # And it is genuinely small at corpus scale, not merely under 1.
        res = beatgrid.bpm_resolution(60.0 / 150.0, 300, FINE_FRAME_S)
        self.assertLess(res, 0.01)


class TestRefinedAnalysis(unittest.TestCase):
    """Known-answer checks through the real ``analyze_track`` path."""

    def test_off_ladder_bpm_recovered(self) -> None:
        # 128.5 BPM cannot be expressed on either the old or new frame ladder.
        rec = _analyzed_click(128.5)
        self.assertAlmostEqual(rec["bpm_refined"], 128.5, delta=0.05)
        self.assertAlmostEqual(rec["bpm_corrected"], 128.5, delta=0.05)

    def test_median_ibi_is_off_the_legacy_raster(self) -> None:
        rec = _analyzed_click(128.5)
        frames = rec["median_ibi_s"] / LEGACY_FRAME_S
        self.assertGreater(abs(frames - round(frames)), 1e-3)
        # ...and also off the fine raster: the shipped grid is continuous.
        fine_frames = rec["median_ibi_s"] / FINE_FRAME_S
        self.assertGreater(abs(fine_frames - round(fine_frames)), 1e-3)

    def test_grid_is_not_frame_snapped(self) -> None:
        rec = _analyzed_click(117.3, 30.0)
        grid = np.asarray(rec["beat_times_s"])
        snapped = np.abs(np.diff(grid) / FINE_FRAME_S)
        # the refined grid's IBIs are all equal to the LS slope — a
        # continuous value, not an integer frame count
        self.assertGreater(np.abs(snapped - np.round(snapped)).max(), 1e-3)

    def test_v4_payload_fields_present(self) -> None:
        rec = _analyzed_click(100.0)
        self.assertEqual(rec["analysis_version"], analyzer.ANALYSIS_VERSION)
        self.assertEqual(rec["beat_grid_version"], beatgrid.BEAT_GRID_VERSION)
        self.assertEqual(rec["beat_hop_length"], beatgrid.BEAT_HOP_LENGTH)
        for field in (
            "bpm_ibi_median",
            "bpm_refined",
            "bpm_resolution",
            "bpm_corrected",
            "median_ibi_detected_s",
            "beat_times_detected_s",
            "beat_quality",
        ):
            self.assertIn(field, rec)
        self.assertLess(rec["bpm_resolution"], 1.0)

    def test_quality_metrics_present(self) -> None:
        rec = _analyzed_click(120.0)
        q = rec["beat_quality"]
        for field in (
            "residual_std_ms",
            "residual_max_ms",
            "coverage_ratio",
            "residual_growth_ratio",
            "phase_drift_ms",
            "ibi_drift_pct",
            "beat_env_ratio",
            "octave_suspect",
            "tempo_unstable",
            "status",
            "reasons",
        ):
            self.assertIn(field, q)
        self.assertEqual(q["status"], "ok")

    def test_fabricated_grid_on_flat_envelope_is_flagged(self) -> None:
        # A sustained tone has no beats: beat_track still emits a perfectly
        # regular grid (LS residual ~0, coverage ~1) — only the envelope
        # strength at beat frames exposes the fabrication.
        with tempfile.TemporaryDirectory() as td:
            sr = MASTER_SR
            y = (0.4 * np.sin(2 * np.pi * 220.0 * np.arange(int(20 * sr)) / sr)).astype(np.float32)
            wav = Path(td) / "tone.wav"
            write_wav_pcm16(wav, y, sr)
            rec = analyzer.analyze_track(wav)
        q = rec["beat_quality"]
        self.assertEqual(q["status"], "suspect", q)
        self.assertLess(q["beat_env_ratio"], beatgrid.BEAT_ENV_RATIO_MIN)

    def test_click_track_has_strong_beat_env_ratio(self) -> None:
        rec = _analyzed_click(120.0)
        self.assertGreaterEqual(rec["beat_quality"]["beat_env_ratio"], beatgrid.BEAT_ENV_RATIO_MIN)

    def test_tempo_change_is_flagged_unstable(self) -> None:
        # Known ground truth: the click track changes tempo halfway through —
        # a constant-tempo grid cannot express it, and the quality block must
        # say so rather than smoothing over it.
        with tempfile.TemporaryDirectory() as td:
            y1, sr, _ = synthesize_click_track(100.0, 15.0)
            y2, _, _ = synthesize_click_track(140.0, 15.0)
            y = np.concatenate([y1, y2])
            wav = Path(td) / "drift.wav"
            write_wav_pcm16(wav, y, sr)
            rec = analyzer.analyze_track(wav)
        q = rec["beat_quality"]
        self.assertTrue(
            q["tempo_unstable"] or q["residual_std_ms"] > beatgrid.RESIDUAL_STD_WARN_MS,
            f"tempo change not flagged: {q}",
        )

    def test_dropped_beat_region_lowers_coverage(self) -> None:
        # Beats covering only half of a sounding span -> coverage_low.
        detected = 0.5 * np.arange(100)  # 49.5 s of 120 BPM beats
        fit = beatgrid.fit_beat_grid(detected)
        q = beatgrid.assess_quality(detected, fit, active_span_s=100.0, octave_diagnosis="agree")
        self.assertAlmostEqual(q["coverage_ratio"], 0.5, delta=0.02)
        self.assertTrue(q["coverage_low"])
        self.assertEqual(q["status"], "suspect")
        self.assertTrue(q["reasons"])

    def test_over_detection_flags_coverage_high(self) -> None:
        # Twice as many detections as the span's implied beat count.
        detected = 0.25 * np.arange(100)  # 24.75 s at double-time
        fit = beatgrid.fit_beat_grid(detected)
        q = beatgrid.assess_quality(detected, fit, active_span_s=12.0, octave_diagnosis="agree")
        self.assertTrue(q["coverage_high"])
        self.assertEqual(q["status"], "suspect")

    def test_growing_residual_flags_tempo_unstable(self) -> None:
        # A real tempo step mid-series: late IBI sits 10% below early IBI —
        # the symmetric V-residual hides it from the slope metrics, so it is
        # caught by the direct early-vs-late IBI shift instead.
        t = np.concatenate([0.5 * np.arange(100), 50.0 + 0.45 * np.arange(100)])
        fit = beatgrid.fit_beat_grid(t)
        q = beatgrid.assess_quality(t, fit, active_span_s=95.0, octave_diagnosis="agree")
        self.assertTrue(q["tempo_unstable"], q)
        self.assertLess(q["ibi_drift_pct"], -beatgrid.IBI_DRIFT_WARN_PCT)

    def test_octave_disagreement_is_suspect(self) -> None:
        t = 0.5 * np.arange(100)
        fit = beatgrid.fit_beat_grid(t)
        q = beatgrid.assess_quality(t, fit, active_span_s=50.0, octave_diagnosis="raw_double_time")
        self.assertTrue(q["octave_suspect"])
        self.assertEqual(q["status"], "suspect")


class TestStaleGridFailsClosed(unittest.TestCase):
    """v3 payloads (continuity-complete, pre-refined grid) are unmeasured."""

    def _v3_record(self, rec_id: str, work_id: str) -> dict:
        step = 60.0 / 100.0
        beats = [round(i * step, 5) for i in range(240)]
        return {
            "recording_id": rec_id,
            "work_id": work_id,
            "title": rec_id,
            "path": f"/fake/{rec_id}.wav",
            "status": "ok",
            "analysis_version": 3,
            "duration_s": 120.0,
            "bpm_corrected": 100.0,
            "median_ibi_s": step,
            "tempo_confidence": 0.9,
            "key": "A minor",
            "energy_mean": 0.5,
            "rms_db": -20.0,
            "intro_end_s": 5.0,
            "outro_start_s": 108.0,
            "beat_times_s": beats,
            "downbeat_times_s": beats[::4],
            "eligible": True,
            "exclusion_reason": None,
            "continuity_version": continuity.CONTINUITY_VERSION,
            "block_ms": int(continuity.BLOCK_S * 1000),
            "channels": "all_channels_rms",
            "continuity_sr": MASTER_SR,
            "block_rms_dbfs": [-20.0] * int(120.0 / continuity.BLOCK_S),
            "silence_intervals": {"digital_minus90": [], "low_level_minus60": []},
            "opener": {"window_s": 30, "audibility_ok": True},
        }

    def test_v3_record_is_unmeasured(self) -> None:
        self.assertFalse(continuity.has_continuity_payload(self._v3_record("r", "w")))

    def test_v3_cache_plan_fails_closed(self) -> None:
        """plan over an all-v3 cache -> exit 5 (CORPUS_UNMEASURED), no plan."""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "v3.sqlite"
            cache = analyzer.AnalysisCache(db)
            for i in range(4):
                rec = self._v3_record(f"rec-{i}", f"work-{i}")
                cache.put(rec)
            cache._conn.execute("UPDATE tracks SET analysis_version = 3")
            cache._conn.commit()
            cache.close()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                code = cli_main(["plan", "--cache", str(db), "--out", str(Path(td) / "p.json")])
            self.assertEqual(code, 5)
            rep = json.loads(buf.getvalue())
            self.assertIn(rep["error"], ("CORPUS_UNMEASURED", "ANALYSIS_REQUIRED"))
            self.assertFalse((Path(td) / "p.json").exists())


# --------------------------------------------------------------------------
# Evidence generation (--emit-evidence): corpus before/after + independent
# accuracy checks. Deliberate, never implicit.
# --------------------------------------------------------------------------


def _onset_landmark_check(path: str, grid_old: np.ndarray, grid_new: np.ndarray) -> dict:
    """Distance from each shipped grid to independently peak-picked onsets.

    The landmarks come from ``librosa.onset.onset_detect`` on a *different*
    frame grid (hop 512) than the beat detector's (hop 128): spectral-flux
    peaks chosen by peak-picking, not by beat_track's dynamic programming —
    and never by the least-squares fit being scored. A grid that is wrong
    about tempo or phase drifts away from the landmarks over the file, so
    median nearest-onset distance is a ground-referenced accuracy measure.
    """
    y, sr = librosa.load(str(path), sr=ANALYSIS_SR, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
    onsets = librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=512, units="time", backtrack=True
    )

    def devs(grid: np.ndarray) -> np.ndarray:
        if grid.size == 0 or onsets.size == 0:
            return np.asarray([])
        return np.abs(grid[:, None] - onsets[None, :]).min(axis=1)

    d_old, d_new = devs(grid_old) * 1000.0, devs(grid_new) * 1000.0
    return {
        "onset_landmarks": int(onsets.size),
        "v3_median_dist_ms": round(float(np.median(d_old)), 2) if d_old.size else None,
        "v4_median_dist_ms": round(float(np.median(d_new)), 2) if d_new.size else None,
        "v3_within_20ms": round(float((d_old <= 20.0).mean()), 4) if d_old.size else None,
        "v4_within_20ms": round(float((d_new <= 20.0).mean()), 4) if d_new.size else None,
    }


# --------------------------------------------------------------------------
# T2 musical synthetic patterns (D26 three-tier benchmark): kick/snare/hi-hat
# patterns whose ground-truth beat times are known BEFORE the audio exists.
# --------------------------------------------------------------------------


def _env(n: int, attack: int, decay: float) -> np.ndarray:
    a = np.ones(max(1, attack), dtype=np.float32)
    d = np.exp(-np.arange(n - len(a), dtype=np.float32) / max(1.0, decay))
    return np.concatenate([a, d])[:n]


def _kick(dur: float = 0.18, sr: int = MASTER_SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float32) / sr
    f = 110.0 * np.exp(-t * 28.0) + 45.0
    return (np.sin(2 * np.pi * f * t) * _env(n, 8, 0.030)).astype(np.float32)


def _snare(dur: float = 0.16, sr: int = MASTER_SR) -> np.ndarray:
    n = int(dur * sr)
    rng = np.random.default_rng(1234)
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n, dtype=np.float32) / sr
    tone = np.sin(2 * np.pi * 190.0 * t).astype(np.float32)
    return ((0.7 * noise + 0.3 * tone) * _env(n, 4, 0.022)).astype(np.float32)


def _hihat(dur: float = 0.045, sr: int = MASTER_SR) -> np.ndarray:
    n = int(dur * sr)
    rng = np.random.default_rng(99)
    noise = rng.standard_normal(n).astype(np.float32)
    noise = np.diff(np.concatenate([[0.0], noise])).astype(np.float32)
    return (noise * _env(n, 2, 0.010)).astype(np.float32)


def render_pattern(
    bpm: float,
    duration_s: float,
    *,
    swing: float = 0.0,
    backbeat: bool = True,
    hats_on_eighths: bool = True,
    missing_every: int = 0,
    kick_every_beat: bool = False,
) -> tuple[np.ndarray, list[float]]:
    """Render a drum pattern; return (audio, ground_truth_beat_times).

    swing=0.0 -> straight eighths; swing=0.5 -> triplet feel.
    missing_every=N -> drop every Nth beat from the audio (truth keeps it, so
    a robust detector must infer it or report low confidence).
    """
    ibi = 60.0 / bpm
    n = int(duration_s * MASTER_SR)
    y = np.zeros(n, dtype=np.float32)
    k, s, h = _kick(), _snare(), _hihat()

    def put(src: np.ndarray, t0: float, gain: float = 1.0) -> None:
        i = int(t0 * MASTER_SR)
        if i < n:
            j = min(n, i + len(src))
            y[i:j] += gain * src[: j - i]

    truth: list[float] = []
    beat_i = 0
    t = 0.0
    while t < duration_s:
        truth.append(round(t, 9))
        drop = missing_every and (beat_i % missing_every == 0) and beat_i > 0
        bar_pos = beat_i % 4
        if not drop:
            if kick_every_beat or bar_pos in (0, 2):
                put(k, t)
            if backbeat and bar_pos in (1, 3):
                put(s, t)
            for frac in (0.0, 0.5 + swing * 0.5):
                put(h, t + ibi * frac, gain=0.35)
        beat_i += 1
        t += ibi
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak * 0.85).astype(np.float32), truth


_T1_BPMS = [
    88.0,
    92.0,
    96.0,
    100.0,
    104.0,
    108.0,
    112.0,
    116.0,
    120.0,
    122.5,
    124.0,
    126.0,
    128.0,
    130.0,
    132.0,
    134.0,
    136.0,
    138.0,
    140.0,
    142.0,
    144.0,
    146.0,
    148.0,
    150.0,
    152.0,
    154.0,
    156.0,
    158.0,
    160.0,
    161.484,
    162.0,
    164.0,
    166.0,
    166.686,
    168.0,
    170.0,
    172.267,
    174.0,
    176.0,
    178.0,
    180.0,
]

_T2_CASES = [
    ("straight_120", 120.0, {}),
    ("straight_128", 128.0, {}),
    ("offladder_166_686", 166.686, {}),
    ("offladder_128_5", 128.5, {}),
    ("swing_120", 120.0, {"swing": 0.5}),
    (
        "half_time_120",
        120.0,
        {"backbeat": True, "kick_every_beat": False, "hats_on_eighths": False},
    ),
    ("four_on_floor_126", 126.0, {"kick_every_beat": True, "backbeat": False}),
    ("missing_every_4", 128.0, {"missing_every": 4}),
]


def _truth_row(name: str, truth_bpm: float, rec: dict, truth_beats=None) -> dict:
    """Known-answer score row; ground truth predates the analysis."""
    ref = rec.get("bpm_refined")
    grid = np.asarray(rec["beat_times_s"])
    row = {
        "case": name,
        "truth_bpm": truth_bpm,
        "bpm_refined": ref,
        "bpm_ibi_median": rec["bpm_ibi_median"],
        "bpm_corrected": rec["bpm_corrected"],
        "octave_diagnosis": rec["octave_diagnosis"],
        "beat_status": (rec.get("beat_quality") or {}).get("status"),
        "bpm_error_vs_truth": (
            round(abs(ref - truth_bpm), 4) if ref and np.isfinite(truth_bpm) else None
        ),
        "half_time_equivalent_error_bpm": (
            round(abs(ref * 2 - truth_bpm), 4) if ref and np.isfinite(truth_bpm) else None
        ),
    }
    if truth_beats is not None:
        truth = np.asarray(truth_beats)
        d = np.abs(grid[:, None] - truth[None, :]).min(axis=1) * 1000.0 if grid.size else []
        row["grid_to_truth_median_ms"] = round(float(np.median(d)), 3) if len(d) else None
        row["grid_to_truth_p95_ms"] = round(float(np.percentile(d, 95)), 3) if len(d) else None
    return row


def _synthetic_validation() -> dict:
    """Three-tier known-answer suite (D26): truth decided before analysis.

    T1: click-track sweep across 88-180 BPM incl. off-ladder values — the old
    raster could not express 128.5/142.3/166.686 at all.
    T2: kick/snare/hi-hat musical patterns (swing, half-time, four-on-floor,
    missing beats) — one transient shape proves nothing about a drum kit.
    T2n: negative material (noise, pure tone, aperiodic clicks) — a correct
    estimator must not claim a confident tempo.
    """
    out = {"tolerance_pct": 2.0}
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        t1 = []
        for bpm in _T1_BPMS:
            y, sr, truth = synthesize_click_track(bpm, 20.0)
            wav = td / f"t1_{bpm:.3f}.wav"
            write_wav_pcm16(wav, y, sr)
            rec = analyzer.analyze_track(wav)
            row = _truth_row(f"click_{bpm:.3f}", bpm, rec, truth)
            ibi = rec.get("median_ibi_s")
            frames = ibi / LEGACY_FRAME_S if ibi else None
            row["ibi_legacy_frames"] = round(frames, 4) if frames else None
            row["ibi_is_integer_frames"] = abs(frames - round(frames)) < 1e-4 if frames else None
            t1.append(row)
        t2 = []
        for name, bpm, kw in _T2_CASES:
            y, truth = render_pattern(bpm, 20.0, **kw)
            wav = td / f"t2_{name}.wav"
            write_wav_pcm16(wav, y, MASTER_SR)
            t2.append(_truth_row(f"t2_{name}", bpm, analyzer.analyze_track(wav), truth))
        rng = np.random.default_rng(20261023)
        dur = 20.0
        n = int(dur * MASTER_SR)
        neg = []
        for name, y in (
            ("neg_white_noise", (rng.standard_normal(n) * 0.3).astype(np.float32)),
            (
                "neg_pure_tone",
                (0.4 * np.sin(2 * np.pi * 220.0 * np.arange(n) / MASTER_SR)).astype(np.float32),
            ),
        ):
            wav = td / f"{name}.wav"
            write_wav_pcm16(wav, y, MASTER_SR)
            rec = analyzer.analyze_track(wav)
            neg.append(
                {
                    "case": name,
                    "expected": "no confident tempo",
                    "bpm_refined": rec["bpm_refined"],
                    "n_beats": rec["n_beats"],
                    "tempo_confidence": rec["tempo_confidence"],
                    "beat_status": (rec.get("beat_quality") or {}).get("status"),
                }
            )
        clicks = np.zeros(n, dtype=np.float32)
        for _ in range(120):
            i = int(rng.uniform(0, dur - 0.05) * MASTER_SR)
            clicks[i : i + int(0.015 * MASTER_SR)] = 0.9
        wav = td / "neg_random_clicks.wav"
        write_wav_pcm16(wav, clicks, MASTER_SR)
        rec = analyzer.analyze_track(wav)
        neg.append(
            {
                "case": "neg_random_clicks",
                "expected": "no confident tempo",
                "bpm_refined": rec["bpm_refined"],
                "n_beats": rec["n_beats"],
                "tempo_confidence": rec["tempo_confidence"],
                "beat_status": (rec.get("beat_quality") or {}).get("status"),
            }
        )
    out["T1_click_sweep"] = t1
    out["T2_musical"] = t2
    out["T2_negative"] = neg
    errs = [r["bpm_error_vs_truth"] for r in t1 + t2 if r["bpm_error_vs_truth"] is not None]
    out["summary"] = {
        "t1_n": len(t1),
        "t1_max_bpm_error": max(r["bpm_error_vs_truth"] for r in t1),
        "t1_ibi_on_legacy_raster_count": sum(1 for r in t1 if r["ibi_is_integer_frames"]),
        "t2_n": len(t2),
        "max_bpm_error_all": max(errs) if errs else None,
    }
    return out


def emit_evidence(cache_v4: Path, corpus_root: Path | None) -> dict:
    """Corpus-scale before/after + landmark check; writes evidence files."""
    v3: dict[str, dict] = {}
    old_cache = ROOT / "analysis_cache.sqlite"
    if old_cache.exists():
        conn = sqlite3.connect(str(old_cache))
        for rid, payload in conn.execute("SELECT recording_id, payload FROM tracks"):
            v3[rid] = json.loads(payload)
        conn.close()
    conn = sqlite3.connect(str(cache_v4))
    v4 = {rid: json.loads(p) for rid, p in conn.execute("SELECT recording_id, payload FROM tracks")}
    conn.close()

    tracks = []
    deltas = []
    for rid, new in sorted(v4.items()):
        if new.get("status") != "ok":
            continue
        old = v3.get(rid) or {}
        row = {
            "recording_id": rid,
            "title": new.get("title"),
            "bpm_v3": old.get("bpm_corrected"),
            "bpm_refined": new.get("bpm_refined"),
            "bpm_ibi_median": new.get("bpm_ibi_median"),
            "bpm_raw": new.get("bpm_raw"),
            "bpm_corrected": new.get("bpm_corrected"),
            "bpm_resolution": new.get("bpm_resolution"),
            "n_beats": new.get("n_beats"),
            "beat_quality": new.get("beat_quality"),
        }
        if row["bpm_v3"] and new.get("bpm_refined"):
            row["delta_bpm"] = round(new["bpm_refined"] - row["bpm_v3"], 4)
            deltas.append(row["delta_bpm"])
        if corpus_root is not None:
            path = Path(new.get("path") or "")
            old_grid = np.asarray(old.get("beat_times_s") or [], dtype=np.float64)
            new_grid = np.asarray(new.get("beat_times_s") or [], dtype=np.float64)
            if path.exists():
                row["landmarks"] = _onset_landmark_check(path, old_grid, new_grid)
        tracks.append(row)

    changed = sum(1 for d in deltas if abs(d) > 0.5)
    doc = {
        "generated_by": "tests/test_beatgrid.py --emit-evidence",
        "caches": {"v3": str(old_cache), "v4": str(cache_v4)},
        "frame_period_ms": {
            "v3_hop512": LEGACY_FRAME_S * 1000,
            "v4_hop128": FINE_FRAME_S * 1000,
        },
        "summary": {
            "tracks": len(tracks),
            "bpm_changed_gt_0p5": changed,
            "delta_abs_median": (round(float(np.median(np.abs(deltas))), 4) if deltas else None),
            "delta_abs_max": (round(float(np.abs(deltas).max()), 4) if deltas else None),
            "delta_histogram_0p5_bins": _histogram(deltas),
        },
        "tracks": tracks,
    }
    (ROOT / "evidence" / "beat_grid_before_after.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_markdown_table(doc)
    synth = {
        "generated_by": "tests/test_beatgrid.py --emit-evidence",
        "method": (
            "Ground truth decided BEFORE analysis in every tier (D6): T1 "
            "synthesize_click_track sweep, T2 render_pattern drum patterns, "
            "T2n negative material; the real-corpus check scores each shipped "
            "grid against onset_detect peak-picked landmarks on a hop-512 "
            "envelope — independent of the hop-128 beat detector and of the "
            "least-squares fit being scored"
        ),
        "synthetic_cases": _synthetic_validation(),
        "corpus_landmarks": [
            {"title": t["title"], **t["landmarks"]} for t in tracks if "landmarks" in t
        ],
    }
    (ROOT / "evidence" / "beat_grid_synthetic_validation.json").write_text(
        json.dumps(synth, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return doc


def _histogram(deltas: list[float]) -> dict:
    bins: dict[str, int] = {}
    for d in deltas:
        lo = np.floor(abs(d) / 0.5) * 0.5
        bins[f"{lo:.1f}-{lo + 0.5:.1f}"] = bins.get(f"{lo:.1f}-{lo + 0.5:.1f}", 0) + 1
    return dict(sorted(bins.items(), key=lambda kv: float(kv[0].split("-")[0])))


def _write_markdown_table(doc: dict) -> None:
    lines = [
        "# Beat grid before/after (v3 hop-512 snapped vs v4 hop-128 refined)",
        "",
        f"Tracks compared: {doc['summary']['tracks']} — "
        f"BPM changed > 0.5: {doc['summary']['bpm_changed_gt_0p5']}",
        "",
        "| track | v3 bpm | v4 refined | delta | resid ms | coverage | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in doc["tracks"]:
        q = t.get("beat_quality") or {}
        lines.append(
            f"| {t['title']} | {t['bpm_v3']} | {t['bpm_refined']} | "
            f"{t.get('delta_bpm')} | {q.get('residual_std_ms')} | "
            f"{q.get('coverage_ratio')} | {q.get('status')} |"
        )
    (ROOT / "evidence" / "beat_grid_before_after.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emit-evidence", action="store_true")
    ap.add_argument(
        "--cache-v4",
        default=str(ROOT / "evidence" / "analysis_cache_v4.sqlite"),
        help="v4 analysis cache produced by `analyze --force`",
    )
    ap.add_argument(
        "--corpus-dir",
        default=None,
        help="audio root for the onset-landmark check (skipped if absent)",
    )
    args = ap.parse_args()
    if args.emit_evidence:
        root = Path(args.corpus_dir) if args.corpus_dir else None
        doc = emit_evidence(Path(args.cache_v4), root)
        print(json.dumps(doc["summary"], indent=2))
        return 0
    return unittest.main(argv=[sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
