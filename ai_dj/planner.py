"""Deterministic, confidence-aware set planner (D3).

Produces ``plan.json`` — a complete musical decision: track order, per-track
cue points (in **samples** on the 48 kHz master timeline, D9), transition type
per pair, gain, tempo stretch, and the target energy arc.

Determinism contract: identical analysis records + identical parameters produce
a byte-identical plan. No wall-clock, no unseeded RNG, no dict-order hashing —
every choice is an explicit sort/min over stable keys.

Confidence-aware transitions (G2b: never force a beatmatch):

* ``beat_crossfade`` — only when both beat grids are trustworthy
  (``tempo_confidence >= MIN_BEAT_CONF``), BPM differs by <= 6% (stretchable
  inside the rubberband-safe ±6% window), and keys are known and compatible.
* ``crossfade`` — moderate BPM difference (<= 15%) or unknown/marginal key:
  phrase-aligned fade, no tempo change.
* ``echo_out`` — incompatible tempo/key but the outgoing track has a usable
  decay tail: asymmetric fade, incoming track enters clean.
* ``cut`` — nothing safe to overlap: hard splice on a downbeat.

Every transition carries ``confidence`` and machine-readable ``reasons``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ai_dj.timing import MASTER_SR

PLAN_VERSION = 1

# --- thresholds (contract constants, documented not magic) ---
MIN_BEAT_CONF = 0.5  # tempo_confidence required to trust a beat grid
BEATMATCH_BPM_TOL = 0.06  # max relative BPM gap for a beat-aligned crossfade
CROSSFADE_BPM_TOL = 0.15  # max gap where a plain overlap is still musical
MIN_DURATION_S = 60.0  # tracks shorter than this are not set material
MAX_STRETCH = 1.06  # rubberband-safe tempo window
XFADE_S = 6.0  # plain crossfade length
ECHO_TAIL_S = 10.0  # echo-out fade region
MIN_BEAT_XFADE_S = 4.0
MAX_BEAT_XFADE_S = 16.0
BEAT_XFADE_BARS = 4  # 4 bars (16 beats) overlap for a beatmix
EDGE_GUARD_S = 2.0  # stay this far from file edges when guessing cues

_SEMITONES = {
    "C": 0,
    "C#": 1,
    "D": 2,
    "D#": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "G": 7,
    "G#": 8,
    "A": 9,
    "A#": 10,
    "B": 11,
}


def key_distance(key_a: str, key_b: str) -> int | None:
    """Chromatic distance 0-6 between key tonics; None when either is UNKNOWN.

    Relative major/minor pairs (minor tonic + 3 semitones = major tonic, e.g.
    A minor / C major) count as 1 — they share a key signature. Parallel
    major/minor on the same tonic count as 2 (mood shift despite same root).
    """
    try:
        pa, ma = key_a.split()
        pb, mb = key_b.split()
        ca, cb = _SEMITONES[pa], _SEMITONES[pb]
        d = abs(ca - cb)
        d = min(d, 12 - d)
        if ma == mb:
            return d
        minor_pc, major_pc = (ca, cb) if ma == "minor" else (cb, ca)
        if (minor_pc + 3) % 12 == major_pc:
            return 1
        if ca == cb:
            return 2
        return min(6, d + 1)
    except (ValueError, KeyError):
        return None


def energy_arc(n: int, lo: float, hi: float) -> list[float]:
    """Target energy per slot: smooth rise to ~65% of the set then release.

    Deterministic shape: sin() bump over slots, scaled into [lo, hi].
    """
    if n <= 0:
        return []
    if n == 1:
        return [round((lo + hi) / 2.0, 4)]
    arc = []
    for i in range(n):
        x = i / (n - 1)
        shape = math.sin(math.pi * min(1.0, x * 1.25)) ** 0.8
        arc.append(round(lo + (hi - lo) * shape, 4))
    return arc


def _eligible(records: list[dict]) -> list[dict]:
    """Analysis records usable for planning (ok, long enough, any beat data)."""
    ok = []
    for r in records:
        if r.get("status") != "ok":
            continue
        if (r.get("duration_s") or 0) < MIN_DURATION_S:
            continue
        ok.append(r)
    return ok


def _dedup_works(records: list[dict]) -> list[dict]:
    """Best recording per work_id (confidence, duration, id — stable order)."""
    best: dict[str, dict] = {}
    for r in sorted(
        records,
        key=lambda r: (
            -(r.get("tempo_confidence") or 0),
            -(r.get("duration_s") or 0),
            r["recording_id"],
        ),
    ):
        best.setdefault(r["work_id"], r)
    return sorted(best.values(), key=lambda r: r["recording_id"])


def _cue_points(rec: dict) -> tuple[float, float]:
    """cue_in/cue_out seconds: intro end on a downbeat, outro start on a downbeat."""
    dur = rec["duration_s"]
    intro_end = float(rec.get("intro_end_s") or 0.0)
    outro_start = float(rec.get("outro_start_s") or dur)
    downbeats = rec.get("downbeat_times_s") or []
    beats = rec.get("beat_times_s") or []

    cue_in = 0.0
    if downbeats:
        before = [b for b in downbeats if b <= intro_end]
        cue_in = before[-1] if before else downbeats[0]
    cue_in = max(0.0, min(cue_in, dur - 2 * EDGE_GUARD_S))

    cue_out = dur - EDGE_GUARD_S
    if downbeats:
        after = [b for b in downbeats if b >= outro_start]
        if after:
            cue_out = after[0]
    elif beats:
        after = [b for b in beats if b >= outro_start]
        if after:
            cue_out = after[0]
    cue_out = max(cue_in + MIN_DURATION_S * 0.5, min(cue_out, dur - 0.5))
    return round(cue_in, 5), round(cue_out, 5)


def _choose_transition(a: dict, b: dict) -> dict:
    """Pick the safest transition for pair (a -> b); never force a beatmatch."""
    bpm_a = a.get("bpm_corrected") or 0.0
    bpm_b = b.get("bpm_corrected") or 0.0
    conf_a = a.get("tempo_confidence") or 0.0
    conf_b = b.get("tempo_confidence") or 0.0
    reasons: list[str] = []

    bpm_rel = abs(bpm_b - bpm_a) / bpm_a if bpm_a > 0 and bpm_b > 0 else 1.0
    beats_ok = conf_a >= MIN_BEAT_CONF and conf_b >= MIN_BEAT_CONF and bpm_rel < 1.0
    kdist = key_distance(a.get("key", "UNKNOWN"), b.get("key", "UNKNOWN"))
    if kdist is None:
        reasons.append("key_unknown")
        key_ok = False
    elif kdist <= 1:
        reasons.append("key_compatible")
        key_ok = True
    elif kdist <= 3:
        reasons.append(f"key_marginal_{kdist}st")
        key_ok = False
    else:
        reasons.append(f"key_clash_{kdist}st")
        key_ok = False
    if not beats_ok:
        reasons.append("low_beat_confidence")
    reasons.append(f"bpm_diff_{bpm_rel * 100:.1f}%")

    dur_a = a["duration_s"]
    tail_a = dur_a - (a.get("outro_start_s") or dur_a)

    if beats_ok and bpm_rel <= BEATMATCH_BPM_TOL and key_ok:
        ibi_a = a.get("median_ibi_s") or 60.0 / bpm_a
        dur_s = min(
            MAX_BEAT_XFADE_S,
            max(MIN_BEAT_XFADE_S, BEAT_XFADE_BARS * 4 * ibi_a),
        )
        conf = min(conf_a, conf_b) * (1.0 - 0.3 * bpm_rel / BEATMATCH_BPM_TOL)
        reasons.insert(0, "bpm_matched")
        return {
            "type": "beat_crossfade",
            "duration_s": round(dur_s, 3),
            "confidence": round(conf, 3),
            "reasons": reasons,
            "target_bpm": round(bpm_a, 3),
            "stretch_factor": round(bpm_a / bpm_b, 6),
        }
    if beats_ok and bpm_rel <= CROSSFADE_BPM_TOL:
        conf = min(conf_a, conf_b) * 0.7
        return {
            "type": "crossfade",
            "duration_s": XFADE_S,
            "confidence": round(conf, 3),
            "reasons": reasons,
            "target_bpm": None,
            "stretch_factor": 1.0,
        }
    if tail_a >= ECHO_TAIL_S * 0.5:
        reasons.append("echo_tail_available")
        return {
            "type": "echo_out",
            "duration_s": ECHO_TAIL_S,
            "confidence": 0.4,
            "reasons": reasons,
            "target_bpm": None,
            "stretch_factor": 1.0,
        }
    return {
        "type": "cut",
        "duration_s": 0.0,
        "confidence": 0.4,
        "reasons": reasons,
        "target_bpm": None,
        "stretch_factor": 1.0,
    }


def plan_set(
    records: list[dict],
    n_tracks: int = 6,
    set_name: str = "mini",
    seed: int = 0,
    energy_target: float | None = None,
) -> dict:
    """Build a deterministic plan for an n-track mini-set.

    ``seed`` is part of the contract (stored in the plan); P0 uses pure greedy
    selection — no RNG calls — so the seed currently only documents intent.
    """
    pool = _dedup_works(_eligible(records))
    if len(pool) < 2:
        raise ValueError(f"only {len(pool)} eligible tracks after dedup/eligibility")
    n = max(2, min(n_tracks, len(pool)))

    energies = [r.get("energy_mean") or 0.0 for r in pool]
    e_lo, e_hi = min(energies), max(energies)
    if energy_target is not None:
        mid = (e_lo + e_hi) / 2.0
        e_lo, e_hi = mid - energy_target / 2.0, mid + energy_target / 2.0
    arc = energy_arc(n, e_lo, e_hi)

    median_rms = sorted(r.get("rms_db") or -30.0 for r in pool)[len(pool) // 2]

    chosen: list[dict] = []
    remaining = list(pool)

    def cost(cand: dict, slot: int, prev: dict | None) -> tuple:
        c_energy = abs((cand.get("energy_mean") or 0.0) - arc[slot])
        if prev is None:
            return (round(c_energy, 6), cand["recording_id"])
        bpm_p = prev.get("bpm_corrected") or 0.0
        bpm_c = cand.get("bpm_corrected") or 0.0
        rel = abs(bpm_c - bpm_p) / bpm_p if bpm_p > 0 and bpm_c > 0 else 1.0
        jump = max(0.0, rel - BEATMATCH_BPM_TOL)
        kd = key_distance(prev.get("key", "UNKNOWN"), cand.get("key", "UNKNOWN"))
        clash = 1.0 if kd is None else (0.0 if kd <= 1 else kd / 6.0)
        return (round(c_energy + 0.8 * jump + 0.25 * clash, 6), cand["recording_id"])

    for slot in range(n):
        prev = chosen[-1] if chosen else None
        best = min(remaining, key=lambda c: cost(c, slot, prev))
        chosen.append(best)
        remaining.remove(best)

    tracks: list[dict] = []
    for i, rec in enumerate(chosen):
        cue_in, cue_out = _cue_points(rec)
        rms = rec.get("rms_db") or median_rms
        gain = max(-12.0, min(12.0, median_rms - rms))
        seg_src = round((cue_out - cue_in) * MASTER_SR)
        beats_in_seg = [
            round(b - cue_in, 5) for b in (rec.get("beat_times_s") or []) if cue_in <= b < cue_out
        ]
        tracks.append(
            {
                "index": i,
                "recording_id": rec["recording_id"],
                "work_id": rec["work_id"],
                "title": rec["title"],
                "path": rec["path"],
                "bpm": rec.get("bpm_corrected"),
                "bpm_raw": rec.get("bpm_raw"),
                "key": rec.get("key", "UNKNOWN"),
                "tempo_confidence": rec.get("tempo_confidence", 0.0),
                "median_ibi_s": rec.get("median_ibi_s"),
                "cue_in_s": cue_in,
                "cue_out_s": cue_out,
                "cue_in_samples": round(cue_in * MASTER_SR),
                "cue_out_samples": round(cue_out * MASTER_SR),
                "segment_source_samples": seg_src,
                "tempo_stretch": 1.0,
                "gain_db": round(gain, 2),
                "energy_mean": rec.get("energy_mean", 0.0),
                "beats_in_segment_s": beats_in_seg,
            }
        )

    transitions: list[dict] = []
    for i in range(n - 1):
        a_rec, b_rec = chosen[i], chosen[i + 1]
        dec = _choose_transition(a_rec, b_rec)
        if dec["type"] == "beat_crossfade":
            tracks[i + 1]["tempo_stretch"] = dec["stretch_factor"]
        transitions.append(
            {
                "index": i,
                "from_track": i,
                "to_track": i + 1,
                "type": dec["type"],
                "duration_s": dec["duration_s"],
                "duration_samples": round(dec["duration_s"] * MASTER_SR),
                "confidence": dec["confidence"],
                "reasons": dec["reasons"],
                "target_bpm": dec["target_bpm"],
            }
        )

    # Master-timeline layout (sample indices, D9): track segments overlapped by
    # their incoming transition. Stretch rescales the *used* segment length.
    cursor = 0
    for i, t in enumerate(tracks):
        stretch = t["tempo_stretch"]
        t["segment_master_samples"] = round(t["segment_source_samples"] / stretch)
        overlap = transitions[i - 1]["duration_samples"] if i > 0 else 0
        t["master_start_sample"] = cursor - overlap
        cursor = t["master_start_sample"] + t["segment_master_samples"]
    for tr in transitions:
        end = (
            tracks[tr["from_track"]]["master_start_sample"]
            + tracks[tr["from_track"]]["segment_master_samples"]
        )
        tr["overlap_start_sample"] = end - tr["duration_samples"]
        tr["overlap_end_sample"] = end

    return {
        "plan_version": PLAN_VERSION,
        "set_name": set_name,
        "seed": seed,
        "generated_by": "ai_dj.planner",
        "master_sr": MASTER_SR,
        "constraints": {
            "min_beat_conf": MIN_BEAT_CONF,
            "beatmatch_bpm_tol": BEATMATCH_BPM_TOL,
            "crossfade_bpm_tol": CROSSFADE_BPM_TOL,
            "max_stretch": MAX_STRETCH,
            "min_duration_s": MIN_DURATION_S,
        },
        "energy_arc": arc,
        "track_count": n,
        "tracks": tracks,
        "transitions": transitions,
        "timeline": {
            "total_samples": cursor,
            "total_duration_s": round(cursor / MASTER_SR, 3),
        },
    }


def write_plan(plan: dict, out_path: str | Path) -> Path:
    """Serialize a plan byte-identically: fixed key order, rounded floats."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def load_plan(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
