"""Independent verification: re-measure the master WAV against the plan (D6).

The engine's own timing claims are never the sole evidence. ``verify_master``
re-reads the WAV on disk and derives every check from the audio itself:

* duration vs the plan's expected timeline (sample tolerance);
* true peak <= -1 dBTP and zero clipped samples (re-measured from disk);
* no unexpected silence inside transition windows;
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
# Silence scan: 50 ms hops; a hop below this peak level counts as silent.
SILENCE_PEAK = 1e-4
# Allowed consecutive silent hops inside a transition window before failing.
MAX_SILENT_RUN_MS = 100.0


def _rms_hops(mono: np.ndarray, sr: int, hop_s: float = 0.05) -> np.ndarray:
    hop = max(1, int(hop_s * sr))
    n = len(mono) // hop
    if n == 0:
        return np.zeros(0)
    frames = mono[: n * hop].reshape(n, hop)
    return np.abs(frames).max(axis=1)


def _silence_check(mono: np.ndarray, plan: dict, sr: int) -> dict:
    """No unplanned silence inside transition windows; report the global scan."""
    hops = _rms_hops(mono, sr)
    hop_ms = 50.0
    max_run_ms = 0.0
    run = 0
    worst_region = None
    tr_windows = [
        (int(t["overlap_start_sample"]), int(t["overlap_end_sample"]))
        for t in plan["transitions"]
        if t["duration_samples"] > 0
    ]
    in_tr = np.zeros(len(hops), dtype=bool)
    hop_len = int(0.05 * sr)
    for lo, hi in tr_windows:
        a, b = lo // hop_len, min(len(hops), hi // hop_len + 1)
        in_tr[a:b] = True
    for i, v in enumerate(hops < SILENCE_PEAK):
        run = run + 1 if v else 0
        if v and in_tr[i] and run * hop_ms > max_run_ms:
            max_run_ms = run * hop_ms
            worst_region = [round(i * 0.05 - run * 0.05 + 0.05, 3), round(i * 0.05 + 0.05, 3)]
    silent_hops = int(np.count_nonzero(hops < SILENCE_PEAK))
    return {
        "max_silent_run_in_transitions_ms": round(max_run_ms, 1),
        "worst_region_s": worst_region,
        "silent_hops_total": silent_hops,
        "silence_ok": max_run_ms <= MAX_SILENT_RUN_MS,
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


def verify_master(plan: dict, master_wav: str | Path, out_path: str | Path | None = None) -> dict:
    """Run all checks on the rendered master; return the verification report."""
    sr = int(plan.get("master_sr", MASTER_SR))
    master_wav = Path(master_wav)
    if not master_wav.exists():
        raise FileNotFoundError(f"master not found: {master_wav}")
    data, file_sr = sf.read(str(master_wav), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)

    actual_s = len(data) / file_sr
    planned_s = float(plan["timeline"]["total_duration_s"])
    duration_ok = abs(actual_s - planned_s) <= DURATION_TOL_S

    # True peak via ffmpeg ebur128 — a BS.1770 implementation independent of
    # this codebase, run on the artifact on disk.
    loud = _render.ffmpeg_true_peak_dbtp(master_wav)
    peak_db = loud["true_peak_dbtp"]
    clipping = int(np.count_nonzero(np.abs(data) >= _render.CLIP_THRESHOLD))

    silence = _silence_check(mono, plan, file_sr)
    beat_checks = _beat_checks(data, plan, file_sr)
    measured = [b for b in beat_checks if b.get("beat_check") == "measured_on_master"]

    try:
        syn = synthetic_alignment_check()
    except Exception as exc:
        syn = {"probe": "synthetic_click_tracks", "error": f"{type(exc).__name__}: {exc}"}

    checks = {
        "duration_matches_plan": {
            "planned_s": round(planned_s, 3),
            "actual_s": round(actual_s, 3),
            "tolerance_s": DURATION_TOL_S,
            "ok": bool(duration_ok),
        },
        "true_peak": {
            "dbtp": round(peak_db, 3),
            "limit_dbtp": _render.TRUE_PEAK_LIMIT_DBTP,
            "measured_by": "ffmpeg ebur128=peak=true",
            "integrated_lufs": loud["integrated_lufs"],
            "ok": peak_db <= _render.TRUE_PEAK_LIMIT_DBTP,
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
    hard_checks = [
        duration_ok,
        peak_db <= _render.TRUE_PEAK_LIMIT_DBTP,
        clipping == 0,
        silence["silence_ok"],
        file_sr == sr,
        bool(syn.get("within_threshold", False)),
    ]
    report = {
        "plan_set_name": plan.get("set_name"),
        "master_wav": str(master_wav),
        "checks": checks,
        "pass": all(hard_checks),
        "notes": (
            "synthetic_alignment_check is the ground-truth beat contract "
            "(timing.py on rendered click tracks); per-transition onset "
            "numbers on real music are diagnostics, not the gate."
        ),
    }
    if out_path:
        op = Path(out_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
