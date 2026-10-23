"""Unified Audio Continuity Contract (DJ-P2-CONTINUITY-001).

Why this exists: the analyzer's D10 eligibility rule and the verifier's D12
gate measure *different* signals at *different* thresholds. D10 reads a mono
22.05 kHz mixdown at -100 dBFS; D12 re-reads the rendered master on disk —
all-channel 50 ms RMS blocks at the master sample rate — and fails on a run
of >= 0.5 s at <= -90 dBFS or >= 2.0 s at <= -60 dBFS. A track can pass D10
and still place an unavoidable silent run inside its played segment: the
plan is born broken (GPT HOLD verdict, 2026-10-22).

The contract: the analyzer additionally measures silence on the **same
signal class the verifier measures** — every channel at MASTER_SR, decoded
by ``render.load_audio_48k`` (exactly what the renderer feeds the mix) and
reduced with the same block math as ``verify._block_rms_dbfs`` — and stores
the per-block levels plus run intervals in the analysis payload. The planner
consumes them as a *hard constraint*: a candidate whose played segment must
violate D12 — after the best legitimate cue adjustment and after overlap
coverage by a sounding neighbour — is removed from the search entirely,
never merely penalised. If no feasible plan exists the planner raises
``PlanInfeasible`` instead of emitting a doomed plan.

D12 keeps veto authority: everything here is a *predictor*. The prediction
is conservative by construction — a master block counts as audible only when
one contributing track provably exceeds the threshold (independent signals
sum in power to at least the largest contributor; cancellation across
unrelated tracks is theoretically possible but negligible for program
material). Thresholds are mirrored constants, never edited to soften D12;
equivalence between ``block_rms_dbfs`` and ``verify._block_rms_dbfs`` is
pinned by tests/test_continuity.py.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

from ai_dj import render as _render
from ai_dj import verify as _verify
from ai_dj.analysis.beatgrid import BEAT_GRID_VERSION
from ai_dj.timing import MASTER_SR

# --- D12 contract constants (mirrored from verify.py — never softened here) ---
CONTINUITY_VERSION = 1
BLOCK_S = 0.05  # == verify.SILENCE_BLOCK_S
DIGITAL_DBFS = -90.0  # == verify.DIGITAL_SILENCE_DBFS
DIGITAL_MIN_RUN_S = 0.5  # == verify.DIGITAL_SILENCE_MIN_RUN_S
LOW_LEVEL_DBFS = -60.0  # == verify.LOW_LEVEL_DBFS
LOW_LEVEL_MIN_RUN_S = 2.0  # == verify.LOW_LEVEL_MIN_RUN_S
OPENER_WINDOW_S = 30.0  # GPT opener audibility gate: first 30 s of play segment
_DB_FLOOR = 1e-12  # == verify._DB_FLOOR — keeps log10 finite on digital zeros

# Cue geometry shared with the planner (identical values; the planner's
# _cue_points delegates to default_cue_points below).
EDGE_GUARD_S = 2.0  # stay this far from file edges when guessing cues
MIN_SEGMENT_S = 30.0  # == planner.MIN_DURATION_S * 0.5 — shortest played segment
MIN_STRETCH = 1.0 / 1.06  # == 1/planner.MAX_STRETCH — worst-case run lengthening
COVER_STRETCH = 1.06  # == planner.MAX_STRETCH — optimistic coverage reach
# Block-count vs declared-duration drift bound (P1): the analyzer counts
# blocks on the 48 kHz render-path decode while ``duration_s`` comes from the
# 22.05 kHz analysis pass — a few blocks of decode/resample edge slack is
# legitimate; a one-block or truncated map is not.
MAX_BLOCK_COUNT_DRIFT = 4


def block_rms_dbfs(data: np.ndarray, sr: int) -> np.ndarray:
    """50 ms all-channel RMS in dBFS — same math as verify._block_rms_dbfs."""
    blk = max(1, int(BLOCK_S * sr))
    n = len(data) // blk
    if n == 0:
        return np.zeros(0)
    frames = data[: n * blk].reshape(n, blk, data.shape[1])
    ss = np.einsum("nbc,nbc->n", frames, frames)
    ms = ss / (blk * data.shape[1])
    return 10.0 * np.log10(np.maximum(ms, _DB_FLOOR * _DB_FLOOR))


def silent_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True in a boolean mask, as [start_blk, end_blk) pairs."""
    edges = np.flatnonzero(np.diff(np.concatenate(([False], mask, [False])).astype(np.int8)))
    return [(int(s), int(e)) for s, e in edges.reshape(-1, 2)]


def _intervals(mask: np.ndarray) -> list[list[float]]:
    return [[round(s * BLOCK_S, 3), round(e * BLOCK_S, 3)] for s, e in silent_runs(mask)]


def measure_master_blocks(path: str | Path) -> np.ndarray:
    """Per-block dBFS of a track decoded exactly as the renderer decodes it.

    ``load_audio_48k`` is the renderer's own decode path: same channel policy
    (mono duplicated, >2 folded to stereo), same resample target — so the
    measured blocks describe the signal that actually lands in the master.
    """
    return block_rms_dbfs(_render.load_audio_48k(path), MASTER_SR)


def has_continuity_payload(rec: dict) -> bool:
    """True when ``rec`` carries the COMPLETE current contract payload.

    P1 (DJ-P2-MINISET-G2B-001): a bare ``continuity_version`` plus a non-empty
    block list is not enough — a one-block map would pass that test while
    describing almost nothing. A complete payload must also declare the block
    grid (``block_ms`` == 50), the signal class (``channels`` ==
    "all_channels_rms", ``continuity_sr`` == MASTER_SR), hold only finite
    per-block levels, cover the stored audio duration within decode slack,
    and carry the silence-interval map. This is the same completeness test
    ``assess_segment``/the master simulation use to mark a record
    ``unmeasured``/``unknown``. Records failing it are *missing data*, not
    audio-infeasible tracks — the CLI ``plan`` input gate reports them as
    ANALYSIS_REQUIRED / CORPUS_UNMEASURED, never PLAN_INFEASIBLE.
    """
    # DJ-P2-BEAT-GRID-001: the contract also consumes the beat/downbeat grids
    # (``default_cue_points`` and the assessments below), so a payload whose
    # ``beat_grid_version`` predates the refined grid is equally stale — a v3
    # analysis row is unmeasured under v4 and is never silently mixed into a
    # current plan.
    versions_ok = (
        rec.get("continuity_version") == CONTINUITY_VERSION
        and rec.get("beat_grid_version") == BEAT_GRID_VERSION
    )
    if not versions_ok:
        return False
    blocks = rec.get("block_rms_dbfs")
    if not isinstance(blocks, (list, tuple, np.ndarray)) or len(blocks) == 0:
        return False
    si = rec.get("silence_intervals")
    grid_ok = (
        rec.get("block_ms") == int(BLOCK_S * 1000)
        and rec.get("continuity_sr") == MASTER_SR
        and rec.get("channels") == "all_channels_rms"
        and isinstance(si, dict)
        and "digital_minus90" in si
        and "low_level_minus60" in si
    )
    if not grid_ok:
        return False
    try:
        arr = np.asarray(blocks, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        return False
    dur = rec.get("duration_s")
    drift_ok = True
    if isinstance(dur, (int, float)) and np.isfinite(float(dur)) and float(dur) > 0:
        expected = int(float(dur) / BLOCK_S)
        drift_ok = abs(int(arr.size) - expected) <= MAX_BLOCK_COUNT_DRIFT
    return drift_ok


def default_cue_points(rec: dict) -> tuple[float, float]:
    """cue_in/cue_out seconds: intro end on a downbeat, outro start on a downbeat.

    The single cue rule: the analyzer evaluates the stored ``opener`` window
    against it and ``planner._cue_points`` delegates to it, so a stored
    opener measurement always refers to the planner's default play segment.
    """
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
    cue_out = max(cue_in + MIN_SEGMENT_S, min(cue_out, dur - 0.5))
    return round(cue_in, 5), round(cue_out, 5)


def continuity_fields(block_db: np.ndarray, rec_view: dict) -> dict:
    """Payload fields for one analyzed track — every value computed from audio.

    ``rec_view`` carries the analyzer-side fields the cue rule needs
    (duration/intro/outro/downbeats/beats). ``silence_intervals`` holds *all*
    maximal silent runs (not only violating-length ones): the planner needs
    run boundaries to reason about stretch, gain and overlap coverage.
    """
    duration_s = float(rec_view["duration_s"])
    cue_in, _cue_out = default_cue_points(rec_view)
    intervals = {
        "digital_minus90": _intervals(block_db <= DIGITAL_DBFS),
        "low_level_minus60": _intervals(block_db <= LOW_LEVEL_DBFS),
    }
    # Opener audibility (unity gain, unstretched — the stored value is the
    # track-level measurement; the planner re-checks the exact gate with the
    # real gain/coverage when it commits to an opener).
    w1 = min(cue_in + OPENER_WINDOW_S, duration_s)
    b0 = max(0, math.floor(cue_in / BLOCK_S))
    b1 = min(block_db.size, math.ceil(w1 / BLOCK_S))
    digital = block_db[b0:b1] <= DIGITAL_DBFS
    low = block_db[b0:b1] <= LOW_LEVEL_DBFS
    d_runs = silent_runs(digital)
    l_runs = silent_runs(low)
    max_d = max(((e - s) for s, e in d_runs), default=0) * BLOCK_S
    max_l = max(((e - s) for s, e in l_runs), default=0) * BLOCK_S
    ok = all((e - s) * BLOCK_S < DIGITAL_MIN_RUN_S for s, e in d_runs) and all(
        (e - s) * BLOCK_S < LOW_LEVEL_MIN_RUN_S for s, e in l_runs
    )
    return {
        "continuity_version": CONTINUITY_VERSION,
        "block_ms": int(BLOCK_S * 1000),
        "channels": "all_channels_rms",
        "continuity_sr": MASTER_SR,
        "silence_intervals": intervals,
        "opener": {
            "window_s": int(OPENER_WINDOW_S),
            "cue_in_s": round(cue_in, 5),
            "max_digital_run_s": round(float(max_d), 3),
            "max_low_level_run_s": round(float(max_l), 3),
            "audibility_ok": bool(ok),
        },
        "block_rms_dbfs": [round(float(v), 2) for v in block_db.tolist()],
    }


def coverage_span_s(dec: dict | None) -> float:
    """Master-time span at a segment edge where a partner can mask silence.

    The *incoming* track's head is overlapped by the outgoing tail for
    ``ov_end - entry``; the *outgoing* track's tail is masked by the incoming
    head over the same span — one number covers both directions:

    * crossfade/beat_crossfade: the incoming segment is present from its
      first sample inside the window (beat shifts only shorten it — the
      duration is the optimistic bound);
    * echo_out: the incoming track enters only over the last
      ``ECHO_IN_FRACTION`` of the window;
    * cut: a 10 ms de-click merge — effectively no coverage.
    """
    if dec is None:
        return 0.0
    ttype, dur = dec["type"], dec["duration_s"]
    if ttype in ("crossfade", "beat_crossfade"):
        return float(dur)
    if ttype == "echo_out":
        return float(dur) * _render.ECHO_IN_FRACTION
    return 0.0


def _n_min(min_run_s: float) -> int:
    """Conservative source-domain block count for a violating master run.

    A run of n source blocks spans ``n * BLOCK_S / stretch`` on the master;
    the smallest possible stretch (1/MAX_STRETCH) makes it longest, so the
    bound uses MIN_STRETCH — near the boundary the predictor may over-flag
    (safe direction: verify.py remains the gate).
    """
    return math.ceil(min_run_s * MIN_STRETCH / BLOCK_S - 1e-9)


def _segment_blocks(block_db: np.ndarray, cue_in: float, cue_out: float) -> tuple[np.ndarray, int]:
    """Blocks overlapping the played segment; boundary blocks included."""
    b0 = max(0, math.floor(cue_in / BLOCK_S))
    b1 = min(block_db.size, math.ceil(cue_out / BLOCK_S))
    return block_db[b0:b1], b0


def _violating_runs(
    block_db: np.ndarray, cue_in: float, cue_out: float, gain_db: float
) -> list[dict]:
    """D12-tier violating runs inside the played segment (source seconds).

    Gain shifts the source level onto the master: a source block at
    ``db + gain_db <= threshold`` is silent in the master.
    """
    seg, b0 = _segment_blocks(block_db, cue_in, cue_out)
    runs: list[dict] = []
    for name, thr, mn in (
        ("digital_minus90", DIGITAL_DBFS, DIGITAL_MIN_RUN_S),
        ("low_level_minus60", LOW_LEVEL_DBFS, LOW_LEVEL_MIN_RUN_S),
    ):
        for s, e in silent_runs(seg + gain_db <= thr):
            if (e - s) >= _n_min(mn):
                runs.append(
                    {
                        "tier": name,
                        "range_s": [round((b0 + s) * BLOCK_S, 3), round((b0 + e) * BLOCK_S, 3)],
                    }
                )
    runs.sort(key=lambda v: (v["range_s"][0], v["tier"]))
    return runs


def _subtract(rng: tuple[float, float], zone: tuple[float, float]) -> list[tuple[float, float]]:
    a, b = rng
    za, zb = zone
    out = []
    if za > a:
        out.append((a, min(b, za)))
    if zb < b:
        out.append((max(a, zb), b))
    return [(x, y) for x, y in out if y - x > 1e-9]


def _uncovered_runs(
    runs: list[dict], cue_in: float, cue_out: float, head_cov_s: float, tail_cov_s: float
) -> list[dict]:
    """Portions of violating runs outside coverable zones (still fatal)."""
    zones = [(cue_in, cue_in + head_cov_s), (cue_out - tail_cov_s, cue_out)]
    fatal: list[dict] = []
    for v in runs:
        pieces = [tuple(v["range_s"])]
        for zone in zones:
            pieces = [p for piece in pieces for p in _subtract(piece, zone)]
        n_min = _n_min(DIGITAL_MIN_RUN_S if v["tier"] == "digital_minus90" else LOW_LEVEL_MIN_RUN_S)
        for a, b in pieces:
            if (b - a) >= n_min * BLOCK_S - 1e-9:
                fatal.append(
                    {
                        "tier": v["tier"],
                        "range_s": v["range_s"],
                        "uncovered_s": [round(a, 3), round(b, 3)],
                        "reason": "uncovered_silence_run",
                    }
                )
    return fatal


def assess_segment(
    rec: dict,
    head_cov_s: float = 0.0,
    tail_cov_s: float = 0.0,
    gain_db: float = 0.0,
) -> dict:
    """Best cue for ``rec`` under the D12 hard constraint, or an infeasible verdict.

    ``head_cov_s``/``tail_cov_s`` are *source-domain* seconds at the segment
    edges where a transition partner could legitimately mask silence
    (callers scale master-time coverage by COVER_STRETCH). Only runs lying
    wholly outside those zones force a cue trim; runs inside them are
    optimistically treated as coverable — the post-order master simulation
    (``find_master_violations``) makes the final exact check.

    Trimming happens only at downbeat boundaries and never inside the run —
    cutting at the edge of a dead stretch is a legitimate cue choice, not a
    mid-lyric splice and not injected audio.
    """
    cue_in, cue_out = default_cue_points(rec)
    if not has_continuity_payload(rec):
        return {
            "cue_in": cue_in,
            "cue_out": cue_out,
            "status": "unmeasured",
            "reason": "no_continuity_data",
            "violations": [],
            "fatal": [],
        }
    blocks = np.asarray(rec["block_rms_dbfs"], dtype=np.float64)
    default = (cue_in, cue_out)
    downbeats = rec.get("downbeat_times_s") or []
    runs: list[dict] = []
    fatal: list[dict] = []
    for _attempt in range(64):
        runs = _violating_runs(blocks, cue_in, cue_out, gain_db)
        fatal = _uncovered_runs(runs, cue_in, cue_out, head_cov_s, tail_cov_s)
        if not fatal:
            return {
                "cue_in": round(cue_in, 5),
                "cue_out": round(cue_out, 5),
                "status": "ok" if (cue_in, cue_out) == default else "adjusted",
                # the only force that moves a cue here is an uncovered run —
                # report the reason, not a bare "ok"
                "reason": "ok" if (cue_in, cue_out) == default else "uncovered_silence_run",
                "violations": runs,
                "fatal": [],
            }
        a, b = fatal[0]["uncovered_s"]
        options: list[tuple[float, float, int]] = []
        prev_db = [d for d in downbeats if d <= a - BLOCK_S]
        if prev_db and prev_db[-1] >= cue_in + MIN_SEGMENT_S:
            options.append((cue_in, prev_db[-1], 0))
        next_db = [d for d in downbeats if d >= b + BLOCK_S]
        if next_db and next_db[0] <= cue_out - MIN_SEGMENT_S:
            options.append((next_db[0], cue_out, 1))
        if not options:
            reason = "no_valid_cue" if not downbeats else "uncoverable_silence"
            return {
                "cue_in": round(cue_in, 5),
                "cue_out": round(cue_out, 5),
                "status": "infeasible",
                "reason": reason,
                "violations": runs,
                "fatal": fatal,
            }
        # Prefer the option keeping the longer segment; ties keep the head
        # (trim the tail) — a late start loses the recognisable opening.
        options.sort(key=lambda o: (-(o[1] - o[0]), o[2]))
        cue_in, cue_out = options[0][0], options[0][1]
    return {
        "cue_in": round(cue_in, 5),
        "cue_out": round(cue_out, 5),
        "status": "infeasible",
        "reason": "uncoverable_silence",
        "violations": runs,
        "fatal": fatal,
    }


ANCHOR_ASSESS_MAX_EVALS = 256  # bound on the preserving cue search below


def assess_anchor_segment(
    rec: dict,
    head_cov_s: float = 0.0,
    tail_cov_s: float = 0.0,
    gain_db: float = 0.0,
) -> dict:
    """Content-preserving cue for an anchor placed last (C1/G2B-001).

    ``assess_segment`` walks to the first legal trim it finds; an anchor
    under preservation instead enumerates every structurally legal placement
    an offending run admits and keeps the cue retaining the MOST source
    content (ties prefer the later cue-out — the musical ending stays):

    * ``default`` — the landmark cue, kept when already legal;
    * ``head_advance`` — enter on a downbeat past the run: the ending plays
      intact, the entry is late;
    * ``head_covered`` — cue_in on a downbeat where the run lies inside the
      incoming transition's coverage zone;
    * ``tail_covered`` — cue_out on a downbeat where the run lies inside the
      master fade-out coverage zone (the set's own ending legitimately
      masks it);
    * ``tail_trim`` — cue_out on a downbeat before the run: the declared
      fallback when nothing better survives.

    This is a content-protection policy, not a D12 convenience: the chosen
    cue is the content-maximal legal placement, never "the first trim that
    passes". Every feasible placement considered is returned in ``options``
    with the chosen one marked — the trim is audited, never hidden — and
    ``status`` is ``infeasible`` when no placement keeps >= MIN_SEGMENT_S
    of the source.
    """
    cue_in, cue_out = default_cue_points(rec)
    if not has_continuity_payload(rec):
        return {
            "cue_in": cue_in,
            "cue_out": cue_out,
            "status": "unmeasured",
            "reason": "no_continuity_data",
            "violations": [],
            "fatal": [],
            "options": [],
        }
    blocks = np.asarray(rec["block_rms_dbfs"], dtype=np.float64)
    downbeats = sorted(rec.get("downbeat_times_s") or [])
    default = (round(cue_in, 5), round(cue_out, 5))
    runs: list[dict] = []
    fatal: list[dict] = []

    def evaluate(ci: float, co: float) -> tuple[list[dict], list[dict]]:
        r = _violating_runs(blocks, ci, co, gain_db)
        return r, _uncovered_runs(r, ci, co, head_cov_s, tail_cov_s)

    def expansions(ci: float, co: float, f: dict) -> list[tuple[str, float, float]]:
        """Candidate cues neutralising one fatal run — all structural options."""
        a, b = f["uncovered_s"]
        slack = (
            _n_min(DIGITAL_MIN_RUN_S if f["tier"] == "digital_minus90" else LOW_LEVEL_MIN_RUN_S)
            * BLOCK_S
        )
        out: list[tuple[str, float, float]] = []
        before = [d for d in downbeats if ci + MIN_SEGMENT_S <= d <= a - BLOCK_S]
        if before:
            out.append(("tail_trim", ci, before[-1]))
        after = [d for d in downbeats if b + BLOCK_S <= d <= co - MIN_SEGMENT_S]
        if after:
            out.append(("head_advance", after[0], co))
        for d in downbeats:
            # cue_out at d puts the run inside (d - tail_cov_s, d) — masked
            # by the set's ending fade; the bound keeps the uncovered stub
            # provably below the run's minimum length.
            if a - BLOCK_S < d <= min(co, a + slack + tail_cov_s) and d >= ci + MIN_SEGMENT_S:
                out.append(("tail_covered", ci, d))
            # cue_in at d puts the run inside (d, d + head_cov_s) — masked
            # by the incoming transition.
            if (
                max(ci, b - head_cov_s - slack) <= d <= b
                and d <= co - MIN_SEGMENT_S
                and d > ci + BLOCK_S
            ):
                out.append(("head_covered", d, co))
        return out

    def key(ci: float, co: float) -> tuple[float, float]:
        return (round(ci, 4), round(co, 4))

    seen = {key(cue_in, cue_out)}
    worklist = [(cue_in, cue_out, "default")]
    feasible: list[dict] = []
    evals = 0
    while worklist and evals < ANCHOR_ASSESS_MAX_EVALS:
        ci, co, mode = worklist.pop(0)
        evals += 1
        if co - ci < MIN_SEGMENT_S - 1e-9:
            continue
        runs, fatal = evaluate(ci, co)
        if not fatal:
            feasible.append(
                {
                    "mode": mode,
                    "cue_in_s": round(ci, 5),
                    "cue_out_s": round(co, 5),
                    "retained_s": round(co - ci, 3),
                    "violations": runs,
                }
            )
            continue
        for mode2, nci, nco in expansions(ci, co, fatal[0]):
            if key(nci, nco) not in seen:
                seen.add(key(nci, nco))
                worklist.append((nci, nco, mode2))

    if not feasible:
        return {
            "cue_in": round(cue_in, 5),
            "cue_out": round(cue_out, 5),
            "status": "infeasible",
            "reason": "uncoverable_silence",
            "violations": runs,
            "fatal": fatal,
            "options": [],
        }
    best = max(feasible, key=lambda f: (f["retained_s"], f["cue_out_s"]))
    ci, co = best["cue_in_s"], best["cue_out_s"]
    options = sorted(feasible, key=lambda f: (-f["retained_s"], -f["cue_out_s"], f["cue_in_s"]))
    for o in options:
        o["chosen"] = o["cue_in_s"] == ci and o["cue_out_s"] == co
    return {
        "cue_in": ci,
        "cue_out": co,
        "status": "ok" if (ci, co) == default else "adjusted",
        "reason": "ok" if (ci, co) == default else "uncovered_silence_run",
        "violations": best["violations"],
        "fatal": [],
        "preserved": True,
        "mode": best["mode"],
        "options": options,
        "evals": evals,
    }


def _transition_geometry(plan: dict, sr: int) -> tuple[list[dict], list[dict]]:
    """Per-track master placement + fade regions, replicating the renderer.

    Reuses ``render._beat_shift_samples`` so the beat_crossfade entry shift is
    the renderer's own number, not a re-derivation.
    """
    tracks = plan["tracks"]
    geom: list[dict] = []
    fade_outs: list[dict] = []
    for i, t in enumerate(tracks):
        seg_len = int(t["segment_master_samples"])
        fin = None
        if i == 0:
            entry = int(t["master_start_sample"])
        else:
            tr = plan["transitions"][i - 1]
            os_, oe = int(tr["overlap_start_sample"]), int(tr["overlap_end_sample"])
            ttype = tr["type"]
            if ttype == "cut":
                half = int(_render.CUT_SMOOTH_S * sr / 2)
                lo = max(0, oe - half)
                entry, fin = lo, (lo, oe + half, 1.0)
                fade_outs.append({"range": (lo, oe + half), "power": 1.0, "incoming": i})
            elif ttype == "echo_out":
                nf = max(1, int(_render.ECHO_IN_FRACTION * (oe - os_)))
                entry, fin = oe - nf, (oe - nf, oe, 1.0)
                fade_outs.append({"range": (os_, oe), "power": 1.5, "incoming": i})
            else:
                win = os_
                if ttype == "beat_crossfade":
                    win = min(
                        os_ + _render._beat_shift_samples(plan, i - 1, sr),
                        oe - 1,
                    )
                win = max(0, win)
                entry, fin = win, (win, oe, 1.0)
                fade_outs.append({"range": (win, oe), "power": 1.0, "incoming": i})
        geom.append({"entry": entry, "exit": entry + seg_len, "fin": fin})
    return geom, fade_outs


def _fade_up_db(frac: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.sin(frac * (math.pi / 2.0)), 1e-12))


def _fade_down_db(frac: np.ndarray, power: float = 1.0) -> np.ndarray:
    return power * 20.0 * np.log10(np.maximum(np.cos(frac * (math.pi / 2.0)), 1e-12))


def _master_level_map(
    plan: dict, records_by_id: dict, sr: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[np.ndarray], int]:
    """Per-50ms-block predicted master level plus presence/unknown masks.

    For every block the predicted level is the maximum over contributing
    tracks of ``source_block_dbfs + gain_db + fade_envelope_db`` — a lower
    bound on the true mixed RMS, so a block predicted audible is certainly
    audible and only boundary combinations can be over-flagged.
    """
    blk = max(1, int(BLOCK_S * sr))
    tracks = plan["tracks"]
    geom, fade_outs = _transition_geometry(plan, sr)
    master_end = max((g["exit"] for g in geom), default=0)
    n = master_end // blk
    centers = (np.arange(n) + 0.5) * blk
    level = np.full(n, -np.inf)
    unknown = np.zeros(n, dtype=bool)
    present_any = np.zeros(n, dtype=bool)
    present_masks: list[np.ndarray] = []

    for i, t in enumerate(tracks):
        pres = (centers >= geom[i]["entry"]) & (centers < geom[i]["exit"])
        present_masks.append(pres)
        present_any |= pres
        rec = records_by_id.get(t["recording_id"]) or {}
        if not has_continuity_payload(rec):
            unknown |= pres
            continue
        blocks = np.asarray(rec["block_rms_dbfs"], dtype=np.float64)
        stretch = float(t.get("tempo_stretch") or 1.0)
        src_blk = np.floor(
            (float(t["cue_in_samples"]) + (centers - geom[i]["entry"]) * stretch) / blk
        ).astype(np.int64)
        env = _fade_envelope_db(geom[i], fade_outs, i, centers, master_end=master_end, sr=sr)
        contrib = np.full(n, -np.inf)
        valid = pres & (src_blk >= 0) & (src_blk < blocks.size)
        contrib[valid] = blocks[src_blk[valid]] + float(t.get("gain_db") or 0.0) + env[valid]
        level = np.maximum(level, contrib)
    return level, present_any, unknown, present_masks, master_end


def _fade_envelope_db(
    geom_i: dict,
    fade_outs: list[dict],
    track_idx: int,
    centers: np.ndarray,
    *,
    master_end: int,
    sr: int,
) -> np.ndarray:
    """dB envelope applied to one track's contribution by every active fade.

    Mirrors the renderer: the incoming fade-in (``fin``), each transition's
    fade-out over the window for every track except its incoming one, and the
    master head/tail fades.
    """
    env = np.zeros(len(centers))
    fin = geom_i["fin"]
    if fin is not None:
        f0, f1, _p = fin
        inreg = (centers >= f0) & (centers < f1)
        env[inreg] += _fade_up_db((centers[inreg] - f0) / max(1, f1 - f0))
    for fo in fade_outs:
        if fo["incoming"] == track_idx:
            continue  # the incoming track's samples are added after *=
        lo, hi = fo["range"]
        inreg = (centers >= lo) & (centers < hi)
        env[inreg] += _fade_down_db((centers[inreg] - lo) / max(1, hi - lo), power=fo["power"])
    fi = int(_render.MASTER_FADE_IN_S * sr)
    inreg = centers < fi
    env[inreg] += _fade_up_db(centers[inreg] / max(1, fi))
    fo_start = master_end - int(_render.MASTER_FADE_OUT_S * sr)
    inreg = centers >= fo_start
    env[inreg] += _fade_down_db((centers[inreg] - fo_start) / max(1, master_end - fo_start))
    return env


def find_master_violations(plan: dict, records_by_id: dict) -> dict:
    """Forward-simulate the D12 block map of the planned master.

    Exempt regions match ``verify._silence_check``: the renderer's declared
    master fades plus plan-declared ``allowed_silent_gaps_s``.

    Tracks without continuity data make their blocks *unknown*: such blocks
    are excluded from the scan (never counted silent, never counted clean)
    so a stale record cannot fake a pass — and cannot trigger a fake fail.
    """
    sr = int(plan.get("master_sr", MASTER_SR))
    blk = max(1, int(BLOCK_S * sr))
    tracks = plan["tracks"]
    level, present_any, unknown, present_masks, master_end = _master_level_map(
        plan, records_by_id, sr
    )
    n = master_end // blk
    if n == 0:
        return {"violations": [], "master_duration_s": 0.0, "unknown_blocks": 0}

    silent_level = np.where(present_any, level, -np.inf)
    exempt = np.zeros(n, dtype=bool)
    master_s = master_end / sr
    allowed = [
        (0.0, _render.MASTER_FADE_IN_S),
        (max(0.0, master_s - _render.MASTER_FADE_OUT_S), master_s),
    ]
    for gap in plan.get("allowed_silent_gaps_s") or []:
        try:
            allowed.append((float(gap[0]), float(gap[1])))
        except (TypeError, IndexError, ValueError):
            continue
    for lo_s, hi_s in allowed:
        lo = max(0, int(lo_s / BLOCK_S))
        hi = min(n, math.ceil(hi_s / BLOCK_S))
        exempt[lo:hi] = True

    violations: list[dict] = []
    for name, thr, mn in (
        ("digital_minus90", DIGITAL_DBFS, DIGITAL_MIN_RUN_S),
        ("low_level_minus60", LOW_LEVEL_DBFS, LOW_LEVEL_MIN_RUN_S),
    ):
        gate = (silent_level <= thr) & ~exempt & ~unknown
        min_blocks = math.ceil(round(mn * sr) / blk - 1e-9)
        for s, e in silent_runs(gate):
            if (e - s) >= min_blocks:
                present_ids = [
                    tracks[i]["recording_id"] for i, m in enumerate(present_masks) if m[s:e].any()
                ]
                violations.append(
                    {
                        "tier": name,
                        "master_range_s": [round(s * blk / sr, 3), round(e * blk / sr, 3)],
                        "duration_s": round((e - s) * blk / sr, 3),
                        "present_recording_ids": present_ids,
                        "reason": "uncovered_silence_run",
                    }
                )
    violations.sort(key=lambda v: v["master_range_s"])
    return {
        "violations": violations,
        "master_duration_s": round(master_s, 3),
        "unknown_blocks": int(np.count_nonzero(unknown)),
        "silent_blocks_predicted": int(np.count_nonzero(silent_level <= LOW_LEVEL_DBFS)),
    }


def master_opener_report(plan: dict, master_wav: str | Path) -> dict:
    """Opener audibility measured on the rendered MASTER, not the source.

    Uses verify.py's own block logic (``_block_rms_dbfs``, ``_silent_runs``)
    on the first ``OPENER_WINDOW_S`` seconds of the file on disk: a violating
    D12 run — digital >= 0.5 s or low-level >= 2.0 s — inside that window
    fails the gate. Only blocks overlapping the renderer's declared
    MASTER_FADE_IN_S region are exempt, exactly as ``_allowed_silence_regions_s``
    masks the master head.
    """
    data, file_sr = sf.read(str(master_wav), dtype="float32", always_2d=True)
    n_keep = min(len(data), int(OPENER_WINDOW_S * file_sr))
    db, _ms, blk = _verify._block_rms_dbfs(data[:n_keep], file_sr)
    n_blocks = len(db)
    exempt = np.zeros(n_blocks, dtype=bool)
    hi = min(n_blocks, math.ceil(_render.MASTER_FADE_IN_S / _verify.SILENCE_BLOCK_S))
    exempt[:hi] = True
    tiers = {}
    ok = True
    for name, thr, mn in (
        ("digital_minus90", _verify.DIGITAL_SILENCE_DBFS, _verify.DIGITAL_SILENCE_MIN_RUN_S),
        ("low_level_minus60", _verify.LOW_LEVEL_DBFS, _verify.LOW_LEVEL_MIN_RUN_S),
    ):
        gate = (db <= thr) & ~exempt
        runs = _verify._silent_runs(gate)
        min_samples = round(mn * file_sr)
        bad = [r for r in runs if (r[1] - r[0]) * blk >= min_samples]
        tiers[name] = {
            "threshold_dbfs": thr,
            "min_run_s": mn,
            "max_run_s": round(max(((e - s) for s, e in runs), default=0) * blk / file_sr, 3),
            "violating_runs_s": [
                [round(s * blk / file_sr, 3), round(e * blk / file_sr, 3)] for s, e in bad
            ],
            "ok": not bad,
        }
        ok = ok and not bad
    return {
        "window_s": OPENER_WINDOW_S,
        "measured_on": str(master_wav),
        "measured_with": "verify._block_rms_dbfs/_silent_runs (D12 block logic)",
        "blocks": int(n_blocks),
        "tiers": tiers,
        "audibility_ok": bool(ok),
    }
