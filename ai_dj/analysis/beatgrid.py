"""Refined beat grid (DJ-P2-BEAT-GRID-001): sub-frame tempo + quality metrics.

Why this exists: at ``ANALYSIS_SR = 22050`` the old hop-512 beat stage snapped
every beat time to a 23.22 ms frame raster. A median of frame-snapped IBIs is
an integer number of frames, so ``bpm_ibi`` could only land on a discrete
ladder (~10 BPM apart near 160 BPM) — coarser than the 20 ms contract it must
support. Running the onset/beat stage at ``BEAT_HOP_LENGTH = 128`` (5.80 ms)
and fitting ``beat_index -> time`` by least squares over the whole detected
series produces a continuous inter-beat interval off the raster entirely.

The shipped ``beat_times_s`` grid is the fitted line ``a*i + b`` — the same
constant-tempo model the renderer already projects for beat_crossfade
transitions — so stored grids and rendered grids now share one model. The fit
does NOT prove the detector found the right beats: ``assess_quality`` reports
residuals, coverage, drift and octave suspicion so a bad grid is *flagged*,
never silently trusted (no hard gate — D25 rejected rejecting on resolution).

``BEAT_GRID_VERSION`` distinguishes grids: v1 = frame-snapped detected times
(analysis_version <= 3), v2 = least-squares-refined grid, v3 = octave-resolved
grid (the shipped level may differ from the detected one by a factor of 2 —
DJ-P2-OCTAVE-001). The continuity contract treats records without the current
version as unmeasured.
"""

from __future__ import annotations

import librosa
import numpy as np

BEAT_GRID_VERSION = 3  # v3: octave-resolved grid (v2 = refined sub-frame, v1 = snapped)

# Hop for the onset/beat stage only — chroma/rms keep the cheaper 512 hop so
# total analysis cost does not blow up. 128/22050 = 5.804 ms frame period.
BEAT_HOP_LENGTH = 128

# Quality-gate thresholds (corpus-calibrated on the 42-track v4 re-analysis;
# see .agent_report.md for the measured distribution behind each value).
RESIDUAL_STD_WARN_MS = 20.0  # LS residual std above this -> tempo model misfit
RESIDUAL_DRIFT_WARN_MS = 40.0  # end-to-end phase drift the constant tempo missed
GROWTH_RATIO_WARN = 1.75  # late-half residual std / early-half residual std
IBI_DRIFT_WARN_PCT = 5.0  # early-third vs last-third median IBI shift
COVERAGE_MIN = 0.85  # detected beats vs beats expected over the active span
COVERAGE_MAX = 1.15  # above this the detector is likely over-firing
# Envelope-support gate on the bounded score (F2 fix): the score is the
# monotone image med/(med+env_med) of the old unbounded ratio, so the former
# `ratio < 2.0` flag boundary maps exactly to `score < 2/3`.
BEAT_ENV_SCORE_MIN = 2.0 / 3.0
_GROWTH_FLOOR_MS = 1.0  # keeps the growth ratio meaningful on perfect grids
_ENV_EPS = 1e-9  # epsilon floor for envelope ratios — keeps scores finite

# Multi-hypothesis octave resolution (D26 §7.1): every detected tempo is
# scored against the onset envelope at {B/2, B, 2B} and the level is chosen
# only when the top-2 score gap clears this margin; below it the honest
# output is `ambiguous`/`unverified`, never a silently picked level.
OCTAVE_LEVELS = (0.5, 1.0, 2.0)
OCTAVE_AMBIGUITY_MARGIN = 0.08
_MIN_BEATS_OCTAVE = 8  # too few beats -> octave level unverifiable
_W_ANTI = 0.35  # penalty: anti-phase positions carry as much env as the teeth
_W_TEETH = 0.25  # reward: teeth sit on strong onsets (not subdivisions)
_W_PRIOR = 0.20  # weak tempo prior around plausible DJ tempi
_OCTAVE_PRIOR_CENTER_BPM = 128.0
_OCTAVE_PRIOR_SIGMA_OCT = 0.8


def fit_beat_grid(detected_times: np.ndarray) -> dict | None:
    """Least-squares ``t_i ~ a*i + b`` over detected beat times.

    Returns ``None`` for fewer than two beats. The slope ``a`` is the refined
    inter-beat interval: off the frame raster because the regression averages
    quantization/jitter over every beat instead of inheriting one snap.
    """
    t = np.asarray(detected_times, dtype=np.float64)
    n = int(t.size)
    if n < 2:
        return None
    idx = np.arange(n, dtype=np.float64)
    coef, *_ = np.linalg.lstsq(np.column_stack([idx, np.ones(n)]), t, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    resid = t - (a * idx + b)
    return {"ibi_s": a, "phase_s": b, "residuals_s": resid, "n_beats": n}


def refined_grid(ibi_s: float, phase_s: float, n_beats: int) -> np.ndarray:
    """The rebuilt beat grid: ``a*i + b`` — continuous, not frame-snapped."""
    return ibi_s * np.arange(n_beats, dtype=np.float64) + phase_s


def bpm_resolution(ibi_s: float, n_beats: int, frame_period_s: float) -> float:
    """BPM resolution of the LS tempo under uniform frame quantization (1 sigma).

    Quantizing each beat to the frame period contributes variance p^2/12 per
    point; the slope variance is sigma^2/Sxx with Sxx = n(n^2-1)/12, which
    collapses to ``p^2 / (n^3 - n)``. Propagated through ``bpm = 60/ibi``:
    ``sigma_bpm = (60/ibi^2) * p / sqrt(n^3 - n)``.
    """
    n = int(n_beats)
    if n < 2 or not np.isfinite(ibi_s) or ibi_s <= 0:
        return float("nan")
    sigma_ibi = frame_period_s / float(np.sqrt(n**3 - n))
    return (60.0 / ibi_s**2) * sigma_ibi


def beat_env_score(onset_env: np.ndarray, beat_frames: np.ndarray) -> float | None:
    """Bounded envelope support at the detected beat frames — replaces
    ``beat_env_ratio`` (F2: that ratio exploded to ~1e10 on sparse percussive
    envelopes whose median is ~0).

    The score is ``m_b / (m_b + m_e + eps)`` where ``m_b`` is the median onset
    strength at beat frames and ``m_e`` the median over the whole envelope —
    the monotone image ``r/(1+r)`` of the old ratio ``r = m_b/m_e`` with an
    epsilon floor. It is finite for every envelope, bounded to [0, 1),
    scale-invariant (multiplying the envelope by any c > 0 leaves it
    unchanged), and preserves the old gate exactly: ``ratio >= 2.0`` is
    ``score >= 2/3``. A fabricated grid on a flat envelope lands near 0.5;
    beats on real transients sit well above the typical envelope level.
    """
    env = np.asarray(onset_env, dtype=np.float64)
    frames = np.asarray(beat_frames, dtype=int)
    if env.size == 0 or frames.size == 0:
        return None
    fr = np.clip(frames, 0, env.size - 1)
    m_b = float(np.median(env[fr]))
    m_e = float(np.median(env))
    eps = _ENV_EPS * max(float(env.max()), 1e-12)
    return m_b / (m_b + m_e + eps)


def tempo_prior(bpm: float) -> float:
    """Weak log-domain tempo prior centred on plausible DJ tempi.

    Gaussian in log2(BPM/128) with sigma 0.8 octaves: 64 and 256 both sit ~0.46,
    32 and 512 ~0.04. It is only a tie-breaker — octave discrimination comes
    from the envelope, not the prior.
    """
    if bpm <= 0 or not np.isfinite(bpm):
        return 0.0
    z = np.log2(bpm / _OCTAVE_PRIOR_CENTER_BPM) / _OCTAVE_PRIOR_SIGMA_OCT
    return float(np.exp(-0.5 * z * z))


def _env_at(env: np.ndarray, times_s: np.ndarray, frame_period_s: float) -> np.ndarray:
    """Onset-envelope values at arbitrary (sub-frame) times."""
    if env.size == 0 or len(times_s) == 0:
        return np.zeros(0)
    fr = np.clip(np.round(np.asarray(times_s) / frame_period_s).astype(int), 0, env.size - 1)
    return env[fr]


def _teeth_anti(
    env: np.ndarray, detected: np.ndarray, factor: float, frame_period_s: float
) -> tuple[np.ndarray, np.ndarray, int]:
    """Beat teeth vs anti-phase reference positions for one octave candidate.

    * ``x1`` — teeth are the detected beats, anti are the midpoints between
      consecutive beats;
    * ``x2`` — teeth are detected beats interleaved with their midpoints
      (the doubled grid), anti are the quarter-phase positions;
    * ``x0.5`` — teeth are every other detected beat (the half grid); the
      parity whose median envelope is stronger is chosen, and the *other*
      parity serves as anti positions. Returns ``(teeth, anti, parity)`` —
      parity is the beat-index offset the shipped grid needs.
    """
    ibi = float(np.median(np.diff(detected)))
    if factor == 1.0:
        return detected, detected[:-1] + ibi / 2.0, 0
    if factor == 0.5:
        ev, od = detected[0::2], detected[1::2]
        if od.size == 0:
            return ev, od, 0
        if np.median(_env_at(env, ev, frame_period_s)) >= np.median(
            _env_at(env, od, frame_period_s)
        ):
            return ev, od, 0
        return od, ev, 1
    mid = detected[:-1] + ibi / 2.0
    teeth = np.sort(np.concatenate([detected, mid]))
    anti = np.sort(np.concatenate([detected[:-1] + ibi / 4.0, detected[:-1] + 3.0 * ibi / 4.0]))
    return teeth, anti, 0


def _acf_at(ac: np.ndarray, lag_frames: float) -> float:
    """Normalised autocorrelation at a (possibly fractional) lag, linear interp."""
    if lag_frames <= 0 or lag_frames >= ac.size - 1:
        return 0.0
    i = int(np.floor(lag_frames))
    f = lag_frames - i
    return float(ac[i] * (1.0 - f) + ac[i + 1] * f)


def resolve_octave_level(
    onset_env: np.ndarray, detected_times: np.ndarray, ibi_s: float, frame_period_s: float
) -> dict:
    """Multi-hypothesis octave resolution (D26 §7.1): evaluate {B/2, B, 2B}.

    Each candidate level is scored on the onset envelope by three terms:

    * **autocorrelation** at the candidate period (normalised so the best
      level's periodicity reads 1.0) — captures accent/periodicity structure;
    * **anti-phase penalty** — median envelope at the grid's anti-phase
      positions relative to the teeth: a level whose grid *misses* real
      onsets (half-time over a dense onset lattice) is penalised, which is
      the term that resolves the silent half-time failures;
    * **teeth strength** — median envelope at the teeth, normalised across
      levels: a doubled grid whose added teeth are weak subdivisions is
      discounted relative to the level whose teeth are the strong onsets;
    * plus a weak :func:`tempo_prior` tie-breaker.

    The top-scoring level is chosen only when its score clears the runner-up
    by ``OCTAVE_AMBIGUITY_MARGIN``; below that the answer is honestly
    ``ambiguous`` (keep the detected level — the conservative guess — and let
    the quality block flag it). Too few beats or a degenerate envelope give
    ``unverified``. Timing alone cannot always identify the perceived beat
    (D26 §7.6): an unverifiable answer is a valid output, a silently wrong
    one is not.
    """
    out: dict = {
        "verdict": "unverified",
        "selected_factor": 1.0,
        "selected_bpm": None,
        "margin": None,
        "ambiguous": True,
        "phase_offset_beats": 0,
        "candidates": [],
    }
    t = np.asarray(detected_times, dtype=np.float64)
    n = int(t.size)
    env = np.asarray(onset_env, dtype=np.float64)
    if n < _MIN_BEATS_OCTAVE or not np.isfinite(ibi_s) or ibi_s <= 0 or env.size == 0:
        return out
    ac = librosa.autocorrelate(env)
    if ac.size == 0 or float(ac[0]) <= 0:
        return out
    ac = ac / float(ac[0])

    bpm_det = 60.0 / ibi_s
    candidates: list[dict] = []
    for factor in OCTAVE_LEVELS:
        bpm_c = bpm_det * factor
        teeth, anti, parity = _teeth_anti(env, t, factor, frame_period_s)
        m_b = float(np.median(_env_at(env, teeth, frame_period_s)))
        m_a = float(np.median(_env_at(env, anti, frame_period_s)))
        candidates.append(
            {
                "factor": factor,
                "bpm": round(bpm_c, 4),
                "acf": _acf_at(ac, (60.0 / bpm_c) / frame_period_s),
                "teeth_median": m_b,
                "anti_median": m_a,
                "anti_penalty": m_a / (m_a + m_b + _ENV_EPS),
                "prior": tempo_prior(bpm_c),
                "parity": parity,
            }
        )
    acf_max = max(c["acf"] for c in candidates)
    teeth_max = max(c["teeth_median"] for c in candidates)
    for c in candidates:
        c["acf_norm"] = c["acf"] / acf_max if acf_max > 0 else 0.0
        c["strength"] = c["teeth_median"] / teeth_max if teeth_max > 0 else 0.0
        c["score"] = (
            c["acf_norm"]
            + _W_TEETH * c["strength"]
            + _W_PRIOR * c["prior"]
            - _W_ANTI * c["anti_penalty"]
        )
    candidates.sort(key=lambda c: -c["score"])
    top, runner_up = candidates[0], candidates[1]
    margin = float(top["score"] - runner_up["score"])

    rounded = [
        {
            "factor": c["factor"],
            "bpm": c["bpm"],
            "acf": round(c["acf"], 4),
            "acf_norm": round(c["acf_norm"], 4),
            "teeth_median": round(c["teeth_median"], 6),
            "anti_median": round(c["anti_median"], 6),
            "anti_penalty": round(c["anti_penalty"], 4),
            "strength": round(c["strength"], 4),
            "prior": round(c["prior"], 4),
            "score": round(c["score"], 4),
        }
        for c in candidates
    ]
    out["candidates"] = rounded
    out["margin"] = round(margin, 4)
    if margin < OCTAVE_AMBIGUITY_MARGIN:
        out["verdict"] = "ambiguous"
        out["selected_bpm"] = round(bpm_det, 4)
        return out
    out.update(
        {
            "verdict": f"x{top['factor']}",
            "selected_factor": top["factor"],
            "selected_bpm": top["bpm"],
            "ambiguous": False,
            "phase_offset_beats": int(top["parity"]),
        }
    )
    return out


def _stability_metrics(resid: np.ndarray, t: np.ndarray, n: int) -> tuple[float, float, float]:
    """(growth_ratio, phase_drift_ms, ibi_drift_pct) — three faces of drift.

    ``phase_drift_ms`` is the edge-median residual difference (last third
    minus first third): a monotone phase walk the single slope cannot
    express. (Regressing residuals on the beat index would be degenerate —
    LS residuals have zero slope by construction.) ``residual_growth_ratio``
    catches detection quality decaying late; ``ibi_drift_pct`` is the direct
    early-vs-late tempo shift a symmetric V-residual hides from the first
    two (a tempo step splits evenly around the fitted line).
    """
    third_resid = max(1, n // 3)
    phase_drift_ms = float(np.median(resid[-third_resid:]) - np.median(resid[:third_resid]))
    half = n // 2
    if half >= 2 and n - half >= 2:
        early = float(resid[:half].std())
        late = float(resid[half:].std())
        growth_ratio = late / max(early, _GROWTH_FLOOR_MS)
    else:
        growth_ratio = 1.0
    ibi_series = np.diff(t)
    third = ibi_series.size // 3
    if third >= 1 and ibi_series.size >= 3 * third:
        ibi_early = float(np.median(ibi_series[:third]))
        ibi_late = float(np.median(ibi_series[-third:]))
        ibi_drift_pct = (ibi_late - ibi_early) / ibi_early * 100.0 if ibi_early > 0 else 0.0
    else:
        ibi_drift_pct = 0.0
    return growth_ratio, phase_drift_ms, ibi_drift_pct


def assess_quality(
    detected_times: np.ndarray,
    fit: dict,
    *,
    active_span_s: float,
    octave_diagnosis: str,
    beat_env_score: float | None = None,
    octave_ambiguous: bool = False,
) -> dict:
    """Quality/uncertainty metrics for a fitted beat grid.

    The fit cannot by itself prove the detector found the right beats, did
    not drop beats, and did not confuse half/double time — this block makes
    those failure modes observable instead of trusting the regression:

    * ``residual_std_ms`` / ``residual_max_ms`` — spread of detected times
      around the fitted line (detector jitter, swing, or grid misfit);
    * ``coverage_ratio`` — detected beat count vs the count the fitted tempo
      implies over the active (sounding) span — dropped regions sink it,
      over-detection inflates it;
    * ``residual_growth_ratio`` / ``phase_drift_ms`` / ``ibi_drift_pct`` —
      whether the residual scatter grows over time, the phase walks between
      the first and last thirds, or the detected IBI itself moves between
      them (tempo instability a single constant tempo cannot express);
    * ``octave_suspect`` — the refined tempo disagrees with the raw librosa
      tempo by more than the agreement band (half/double-time suspicion);
    * ``octave_ambiguous`` — the multi-hypothesis resolver could not
      distinguish the level from a neighbouring octave (or could not verify
      it at all): the answer is unverifiable, never silently ``ok``;
    * ``beat_env_score`` — median onset-envelope strength at the detected
      beat frames as a bounded share ``m_b/(m_b+m_e)`` of the typical
      envelope level (monotone image of the old unbounded ratio; the former
      ``< 2.0`` gate is ``< 2/3`` here). Detected beats on real music land
      on transients (corpus ~0.77-0.91), while a *regular* grid over a flat
      envelope — a sustained tone, where ``beat_track`` happily fabricates
      a tempo from nothing — lands near 0.5. (Distance-to-peak-picked-onset
      support was considered and rejected: noise-floor peaks make it read
      1.0 on a pure tone.)

    ``status`` is the proposed quality gate: ``suspect`` when the grid misses
    coverage, is unstable, or fits too loosely to support a 20 ms contract —
    informational for planning (D25: no hard gate), not a rejection.
    """
    resid = np.asarray(fit["residuals_s"], dtype=np.float64) * 1000.0
    ibi = float(fit["ibi_s"])
    n = int(fit["n_beats"])
    t = np.asarray(detected_times, dtype=np.float64)

    residual_std_ms = float(resid.std())
    residual_max_ms = float(np.abs(resid).max())

    growth_ratio, phase_drift_ms, ibi_drift_pct = _stability_metrics(resid, t, n)

    # Coverage: beats the fitted tempo implies over the active span vs beats
    # actually detected.
    expected = active_span_s / ibi if ibi > 0 else 0.0
    coverage = n / expected if expected > 0 else 0.0

    # Fabrication check: a regular grid over material whose beat frames carry
    # no transient energy above the typical envelope is a tempo the detector
    # invented — coverage/residuals cannot see it because the fabricated grid
    # is perfectly self-consistent.
    fabricated = bool(
        beat_env_score is not None
        and np.isfinite(beat_env_score)
        and beat_env_score < BEAT_ENV_SCORE_MIN
    )

    detected_span_s = float(t[-1] - t[0]) if n >= 2 else 0.0
    octave_suspect = octave_diagnosis in (
        "raw_double_time",
        "raw_half_time",
        "raw_off_grid",
    )
    tempo_unstable = bool(
        growth_ratio > GROWTH_RATIO_WARN
        or abs(phase_drift_ms) > RESIDUAL_DRIFT_WARN_MS
        or abs(ibi_drift_pct) > IBI_DRIFT_WARN_PCT
    )
    coverage_low = bool(coverage < COVERAGE_MIN)
    coverage_high = bool(coverage > COVERAGE_MAX)
    residual_loose = bool(residual_std_ms > RESIDUAL_STD_WARN_MS)

    reasons: list[str] = []
    if residual_loose:
        reasons.append(f"residual_std_ms {residual_std_ms:.1f} > {RESIDUAL_STD_WARN_MS}")
    if coverage_low:
        reasons.append(f"coverage_ratio {coverage:.3f} < {COVERAGE_MIN}")
    if coverage_high:
        reasons.append(f"coverage_ratio {coverage:.3f} > {COVERAGE_MAX}")
    if tempo_unstable:
        reasons.append(
            f"tempo_unstable growth {growth_ratio:.2f} / drift {phase_drift_ms:.1f} ms"
            f" / ibi_drift {ibi_drift_pct:.1f}%"
        )
    if octave_suspect:
        reasons.append(f"octave_suspect ({octave_diagnosis})")
    if octave_ambiguous:
        reasons.append(
            "octave_ambiguous — level indistinguishable from a neighbouring "
            "octave hypothesis (or unverifiable); not a verified tempo (D26 B1)"
        )
    if fabricated:
        reasons.append(
            f"beat_env_score {beat_env_score:.3f} < {BEAT_ENV_SCORE_MIN:.3f} — "
            "regular grid over an envelope with no transient support"
        )

    suspect = (
        residual_loose
        or tempo_unstable
        or coverage_low
        or coverage_high
        or octave_suspect
        or octave_ambiguous
        or fabricated
    )
    return {
        "residual_std_ms": round(residual_std_ms, 3),
        "residual_max_ms": round(residual_max_ms, 3),
        "detected_span_s": round(detected_span_s, 3),
        "active_span_s": round(active_span_s, 3),
        "coverage_ratio": round(coverage, 4),
        "coverage_low": coverage_low,
        "coverage_high": coverage_high,
        "residual_growth_ratio": round(growth_ratio, 3),
        "phase_drift_ms": round(phase_drift_ms, 2),
        "ibi_drift_pct": round(ibi_drift_pct, 2),
        "beat_env_score": (
            round(beat_env_score, 4)
            if beat_env_score is not None and np.isfinite(beat_env_score)
            else None
        ),
        "octave_suspect": octave_suspect,
        "octave_ambiguous": bool(octave_ambiguous),
        "tempo_unstable": tempo_unstable,
        "status": "suspect" if suspect else "ok",
        "reasons": reasons,
    }
