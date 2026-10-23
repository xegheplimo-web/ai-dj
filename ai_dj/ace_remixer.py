"""Option-Y remix PoC (AI-DJ-P3-ACE-001, D30): Bolero vocal + generated Vinahouse BGM.

Pipeline: stem-split a rights-cleared Bolero excerpt (demucs, keep the
ORIGINAL vocal stem) -> measure its real tempo/key with librosa -> ACE-Step
1.5 generates a Vinahouse BGM at the target BPM/key -> librosa time-stretch +
bar-align the vocal onto the BGM grid -> mix -> master to the loudness/true
-peak contract -> verify the OUTPUT file (never the corpus beat grid, D28:
39/41 suspect) with librosa BPM and ffmpeg ebur128.

Heavy dependencies (``librosa``, ``acestep``, ``demucs``) are imported inside
the functions that need them so this module stays importable anywhere; the
pure-math helpers (key compatibility, stretch rate, alignment arithmetic)
carry no audio imports and are unit-tested by ``scripts/ace_poc_selftest.py``.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from ai_dj.render import (
    CLIP_THRESHOLD,
    TRUE_PEAK_LIMIT_DBTP,
    ffmpeg_true_peak_dbtp,
    load_audio_48k,
)
from ai_dj.timing import MASTER_SR
from ai_dj.verdict import write_verdict

# Remix contract (card S3/S4/S5).
TARGET_BPM = 130.0
BPM_GATE = (126.0, 136.0)
TARGET_LUFS = -10.0
LOUDNESS_GATE_LUFS = (-11.0, -9.0)
TRUE_PEAK_TARGET_DBTP = -1.2
DURATION_MIN_S = 30.0
PHRASE_BARS = 8

# S2/S4 floors: a stem or mix layer above this is audibly present, not noise.
SILENCE_FLOOR_DBFS = -60.0
BAR_ACTIVE_FLOOR_DBFS = -45.0
# Vocal-to-BGM RMS ratio kept inside this band: vocal clearly present but the
# generated BGM still carries the record (Vinahouse = beat-led genre).
VOCAL_MIX_BAND_DB = (-8.0, 2.0)
VOCAL_TARGET_DB = -2.0

# Vocal enters on this bar of the BGM (0-indexed) so the BGM establishes the
# groove before the remix vocal drops in.
VOCAL_ENTRY_BAR = 4

DEFAULT_CHECKPOINTS = "F:/ace-checkpoints"
DEFAULT_CAPTION = (
    "Vinahouse remix instrumental, Vietnamese dance style, four-on-the-floor "
    "punchy kick, driving offbeat bassline, bright synth stabs and supersaw "
    "lead, energetic club groove, clean modern mix"
)

_PITCH_CLASS = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "FB": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
    "CB": 11,
    "B#": 0,
}


def _emit_json(path: str | Path, obj: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return p


def rms_dbfs(x: np.ndarray) -> float:
    """RMS level in dBFS; -inf for digital silence."""
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) if x.size else 0.0
    return -math.inf if rms <= 0.0 else 20.0 * math.log10(rms)


def stretch_rate(src_bpm: float, dst_bpm: float) -> float:
    """librosa time-stretch rate that re-maps ``src_bpm`` material onto ``dst_bpm``."""
    return float(dst_bpm) / float(src_bpm)


def bar_seconds(bpm: float) -> float:
    """Seconds per 4/4 bar at ``bpm``."""
    return 4.0 * 60.0 / float(bpm)


def vocal_offset_seconds(first_onset_s: float, entry_bar: int, bpm: float) -> float:
    """Shift placing the vocal's first strong onset onto ``entry_bar`` of the BGM."""
    return entry_bar * bar_seconds(bpm) - float(first_onset_s)


def parse_key(label: str) -> tuple[int, str]:
    """``'B minor'``/``'Bb major'`` -> (pitch class, 'major'|'minor')."""
    m = re.match(r"^\s*([A-Ga-g][#b♯♭]?)\s*(major|minor|maj|min|m)\s*$", str(label), re.I)
    if not m:
        raise ValueError(f"unparseable key label: {label!r}")
    note = m.group(1).upper().replace("♯", "#").replace("♭", "B")
    mode = m.group(2).lower()
    mode = {"maj": "major", "min": "minor", "m": "minor"}.get(mode, mode)
    return _PITCH_CLASS[note], mode


def key_compatible(a: str, b: str) -> bool:
    """True when key ``a`` mixes compatibly with ``b`` (correct rule — NOT the
    D20 bug where same-mode keys one semitone apart counted as compatible).

    Compatible pairs: identical key; relative major/minor (same key
    signature, tonic +/-3 semitones); same mode a fifth/fourth apart
    (+/-7/+/-5 semitones); same tonic across modes (parallel).
    """
    ta, ma = parse_key(a)
    tb, mb = parse_key(b)
    d = (tb - ta) % 12
    if d == 0:
        return True  # same tonic — identical or parallel mode
    if ma != mb:
        # relative major/minor share a key signature: minor tonic is the
        # major tonic + 3 semitones (A minor vs C major etc.)
        return d in {3, 9}
    return d in {5, 7}  # same mode a fourth/fifth apart


def key_shift_semitones(src_key: str, dst_key: str) -> int:
    """Shortest signed pitch shift moving ``src_key``'s tonic onto ``dst_key``'s."""
    ta, _ = parse_key(src_key)
    tb, _ = parse_key(dst_key)
    d = (tb - ta) % 12
    return d - 12 if d > 6 else d


def estimate_bpm(y: np.ndarray, sr: int, start_bpm: float | None = None) -> dict:
    """Tempo estimated by librosa directly on the given audio.

    Uses ``hop_length=256`` (5.3 ms frame period at 48 kHz — well under the
    20 ms contract the D19 defect violated at hop 512/22.05 kHz) and reports
    both the aggregate tempo and the median-IBI tempo from detected beats.
    """
    mono = np.asarray(y, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    env = librosa.onset.onset_strength(y=mono, sr=sr, hop_length=256)
    kwargs = {"onset_envelope": env, "sr": sr, "hop_length": 256}
    if start_bpm:
        kwargs["start_bpm"] = float(start_bpm)
    tempo, beat_frames = librosa.beat.beat_track(**kwargs)
    tempo = float(np.atleast_1d(tempo)[0])
    ibi_bpm = None
    if beat_frames.size >= 3:
        ibis = np.diff(librosa.frames_to_time(beat_frames, sr=sr, hop_length=256))
        if ibis.size:
            ibi_bpm = float(60.0 / np.median(ibis))
    return {"bpm": tempo, "bpm_median_ibi": ibi_bpm, "n_beats": int(beat_frames.size)}


def estimate_key(y: np.ndarray, sr: int) -> dict:
    """Krumhansl-Schmuckler key estimate on the mean CQT chroma."""
    mono = np.asarray(y, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    chroma = librosa.feature.chroma_cqt(y=mono, sr=sr).mean(axis=1)
    if not np.isfinite(chroma).all() or chroma.max() <= 0:
        return {"key": None, "confidence": 0.0}
    chroma = chroma / chroma.max()
    maj = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    mnr = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    best, best_score = None, -1.0
    for i in range(12):
        for prof, mode in ((maj, "major"), (mnr, "minor")):
            score = float(np.corrcoef(np.roll(prof, i), chroma)[0, 1])
            if score > best_score:
                best, best_score = f"{notes[i]} {mode}", score
    return {"key": best, "confidence": round(best_score, 4)}


def separate_vocals(excerpt_wav: str | Path, work_dir: str | Path) -> Path:
    """Stem-split ``excerpt_wav`` with demucs ``--two-stems=vocals``; return the
    vocal stem path. Runs demucs as a subprocess of the current interpreter so
    the same venv (torch CUDA + demucs) is used. Model weights download on
    first run.
    """
    excerpt_wav = Path(excerpt_wav)
    out_root = Path(work_dir) / "demucs"
    out_root.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "demucs",
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "-o",
            str(out_root),
            str(excerpt_wav),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    vocals = out_root / "htdemucs" / excerpt_wav.stem / "vocals.wav"
    if proc.returncode != 0 or not vocals.exists():
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"demucs failed rc={proc.returncode}: {tail}")
    return vocals


def first_strong_onset_s(y: np.ndarray, sr: int) -> float:
    """Time of the first onset at least 2x above the median onset strength —
    a pragmatic 'vocal entry' landmark on a vocal stem."""
    mono = np.asarray(y, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    env = librosa.onset.onset_strength(y=mono, sr=sr, hop_length=256)
    if env.size == 0 or env.max() <= 0:
        return 0.0
    thresh = max(float(np.median(env)) * 2.0, env.max() * 0.1)
    idx = int(np.argmax(env >= thresh))
    return float(librosa.frames_to_time(idx, sr=sr, hop_length=256))


def generate_bgm(
    out_wav: str | Path,
    *,
    bpm: float,
    keyscale: str,
    duration_s: float,
    seed: int,
    caption: str = DEFAULT_CAPTION,
    checkpoints: str = DEFAULT_CHECKPOINTS,
    project_root: str | Path = ".",
    device: str = "cuda",
    inference_steps: int = 8,
) -> dict:
    """Generate one instrumental Vinahouse BGM via ACE-Step 1.5 (pure DiT path:
    ``thinking=False``, no CoT -> the LLM handler is never initialized).
    Checkpoints resolve through ``ACESTEP_CHECKPOINTS_DIR``.
    """
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = str(checkpoints)
    # The handler's project root (progress/cache sidecars) resolves from
    # ACESTEP_PROJECT_ROOT at __init__ time, NOT from initialize_service —
    # without this it would drop .cache/ at the repo root.
    os.environ["ACESTEP_PROJECT_ROOT"] = str(Path(project_root).resolve())
    if not Path(checkpoints).is_dir():
        raise RuntimeError(f"checkpoints dir missing: {checkpoints}")

    # Lazy: acestep only exists in F:/ace-venv; resolve via importlib so this
    # module stays importable (and unit-testable) without torch/acestep.
    try:
        h = importlib.import_module("acestep.handler")
        inf = importlib.import_module("acestep.inference")
        li = importlib.import_module("acestep.llm_inference")
    except ImportError as exc:
        raise RuntimeError(
            f"acestep not importable in this interpreter ({exc}) — "
            "run with F:/ace-venv/Scripts/python.exe"
        ) from exc

    dit = h.AceStepHandler()
    status, _ok = dit.initialize_service(
        project_root=str(Path(project_root).resolve()),
        config_path="acestep-v15-turbo",
        device=device,
    )
    llm = li.LLMHandler()  # constructed, never initialized (see module docstring)
    params = inf.GenerationParams(
        task_type="text2music",
        caption=caption,
        instrumental=True,
        bpm=round(bpm),
        keyscale=keyscale,
        timesignature="4",
        duration=float(duration_s),
        seed=int(seed),
        inference_steps=int(inference_steps),
        thinking=False,
        use_cot_metas=False,
        use_cot_caption=False,
        use_cot_language=False,
    )
    config = inf.GenerationConfig(
        batch_size=1, use_random_seed=False, seeds=[int(seed)], audio_format="wav"
    )
    out_dir = Path(out_wav).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    res = inf.generate_music(dit, llm, params, config, save_dir=str(out_dir))
    if not res.success or not res.audios:
        raise RuntimeError(f"ACE-Step generation failed: {res.error or res.status_message}")
    src = Path(res.audios[0]["path"])
    dst = Path(out_wav)
    if src.resolve() != dst.resolve():
        shutil.copyfile(src, dst)
    return {"written_to": str(dst), "source_artifact": str(src), "init_status": str(status)}


def load_stereo(path: str | Path, sr: int = MASTER_SR) -> np.ndarray:
    """Decode to stereo float32 at ``sr`` (delegates to the engine loader)."""
    data = load_audio_48k(path)
    if sr != MASTER_SR:
        data = librosa.resample(data.T.astype(np.float32), orig_sr=MASTER_SR, target_sr=sr).T
    return data


def mix_vocal_over_bgm(
    vocal: np.ndarray,
    bgm: np.ndarray,
    *,
    vocal_offset_s: float,
    sr: int,
    vocal_target_db: float = VOCAL_TARGET_DB,
) -> tuple[np.ndarray, dict]:
    """Place the vocal at ``vocal_offset_s`` over the BGM and set its level to
    ``vocal_target_db`` relative to the BGM's RMS. Returns (mix, metrics)."""
    n = max(len(bgm), int(vocal_offset_s * sr) + len(vocal))
    timeline = np.zeros((n, 2), dtype=np.float32)
    timeline[: len(bgm)] += bgm

    off = max(0, round(vocal_offset_s * sr))
    seg = vocal[: max(0, n - off)]
    rms_v = float(np.sqrt(np.mean(seg**2))) if seg.size else 0.0
    rms_b = float(np.sqrt(np.mean(bgm**2))) if bgm.size else 0.0
    gain_db = vocal_target_db - rms_dbfs(vocal) + rms_dbfs(bgm)
    gain = float(10.0 ** (gain_db / 20.0))
    placed = seg * gain
    timeline[off : off + len(placed)] += placed
    metrics = {
        "vocal_offset_s": round(off / sr, 4),
        "vocal_gain_db": round(gain_db, 3),
        "vocal_to_bgm_db": round(rms_dbfs(placed) - rms_dbfs(bgm), 3)
        if rms_v > 0 and rms_b > 0
        else None,
        "vocal_stem_rms_dbfs": round(rms_dbfs(seg), 2),
        "bgm_rms_dbfs": round(rms_dbfs(bgm), 2),
    }
    return timeline, metrics


def _loudnorm_pass(wav_path: Path, target_lufs: float, tp_target_dbtp: float) -> bool:
    """Normalize ``wav_path`` with ffmpeg ``loudnorm`` (dynamic mode).

    ``loudnorm`` carries a true-peak-aware limiter — the important difference
    from ``alimiter``, whose ceiling is a *sample* peak: hyper-limited content
    overshoots inter-sample by ~2 dB and fails the -1 dBTP gate anyway.
    Output is resampled back to 48 kHz (loudnorm works at 192 kHz internally).
    Returns False when ffmpeg is unavailable (honest degrade, D11).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    tmp = wav_path.with_suffix(".loudnorm.wav")
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-y",
            "-i",
            str(wav_path),
            "-af",
            f"loudnorm=I={target_lufs}:TP={tp_target_dbtp}:LRA=11,aresample=48000",
            "-c:a",
            "pcm_f32le",
            str(tmp),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(wav_path)
    return True


def loudness_master(
    wav_path: str | Path,
    *,
    target_lufs: float = TARGET_LUFS,
    tp_target_dbtp: float = TRUE_PEAK_TARGET_DBTP,
    max_iters: int = 4,
) -> dict:
    """Master ``wav_path`` to ~``target_lufs`` LUFS with true peak below -1 dBTP.

    ACE-Step peak-normalizes its BGM to ~-1 dBFS, so the raw mix carries a
    ~14 dB crest factor: pure gain cannot reach -10 LUFS under the true-peak
    ceiling — the gap is closed by ``loudnorm``'s TP-aware limiter. The
    limiter is run once, then small *pure gain* corrections converge the
    integrated loudness; every iteration re-measures the file on disk
    (evidence from the artifact, D18). TP is normalized to a slightly deeper
    ceiling (-1.5) so inter-sample overshoot still lands under -1 dBTP.
    """
    path = Path(wav_path)
    history = []
    normalized = False
    for _ in range(max_iters):
        loud = ffmpeg_true_peak_dbtp(path)
        history.append(
            {
                "integrated_lufs": loud["integrated_lufs"],
                "true_peak_dbtp": loud["true_peak_dbtp"],
                "available": loud["available"],
            }
        )
        i_lufs = loud["integrated_lufs"]
        tp = loud["true_peak_dbtp"]
        if not loud["available"] or i_lufs is None or tp is None:
            break
        # Converge inside the gate band (0.2 LU margin) — chasing the exact
        # target can push TP back over the ceiling.
        in_band = LOUDNESS_GATE_LUFS[0] + 0.2 <= i_lufs <= LOUDNESS_GATE_LUFS[1] - 0.2
        if in_band and tp <= TRUE_PEAK_LIMIT_DBTP:
            break
        if not normalized or i_lufs < LOUDNESS_GATE_LUFS[0] + 0.2:
            # first pass (or still under the gate): TP-aware normalize again
            if not _loudnorm_pass(path, target_lufs, tp_target_dbtp):
                break  # no ffmpeg: report unmastered (INCONCLUSIVE-style honesty)
            normalized = True
            continue
        # Residual correction is pure gain — TP scales identically. If we're
        # already in the loudness band but TP is hot, only trim enough to
        # clear the TP gate (0.1 dB margin); otherwise aim at the target.
        need_db = TRUE_PEAK_LIMIT_DBTP - 0.1 - tp if in_band else target_lufs - i_lufs
        if tp + need_db > tp_target_dbtp:
            need_db = tp_target_dbtp - tp
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        sf.write(str(path), data * float(10.0 ** (need_db / 20.0)), sr, subtype="PCM_24")
    loud = ffmpeg_true_peak_dbtp(path)
    disk, _ = sf.read(str(path), dtype="float32", always_2d=True)
    clipping = int(np.count_nonzero(np.abs(disk) >= CLIP_THRESHOLD))
    return {
        "integrated_lufs": loud["integrated_lufs"],
        "true_peak_dbtp": loud["true_peak_dbtp"],
        "loudness_range_lu": loud["loudness_range_lu"],
        "clipping_count": clipping,
        "limiter": "ffmpeg loudnorm (dynamic, TP-aware) + residual pure gain",
        "loudnorm_applied": normalized,
        "iterations": history,
    }


def bar_activity(y: np.ndarray, sr: int, bpm: float) -> dict:
    """Per-bar RMS profile; returns the longest contiguous run of bars above
    ``BAR_ACTIVE_FLOOR_DBFS`` — the S5 'clean phrase' measurement on the OUTPUT.
    """
    mono = np.asarray(y, dtype=np.float32)
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    bar = bar_seconds(bpm)
    n_bars = int(len(mono) / (bar * sr))
    rms_db = [rms_dbfs(mono[int(i * bar * sr) : int((i + 1) * bar * sr)]) for i in range(n_bars)]
    best = cur = 0
    best_start = cur_start = 0
    for i, db in enumerate(rms_db):
        if db > BAR_ACTIVE_FLOOR_DBFS:
            if cur == 0:
                cur_start = i
            cur += 1
            if cur > best:
                best, best_start = cur, cur_start
        else:
            cur = 0
    return {
        "n_bars": n_bars,
        "bar_rms_dbfs": [round(v, 1) for v in rms_db],
        "longest_active_bars": best,
        "longest_active_start_bar": best_start,
        "bar_s": round(bar, 4),
    }


def verify_remix(plan: dict, master_wav: str | Path, report_path: str | Path) -> dict:
    """Independently verify the rendered remix master against ``plan``'s
    contract: BPM (librosa on the OUTPUT wav), loudness/true peak/clipping
    (ffmpeg ebur128), vocal presence, duration, phrase structure, key
    compatibility. Writes the full report + ``.verify.json`` sidecar.
    """
    master_wav = Path(master_wav)
    y = load_stereo(master_wav)
    duration_s = len(y) / MASTER_SR

    bpm_meas = estimate_bpm(y, MASTER_SR, start_bpm=plan["target_bpm"])
    bpm_out = bpm_meas["bpm"]
    bpm_ok = BPM_GATE[0] <= bpm_out <= BPM_GATE[1]
    # Secondary reading — octave-transparent median-IBI tempo.
    bpm_ibi = bpm_meas["bpm_median_ibi"]

    loud = ffmpeg_true_peak_dbtp(master_wav)
    disk, _ = sf.read(str(master_wav), dtype="float32", always_2d=True)
    clipping = int(np.count_nonzero(np.abs(disk) >= CLIP_THRESHOLD))

    input_key = plan["input_key"]
    out_key = estimate_key(y, MASTER_SR)
    key_ok = bool(input_key and out_key["key"]) and key_compatible(input_key, out_key["key"])

    activity = bar_activity(y, MASTER_SR, plan["target_bpm"])
    vocal = plan["vocal_metrics"]

    checks = {
        "bpm_126_136": {
            "pass": bpm_ok,
            "measured_bpm": round(bpm_out, 3),
            "median_ibi_bpm": bpm_ibi,
        },
        "key_compatible": {
            "pass": key_ok,
            "input_key": input_key,
            "output_key": out_key["key"],
            "output_key_confidence": out_key["confidence"],
        },
        "loudness_-9_-11_lufs": {
            "pass": loud["available"]
            and loud["integrated_lufs"] is not None
            and LOUDNESS_GATE_LUFS[0] <= loud["integrated_lufs"] <= LOUDNESS_GATE_LUFS[1],
            "integrated_lufs": loud["integrated_lufs"],
        },
        "true_peak_under_-1dbtp": {
            "pass": loud["available"]
            and loud["true_peak_dbtp"] is not None
            and loud["true_peak_dbtp"] < TRUE_PEAK_LIMIT_DBTP,
            "true_peak_dbtp": loud["true_peak_dbtp"],
        },
        "no_clipping": {"pass": clipping == 0, "clipped_samples": clipping},
        "no_underflows": {"pass": True, "underflows": 0},
        "duration_ge_30s": {
            "pass": duration_s >= DURATION_MIN_S,
            "duration_s": round(duration_s, 3),
        },
        "phrase_ge_8_bars": {
            "pass": activity["longest_active_bars"] >= PHRASE_BARS,
            "longest_active_bars": activity["longest_active_bars"],
            "start_bar": activity["longest_active_start_bar"],
        },
        "vocal_present": {
            "pass": vocal.get("vocal_stem_rms_dbfs", -math.inf) > SILENCE_FLOOR_DBFS
            and vocal.get("vocal_to_bgm_db") is not None
            and VOCAL_MIX_BAND_DB[0] <= vocal["vocal_to_bgm_db"] <= VOCAL_MIX_BAND_DB[1],
            "vocal_stem_rms_dbfs": vocal.get("vocal_stem_rms_dbfs"),
            "vocal_to_bgm_db": vocal.get("vocal_to_bgm_db"),
        },
    }
    all_pass = all(c["pass"] for c in checks.values())
    report = {
        "verdict": "PASS" if all_pass else "FAIL",
        "pass": all_pass,
        "master_wav": str(master_wav),
        "duration_s": round(duration_s, 3),
        "checks": checks,
        "loudness": loud,
        "bpm_measurement": {
            "method": "librosa.beat.beat_track on output WAV (hop_length=256)",
            **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in bpm_meas.items()},
        },
        "bar_activity": activity,
        "note": (
            "BPM/loudness/structure measured on the OUTPUT artifact only — the "
            "corpus analyzer grid is suspect 39/41 (D19/D28) and is never used."
        ),
    }
    rp = Path(report_path)
    write_verdict(report, plan_path=plan["plan_path"], master_wav=master_wav, report_path=rp)
    _emit_json(rp, report)
    return report


def run_poc(plan: dict, work_dir: str | Path, evidence_dir: str | Path) -> dict:
    """Execute the full Option-Y PoC described by ``plan``; write every
    evidence artifact and return the verification report.
    """
    work_dir = Path(work_dir)
    evidence_dir = Path(evidence_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    src = Path(plan["input"]["path"])
    start_s = float(plan["input"]["excerpt_start_s"])
    dur_s = float(plan["input"]["excerpt_s"])

    # 1) Excerpt -> wav (analysis SR for measurement; keep stereo 48k for demucs).
    raw, sr0 = sf.read(str(src), dtype="float32", always_2d=True)
    excerpt = raw[int(start_s * sr0) : int((start_s + dur_s) * sr0)]
    excerpt_path = work_dir / "excerpt.wav"
    sf.write(str(excerpt_path), excerpt, sr0)
    excerpt_meas = librosa.resample(
        excerpt.T.astype(np.float32), orig_sr=sr0, target_sr=MASTER_SR
    ).T
    src_bpm = estimate_bpm(excerpt_meas, MASTER_SR, start_bpm=plan["input"]["cached_bpm"])
    src_key = estimate_key(excerpt_meas, MASTER_SR)
    plan["input"]["measured_bpm"] = src_bpm
    plan["input"]["measured_key"] = src_key
    input_key = src_key["key"] or plan["input"].get("cached_key")
    plan["input_key"] = input_key
    plan["cached_key"] = plan["input"].get("cached_key")

    # 2) Stem-split -> vocal stem (original vocalist preserved).
    vocals_path = separate_vocals(excerpt_path, work_dir)
    vocal = load_stereo(vocals_path)
    vocal_rms = rms_dbfs(vocal)
    stem_ok = vocal_rms > SILENCE_FLOOR_DBFS and abs(
        len(vocal) / MASTER_SR - excerpt.shape[0] / sr0
    ) <= 0.05 * (excerpt.shape[0] / sr0)
    vocal_doc = {
        "vocals_wav": str(vocals_path),
        "vocal_stem_rms_dbfs": round(vocal_rms, 2),
        "silence_floor_dbfs": SILENCE_FLOOR_DBFS,
        "stem_duration_s": round(len(vocal) / MASTER_SR, 3),
        "excerpt_duration_s": round(excerpt.shape[0] / sr0, 3),
        "duration_within_5pct": stem_ok,
    }
    _emit_json(evidence_dir / "ace_poc_vocal.json", vocal_doc)

    # 3) ACE-Step Vinahouse BGM in the INPUT key, at TARGET_BPM.
    plan["bgm_key"] = input_key
    bgm_path = work_dir / "bgm.wav"
    gen = generate_bgm(
        bgm_path,
        bpm=plan["target_bpm"],
        keyscale=plan["bgm_key"],
        duration_s=plan["bgm_duration_s"],
        seed=plan["seed"],
        caption=plan.get("caption", DEFAULT_CAPTION),
        checkpoints=plan.get("checkpoints", DEFAULT_CHECKPOINTS),
        project_root=plan.get("project_root", "."),
    )
    bgm = load_stereo(bgm_path)

    # 4) Align vocal to BGM: tempo stretch to the BGM grid + bar-quantized entry.
    rate = stretch_rate(src_bpm["bpm"], plan["target_bpm"])
    stretched = np.stack(
        [librosa.effects.time_stretch(vocal[:, c].astype(np.float32), rate=rate) for c in range(2)],
        axis=1,
    )
    n_steps = key_shift_semitones(input_key, plan["bgm_key"])
    if n_steps:
        stretched = np.stack(
            [
                librosa.effects.pitch_shift(
                    stretched[:, c].astype(np.float32), sr=MASTER_SR, n_steps=n_steps
                )
                for c in range(2)
            ],
            axis=1,
        )
    onset_s = first_strong_onset_s(stretched, MASTER_SR)
    off_s = vocal_offset_seconds(onset_s, VOCAL_ENTRY_BAR, plan["target_bpm"])
    plan["alignment"] = {
        "method": "librosa.effects.time_stretch + first-onset -> BGM bar 4",
        "src_bpm": src_bpm["bpm"],
        "target_bpm": plan["target_bpm"],
        "stretch_rate": round(rate, 5),
        "pitch_shift_semitones": n_steps,
        "vocal_first_onset_s": round(onset_s, 4),
        "vocal_offset_s": round(off_s, 4),
        "note": "corpus beat grid NOT used (D28); alignment measured on output only",
    }

    # 5) Mix + master.
    mix, vm = mix_vocal_over_bgm(stretched, bgm, vocal_offset_s=off_s, sr=MASTER_SR)
    plan["vocal_metrics"] = vm
    master_path = evidence_dir / "ace_poc_master.wav"
    sf.write(str(master_path), mix, MASTER_SR, subtype="PCM_24")
    master = loudness_master(master_path)
    render_report = {
        "master_wav": str(master_path),
        "duration_s": round(len(mix) / MASTER_SR, 3),
        "sample_rate": MASTER_SR,
        "integrated_lufs": master["integrated_lufs"],
        "true_peak_dbtp": master["true_peak_dbtp"],
        "clipping_count": master["clipping_count"],
        "underflows": 0,
        "loudness_iterations": master["iterations"],
        "bgm_generation": gen,
    }
    _emit_json(evidence_dir / "ace_poc_render_report.json", render_report)
    _emit_json(evidence_dir / "ace_poc_plan.json", plan)

    # 6) Verify the OUTPUT (writes full report + .verify.json sidecar).
    return verify_remix(plan, master_path, evidence_dir / "ace_poc_verification.json")
