"""Audio analysis: BPM, key, downbeat grid, energy, intro/outro — cached in sqlite.

Per VERIFICATION.md, ``librosa.feature.tempo``/``beat_track`` reports 92-144 BPM
for Bolero where the true beat can sit an octave away. The fix applied here:

* the **median inter-beat interval (IBI)** of the detected beat grid is treated
  as the beat period of record — it comes from actual beat positions, not the
  tempo histogram peak;
* the raw librosa tempo is then *diagnosed* against it: ratio ≈2 -> librosa was
  in double-time, ratio ≈0.5 -> half-time, ≈1 -> agreement;
* both ``bpm_raw`` and ``bpm_corrected`` are stored, plus ``octave_diagnosis``.

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

ANALYSIS_VERSION = 3  # v3: + Unified Audio Continuity Contract payload fields
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


def analyze_track(path: str | Path) -> dict:
    """Analyze one audio file; raise on failure (caller records the error)."""
    y, sr = librosa.load(str(path), sr=ANALYSIS_SR, mono=True)
    duration_s = len(y) / float(sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    bpm_raw = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).astype(np.float64)

    if beat_times.size >= 2:
        ibis = np.diff(beat_times)
        median_ibi = float(np.median(ibis))
        bpm_ibi = 60.0 / median_ibi if median_ibi > 0 else float("nan")
        ibi_cv = float(ibis.std() / ibis.mean()) if ibis.mean() > 0 else float("nan")
    else:
        median_ibi, bpm_ibi, ibi_cv = float("nan"), float("nan"), float("nan")

    bpm_corrected, octave_diag = resolve_octave(bpm_raw, bpm_ibi)
    conf = tempo_confidence(ibi_cv, beat_times.size)

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

    downbeat_times = beat_times[::_BEATS_PER_BAR]

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
            "downbeat_times_s": downbeat_times.tolist(),
            "beat_times_s": beat_times.tolist(),
        },
    )

    return {
        "analysis_version": ANALYSIS_VERSION,
        "analysis_sr": sr,
        "duration_s": round(duration_s, 3),
        "bpm_raw": round(bpm_raw, 3),
        "bpm_ibi": round(bpm_ibi, 3) if np.isfinite(bpm_ibi) else None,
        "bpm_corrected": round(bpm_corrected, 3),
        "octave_diagnosis": octave_diag,
        "median_ibi_s": round(median_ibi, 6) if np.isfinite(median_ibi) else None,
        "ibi_cv": round(ibi_cv, 4) if np.isfinite(ibi_cv) else None,
        "tempo_confidence": conf,
        "n_beats": int(beat_times.size),
        "beat_times_s": [round(t, 5) for t in beat_times.tolist()],
        "downbeat_times_s": [round(t, 5) for t in downbeat_times.tolist()],
        "downbeat_estimated": True,
        "meter": "4/4-assumed",
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
