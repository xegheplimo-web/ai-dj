"""Adversarial predictor tests (DJ-P2-CONTINUITY-002, clause C4).

The planner's D12 simulation (``continuity.find_master_violations``) is a
*predictor*: it estimates each 50 ms master block as the max over contributing
tracks of ``source_dbfs + gain_db + fade_env_db``. It does not render audio and
it cannot see phase, the renderer's post-mix peak-protection scale, or
sub-block waveform detail. This file quantifies where that prediction diverges
from the verifier reading the real master (``verify._silence_check`` — the D12
gate itself).

Outcome classes (predicting "a D12 violation exists"):

* ``agreement_violation`` — predictor flags, verifier fails (true positive)
* ``agreement_clean`` — both pass (true negative)
* ``false_positive`` — predictor flags a violation the verifier clears
* ``false_negative`` — predictor clears, verifier fails  ← the dangerous one

Every ``false_negative`` (predictor PASS but verifier FAIL) MUST be recorded
in ``tests/fixtures/predictor_divergence.json`` as a regression fixture.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj import continuity, verify
from ai_dj.render import render_plan
from ai_dj.timing import MASTER_SR

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "predictor_divergence.json"
BLK = int(continuity.BLOCK_S * MASTER_SR)


def _tone(dur_s: float, *, amp: float = 0.5, freq: float = 220.0) -> np.ndarray:
    n = int(dur_s * MASTER_SR)
    t = np.arange(n, dtype=np.float64) / MASTER_SR
    sig = amp * np.sin(2.0 * np.pi * freq * t)
    return np.stack([sig, sig], axis=1).astype(np.float32)


def _sine_rms_dbfs(dbfs: float, dur_s: float) -> np.ndarray:
    return _tone(dur_s, amp=float(np.sqrt(2.0) * 10.0 ** (dbfs / 20.0)))


def _write(path: Path, data: np.ndarray, subtype: str = "PCM_24") -> Path:
    sf.write(str(path), data, MASTER_SR, subtype=subtype)
    return path


def _record_for(rec_id: str, wav: Path) -> dict:
    """Analysis record carrying the contract payload for ``wav``."""
    return {
        "recording_id": rec_id,
        "continuity_version": continuity.CONTINUITY_VERSION,
        "block_rms_dbfs": [round(float(v), 2) for v in continuity.measure_master_blocks(wav)],
    }


def _track(
    i: int,
    rec_id: str,
    path: Path,
    *,
    cue_in_s: float,
    cue_out_s: float,
    start_s: float,
    stretch: float = 1.0,
    gain_db: float = 0.0,
) -> dict:
    seg_src = int((cue_out_s - cue_in_s) * MASTER_SR)
    return {
        "index": i,
        "recording_id": rec_id,
        "path": str(path),
        "cue_in_samples": int(cue_in_s * MASTER_SR),
        "cue_out_samples": int(cue_out_s * MASTER_SR),
        "segment_source_samples": seg_src,
        "segment_master_samples": round(seg_src / stretch),
        "tempo_stretch": stretch,
        "gain_db": gain_db,
        "master_start_sample": int(start_s * MASTER_SR),
    }


def _transition(i: int, ttype: str, *, end_s: float, dur_s: float) -> dict:
    end = int(end_s * MASTER_SR)
    return {
        "index": i,
        "type": ttype,
        "duration_samples": int(dur_s * MASTER_SR),
        "overlap_start_sample": end - int(dur_s * MASTER_SR),
        "overlap_end_sample": end,
    }


def _plan(tracks: list[dict], transitions: list[dict]) -> dict:
    total = max(t["master_start_sample"] + t["segment_master_samples"] for t in tracks)
    return {
        "plan_version": 1,
        "set_name": "adversarial",
        "master_sr": MASTER_SR,
        "allowed_silent_gaps_s": [],
        "tracks": tracks,
        "transitions": transitions,
        "timeline": {"total_samples": total, "total_duration_s": total / MASTER_SR},
    }


def _predict(plan: dict, by_id: dict) -> dict:
    sim = continuity.find_master_violations(plan, by_id)
    return {
        "predictor_pass": not sim["violations"],
        "predicted_violations": sim["violations"],
        "silent_blocks_predicted": sim.get("silent_blocks_predicted"),
    }


def _verify(plan: dict, master_wav: Path) -> dict:
    data, file_sr = sf.read(str(master_wav), dtype="float32", always_2d=True)
    sil = verify._silence_check(data, plan, file_sr)
    return {
        "verifier_pass": sil["silence_ok"],
        "digital": {
            "ok": sil["digital_silence"]["ok"],
            "max_run_s": sil["digital_silence"]["max_run_s"],
            "violating_runs_s": sil["digital_silence"]["violating_runs_s"],
        },
        "low_level": {
            "ok": sil["low_level"]["ok"],
            "max_run_s": sil["low_level"]["max_run_s"],
            "violating_runs_s": sil["low_level"]["violating_runs_s"],
        },
    }


def _classify(predictor_pass: bool, verifier_pass: bool) -> str:
    if predictor_pass and verifier_pass:
        return "agreement_clean"
    if not predictor_pass and not verifier_pass:
        return "agreement_violation"
    if predictor_pass and not verifier_pass:
        return "false_negative"
    return "false_positive"


def run_cases(workdir: Path) -> list[dict]:
    workdir = Path(workdir)
    results = []
    for name, builder in CASE_BUILDERS.items():
        if name == "time_stretch" and not shutil.which("ffmpeg"):
            results.append(
                {
                    "case": "time_stretch",
                    "category": "time_stretch",
                    "harness": "render_plan+rubberband",
                    "note": "rubberband stretch requires ffmpeg — unmeasured here",
                    "outcome": "skipped_no_ffmpeg",
                }
            )
            continue
        wd = workdir / name
        wd.mkdir(parents=True, exist_ok=True)
        results.append(builder(wd))
    return results


def _phase_cancellation_constructed(wd: Path) -> dict:
    """Two loud contributors sum to digital silence — the max-of-contributors
    bound cannot see phase. ``render_plan``'s equal-power fades cannot sustain
    cancellation, so the master is constructed to model the bound failure."""
    a = _write(wd / "a.wav", _tone(20.0))
    b = _write(wd / "b.wav", -1.0 * _tone(20.0))  # same wave, inverted
    ta = _track(0, "rec-a", a, cue_in_s=0.0, cue_out_s=20.0, start_s=0.0)
    tb = _track(1, "rec-b", b, cue_in_s=0.0, cue_out_s=20.0, start_s=16.0)
    tr = [_transition(0, "crossfade", end_s=20.0, dur_s=4.0)]
    plan = _plan([ta, tb], tr)
    by_id = {r["recording_id"]: r for r in (_record_for("rec-a", a), _record_for("rec-b", b))}
    # Construct the master the bound assumes is impossible: sustained
    # cancellation over the whole overlap (full-amplitude correlated sources).
    master = _tone(36.0)
    master[int(16.0 * MASTER_SR) : int(20.0 * MASTER_SR)] = 0.0
    wav = _write(wd / "master.wav", master)
    p = _predict(plan, by_id)
    v = _verify(plan, wav)
    return {
        "case": "phase_cancellation_constructed",
        "category": "phase_cancellation",
        "harness": "constructed_master",
        "note": (
            "max-of-contributors is phase-blind: two loud sources cancelling "
            "to digital zeros is invisible to the predictor; the verifier sees "
            "the 4 s hole. render_plan's equal-power fades cannot sustain "
            "cancellation, so the bound failure is shown on a constructed master."
        ),
        **p,
        **v,
        "outcome": _classify(p["predictor_pass"], v["verifier_pass"]),
    }


def _phase_cancellation_render(wd: Path) -> dict:
    """Real render of an anti-phase pair: the equal-power fade collapses the
    cancellation to a sub-0.5 s dip, so predictor and verifier agree — the
    renderer's own math currently masks the blind spot."""
    a = _write(wd / "a.wav", _tone(20.0))
    b = _write(wd / "b.wav", -1.0 * _tone(20.0))
    ta = _track(0, "rec-a", a, cue_in_s=0.0, cue_out_s=20.0, start_s=0.0)
    tb = _track(1, "rec-b", b, cue_in_s=0.0, cue_out_s=20.0, start_s=16.0)
    plan = _plan([ta, tb], [_transition(0, "crossfade", end_s=20.0, dur_s=4.0)])
    by_id = {r["recording_id"]: r for r in (_record_for("rec-a", a), _record_for("rec-b", b))}
    wav = wd / "master.wav"
    render_plan(plan, wav)
    p = _predict(plan, by_id)
    v = _verify(plan, wav)
    return {
        "case": "phase_cancellation_render",
        "category": "phase_cancellation",
        "harness": "render_plan",
        "note": "anti-phase sources through the real equal-power crossfade",
        **p,
        **v,
        "outcome": _classify(p["predictor_pass"], v["verifier_pass"]),
    }


def _gain_peak_scale_gap(wd: Path) -> dict:
    """The renderer's post-mix peak-protection scale is invisible to the
    predictor: a -55 dBFS stretch the predictor clears lands below -60 dBFS
    once the whole master is scaled for true-peak headroom."""
    loud = _tone(20.0, amp=3.0)  # float WAV keeps >0 dBFS peaks
    quiet = _sine_rms_dbfs(-55.0, 3.0)
    a_data = loud.copy()
    a_data[int(14.0 * MASTER_SR) : int(17.0 * MASTER_SR)] = quiet
    a = _write(wd / "a.wav", a_data, subtype="FLOAT")
    b = _write(wd / "b.wav", _tone(20.0))
    ta = _track(0, "rec-a", a, cue_in_s=0.0, cue_out_s=20.0, start_s=0.0)
    tb = _track(1, "rec-b", b, cue_in_s=0.0, cue_out_s=20.0, start_s=17.0)
    plan = _plan([ta, tb], [_transition(0, "crossfade", end_s=20.0, dur_s=3.0)])
    by_id = {r["recording_id"]: r for r in (_record_for("rec-a", a), _record_for("rec-b", b))}
    wav = wd / "master.wav"
    render_plan(plan, wav)
    p = _predict(plan, by_id)
    v = _verify(plan, wav)
    return {
        "case": "gain_peak_scale_gap",
        "category": "gain",
        "harness": "render_plan",
        "note": (
            "master-wide peak-protection gain is applied after the mix; the "
            "predictor models only per-track gain_db, so a -55 dBFS stretch it "
            "clears measures below -60 dBFS on disk"
        ),
        **p,
        **v,
        "outcome": _classify(p["predictor_pass"], v["verifier_pass"]),
    }


def _fade_quiet_pair(wd: Path) -> dict:
    """Two -58 dBFS sources in an equal-power crossfade: the predictor takes
    max(-58+envA, -58+envB) and sees both below -60 dBFS mid-window, while the
    true summed power stays ~-55 dBFS — a conservative over-flag."""
    a = _write(wd / "a.wav", _sine_rms_dbfs(-58.0, 24.0))
    b = _write(wd / "b.wav", _sine_rms_dbfs(-58.0, 24.0))
    ta = _track(0, "rec-a", a, cue_in_s=0.0, cue_out_s=24.0, start_s=0.0)
    tb = _track(1, "rec-b", b, cue_in_s=0.0, cue_out_s=24.0, start_s=12.0)
    plan = _plan([ta, tb], [_transition(0, "crossfade", end_s=24.0, dur_s=12.0)])
    by_id = {r["recording_id"]: r for r in (_record_for("rec-a", a), _record_for("rec-b", b))}
    wav = wd / "master.wav"
    render_plan(plan, wav)
    p = _predict(plan, by_id)
    v = _verify(plan, wav)
    return {
        "case": "fade_crossfade_quiet_pair",
        "category": "fade",
        "harness": "render_plan",
        "note": (
            "equal-power sum of two -58 dBFS sources stays ~-55 dBFS; "
            "max-of-contributors dips below -60 dBFS where both envelopes are "
            "low — predictor over-flags"
        ),
        **p,
        **v,
        "outcome": _classify(p["predictor_pass"], v["verifier_pass"]),
    }


def _time_stretch(wd: Path) -> dict:
    """A source hole under tempo stretch: the predictor maps blocks through
    the planned factor; rubberband's real output differs at the edges."""
    b_data = _tone(20.0)
    b_data[int(8.0 * MASTER_SR) : int(8.5 * MASTER_SR)] = 0.0
    a = _write(wd / "a.wav", _tone(20.0))
    b = _write(wd / "b.wav", b_data)
    stretch = 1.05
    ta = _track(0, "rec-a", a, cue_in_s=0.0, cue_out_s=20.0, start_s=0.0)
    tb = _track(1, "rec-b", b, cue_in_s=0.0, cue_out_s=20.0, start_s=16.0, stretch=stretch)
    plan = _plan([ta, tb], [_transition(0, "crossfade", end_s=20.0, dur_s=4.0)])
    by_id = {r["recording_id"]: r for r in (_record_for("rec-a", a), _record_for("rec-b", b))}
    wav = wd / "master.wav"
    render_plan(plan, wav)
    p = _predict(plan, by_id)
    v = _verify(plan, wav)
    return {
        "case": "time_stretch",
        "category": "time_stretch",
        "harness": "render_plan+rubberband",
        "note": "0.5 s source hole at stretch 1.05 lands ~0.48 s on the master",
        **p,
        **v,
        "outcome": _classify(p["predictor_pass"], v["verifier_pass"]),
    }


def _block_boundary(wd: Path) -> dict:
    """A 10-block source hole mapped onto a master grid offset by half a
    block: the verifier's block-RMS dilutes the straddled edges to a 9-block
    run while the predictor samples at block centers and counts 10."""
    a = _write(wd / "a.wav", _tone(20.0))
    b_data = _tone(20.0)
    b_data[int(8.0 * MASTER_SR) : int(8.5 * MASTER_SR)] = 0.0
    b = _write(wd / "b.wav", b_data)
    ta = _track(0, "rec-a", a, cue_in_s=0.0, cue_out_s=20.0, start_s=0.0)
    # B enters 25 ms off the 50 ms block grid -> master blocks straddle the
    # source hole, diluting the two edge blocks above the threshold.
    tb = _track(1, "rec-b", b, cue_in_s=0.0, cue_out_s=20.0, start_s=19.025)
    plan = _plan([ta, tb], [_transition(0, "crossfade", end_s=20.0, dur_s=0.975)])
    by_id = {r["recording_id"]: r for r in (_record_for("rec-a", a), _record_for("rec-b", b))}
    wav = wd / "master.wav"
    render_plan(plan, wav)
    p = _predict(plan, by_id)
    v = _verify(plan, wav)
    return {
        "case": "block_boundary",
        "category": "block_boundary",
        "harness": "render_plan",
        "note": (
            "predictor samples each source block at the master block's center; "
            "the verifier averages true RMS, so half-block straddles shrink "
            "the measured run"
        ),
        **p,
        **v,
        "outcome": _classify(p["predictor_pass"], v["verifier_pass"]),
    }


CASE_BUILDERS = {
    "phase_cancellation_constructed": _phase_cancellation_constructed,
    "phase_cancellation_render": _phase_cancellation_render,
    "gain_peak_scale_gap": _gain_peak_scale_gap,
    "fade_crossfade_quiet_pair": _fade_quiet_pair,
    "time_stretch": _time_stretch,
    "block_boundary": _block_boundary,
}


class TestAdversarialPredictor(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.results = {r["case"]: r for r in run_cases(Path(cls._tmp.name))}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _fixture(self) -> dict:
        self.assertTrue(FIXTURE_PATH.exists(), f"recorded fixtures missing: {FIXTURE_PATH}")
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_all_named_categories_covered(self) -> None:
        cats = {r["category"] for r in self.results.values()}
        for required in (
            "phase_cancellation",
            "gain",
            "fade",
            "time_stretch",
            "block_boundary",
        ):
            self.assertIn(required, cats)

    def test_every_false_negative_is_a_recorded_fixture(self) -> None:
        """C4: predictor-PASS + verifier-FAIL must exist as regression fixtures."""
        recorded = {c["case"] for c in self._fixture()["cases"] if c.get("regression_fixture")}
        live_fn = {n for n, r in self.results.items() if r["outcome"] == "false_negative"}
        self.assertTrue(live_fn, "expected at least one false-negative case")
        unrecorded = live_fn - recorded
        self.assertFalse(unrecorded, f"predictor-PASS/verifier-FAIL not recorded: {unrecorded}")

    def test_outcomes_match_recorded_fixtures(self) -> None:
        """Regression pin: each recorded case still produces its outcome."""
        recorded = {c["case"]: c for c in self._fixture()["cases"]}
        self.assertEqual(set(recorded), set(self.results))
        for name, live in self.results.items():
            if live["outcome"] == "skipped_no_ffmpeg":
                continue  # unmeasured here — the recorded outcome stands
            rec = recorded[name]
            self.assertEqual(
                live["outcome"],
                rec["outcome"],
                f"{name}: recorded {rec['outcome']} but live run gives "
                f"{live['outcome']} — predictor/renderer drifted; re-chart the fixture",
            )
            self.assertEqual(live["predictor_pass"], rec["predictor_pass"], name)
            self.assertEqual(live["verifier_pass"], rec["verifier_pass"], name)


if __name__ == "__main__":
    unittest.main()
