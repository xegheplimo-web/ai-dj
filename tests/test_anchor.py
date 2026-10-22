"""Tests for anchor-aware set planning (DJ-P1-ANCHOR-001)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj.planner import plan_set


def _record(
    rec_id: str,
    work_id: str,
    bpm: float,
    key: str,
    energy: float,
    *,
    duration_s: float = 120.0,
    conf: float = 0.9,
    **overrides,
) -> dict:
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
        "eligible": True,
        "exclusion_reason": None,
        "sha256": (rec_id + "-").ljust(64, "0")[:64],
    }
    rec.update(overrides)
    return rec


def _records() -> list[dict]:
    return [
        _record("rec-1", "work-1", 100.0, "A minor", 0.30),
        _record("rec-2", "work-2", 103.0, "C major", 0.45),
        _record("rec-3", "work-3", 130.0, "F# minor", 0.60),
        _record("rec-4", "work-4", 105.0, "G major", 0.55),
    ]


# Byte-identical output of the pre-anchor greedy planner on _records() above
# (n_tracks=3, set_name="anchor-test", seed=0). Captured at HEAD b22eeb6.
LEGACY_GOLDEN = """{
  "plan_version": 1,
  "set_name": "anchor-test",
  "seed": 0,
  "generated_by": "ai_dj.planner",
  "master_sr": 48000,
  "constraints": {
    "min_beat_conf": 0.5,
    "beatmatch_bpm_tol": 0.06,
    "crossfade_bpm_tol": 0.15,
    "max_stretch": 1.06,
    "min_duration_s": 60.0
  },
  "energy_arc": [
    0.3,
    0.5816,
    0.3
  ],
  "eligibility": {
    "include_ineligible": false,
    "excluded": [],
    "overridden": []
  },
  "track_count": 3,
  "tracks": [
    {
      "index": 0,
      "recording_id": "rec-1",
      "work_id": "work-1",
      "title": "rec-1",
      "path": "/fake/rec-1.wav",
      "bpm": 100.0,
      "bpm_raw": 200.0,
      "key": "A minor",
      "tempo_confidence": 0.9,
      "median_ibi_s": 0.6,
      "cue_in_s": 0.0,
      "cue_out_s": 118.0,
      "cue_in_samples": 0,
      "cue_out_samples": 5664000,
      "segment_source_samples": 5664000,
      "tempo_stretch": 1.0,
      "gain_db": 0.0,
      "energy_mean": 0.3,
      "beats_in_segment_s": [],
      "segment_master_samples": 5664000,
      "master_start_sample": 0
    },
    {
      "index": 1,
      "recording_id": "rec-2",
      "work_id": "work-2",
      "title": "rec-2",
      "path": "/fake/rec-2.wav",
      "bpm": 103.0,
      "bpm_raw": 206.0,
      "key": "C major",
      "tempo_confidence": 0.9,
      "median_ibi_s": 0.5825242718446602,
      "cue_in_s": 0.0,
      "cue_out_s": 118.0,
      "cue_in_samples": 0,
      "cue_out_samples": 5664000,
      "segment_source_samples": 5664000,
      "tempo_stretch": 0.970874,
      "gain_db": 0.0,
      "energy_mean": 0.45,
      "beats_in_segment_s": [],
      "segment_master_samples": 5833919,
      "master_start_sample": 5203200
    },
    {
      "index": 2,
      "recording_id": "rec-4",
      "work_id": "work-4",
      "title": "rec-4",
      "path": "/fake/rec-4.wav",
      "bpm": 105.0,
      "bpm_raw": 210.0,
      "key": "G major",
      "tempo_confidence": 0.9,
      "median_ibi_s": 0.5714285714285714,
      "cue_in_s": 0.0,
      "cue_out_s": 118.0,
      "cue_in_samples": 0,
      "cue_out_samples": 5664000,
      "segment_source_samples": 5664000,
      "tempo_stretch": 1.0,
      "gain_db": 0.0,
      "energy_mean": 0.55,
      "beats_in_segment_s": [],
      "segment_master_samples": 5664000,
      "master_start_sample": 10749119
    }
  ],
  "transitions": [
    {
      "index": 0,
      "from_track": 0,
      "to_track": 1,
      "type": "beat_crossfade",
      "duration_s": 9.6,
      "duration_samples": 460800,
      "confidence": 0.765,
      "reasons": [
        "bpm_matched",
        "key_compatible",
        "bpm_diff_3.0%"
      ],
      "target_bpm": 100.0,
      "overlap_start_sample": 5203200,
      "overlap_end_sample": 5664000
    },
    {
      "index": 1,
      "from_track": 1,
      "to_track": 2,
      "type": "crossfade",
      "duration_s": 6.0,
      "duration_samples": 288000,
      "confidence": 0.63,
      "reasons": [
        "key_clash_5st",
        "bpm_diff_1.9%"
      ],
      "target_bpm": null,
      "overlap_start_sample": 10749119,
      "overlap_end_sample": 11037119
    }
  ],
  "timeline": {
    "total_samples": 16413119,
    "total_duration_s": 341.94
  }
}
"""


class TestAnchorPlanning(unittest.TestCase):
    def test_anchor_included_exactly_once(self) -> None:
        # rec-3 loses the greedy pick (see LEGACY_GOLDEN); the anchor forces it in.
        plan = plan_set(_records(), n_tracks=3, set_name="t", include_ids=["rec-3"])
        ids = [t["recording_id"] for t in plan["tracks"]]
        self.assertEqual(ids.count("rec-3"), 1)
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

        self.assertEqual(plan["request"]["request_type"], "include_track")
        self.assertEqual(plan["request"]["include_track_ids"], ["rec-3"])
        self.assertEqual(plan["planner"]["algorithm"], "anchored_beam_v1")
        self.assertEqual(plan["planner"]["beam_width"], 64)
        self.assertEqual(plan["planner"]["seed"], 0)

        res = plan["anchor_resolution"]
        self.assertEqual(res["requested_count"], 1)
        self.assertEqual(res["included_count"], 1)
        self.assertTrue(res["all_included"])
        anchor = res["anchors"][0]
        self.assertEqual(anchor["recording_id"], "rec-3")
        self.assertEqual(ids[anchor["slot_index"]], "rec-3")
        self.assertEqual(anchor["source_sha256"], _records()[2]["sha256"])

    def test_anchor_plan_is_deterministic(self) -> None:
        records = _records()
        p1 = plan_set(records, n_tracks=3, set_name="t", seed=0, include_ids=["rec-3"])
        p2 = plan_set(
            list(reversed(records)), n_tracks=3, set_name="t", seed=0, include_ids=["rec-3"]
        )
        self.assertEqual(
            json.dumps(p1, ensure_ascii=False, sort_keys=True),
            json.dumps(p2, ensure_ascii=False, sort_keys=True),
        )

    def test_anchor_plan_has_valid_transitions(self) -> None:
        plan = plan_set(_records(), n_tracks=3, set_name="t", include_ids=["rec-3"])
        self.assertEqual(len(plan["transitions"]), len(plan["tracks"]) - 1)
        for tr in plan["transitions"]:
            self.assertIn(tr["type"], ("beat_crossfade", "crossfade", "echo_out", "cut"))

    def test_multiple_anchors_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            plan_set(_records(), include_ids=["rec-1", "rec-2"])

    def test_nonexistent_anchor_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not found"):
            plan_set(_records(), include_ids=["rec-nope"])

    def test_ineligible_anchor_rejected(self) -> None:
        records = _records()
        records[0]["eligible"] = False
        records[0]["exclusion_reason"] = "silence_run_ratio 0.5 > 0.02"
        with self.assertRaisesRegex(ValueError, "eligibility"):
            plan_set(records, include_ids=["rec-1"])

    def test_deduped_anchor_rejected(self) -> None:
        # rec-1b shares work-1 with rec-1 but has lower confidence, so it is
        # deduplicated out of the pool and cannot anchor a plan.
        records = _records()
        records.append(_record("rec-1b", "work-1", 99.0, "A minor", 0.31, conf=0.4))
        with self.assertRaisesRegex(ValueError, "deduplicated"):
            plan_set(records, include_ids=["rec-1b"])

    def test_no_anchor_matches_legacy_golden(self) -> None:
        """Regression: without include_ids the greedy plan is byte-identical."""
        plan = plan_set(_records(), n_tracks=3, set_name="anchor-test", seed=0)
        serialized = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
        self.assertEqual(serialized, LEGACY_GOLDEN)
        for key in ("request", "planner", "anchor_resolution"):
            self.assertNotIn(key, plan)


if __name__ == "__main__":
    unittest.main()
