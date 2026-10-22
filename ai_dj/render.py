"""Offline renderer: plan.json -> canonical master WAV at 48 kHz (D2, D9).

Everything is placed by **sample index** on one master timeline. No
``time.sleep()``, no wall-clock decisions: the plan's cue/overlap sample
positions are the contract.

* ``beat_crossfade`` — incoming track is time-stretched by FFmpeg
  ``rubberband`` to the outgoing track's BPM, then phase-aligned so its first
  beat inside the overlap lands exactly on the outgoing grid (deterministic
  shift computed from the analyzer's beat positions).
* ``crossfade`` — equal-power overlap, no tempo change.
* ``echo_out`` — the outgoing tail decays over the window; the incoming track
  enters clean over the last quarter.
* ``cut`` — butt splice with a 10 ms de-click merge.

Peak safety: the finished master is measured for true peak (4x FFT
oversampling); if it exceeds the headroom target the whole master is scaled
down and re-measured, so ``clipping_count`` is 0 by construction — and reported
honestly from the file on disk, not from memory.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from ai_dj.timing import MASTER_SR, MAX_DEVIATION_MS, deviation_stats

# Target/contract peak headroom (D7): true peak must stay <= -1 dBTP.
TRUE_PEAK_LIMIT_DBTP = -1.0
# Scale target when the limit is exceeded (margin absorbs re-measure error).
TRUE_PEAK_TARGET_DBTP = -1.2
# Sample value at/above which a sample counts as clipped.
CLIP_THRESHOLD = 1.0 - 1e-4
# De-click seam for hard cuts.
CUT_SMOOTH_S = 0.010
# Fades applied to the master edges for a clean start/end.
MASTER_FADE_IN_S = 0.05
MASTER_FADE_OUT_S = 3.0
# Extra timeline headroom so a slightly-longer stretched segment can't overflow.
TAIL_MARGIN_S = 30.0

# Share of an echo_out window where the incoming track fades in (clean entry).
ECHO_IN_FRACTION = 0.25


def load_audio_48k(path: str | Path) -> np.ndarray:
    """Decode any supported file to stereo float32 at MASTER_SR."""
    path = str(path)
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception:
        y, sr = librosa.load(path, sr=MASTER_SR, mono=False)
        data = np.atleast_2d(y).T if y.ndim == 1 else y.T
    if data.ndim == 1:
        data = data[:, None]
    if sr != MASTER_SR:
        data = librosa.resample(data.T.astype(np.float32), orig_sr=sr, target_sr=MASTER_SR).T
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    return np.ascontiguousarray(data, dtype=np.float32)


def rubberband_stretch(seg: np.ndarray, factor: float, workdir: Path) -> np.ndarray:
    """Time-stretch a stereo segment with FFmpeg rubberband (tempo=factor)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH (required for tempo stretch)")
    src = workdir / "stretch_in.wav"
    dst = workdir / "stretch_out.wav"
    sf.write(str(src), seg, MASTER_SR, subtype="PCM_24")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(src),
            "-af",
            f"rubberband=tempo={factor!r}",
            "-ar",
            str(MASTER_SR),
            str(dst),
        ],
        check=True,
        capture_output=True,
    )
    out, _sr = sf.read(str(dst), dtype="float32", always_2d=True)
    return np.ascontiguousarray(out, dtype=np.float32)


def _cos_down(n: int) -> np.ndarray:
    x = np.linspace(0.0, 1.0, max(1, n), dtype=np.float32)
    return np.cos(x * (math.pi / 2.0))


def _cos_up(n: int) -> np.ndarray:
    x = np.linspace(0.0, 1.0, max(1, n), dtype=np.float32)
    return np.sin(x * (math.pi / 2.0))


def _beat_shift_samples(plan: dict, i: int, sr: int) -> int:
    """Shift for track i+1 so its first post-cue beat lands on track i's grid.

    Deterministic: computed only from beat positions stored in the plan.
    Returns 0 when either grid is missing (then it's a plain overlap).
    """
    a = plan["tracks"][i]
    b = plan["tracks"][i + 1]
    tr = plan["transitions"][i]
    ibi_a = a.get("median_ibi_s")
    if not ibi_a or not a.get("beats_in_segment_s") or not b.get("beats_in_segment_s"):
        return 0
    ibi_a_samples = ibi_a * sr
    a_anchor = a["master_start_sample"] + a["beats_in_segment_s"][-1] * sr
    stretch = b.get("tempo_stretch") or 1.0
    b_first = b["beats_in_segment_s"][0] / stretch * sr
    p0 = tr["overlap_start_sample"] + b_first
    k = math.ceil((p0 - a_anchor) / ibi_a_samples - 1e-9)
    return round(a_anchor + k * ibi_a_samples - p0)


def _mix_fade(
    master: np.ndarray,
    seg: np.ndarray,
    start: int,
    win_start: int,
    win_end: int,
    *,
    fade_in: np.ndarray,
    fade_out: np.ndarray,
) -> int:
    """Place ``seg`` at ``start``; equal-power merge inside [win_start, win_end)."""
    head = win_start - start
    if head > 0:
        master[start:win_start] += seg[:head]
    n_win = min(win_end - win_start, len(seg) - head)
    if n_win > 0:
        master[win_start : win_start + n_win] *= fade_out[:n_win, None]
        master[win_start : win_start + n_win] += seg[head : head + n_win] * fade_in[:n_win, None]
    rest = head + n_win
    end = start + len(seg)
    if rest < len(seg):
        master[win_start + n_win : end] += seg[rest:]
    return end


def _prepare_segment(t: dict, workdir: Path) -> np.ndarray:
    """Decode, cue-slice, stretch and gain one planned track segment."""
    audio = load_audio_48k(t["path"])
    seg = audio[t["cue_in_samples"] : t["cue_out_samples"]].copy()
    stretch = float(t.get("tempo_stretch") or 1.0)
    if abs(stretch - 1.0) > 1e-6:
        seg = rubberband_stretch(seg, stretch, workdir)
    gain = float(t.get("gain_db") or 0.0)
    if abs(gain) > 1e-6:
        seg = seg * float(10.0 ** (gain / 20.0))
    return seg


def _place_transition(
    master: np.ndarray,
    seg: np.ndarray,
    plan: dict,
    i: int,
    start: int,
    *,
    sr: int,
) -> tuple[int, dict]:
    """Mix segment i into ``master`` per transition i-1; return (end, report)."""
    tr = plan["transitions"][i - 1]
    ttype = tr["type"]
    ov_start = tr["overlap_start_sample"]
    ov_end = tr["overlap_end_sample"]
    base = {
        "index": i - 1,
        "type": ttype,
        "overlap_planned_samples": tr["duration_samples"],
    }

    if ttype == "cut":
        # 10 ms de-click merge centred on the splice: A's tail fades out across
        # [lo, hi) while B's head fades in, then B's body continues.
        half = int(CUT_SMOOTH_S * sr / 2)
        lo, hi = max(0, ov_end - half), ov_end + half
        n_sm = hi - lo
        master[lo:hi] *= _cos_down(n_sm)[:, None]
        m = min(n_sm, len(seg))
        master[lo : lo + m] += seg[:m] * _cos_up(n_sm)[:m, None]
        master[hi : lo + len(seg)] += seg[m:]
        return lo + len(seg), {**base, "window": [lo, hi], "track_start_sample": lo}

    if ttype == "echo_out":
        n_win = ov_end - ov_start
        # Outgoing tail decays over the whole window; the incoming track enters
        # clean over the last quarter.
        master[ov_start:ov_end] *= _cos_down(n_win)[:, None] ** 1.5
        nf = max(1, int(ECHO_IN_FRACTION * n_win))
        b_start = ov_end - nf
        master[b_start:ov_end] += seg[:nf] * _cos_up(nf)[:, None]
        master[ov_end : b_start + len(seg)] += seg[nf:]
        return b_start + len(seg), {
            **base,
            "window": [ov_start, ov_end],
            "track_start_sample": b_start,
        }

    # crossfade / beat_crossfade: equal-power merge over the window.
    win_start = start
    if ttype == "beat_crossfade":
        win_start = min(ov_start + _beat_shift_samples(plan, i - 1, sr), ov_end - 1)
    n_win = max(1, ov_end - win_start)
    end = _mix_fade(
        master,
        seg,
        win_start,
        win_start,
        ov_end,
        fade_in=_cos_up(n_win),
        fade_out=_cos_down(n_win),
    )
    return end, {
        **base,
        "window": [win_start, ov_end],
        "track_start_sample": win_start,
    }


def render_plan(plan: dict, out_wav: str | Path, report_path: str | Path | None = None) -> dict:
    """Render the plan to a master WAV; return (and optionally write) a report."""
    sr = int(plan.get("master_sr", MASTER_SR))
    total = plan["timeline"]["total_samples"] + int(TAIL_MARGIN_S * sr)
    master = np.zeros((total, 2), dtype=np.float32)
    segments: list[dict] = []
    tr_report: list[dict] = []
    actual_end = 0

    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        for i, t in enumerate(plan["tracks"]):
            seg = _prepare_segment(t, workdir)
            segments.append(
                {
                    "index": i,
                    "recording_id": t["recording_id"],
                    "tempo_stretch": float(t.get("tempo_stretch") or 1.0),
                    "planned_samples": t["segment_master_samples"],
                    "actual_samples": len(seg),
                }
            )
            if i == 0:
                start = t["master_start_sample"]
                master[start : start + len(seg)] += seg
                actual_end = start + len(seg)
                continue
            end, entry = _place_transition(master, seg, plan, i, t["master_start_sample"], sr=sr)
            tr_report.append(entry)
            actual_end = max(actual_end, end)

    master = master[:actual_end]
    fi = int(MASTER_FADE_IN_S * sr)
    master[:fi] *= _cos_up(fi)[:, None]
    fo = int(MASTER_FADE_OUT_S * sr)
    master[-fo:] *= _cos_down(fo)[:, None]

    # Pre-scale by sample peak (cheap); the authoritative true-peak check below
    # is ffmpeg ebur128 on the written file — catches inter-sample overshoot.
    sample_peak_db = 20.0 * math.log10(max(float(np.abs(master).max()), 1e-9))
    scale_db = 0.0
    if sample_peak_db > TRUE_PEAK_TARGET_DBTP:
        scale_db = TRUE_PEAK_TARGET_DBTP - sample_peak_db
        master *= float(10.0 ** (scale_db / 20.0))

    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), master, sr, subtype="PCM_24")

    loud = ffmpeg_true_peak_dbtp(out)
    if loud["true_peak_dbtp"] > TRUE_PEAK_LIMIT_DBTP:
        extra = TRUE_PEAK_TARGET_DBTP - loud["true_peak_dbtp"]
        scale_db += extra
        master *= float(10.0 ** (extra / 20.0))
        sf.write(str(out), master, sr, subtype="PCM_24")
        loud = ffmpeg_true_peak_dbtp(out)

    # Re-measure from the file on disk — evidence must come from the artifact.
    disk, _ = sf.read(str(out), dtype="float32", always_2d=True)
    clipping = int(np.count_nonzero(np.abs(disk) >= CLIP_THRESHOLD))

    report = {
        "out_wav": str(out),
        "master_sr": sr,
        "total_samples_planned": plan["timeline"]["total_samples"],
        "total_samples_actual": disk.shape[0],
        "duration_s": round(disk.shape[0] / sr, 3),
        "true_peak_dbtp": loud["true_peak_dbtp"],
        "integrated_lufs": loud["integrated_lufs"],
        "loudness_range_lu": loud["loudness_range_lu"],
        "true_peak_measured_by": "ffmpeg ebur128=peak=true",
        "peak_limit_dbtp": TRUE_PEAK_LIMIT_DBTP,
        "peak_scale_applied_db": round(scale_db, 3),
        "clipping_count": clipping,
        "underflows": 0,
        "segments": segments,
        "transitions": tr_report,
        "transitions_planned": len(plan["transitions"]),
    }
    report.update(measure_master_beat_deviation(plan, disk, sr))

    if report_path:
        rp = Path(report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def ffmpeg_true_peak_dbtp(path: str | Path) -> dict:
    """True peak + loudness of a WAV file via ffmpeg ``ebur128=peak=true``.

    Returns ``{true_peak_dbtp, integrated_lufs, loudness_range_lu}`` parsed from
    the ebur128 summary — an implementation independent of this codebase.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH (required for true-peak check)")
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    text = proc.stderr + proc.stdout
    peak_m = re.search(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+)\s*dB", text)
    i_m = re.search(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+)\s*LUFS", text)
    lra_m = re.search(r"Loudness range:\s*\n\s*LRA:\s*(-?[\d.]+)\s*LU", text)
    if not peak_m:
        raise RuntimeError(f"could not parse ebur128 output for {path}")
    return {
        "true_peak_dbtp": float(peak_m.group(1)),
        "integrated_lufs": float(i_m.group(1)) if i_m else None,
        "loudness_range_lu": float(lra_m.group(1)) if lra_m else None,
    }


def _planned_grid_samples(plan: dict, tr: dict, sr: int) -> np.ndarray:
    """Beat positions the plan claims inside a transition's overlap window.

    The outgoing track's grid continues through the overlap at its own IBI;
    the incoming track was phase-shifted to land on the same grid. The anchor
    is the outgoing track's last detected beat, projected forward.
    """
    a = plan["tracks"][tr.get("from_track", tr["index"])]
    beats_a = a.get("beats_in_segment_s") or []
    if not beats_a or not tr.get("target_bpm"):
        return np.zeros(0)
    ibi_samp = 60.0 / float(tr["target_bpm"]) * sr
    anchor = a["master_start_sample"] + beats_a[-1] * sr
    lo, hi = tr["overlap_start_sample"], tr["overlap_end_sample"]
    k0 = math.ceil((lo - anchor) / ibi_samp - 1e-9)
    k1 = math.floor((hi - anchor) / ibi_samp + 1e-9)
    if k1 < k0:
        return np.zeros(0)
    return anchor + np.arange(k0, k1 + 1) * ibi_samp


def measure_master_beat_deviation(plan: dict, master: np.ndarray, sr: int) -> dict:
    """Deviation of detected onsets from the plan's beat grid, on the master.

    For each ``beat_crossfade`` window the plan claims beats at specific sample
    positions; this detects onsets in the rendered audio and reports how far
    the nearest onset sits from each claimed grid point. Measured on the
    artifact, not on engine logs (D6). Non-aligned transition types are
    reported as ``not_beat_aligned`` — no claim is made for them.
    """
    results = []
    mono = master.mean(axis=1) if master.ndim == 2 else master
    for tr in plan["transitions"]:
        entry = {"index": tr["index"], "type": tr["type"]}
        if tr["type"] != "beat_crossfade" or not tr.get("target_bpm"):
            results.append({**entry, "beat_check": "not_beat_aligned"})
            continue
        grid = _planned_grid_samples(plan, tr, sr)
        if grid.size == 0:
            results.append({**entry, "beat_check": "no_grid"})
            continue
        lo = max(0, tr["overlap_start_sample"])
        hi = min(len(mono), tr["overlap_end_sample"] + int(0.5 * sr))
        try:
            env = librosa.onset.onset_strength(y=mono[lo:hi], sr=sr)
            onsets = librosa.onset.onset_detect(
                onset_envelope=env, sr=sr, units="samples", backtrack=True
            )
        except Exception as exc:
            results.append(
                {
                    **entry,
                    "beat_check": "measure_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if onsets.size < 3:
            results.append(
                {**entry, "beat_check": "insufficient_onsets", "onsets": int(onsets.size)}
            )
            continue
        ibi_samp = 60.0 / float(tr["target_bpm"]) * sr
        match_tol = ibi_samp * 0.25
        devs = np.abs(onsets[None, :] - (grid - lo)[:, None]).min(axis=1)
        matched = devs <= match_tol
        dev_ms = devs[matched] / sr * 1000.0
        measured_ibi = float(np.median(np.diff(onsets / sr)) * 1000.0)
        ibi_stat = deviation_stats(measured_ibi, 60000.0 / float(tr["target_bpm"]))
        within = (
            bool(matched.mean() >= 0.5)
            and dev_ms.size > 0
            and float(np.percentile(dev_ms, 95)) <= MAX_DEVIATION_MS
        )
        results.append(
            {
                **entry,
                "beat_check": "measured_on_master",
                "grid_points": int(grid.size),
                "onsets": int(onsets.size),
                "matched": int(np.count_nonzero(matched)),
                "coverage": round(float(matched.mean()), 3),
                "median_abs_dev_ms": round(float(np.median(dev_ms)), 3) if dev_ms.size else None,
                "p95_abs_dev_ms": round(float(np.percentile(dev_ms, 95)), 3)
                if dev_ms.size
                else None,
                "ibi_diagnostic_ms": round(ibi_stat["deviation_ms"], 3),
                "within_threshold": within,
            }
        )
    within_list = [r for r in results if r.get("beat_check") == "measured_on_master"]
    return {
        "beat_deviation": results,
        "beat_deviation_within_threshold": all(r["within_threshold"] for r in within_list)
        if within_list
        else None,
        "beat_deviation_note": (
            "coverage/p95 = nearest-onset distance to the planned grid; the "
            "hard 20 ms contract is verified on synthetic click tracks "
            "(timing.py); real-music onset offsets are diagnostics."
        ),
    }
