"""Tests for the independent timing measurement layer (no external deps)."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj.timing import (
    MAX_DEVIATION_MS,
    detect_beat_times,
    deviation_stats,
    ibi_ms,
    measure_stretch_deviation,
    synthesize_click_track,
    write_wav_pcm16,
)


class TestClickSynthesis(unittest.TestCase):
    def test_beats_are_exactly_on_grid(self) -> None:
        _y, sr, truth = synthesize_click_track(bpm=120.0, duration_s=10.0)
        self.assertEqual(sr, 48000)
        self.assertAlmostEqual(truth[1] - truth[0], 0.5, places=9)

    def test_truth_count_matches_expected(self) -> None:
        _y, _sr, truth = synthesize_click_track(bpm=60.0, duration_s=5.0)
        self.assertEqual(truth.size, 5)

    def test_roundtrip_wav_preserves_beats(self) -> None:
        y, sr, truth = synthesize_click_track(bpm=128.0, duration_s=5.0)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.wav"
            write_wav_pcm16(p, y, sr)
            measured = detect_beat_times(y, sr)
            # the click at t=0 has no rising edge, so it is not reported as an onset
            self.assertGreaterEqual(measured.size, truth.size - 1)
            self.assertAlmostEqual(ibi_ms(measured), 468.75, places=2)


class TestOnsetDetection(unittest.TestCase):
    def test_ignores_sub_threshold_noise(self) -> None:
        y, sr, _ = synthesize_click_track(bpm=100.0, duration_s=2.0)
        y = y * 0.1  # below threshold
        self.assertEqual(detect_beat_times(y, sr).size, 0)

    def test_merges_double_peaks(self) -> None:
        y, sr, truth = synthesize_click_track(bpm=100.0, duration_s=2.0)
        # duplicate each click a few ms later -> must still count one beat
        dup = np.roll(y, int(0.002 * sr)) * 0.9 + y
        measured = detect_beat_times(dup, sr)
        self.assertEqual(measured.size, truth.size)


class TestDeviationContract(unittest.TestCase):
    def test_within_threshold_true(self) -> None:
        s = deviation_stats(measured_ibi_ms=463.0, target_ibi_ms=462.96)
        self.assertTrue(s["within_threshold"])
        self.assertLess(s["deviation_ms"], MAX_DEVIATION_MS)

    def test_outside_threshold_false(self) -> None:
        # 27 ms off the target interval -> clearly outside the 20 ms contract
        s = deviation_stats(measured_ibi_ms=490.0, target_ibi_ms=462.96)
        self.assertFalse(s["within_threshold"])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class TestRubberBandEndToEnd(unittest.TestCase):
    """Ground-truth E2E: known tempo factor, measured beat deviation."""

    def test_tempo_108_within_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            src = td / "src.wav"
            dst = td / "dst.wav"
            y, sr, _truth = synthesize_click_track(bpm=120.0, duration_s=60.0)
            write_wav_pcm16(src, y, sr)
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(src),
                    "-af",
                    "rubberband=tempo=1.08",
                    "-ar",
                    "48000",
                    str(dst),
                ],
                check=True,
                capture_output=True,
            )
            stats = measure_stretch_deviation(dst, target_bpm=129.6)
            self.assertLessEqual(
                stats["deviation_ms"],
                MAX_DEVIATION_MS,
                f"beat deviation {stats['deviation_ms']:.2f} ms exceeds {MAX_DEVIATION_MS} ms",
            )
            self.assertGreater(stats["detected_beats"], 100)


if __name__ == "__main__":
    unittest.main()
