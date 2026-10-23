# ACE-Step 1.5 Remix PoC — Report (AI-DJ-P3-ACE-001)

**Lane:** Devin CLI, worktree `F:/ai-dj-worktrees/AI-DJ-P3-ACE-001`, branch
`task/AI-DJ-P3-ACE-001-ace-remix-poc`. **Date:** 2026-10-23.
**Architecture:** D30 Option Y — demucs vocal stem of a rights-cleared Bolero +
ACE-Step 1.5 generated Vinahouse BGM -> align -> mix -> master -> verify on the
OUTPUT artifact only (corpus beat grid never used; D19/D28: 39/41 suspect).

**Verdict: PASS (9/9 checks).** `evidence/ace_poc_verification.json`,
`evidence/ace_poc_master.verify.json` (SHA-256 bound to plan + master).

## Input selection

`F:\DỮ LIỆU\ok\Đam Mê (Cover Duy) .wav` — `rec-1d80838e4a40f4cb`, cached
99.38 BPM / B minor (key_confidence 0.90, the strongest estimate in the
88–110 BPM band), eligible, 321 s. The `Gõ Cửa` 96->192 doubling case
(D29/F3) was avoided. Excerpt: 56 s–100 s (`intro_end_s`=54.0 -> inside the
verse), 44 s long, SHA-256 recorded in `evidence/ace_poc_plan.json`.

Measured independently on the excerpt (librosa, hop_length=256 @48 kHz):
**100.45 BPM** (median-IBI 104.17) — vs the cached ladder value 99.38, a live
illustration of the D19 quantization artifact. Key: **B minor** (Krumhansl
confidence 0.764, agrees with cache).

## S1 — generation driver + smoke gen

```
$ F:/ace-venv/Scripts/python.exe scripts/ace_poc_generate.py --out evidence/ace_poc_sample.wav --bpm 130 --key "B minor" --duration 30 --seed 42
{
  "written_to": "evidence\\ace_poc_sample.wav",
  "source_artifact": "F:\\...\\evidence\\42683b21-6444-39e8-8c36-e64abfb22dee.wav",
  "params": { "task_type": "text2music", "bpm": 130, "keyscale": "B minor",
              "duration": 30.0, "seed": 42, "inference_steps": 8, "instrumental": true },
  "checkpoints": "F:\\ace-checkpoints",
  "size_bytes": 11520088
}

$ ffprobe evidence/ace_poc_sample.wav
  Duration: 00:00:30.00, bitrate: 3072 kb/s
  Stream #0:0: Audio: pcm_f32le, 48000 Hz, 2 channels, flt, 3072 kb/s
```

Valid PCM, exactly 30.0 s. Checkpoints resolve via `ACESTEP_CHECKPOINTS_DIR` —
no model bytes copied into the worktree. The LM is never loaded:
`thinking=False` + all `use_cot_*`=False -> `use_lm=False` in
`acestep.inference.generate_music` (pure DiT path).

## S2 — stem split

`demucs --two-stems vocals -n htdemucs` on the 44 s excerpt (installed into
`F:/ace-venv`: `demucs==4.0.1`, `torchaudio` already 2.7.1+cu128 — torch
untouched). `evidence/ace_poc_vocal.json`:

```json
{ "vocal_stem_rms_dbfs": -16.31, "silence_floor_dbfs": -60.0,
  "stem_duration_s": 44.0, "excerpt_duration_s": 44.0,
  "duration_within_5pct": true }
```

## S3–S5 — mix, master, output verification

- Vocal time-stretched 100.45->130 BPM (`librosa` rate **1.2942**), first
  strong onset placed on **bar 4** of the BGM grid (offset 7.230 s); no pitch
  shift needed (input B minor == BGM B minor). Corpus grid NOT used.
- BGM: `acestep-v15-turbo`, text2music, instrumental, 130 BPM, B minor,
  44 s, seed 42, 8 steps, bfloat16 CUDA. Init: `[OK] Model initialized
  successfully on cuda / Main model: F:\ace-checkpoints\acestep-v15-turbo`.
- Master: `ffmpeg loudnorm=I=-10:TP=-1.5:LRA=11` (true-peak-aware limiter —
  `alimiter` was tried first and failed the TP gate: it clamps **sample**
  peaks at -1.2 dBFS but this content overshoots **+2.2 dB inter-sample**,
  reading +1.0 dBTP). Iterations: -14.0 -> -11.5 -> **-10.7 LUFS**.

`evidence/ace_poc_verification.json` (measured on `ace_poc_master.wav`):

| check | measured | gate |
|---|---|---|
| bpm | **129.31** (median-IBI 129.31, 87 beats) | 126–136 ✅ |
| key | input B minor -> output **F# minor** (conf 0.762) | compatible (5th, same mode) ✅ |
| loudness | **-10.7 LUFS** | -9..-11 ✅ |
| true peak | **-1.1 dBTP** | < -1 ✅ |
| clipped samples | **0** | 0 ✅ |
| underflows | **0** | 0 ✅ |
| duration | **44.0 s** | >=30 ✅ |
| phrase | **23 contiguous active bars** (bar 0) | >=8 ✅ |
| vocal | stem RMS -19.8 dBFS (placed), vocal/BGM **-2.0 dB** | in band ✅ |

Independent `ffmpeg ebur128` on the OUTPUT file
(`evidence/ace_poc_ebur128.txt`):

```
  Integrated loudness:
    I:         -10.7 LUFS
  True peak:
    Peak:       -1.1 dBFS
```

Independent librosa tempo on the OUTPUT file (default hop, fresh process):

```
$ F:/ace-venv/Scripts/python.exe -c "import librosa,numpy as np; \
    y,sr=librosa.load('evidence/ace_poc_master.wav'); \
    env=librosa.onset.onset_strength(y=y,sr=sr,hop_length=256); \
    t,b=librosa.beat.beat_track(onset_envelope=env,sr=sr,hop_length=256); \
    print('bpm',float(np.atleast_1d(t)[0]))"
bpm 129.19921875
```

## S6 — gates

```
$ ruff check .
All checks passed!
$ ruff format --check .
72 files already formatted
$ python -m pytest tests/ -q           # F:/ai-dj-venv
150 passed, 1 skipped in 18.02s
$ python -m pytest scripts/ace_poc_selftest.py -q
11 passed in 1.04s
```

No `.devin/` writes (card constraint; toolchain-setup hook declined by card).

## S7 — diff scope

```
$ git status --porcelain
 M ai_dj/cli.py
?? agent_logs/                <- Devin-CLI session log (harness artifact, not mine)
?? ai_dj/ace_remixer.py
?? evidence/ace_poc_*.json|.log|.txt
?? scripts/ace_poc_generate.py / ace_poc_run.py / ace_poc_selftest.py
```

All created/modified files are inside the card whitelist. `agent_logs/` is
created and written by the Devin CLI launcher itself (it existed, empty, at
session start before any agent writes); lane logs were moved to
`evidence/ace_poc_{generate,run}.log`. All evidence WAVs are gitignored.

## Card-command deviations (honest notes)

- `ebur128=framing=1` in the card's verify block fails on ffmpeg 9.0.1 —
  `framing` is not an ebur128 option here ("Error applying option
  'framing'"). Ran `ebur128=peak=true` (identical metrics, saved to
  `evidence/ace_poc_ebur128.txt`).
- `librosa.beat.tempo` no longer exists in librosa 1.0.0 — used
  `librosa.beat.beat_track` (and `librosa.feature.rhythm.tempo` is the moved
  alias). Measured the OUTPUT wav both ways: 129.31 / 129.20 BPM.
- `tests/` is read-only per the card's read-only list, so the new tests live
  in `scripts/ace_poc_selftest.py` (whitelisted `scripts/ace_poc_*` glob),
  runnable via `pytest scripts/ace_poc_selftest.py` or standalone.

## Known limitations (PoC honesty)

- `loudnorm` dynamic mode compressed LRA 11.2 -> 3.3 LU — the -10 LUFS / -1
  dBTP contract on an already peak-normalized BGM requires limiting; audible
  as a denser, more squashed master (acceptable: Vinahouse convention).
- Vocal alignment is onset-anchored to a bar boundary, not per-beat
  phase-locked; rubato Bolero phrasing is preserved by the stretch (rate is
  constant). First-onset anchoring verified on the OUTPUT via bar activity,
  not on the source grid.
- Output key estimate (F# minor) disagrees with the requested B minor — the
  keys are compatible (dominant relationship), which is what the gate checks;
  ACE-Step's keyscale adherence is approximate.
- Listening verdict (M7-style) is not automated — the artifact is produced
  for human review; all *technical* gates pass on the output artifact.
