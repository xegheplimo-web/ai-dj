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
(analysis_version <= 3), v2 = least-squares-refined grid. The continuity
contract treats records without the current version as unmeasured.
"""

from __future__ import annotations

import numpy as np

BEAT_GRID_VERSION = 2  # v2: refined sub-frame grid (v1 = frame-snapped, v3 payload)

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
BEAT_ENV_RATIO_MIN = 2.0  # onset-env strength at beat frames vs env median
_GROWTH_FLOOR_MS = 1.0  # keeps the growth ratio meaningful on perfect grids


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
    beat_env_ratio: float | None = None,
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
    * ``beat_env_ratio`` — median onset-envelope strength at the detected
      beat frames divided by the envelope median. Detected beats on real
      music land on transients (corpus range ~4-10), while a *regular* grid
      over a flat envelope — a sustained tone, where ``beat_track`` happily
      fabricates a tempo from nothing — lands on noise-floor values
      (ratio ≈ 1). (Distance-to-peak-picked-onset support was considered
      and rejected: noise-floor peaks make it read 1.0 on a pure tone.)

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
    # no transient energy above the envelope median is a tempo the detector
    # invented — coverage/residuals cannot see it because the fabricated grid
    # is perfectly self-consistent.
    fabricated = bool(
        beat_env_ratio is not None
        and np.isfinite(beat_env_ratio)
        and beat_env_ratio < BEAT_ENV_RATIO_MIN
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
    if fabricated:
        reasons.append(
            f"beat_env_ratio {beat_env_ratio:.2f} < {BEAT_ENV_RATIO_MIN} — "
            "regular grid over an envelope with no transient support"
        )

    suspect = (
        residual_loose
        or tempo_unstable
        or coverage_low
        or coverage_high
        or octave_suspect
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
        "beat_env_ratio": (
            round(beat_env_ratio, 3)
            if beat_env_ratio is not None and np.isfinite(beat_env_ratio)
            else None
        ),
        "octave_suspect": octave_suspect,
        "tempo_unstable": tempo_unstable,
        "status": "suspect" if suspect else "ok",
        "reasons": reasons,
    }
