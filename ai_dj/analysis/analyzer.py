"""Audio analysis: BPM, key, downbeat grid, energy, intro/outro — cached in sqlite.

Per VERIFICATION.md, ``librosa.feature.tempo``/``beat_track`` reports 92-144 BPM
for Bolero where the true beat can sit an octave away. The fix applied here:

* the **median inter-beat interval (IBI)** of the detected beat grid is treated
  as the beat period of record — it comes from actual beat positions, not the
  tempo histogram peak;
* the raw librosa tempo is then *diagnosed* against it: ratio ≈2 -> librosa was
  in double-time, ratio ≈0.5 -> half-time, ≈1 -> agreement;
* both ``bpm_raw`` and ``bpm_corrected`` are stored, plus ``octave_diagnosis``.

v4 (DJ-P2-BEAT-GRID-001): the onset/beat stage runs at ``BEAT_HOP_LENGTH = 128``
(5.80 ms frame period, was 512 -> 23.22 ms) and the inter-beat interval is the
least-squares slope over the whole detected beat series — the shipped
``beat_times_s`` grid is the fitted line ``a*i + b``, off the frame raster.
``bpm_ibi_median`` keeps the old median-IBI estimator for comparison;
``bpm_refined`` is the LS value; ``bpm_resolution`` is the BPM-equivalent of
the quantization bound; ``beat_quality`` carries the residual/coverage/drift
metrics plus the quality-gate verdict — a fit alone cannot prove the detector
found the right beats (D25: quality gate, no hard frame-period rejection).

v5 (DJ-P2-OCTAVE-001): multi-hypothesis octave resolution (D26 §7.1) scores
the detected grid's level against the onset envelope at ``{B/2, B, 2B}`` —
the shipped grid and ``bpm_refined``/``bpm_corrected`` carry the resolved
level (``bpm_detected`` keeps the raw detected tempo), and a level that
cannot be distinguished from its neighbours is reported ``ambiguous`` /
``unverified`` — never a silent ``ok``. The unbounded ``beat_env_ratio`` is
replaced by the bounded, scale-invariant ``beat_env_score`` (F2).

Confidence is honest: fields that cannot be estimated reliably are explicitly
``UNKNOWN``/low-confidence (D6 — no fabricated key/downbeat confidence). The
downbeat grid is marked ``estimated: true`` because it assumes 4/4 from the
first detected beat; nothing stronger is claimed.

Cache (``analysis_cache.sqlite`` by default): keyed by ``recording_id`` (SHA-256
of file bytes) so a changed file is re-analyzed and an unchanged file returns a
byte-identical record. Failed files are recorded with ``status="error"`` and
never abort the corpus run.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import librosa
import numpy as np

from ai_dj import continuity
from ai_dj.analysis import beatgrid

ANALYSIS_VERSION = 5  # v5: octave-resolved grid + bounded beat_env_score
ANALYSIS_SR = 22050
DEFAULT_CACHE = "analysis_cache.sqlite"

# Corpus eligibility contract (D10): non-song files (training clips, raw
# sessions) carry long contiguous silent stretches; real songs do not. A
# block of SILENCE_BLOCK_S seconds counts as silent when its RMS stays below
# -SILENCE_DB dBFS; only runs of at least MIN_SILENCE_RUN_S count, so quiet
# intros/outros and inter-phrase gaps cannot move the metric. Measured
# separation on the real corpus: songs 0.0-0.2 %, non-songs 21.8-31.9 %.
MAX_SILENCE_RUN_FRACTION = 0.02
MIN_SILENCE_RUN_S = 2.0
SILENCE_DB = 100.0
SILENCE_BLOCK_S = 0.05

# Beats per bar assumed for the (estimated) downbeat grid.
_BEATS_PER_BAR = 4
# Minimum detected beats before any BPM figure is trusted at all.
_MIN_BEATS = 8

# Krumhansl-Schmucker key profiles.
_KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Below this Pearson correlation the key estimate is reported as UNKNOWN.
_KEY_MIN_CORR = 0.40


def resolve_octave(bpm_raw: float, bpm_ibi: float) -> tuple[float, str]:
    """Diagnose the raw tempo against the median-IBI tempo.

    Returns (bpm_corrected, diagnosis). The corrected value is always the
    IBI-derived tempo — it reflects where beats actually landed; the diagnosis
    records how the raw estimate related to it.
    """
    if not np.isfinite(bpm_ibi) or bpm_ibi <= 0:
        return float(bpm_raw), "ibi_unavailable"
    if not np.isfinite(bpm_raw) or bpm_raw <= 0:
        return float(bpm_ibi), "raw_unavailable"
    ratio = bpm_raw / bpm_ibi
    if 0.90 <= ratio <= 1.10:
        return float(bpm_ibi), "agree"
    if 1.80 <= ratio <= 2.20:
        return float(bpm_ibi), "raw_double_time"
    if 0.45 <= ratio <= 0.55:
        return float(bpm_ibi), "raw_half_time"
    return float(bpm_ibi), "raw_off_grid"


def tempo_confidence(ibi_cv: float, n_beats: int) -> float:
    """Confidence in the beat grid from IBI dispersion and beat count."""
    if n_beats < _MIN_BEATS or not np.isfinite(ibi_cv):
        return 0.0
    if ibi_cv < 0.08:
        base = 0.9
    elif ibi_cv < 0.15:
        base = 0.7
    elif ibi_cv < 0.30:
        base = 0.5
    else:
        base = 0.25
    if n_beats < 32:
        base *= 0.7
    return round(base, 3)


def estimate_key(chroma_mean: np.ndarray) -> tuple[str, float]:
    """Krumhansl-Schmucker over mean chroma; UNKNOWN below the correlation floor."""
    c = np.asarray(chroma_mean, dtype=np.float64)
    if c.size != 12 or c.std() < 1e-6:
        return "UNKNOWN", 0.0
    c = (c - c.mean()) / c.std()
    scores: list[tuple[float, str]] = []
    for profile, mode in ((_KS_MAJOR, "major"), (_KS_MINOR, "minor")):
        p = np.asarray(profile, dtype=np.float64)
        p = (p - p.mean()) / p.std()
        for i in range(12):
            corr = float(np.mean(c * np.roll(p, i)))
            scores.append((corr, f"{_PITCHES[i]} {mode}"))
    scores.sort(key=lambda s: s[0], reverse=True)
    best_corr, best_key = scores[0]
    if best_corr < _KEY_MIN_CORR:
        return "UNKNOWN", round(best_corr, 3)
    return best_key, round(best_corr, 3)


def silence_metrics(y: np.ndarray, sr: int) -> tuple[float, float]:
    """Share of the signal inside long silent runs, and the longest such run.

    Returns ``(ratio, longest_run_s)`` where ``ratio`` is the fraction of the
    duration occupied by contiguous silent runs of ``MIN_SILENCE_RUN_S`` or
    more — the D10 eligibility metric, computed on SILENCE_BLOCK_S blocks at
    ``-SILENCE_DB`` dBFS RMS. Short silences (between phrases, after clicks)
    never reach the run threshold and do not count.
    """
    block = max(1, int(SILENCE_BLOCK_S * sr))
    n = len(y) // block
    if n == 0:
        return 0.0, 0.0
    frames = y[: n * block].reshape(n, block)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    silent = rms <= 10.0 ** (-SILENCE_DB / 20.0)
    run_s = long_s = longest_s = 0.0
    for v in np.append(silent, False):
        if v:
            run_s += SILENCE_BLOCK_S
        elif run_s > 0.0:
            longest_s = max(longest_s, run_s)
            if run_s >= MIN_SILENCE_RUN_S:
                long_s += run_s
            run_s = 0.0
    duration_s = len(y) / float(sr)
    return (long_s / duration_s if duration_s > 0 else 0.0), longest_s


def _energy_regions(
    times: np.ndarray, energy: np.ndarray, active_frac: float = 0.25
) -> tuple[float, float]:
    """First/last second where energy stays above ``active_frac`` of the peak.

    ``intro_end_s`` is the start of the first sustained-active second;
    ``outro_start_s`` is the start of the decay after the last sustained-active
    second. Both are honest heuristics, stored alongside the energy curve so a
    human can override them later.
    """
    if energy.size == 0 or float(energy.max()) <= 0:
        return 0.0, float(times[-1]) if times.size else 0.0
    active = energy > active_frac * float(energy.max())
    run = 0
    intro_idx = 0
    for i, a in enumerate(active):
        run = run + 1 if a else 0
        if run >= 2:
            intro_idx = i - run + 1
            break
    run = 0
    outro_idx = int(active.size) - 1
    for i in range(active.size - 1, -1, -1):
        run = run + 1 if active[i] else 0
        if run >= 2:
            outro_idx = i + run
            break
    return float(times[intro_idx]), float(times[min(outro_idx, times.size - 1)])


def _beat_grid_fields(
    y: np.ndarray, sr: int, active_span_s: float
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Detect beats at the fine hop, fit the refined grid, report quality.

    Returns ``(beat_times, detected_times, fields)`` where ``fields`` carries
    every beat-grid payload entry. The onset/beat stage runs at
    ``BEAT_HOP_LENGTH`` (5.80 ms frames, was 512 -> 23.22 ms): the old raster
    snapped every beat time, so a median IBI could only take integer-frame
    values and ``bpm_ibi`` sat on a ~10 BPM-spaced ladder.
    """
    beat_hop = beatgrid.BEAT_HOP_LENGTH
    frame_period_s = beat_hop / float(sr)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=beat_hop)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=beat_hop
    )
    bpm_raw = float(np.atleast_1d(tempo)[0])
    detected_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=beat_hop).astype(
        np.float64
    )
    n_detected = int(detected_times.size)

    # Fabrication check: does the envelope carry real transient strength at
    # the detected beat frames? On music beats land on peaks (score ~0.8-0.9);
    # on a flat envelope (sustained tone) beat_track emits a regular grid
    # anyway and the score sits near 0.5 — self-consistency metrics cannot
    # distinguish that from real tracking. Bounded replacement for the old
    # unbounded ratio (F2).
    beat_env_sc = beatgrid.beat_env_score(onset_env, beat_frames)

    fit = beatgrid.fit_beat_grid(detected_times)
    ibi_refined = fit["ibi_s"] if fit is not None else float("nan")
    bpm_detected = 60.0 / ibi_refined if ibi_refined > 0 else float("nan")

    # Multi-hypothesis octave resolution (D26 §7.1): score {B/2, B, 2B}
    # against the onset envelope; below the ambiguity margin the detected
    # level ships with an explicit unverified flag — never a silent guess.
    oct_diag = beatgrid.resolve_octave_level(onset_env, detected_times, ibi_refined, frame_period_s)
    level = 1.0 if oct_diag["ambiguous"] else float(oct_diag["selected_factor"])
    parity = int(oct_diag["phase_offset_beats"])

    if fit is not None:
        # The shipped grid is the fitted line — the same constant-tempo model
        # the renderer projects into beat_crossfade overlaps — not the raw
        # frame-snapped detections — at the octave level the resolver chose:
        # x2 interleaves the midpoints, x0.5 keeps the stronger parity.
        ibi_ship = ibi_refined / level
        if level == 2.0:
            n_ship = 2 * n_detected - 1
        elif level == 0.5:
            n_ship = (n_detected - parity + 1) // 2
        else:
            n_ship = n_detected
        beat_times = beatgrid.refined_grid(ibi_ship, fit["phase_s"] + parity * ibi_refined, n_ship)
    else:
        ibi_ship = float("nan")
        beat_times = detected_times
    median_ibi = float(np.median(np.diff(beat_times))) if beat_times.size >= 2 else float("nan")
    bpm_refined = 60.0 / ibi_ship if ibi_ship > 0 else float("nan")

    if n_detected >= 2:
        detected_ibis = np.diff(detected_times)
        median_ibi_detected = float(np.median(detected_ibis))
        bpm_ibi_median = 60.0 / median_ibi_detected if median_ibi_detected > 0 else float("nan")
        ibi_cv = (
            float(detected_ibis.std() / detected_ibis.mean())
            if detected_ibis.mean() > 0
            else float("nan")
        )
    else:
        median_ibi_detected, bpm_ibi_median, ibi_cv = (
            float("nan"),
            float("nan"),
            float("nan"),
        )

    # Raw-vs-detected diagnostic kept for provenance: how the librosa tempo
    # histogram related to where beats actually landed.
    _raw_ref, raw_diag = resolve_octave(bpm_raw, bpm_detected)
    bpm_corrected = bpm_refined if np.isfinite(bpm_refined) else bpm_raw
    oct_diag["raw_vs_detected"] = raw_diag

    if fit is not None:
        beat_quality = beatgrid.assess_quality(
            detected_times,
            fit,
            active_span_s=active_span_s,
            octave_diagnosis=raw_diag,
            beat_env_score=beat_env_sc,
            octave_ambiguous=oct_diag["ambiguous"],
        )
        bpm_resolution = beatgrid.bpm_resolution(ibi_ship, int(beat_times.size), frame_period_s)
    else:
        beat_quality = {
            "status": "insufficient_beats",
            "reasons": [f"n_beats {n_detected} < 2"],
            "beat_env_score": (round(beat_env_sc, 4) if beat_env_sc is not None else None),
            "octave_suspect": raw_diag in ("raw_double_time", "raw_half_time", "raw_off_grid"),
            "octave_ambiguous": True,
        }
        bpm_resolution = float("nan")

    # An unverified octave is not a confident tempo (D26 §7.6): cap the
    # confidence below the planner's beatmatch floor rather than fabricate
    # certainty the envelope does not support.
    conf = tempo_confidence(ibi_cv, n_detected)
    if oct_diag["ambiguous"]:
        conf = min(conf, 0.4)

    fields = {
        "beat_grid_version": beatgrid.BEAT_GRID_VERSION,
        "beat_hop_length": beat_hop,
        "beat_frame_period_ms": round(frame_period_s * 1000.0, 4),
        "bpm_raw": round(bpm_raw, 3),
        "bpm_ibi_median": round(bpm_ibi_median, 3) if np.isfinite(bpm_ibi_median) else None,
        "bpm_detected": round(bpm_detected, 4) if np.isfinite(bpm_detected) else None,
        "bpm_refined": round(bpm_refined, 4) if np.isfinite(bpm_refined) else None,
        "bpm_resolution": (round(bpm_resolution, 5) if np.isfinite(bpm_resolution) else None),
        "bpm_corrected": round(bpm_corrected, 3),
        "octave_diagnosis": oct_diag,
        "median_ibi_s": round(median_ibi, 6) if np.isfinite(median_ibi) else None,
        "median_ibi_detected_s": (
            round(median_ibi_detected, 6) if np.isfinite(median_ibi_detected) else None
        ),
        "ibi_cv": round(ibi_cv, 4) if np.isfinite(ibi_cv) else None,
        "beat_quality": beat_quality,
        "tempo_confidence": conf,
        "n_beats": int(beat_times.size),
        "beat_times_s": [round(t, 5) for t in beat_times.tolist()],
        "beat_times_detected_s": [round(t, 5) for t in detected_times.tolist()],
        "downbeat_times_s": [round(t, 5) for t in beat_times[::_BEATS_PER_BAR].tolist()],
        "downbeat_estimated": True,
        "meter": "4/4-assumed",
    }
    return beat_times, detected_times, fields


def analyze_track(path: str | Path) -> dict:
    """Analyze one audio file; raise on failure (caller records the error)."""
    y, sr = librosa.load(str(path), sr=ANALYSIS_SR, mono=True)
    duration_s = len(y) / float(sr)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key, key_conf = estimate_key(chroma.mean(axis=1))

    rms = librosa.feature.rms(y=y)[0]
    frame_times = librosa.frames_to_time(np.arange(rms.size), sr=sr)
    if rms.size:
        sec = np.maximum(1, round(duration_s))
        bins = np.clip(frame_times.astype(int), 0, sec - 1)
        energy = np.zeros(sec, dtype=np.float64)
        counts = np.zeros(sec, dtype=np.int64)
        np.add.at(energy, bins, rms)
        np.add.at(counts, bins, 1)
        counts[counts == 0] = 1
        energy /= counts
        peak = float(energy.max()) or 1.0
        energy_norm = energy / peak
    else:
        energy_norm = np.zeros(0, dtype=np.float64)

    sec_times = np.arange(energy_norm.size, dtype=np.float64)
    intro_end_s, outro_start_s = _energy_regions(sec_times, energy_norm)

    active = energy_norm[energy_norm > 0.10]
    rms_db = float(20.0 * np.log10(max(float(active.mean()), 1e-9))) if active.size else -120.0
    peak_db = float(20.0 * np.log10(max(float(np.abs(y).max()), 1e-9)))

    # Coverage denominator for beat quality: the sounding span (first..last
    # active second) — beats are expected only where the track is sounding.
    active_idx = np.flatnonzero(energy_norm > 0.10)
    active_span_s = float(active_idx[-1] - active_idx[0] + 1) if active_idx.size else 0.0
    _beat_times, _detected, beat_fields = _beat_grid_fields(y, sr, active_span_s)

    s_ratio, s_longest = silence_metrics(y, sr)
    eligible = bool(s_ratio <= MAX_SILENCE_RUN_FRACTION)

    # Unified Audio Continuity Contract (DJ-P2-CONTINUITY-001): the silence
    # picture the D12 gate will see is measured on the renderer's own decode
    # path — all channels at MASTER_SR — not on the 22.05 kHz mono analysis
    # mixdown used for BPM/key/energy above. Two different signal classes is
    # precisely why thresholds alone could not close the D10<->D12 gap.
    block_db = continuity.measure_master_blocks(path)
    cont = continuity.continuity_fields(
        block_db,
        {
            "duration_s": duration_s,
            "intro_end_s": intro_end_s,
            "outro_start_s": outro_start_s,
            "downbeat_times_s": beat_fields["downbeat_times_s"],
            "beat_times_s": beat_fields["beat_times_s"],
        },
    )

    return {
        "analysis_version": ANALYSIS_VERSION,
        "analysis_sr": sr,
        "duration_s": round(duration_s, 3),
        **beat_fields,
        "key": key,
        "key_confidence": key_conf,
        "energy_curve": [round(float(e), 4) for e in energy_norm.tolist()],
        "energy_mean": round(float(energy_norm.mean()), 4) if energy_norm.size else 0.0,
        "intro_end_s": round(intro_end_s, 3),
        "outro_start_s": round(outro_start_s, 3),
        "rms_db": round(rms_db, 2),
        "peak_db": round(peak_db, 2),
        "silence_run_ratio": round(s_ratio, 4),
        "longest_silence_run_s": round(s_longest, 3),
        "eligible": eligible,
        **cont,
        "exclusion_reason": (
            None if eligible else f"silence_run_ratio {s_ratio:.3f} > {MAX_SILENCE_RUN_FRACTION}"
        ),
    }


class AnalysisCache:
    """sqlite-backed analysis store keyed by recording_id (content hash)."""

    def __init__(self, db_path: str | Path = DEFAULT_CACHE):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                recording_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                work_id TEXT,
                title TEXT,
                status TEXT NOT NULL,
                error TEXT,
                analysis_version INTEGER,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, recording_id: str) -> dict | None:
        # Rows written by an older ANALYSIS_VERSION are treated as misses so
        # the next analyze run transparently recomputes them (schema upgrade).
        row = self._conn.execute(
            "SELECT payload FROM tracks WHERE recording_id = ? AND analysis_version = ?",
            (recording_id, ANALYSIS_VERSION),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        # Continuity contract staleness: an ok-status payload whose
        # continuity_version is missing or older than the contract's is a
        # cache miss — re-analyze, never silently reuse (DJ-P2-CONTINUITY-001).
        if payload.get("status") == "ok" and (
            payload.get("continuity_version") != continuity.CONTINUITY_VERSION
        ):
            return None
        return payload

    def put(self, record: dict) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO tracks
                (recording_id, path, work_id, title, status, error,
                 analysis_version, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["recording_id"],
                record["path"],
                record.get("work_id"),
                record.get("title"),
                record["status"],
                record.get("error"),
                ANALYSIS_VERSION,
                json.dumps(record, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._conn.commit()

    def all_records(self) -> list[dict]:
        rows = self._conn.execute("SELECT payload FROM tracks ORDER BY recording_id").fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()


def analyze_corpus(
    refs: list, cache_path: str | Path = DEFAULT_CACHE, force: bool = False
) -> list[dict]:
    """Analyze every ok-status TrackRef; one bad file never sinks the corpus.

    Cached records are returned verbatim (idempotent); ``force`` recomputes and
    overwrites — librosa's pipeline is deterministic so results are identical.
    """
    cache = AnalysisCache(cache_path)
    out: list[dict] = []
    try:
        for ref in refs:
            cached = None if force else cache.get(ref.recording_id)
            if cached is not None:
                out.append(cached)
                continue
            record = {
                "recording_id": ref.recording_id,
                "work_id": ref.work_id,
                "path": ref.path,
                "relpath": ref.relpath,
                "title": ref.title,
                "normalized_title": ref.normalized_title,
                "sha256": ref.sha256,
            }
            try:
                record.update(analyze_track(ref.path))
                record["status"] = "ok"
                record["error"] = None
            except Exception as exc:  # per-file isolation is the contract
                record["status"] = "error"
                record["error"] = f"{type(exc).__name__}: {exc}"
            cache.put(record)
            out.append(record)
    finally:
        cache.close()
    return out
