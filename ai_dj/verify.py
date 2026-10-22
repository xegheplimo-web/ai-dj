"""Independent verification: re-measure the master WAV against the plan (D6).

The engine's own timing claims are never the sole evidence. ``verify_master``
re-reads the WAV on disk and derives every check from the audio itself:

* duration vs the plan's expected timeline (sample tolerance);
* true peak <= -1 dBTP and zero clipped samples (re-measured from disk);
* no unplanned silence anywhere in the master (D12: a full-master continuity
  gate measured on 50 ms multi-channel RMS blocks, never a mono mixdown —
  two tiers: digital silence <= -90 dBFS for >= 0.5 s fails, low-level
  near-silence <= -60 dBFS for >= 2.0 s fails; only silence the renderer or
  the plan deliberately placed is exempt);
* a required artifact that is absent, corrupt, or undecodable is a measured
  artifact-integrity failure — FAIL (``pass: false``, exit 1), never a usage
  error and never INCONCLUSIVE;
* a required check that cannot be measured makes the verdict INCONCLUSIVE
  (``pass: null``, exit code 3), never PASS and never a fake number (D13);
* beat deviation in ``beat_crossfade`` windows, re-detected on the master via
  onset analysis and scored with ``ai_dj.timing.deviation_stats``;
* ``synthetic_alignment_check`` — two synthetic click tracks (beat positions
  known *before* processing) are pushed through the real ``render_plan`` code
  path and the overlap's IBI is measured by the independent harness
  ``ai_dj.timing``. This is the hard G2a-style contract for the render path;
  the real-music onset numbers are reported alongside as diagnostics.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from ai_dj import render as _render
from ai_dj.timing import (
    MASTER_SR,
    detect_beat_times,
    deviation_stats,
    ibi_ms,
    synthesize_click_track,
    write_wav_pcm16,
)

# Duration tolerance between plan and rendered file.
DURATION_TOL_S = 1.0
# Silence scan: non-overlapping 50 ms multi-channel RMS blocks (D12). Two
# tiers, both full-master gates: digital silence fails at a >= 0.5 s run,
# low-level near-silence at a >= 2.0 s run. Run lengths are compared in
# samples at the master's sample rate (>=, inclusive boundary).
SILENCE_BLOCK_S = 0.05
DIGITAL_SILENCE_DBFS = -90.0
DIGITAL_SILENCE_MIN_RUN_S = 0.5
LOW_LEVEL_DBFS = -60.0
LOW_LEVEL_MIN_RUN_S = 2.0
# 3 s short-term loudness window — diagnostic only, never a gate.
SHORT_TERM_WINDOW_S = 3.0
_DB_FLOOR = 1e-12  # -240 dBFS, keeps log10 finite on digital zeros


def _block_rms_dbfs(data: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, int]:
    """50 ms RMS blocks over **all** channels — never a mono mixdown (Rule A:
    anti-phase L/R cancels in a mean while carrying real audio on both).

    Returns (block_dbfs, block_mean_square, block_len_samples). einsum keeps
    the accumulation exact and avoids materializing a squared copy.
    """
    blk = max(1, int(SILENCE_BLOCK_S * sr))
    n = len(data) // blk
    if n == 0:
        return np.zeros(0), np.zeros(0), blk
    frames = data[: n * blk].reshape(n, blk, data.shape[1])
    ss = np.einsum("nbc,nbc->n", frames, frames)
    ms = ss / (blk * data.shape[1])
    return 10.0 * np.log10(np.maximum(ms, _DB_FLOOR * _DB_FLOOR)), ms, blk


def _channel_rms_dbfs(data: np.ndarray) -> list[float]:
    """Whole-file RMS per channel; a dead channel reads <= -90 dBFS."""
    if len(data) == 0:
        return []
    ss = np.einsum("nc,nc->c", data, data)
    rms = np.sqrt(np.maximum(ss / len(data), _DB_FLOOR * _DB_FLOOR))
    return [round(float(v), 2) for v in 20.0 * np.log10(rms)]


def _silent_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True in a boolean mask, as [start_blk, end_blk) pairs."""
    edges = np.flatnonzero(np.diff(np.concatenate(([False], mask, [False])).astype(np.int8)))
    return [(int(s), int(e)) for s, e in edges.reshape(-1, 2)]


def _allowed_silence_regions_s(plan: dict, master_duration_s: float) -> list[tuple[float, float]]:
    """Regions where silence is deliberate (D12): the renderer's own fade-in /
    fade-out edges (the exact sample ranges the renderer declares via
    ``MASTER_FADE_IN_S`` / ``MASTER_FADE_OUT_S`` — no arbitrary head/tail
    exemption), plus gaps the plan declares intentional via
    ``allowed_silent_gaps_s`` ([start_s, end_s) on the master timeline)."""
    regions = [
        (0.0, _render.MASTER_FADE_IN_S),
        (max(0.0, master_duration_s - _render.MASTER_FADE_OUT_S), master_duration_s),
    ]
    for gap in plan.get("allowed_silent_gaps_s") or []:
        try:
            regions.append((float(gap[0]), float(gap[1])))
        except (TypeError, IndexError, ValueError):
            continue
    return regions


def _tier_report(
    silent: np.ndarray,
    exempt: np.ndarray,
    blk: int,
    sr: int,
    *,
    dbfs: float,
    min_run_s: float,
) -> dict:
    """One silence tier: maximal runs of silent blocks outside exempt regions.

    A run fails when its length in samples reaches ``min_run_s * sr`` — the
    comparison is on sample counts at the master rate with ``>=`` so a run at
    exactly the minimum is a violation.
    """
    gate = silent & ~exempt
    runs = _silent_runs(gate)
    min_run_samples = round(min_run_s * sr)
    violating = [r for r in runs if (r[1] - r[0]) * blk >= min_run_samples]
    if runs:
        ws, we = max(runs, key=lambda r: r[1] - r[0])
        max_run_s = (we - ws) * blk / sr
        worst = [round(ws * blk / sr, 3), round(we * blk / sr, 3)]
    else:
        max_run_s, worst = 0.0, None
    return {
        "threshold_dbfs": dbfs,
        "min_run_s": min_run_s,
        "min_run_samples": min_run_samples,
        "silent_blocks": int(np.count_nonzero(silent)),
        "max_run_s": round(max_run_s, 3),
        "worst_region_s": worst,
        "violating_runs_s": [
            [round(s * blk / sr, 3), round(e * blk / sr, 3)] for s, e in violating
        ],
        "ok": not violating,
    }


def _silence_check(data: np.ndarray, plan: dict, sr: int) -> dict:
    """Two-tier full-master continuity gate on 50 ms multi-channel RMS blocks.

    A block is silent for the digital tier when its RMS <= -90 dBFS (>= 0.5 s
    run fails) and for the low-level tier when <= -60 dBFS (>= 2.0 s run
    fails). Blocks overlapping a deliberate region — the renderer's declared
    fade edges or plan-declared gaps — are masked out of the gate but still
    counted in the report. Per-channel RMS flags dead channels; the 3 s
    short-term-loudness dip and the transition-window scan stay diagnostic.
    """
    db, ms, blk = _block_rms_dbfs(data, sr)
    n_blocks = len(db)
    silent_digital = db <= DIGITAL_SILENCE_DBFS
    silent_low = db <= LOW_LEVEL_DBFS

    # Blocks overlapping an allowed region don't count toward either gate.
    exempt = np.zeros(n_blocks, dtype=bool)
    allowed = _allowed_silence_regions_s(plan, len(data) / sr)
    for lo_s, hi_s in allowed:
        lo = max(0, int(lo_s / SILENCE_BLOCK_S))
        hi = min(n_blocks, int(np.ceil(hi_s / SILENCE_BLOCK_S)))
        exempt[lo:hi] = True

    digital = _tier_report(
        silent_digital,
        exempt,
        blk,
        sr,
        dbfs=DIGITAL_SILENCE_DBFS,
        min_run_s=DIGITAL_SILENCE_MIN_RUN_S,
    )
    low = _tier_report(
        silent_low, exempt, blk, sr, dbfs=LOW_LEVEL_DBFS, min_run_s=LOW_LEVEL_MIN_RUN_S
    )

    # Per-channel RMS: a channel at <= -90 dBFS overall is flagged dead.
    channel_db = _channel_rms_dbfs(data)
    dead = [i for i, v in enumerate(channel_db) if v <= DIGITAL_SILENCE_DBFS]

    # 3 s short-term loudness (block-grid sliding window) — diagnostic only.
    win = round(SHORT_TERM_WINDOW_S / SILENCE_BLOCK_S)
    if n_blocks >= win:
        # float64 accumulation: float32 cumsum differences between ~1e2-prefix
        # sums catastrophically cancel a ~1e-7 quiet window into the floor.
        cum = np.concatenate(([0.0], np.cumsum(ms, dtype=np.float64)))
        st = (cum[win:] - cum[:-win]) / win
        k = int(np.argmin(st))
        st_min_dbfs = round(float(10.0 * np.log10(max(st[k], _DB_FLOOR * _DB_FLOOR))), 2)
        st_at_s = round(k * blk / sr, 3)
    elif n_blocks:
        st_min_dbfs = round(float(10.0 * np.log10(max(float(ms.mean()), _DB_FLOOR * _DB_FLOOR))), 2)
        st_at_s = 0.0
    else:
        st_min_dbfs, st_at_s = None, None

    tr_windows = [
        (int(t["overlap_start_sample"]), int(t["overlap_end_sample"]))
        for t in plan["transitions"]
        if t["duration_samples"] > 0
    ]
    in_tr = np.zeros(n_blocks, dtype=bool)
    for lo, hi in tr_windows:
        in_tr[lo // blk : min(n_blocks, hi // blk + 1)] = True
    tr_runs = _silent_runs(silent_digital & in_tr)
    if tr_runs:
        ts, te = max(tr_runs, key=lambda r: r[1] - r[0])
        tr_ms, tr_region = (
            (te - ts) * blk / sr * 1000.0,
            [
                round(ts * blk / sr, 3),
                round(te * blk / sr, 3),
            ],
        )
    else:
        tr_ms, tr_region = 0.0, None

    return {
        "block_s": SILENCE_BLOCK_S,
        "digital_silence": digital,
        "low_level": low,
        "channel_rms_dbfs": channel_db,
        "dead_channels": dead,
        "short_term_3s": {"min_dbfs": st_min_dbfs, "at_s": st_at_s},
        "max_silent_run_ms": round(digital["max_run_s"] * 1000.0, 1),
        "worst_region_s": digital["worst_region_s"],
        "silent_hops_total": digital["silent_blocks"],
        "allowed_silent_regions_s": [[round(a, 3), round(b, 3)] for a, b in allowed if b > a],
        "transition_windows": {
            "max_silent_run_in_transitions_ms": round(tr_ms, 1),
            "worst_region_s": tr_region,
        },
        "silence_ok": digital["ok"] and low["ok"],
    }


def _beat_checks(master: np.ndarray, plan: dict, sr: int) -> list[dict]:
    """Per-transition beat deviation re-measured on the master audio.

    Delegates to the renderer's grid-offset measurement: onsets detected on
    the master WAV are compared to the beat positions the plan claims — the
    claim is tested against the artifact, never trusted.
    """
    return _render.measure_master_beat_deviation(plan, master, sr)["beat_deviation"]


def synthetic_alignment_check(bpm_a: float = 100.0, bpm_b: float = 104.0) -> dict:
    """Push two synthetic click tracks through the real render path.

    Ground truth is known before processing (D6): track A plays at ``bpm_a``;
    track B is stretched to ``bpm_a`` inside a beat_crossfade. The IBI inside
    the overlap is detected by ``ai_dj.timing`` on the rendered file — the
    engine's own numbers are not consulted.
    """
    sr = MASTER_SR
    dur_s = 20.0
    overlap_s = 4.0
    stretch = bpm_a / bpm_b

    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        ya, _, _ = synthesize_click_track(bpm_a, dur_s, sr=sr)
        yb, _, _ = synthesize_click_track(bpm_b, dur_s, sr=sr)
        pa, pb = td / "a.wav", td / "b.wav"
        write_wav_pcm16(pa, ya, sr)
        write_wav_pcm16(pb, yb, sr)

        def beats(k: float, n: int) -> list[float]:
            step = 60.0 / k
            return [round(i * step, 5) for i in range(int(dur_s / step))]

        n_a = int(dur_s * sr)
        n_b = int(dur_s * sr)
        d = int(overlap_s * sr)
        plan = {
            "plan_version": 1,
            "set_name": "synthetic_alignment_check",
            "master_sr": sr,
            "tracks": [
                {
                    "index": 0,
                    "recording_id": "rec-syn-a",
                    "path": str(pa),
                    "cue_in_samples": 0,
                    "cue_out_samples": n_a,
                    "segment_master_samples": n_a,
                    "tempo_stretch": 1.0,
                    "gain_db": -6.0,
                    "median_ibi_s": 60.0 / bpm_a,
                    "beats_in_segment_s": beats(bpm_a, n_a),
                    "master_start_sample": 0,
                },
                {
                    "index": 1,
                    "recording_id": "rec-syn-b",
                    "path": str(pb),
                    "cue_in_samples": 0,
                    "cue_out_samples": n_b,
                    "segment_master_samples": round(n_b / stretch),
                    "tempo_stretch": stretch,
                    "gain_db": -6.0,
                    "median_ibi_s": 60.0 / bpm_b,
                    "beats_in_segment_s": beats(bpm_b, n_b),
                    "master_start_sample": n_a - d,
                },
            ],
            "transitions": [
                {
                    "index": 0,
                    "type": "beat_crossfade",
                    "duration_samples": d,
                    "overlap_start_sample": n_a - d,
                    "overlap_end_sample": n_a,
                    "target_bpm": bpm_a,
                }
            ],
            "timeline": {
                "total_samples": n_a - d + round(n_b / stretch),
                "total_duration_s": (n_a - d + round(n_b / stretch)) / sr,
            },
        }
        out_wav = td / "master.wav"
        report = _render.render_plan(plan, out_wav)
        disk, _sr = sf.read(str(out_wav), dtype="float32", always_2d=True)
        mono = disk.mean(axis=1)
        lo, hi = n_a - d, min(len(mono), n_a + 2 * sr)
        times = detect_beat_times(mono[lo:hi], sr)
        measured = ibi_ms(times)
        stats = deviation_stats(measured, 60000.0 / bpm_a)
        return {
            "probe": "synthetic_click_tracks_through_render_plan",
            "bpm_a": bpm_a,
            "bpm_b": bpm_b,
            "stretch_factor": round(stretch, 6),
            "detected_beats_in_overlap": int(times.size),
            "render_true_peak_dbtp": report["true_peak_dbtp"],
            **stats,
        }


def _required_check_names() -> list[str]:
    """Required checks in report order; ``artifact`` is the decode gate."""
    return [
        "artifact",
        "duration_matches_plan",
        "true_peak",
        "clipping",
        "silence",
        "samplerate",
        "synthetic_alignment",
    ]


def _artifact_fail_report(
    plan: dict,
    master_wav: Path,
    error: str,
    out_path: str | Path | None,
) -> dict:
    """FAIL report for a required artifact that is absent/corrupt/undecodable.

    Artifact integrity is a measured required check: the file was attempted
    and failed. That is FAIL (``pass: false``, exit 1) — not INCONCLUSIVE and
    not a usage error (Gap 2). Every downstream check is unmeasured.
    """
    report = {
        "plan_set_name": plan.get("set_name"),
        "master_wav": str(master_wav),
        "checks": {
            "artifact": {
                "path": str(master_wav),
                "exists": master_wav.exists(),
                "decodable": False,
                "error": error,
                "ok": False,
            }
        },
        "pass": False,
        "verdict": "FAIL",
        "unmeasured_checks": [c for c in _required_check_names() if c != "artifact"],
        "notes": (
            "required artifact is absent, corrupt, or undecodable — a measured "
            "artifact-integrity failure (FAIL), not a usage error and not "
            "INCONCLUSIVE."
        ),
    }
    if out_path:
        op = Path(out_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def verify_master(plan: dict, master_wav: str | Path, out_path: str | Path | None = None) -> dict:
    """Run all checks on the rendered master; return the verification report."""
    sr = int(plan.get("master_sr", MASTER_SR))
    master_wav = Path(master_wav)
    artifact_error = None
    if not master_wav.exists():
        artifact_error = f"master not found: {master_wav}"
    else:
        try:
            data, file_sr = sf.read(str(master_wav), dtype="float32", always_2d=True)
        except Exception as exc:  # corrupt / undecodable required artifact
            artifact_error = f"{type(exc).__name__}: {exc}"
    if artifact_error is not None:
        return _artifact_fail_report(plan, master_wav, artifact_error, out_path)

    actual_s = len(data) / file_sr
    planned_s = float(plan["timeline"]["total_duration_s"])
    duration_ok = abs(actual_s - planned_s) <= DURATION_TOL_S

    # True peak via ffmpeg ebur128 — a BS.1770 implementation independent of
    # this codebase, run on the artifact on disk.
    loud = _render.ffmpeg_true_peak_dbtp(master_wav)
    peak_db = loud["true_peak_dbtp"]
    # D11: no ffmpeg -> unmeasured (ok: None), not a failure and not a fake 0.
    peak_ok = None if peak_db is None else peak_db <= _render.TRUE_PEAK_LIMIT_DBTP
    clipping = int(np.count_nonzero(np.abs(data) >= _render.CLIP_THRESHOLD))

    silence = _silence_check(data, plan, file_sr)
    beat_checks = _beat_checks(data, plan, file_sr)
    measured = [b for b in beat_checks if b.get("beat_check") == "measured_on_master"]

    try:
        syn = synthetic_alignment_check()
    except Exception as exc:
        syn = {"probe": "synthetic_click_tracks", "error": f"{type(exc).__name__}: {exc}"}

    checks = {
        "artifact": {
            "path": str(master_wav),
            "exists": True,
            "decodable": True,
            "channels": data.shape[1],
            "frames": len(data),
            "ok": True,
        },
        "duration_matches_plan": {
            "planned_s": round(planned_s, 3),
            "actual_s": round(actual_s, 3),
            "tolerance_s": DURATION_TOL_S,
            "ok": bool(duration_ok),
        },
        "true_peak": {
            "dbtp": round(peak_db, 3) if peak_db is not None else None,
            "limit_dbtp": _render.TRUE_PEAK_LIMIT_DBTP,
            "measured_by": (
                "ffmpeg ebur128=peak=true"
                if loud.get("available")
                else "unavailable: ffmpeg not on PATH"
            ),
            "integrated_lufs": loud["integrated_lufs"],
            "ok": peak_ok,
        },
        "clipping": {"count": clipping, "ok": clipping == 0},
        "silence": silence,
        "beat_deviation_transitions": beat_checks,
        "beat_deviation_within_threshold": (
            all(b["within_threshold"] for b in measured) if measured else None
        ),
        "synthetic_alignment_check": syn,
        "samplerate_matches": {"expected": sr, "actual": int(file_sr), "ok": file_sr == sr},
    }
    # D13: a required check that could not be measured is INCONCLUSIVE, not a
    # pass and not a fabricated failure-free number. A measured failure is
    # never masked by an unmeasured one — FAIL beats INCONCLUSIVE.
    syn_threshold = syn.get("within_threshold")
    required_checks = {
        "artifact": True,
        "duration_matches_plan": bool(duration_ok),
        "true_peak": peak_ok,
        "clipping": clipping == 0,
        "silence": silence["silence_ok"],
        "samplerate": file_sr == sr,
        "synthetic_alignment": None if syn_threshold is None else bool(syn_threshold),
    }
    failed_checks = [name for name, ok in required_checks.items() if ok is False]
    unmeasured_checks = [name for name, ok in required_checks.items() if ok is None]
    if failed_checks:
        passed, verdict = False, "FAIL"
    elif unmeasured_checks:
        passed, verdict = None, "INCONCLUSIVE"
    else:
        passed, verdict = True, "PASS"
    report = {
        "plan_set_name": plan.get("set_name"),
        "master_wav": str(master_wav),
        "checks": checks,
        "pass": passed,
        "verdict": verdict,
        "unmeasured_checks": unmeasured_checks,
        "notes": (
            "synthetic_alignment_check is the ground-truth beat contract "
            "(timing.py on rendered click tracks); per-transition onset "
            "numbers on real music are diagnostics, not the gate. silence_ok "
            "is a two-tier full-master continuity gate on 50 ms multi-channel "
            "RMS blocks: <= -90 dBFS for >= 0.5 s or <= -60 dBFS for >= 2.0 s "
            "fails (D12); dead_channels and short_term_3s are diagnostics. An "
            "absent/corrupt/undecodable required artifact is a measured FAIL; "
            "a required check that cannot be measured yields INCONCLUSIVE, "
            "never PASS, and never masks a measured FAIL (D13)."
        ),
    }
    if out_path:
        op = Path(out_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
