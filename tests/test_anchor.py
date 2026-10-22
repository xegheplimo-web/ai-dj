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


# G8 regression contract (DJ-P2-CONTINUITY-002, clause C2): the legacy
# byte-identical golden captured at HEAD b22eeb6 (pre-continuity, v2 snapshot)
# is ARCHIVED at tests/fixtures/legacy_golden_plan_v2.json for reference only —
# it is NOT a gate. The current contract: same v3 snapshot + same
# algorithm/params/seed -> byte-identical plan.json (see
# TestG8CurrentContract in tests/test_hardening.py).


class TestAnchorPlanning(unittest.TestCase):
    def test_anchor_included_exactly_once(self) -> None:
        # rec-3 loses the greedy pick (see the archived v2 golden fixture);
        # the anchor forces it in.
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

    def test_no_anchor_omits_anchor_metadata(self) -> None:
        """Regression: without include_ids the plan carries no anchor keys."""
        plan = plan_set(_records(), n_tracks=3, set_name="anchor-test", seed=0)
        for key in ("request", "planner", "anchor_resolution"):
            self.assertNotIn(key, plan)

    def test_no_anchor_plan_is_byte_deterministic(self) -> None:
        """G8-current on the greedy path: same input -> byte-identical output."""
        p1 = plan_set(_records(), n_tracks=3, set_name="anchor-test", seed=0)
        p2 = plan_set(list(reversed(_records())), n_tracks=3, set_name="anchor-test", seed=0)
        self.assertEqual(
            json.dumps(p1, indent=2, ensure_ascii=False) + "\n",
            json.dumps(p2, indent=2, ensure_ascii=False) + "\n",
        )


if __name__ == "__main__":
    unittest.main()
