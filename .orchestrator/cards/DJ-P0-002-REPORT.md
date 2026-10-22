# DJ-P0-002 — Report: corpus eligibility rule + ffmpeg-optional render

**Coder:** Devin CLI · **Date:** 2026-10-22
**Status:** implemented & verified locally. Not committed (Hermes owns git).

Environment used for all commands: `F:\ai-dj` on Windows, Python 3.13.15
(`C:\Users\atton\AppData\Local\hermes\cache\scratch\ai-dj-probe\venv`),
ruff 0.17.0 on PATH.

---

## 1. What changed

| File | Change |
|---|---|
| `ai_dj/analysis/analyzer.py` | `ANALYSIS_VERSION` 1 → 2. New D10 constants (`MAX_SILENCE_RUN_FRACTION=0.02`, `MIN_SILENCE_RUN_S=2.0`, `SILENCE_DB=100.0`, `SILENCE_BLOCK_S=0.05`). New `silence_metrics(y, sr) -> (ratio, longest_run_s)`. `analyze_track` stores `silence_run_ratio`, `longest_silence_run_s`, `eligible` (bool), `exclusion_reason` (str or `None`). `AnalysisCache.get` treats stale-version rows as misses → `analyze` transparently recomputes v1 caches (no `--force` needed). |
| `ai_dj/planner.py` | `_eligible` returns `(usable, flagged)`; a record is usable only with `eligible is True` — **fail closed** for missing flags (v1 rows). `plan_set` gains keyword-only `include_ineligible`; the plan records `eligibility.excluded` (dropped) / `eligibility.overridden` (dropped-only-if-flag-off). |
| `ai_dj/render.py` | `ffmpeg_true_peak_dbtp` returns `{true_peak_dbtp: None, integrated_lufs: None, loudness_range_lu: None, available: False}` when ffmpeg is absent (was `RuntimeError`). Report gets `true_peak_measured_by: "unavailable: ffmpeg not on PATH"` + `peak_protection` saying sample-peak pre-scale is the only protection. Re-scale block guarded by `is not None`. |
| `ai_dj/verify.py` | **Minimal consistency fix** (file was in neither the may-change nor the do-not-change list): the new `ffmpeg_true_peak_dbtp` contract returns `None` values instead of raising, so `verify_master` now reports `ok: null` / `measured_by: "unavailable: ffmpeg not on PATH"` for the true-peak check when ffmpeg is absent, and the unmeasurable check does not fail the gate (`peak_ok is not False`). Without this, `round(None, 3)` would raise `TypeError`. |
| `ai_dj/cli.py` | `plan --include-ineligible` (stderr warning + `overridden` list). `plan` output emits `eligibility`. `analyze` output emits per-track `silence_run_ratio`/`eligible`/`exclusion_reason` + top-level `excluded`. `cmd_render` treats `true_peak_dbtp: None` as not-failing (render itself succeeded; report says measurement unavailable). |
| `tests/test_engine.py` | `_record` fixtures carry the v2 fields (+ `**overrides`). `test_cut_render_is_deterministic` now `@unittest.skipUnless(shutil.which("ffmpeg"))` (mirrors `test_timing.py`). New: `TestAnalyzerEligibility` (3), `TestPlannerEligibility` (4, incl. real-cache assert), `test_render_without_ffmpeg_is_not_fatal` (mocked `shutil.which`). `_cut_plan` helper extracted. |

`ai_dj/analysis/corpus.py` untouched (the flag lives on analysis records; a
filename-only scan cannot measure silence). `ai_dj/timing.py`,
`scripts/timing_probe.py`, `.github/**` untouched.

## 2. Metric vs Hermes' audit (independent reproduction)

`silence_metrics` on the 5 audited files (22050 Hz mono, same params):

```
INELIGIBLE ratio=0.3187 longest_run=  46.85s  huan luyen.wav
INELIGIBLE ratio=0.2179 longest_run=  40.35s  ĐỐI DIỆN NHÀ EM - DUY GỐC.wav
eligible   ratio=0.0000 longest_run=   0.30s  Khóa Ly Biệt (Cover Duy).wav
eligible   ratio=0.0000 longest_run=   0.00s  Chuyện tình cô lái đò-Bolero(Cover Duy).wav
eligible   ratio=0.0000 longest_run=   0.25s  Untitled Session 32_mixdown.wav
```

Matches Hermes (31.9 %/46.8 s, 21.8 %/40.3 s, 0.0/0.3 s, 0.0/none, 0.0/0.2 s)
within block granularity.

## 3. Real corpus — excluded list (acceptance #2)

Command:
```
python -m ai_dj.cli analyze --dir "F:\DỮ LIỆU\ok" --cache analysis_cache.sqlite
```
Result: `total: 41 | analyzed_ok: 41 | errors: 0`, all rows now
`analysis_version = 2`. Excluded (from the analyze output):

```
EXCLUDED (2):
  ĐỐI DIỆN NHÀ EM - DUY GỐC -> silence_run_ratio 0.218 > 0.02
  huan luyen                 -> silence_run_ratio 0.319 > 0.02
```

Exactly the 2 measured non-songs. Worst eligible `silence_run_ratio` in the
corpus = **0.0000** (`âm thanh huấn luyện ok`, longest run 1.2 s — below the
2 s run threshold, so it stays eligible by the metric, not by its name).
41 files → 40 unique `recording_id`s (`vô thường và buông xả.MP3` is a
byte-identical duplicate of the `(1)` variant — pre-existing dedup behavior).

## 4. Planner (acceptance #3, #4)

```
python -m ai_dj.cli plan --cache analysis_cache.sqlite --out evidence/plan_eligible.json
```
Plan tracks (no non-song): `Untitled Session 32_mixdown`, `BUỒN TRONG NỖI NHỚ
(Cover- Duy)`, `Untitled Session 6_mixdown`, `vô thường và buông xả(1)`,
`Khóa Ly Biệt (Cover Duy)`, `Untitled Session 35_mixdown`; plan JSON records
both exclusions with reasons under `eligibility.excluded`.

```
python -m ai_dj.cli plan --out /tmp/plan_all.json --set-name all --include-ineligible
```
stderr:
```
WARNING: --include-ineligible — 2 track(s) that failed the eligibility rule are selectable again:
  - ĐỐI DIỆN NHÀ EM - DUY GỐC: silence_run_ratio 0.218 > 0.02
  - huan luyen: silence_run_ratio 0.319 > 0.02
```
and the resulting track list is **identical to the old buggy plan**
(`huan luyen` first) — literal restoration of pre-rule behaviour.

## 5. Re-rendered master — longest silent run (acceptance #5)

```
python -m ai_dj.cli render --plan evidence/plan_eligible.json \
    --out evidence/eligible_set.wav --report evidence/render_report_eligible.json
```
Render: 1527.194 s master, true peak −1.2 dBTP (ffmpeg ebur128), clipping 0.

Measurement command (same params as the audit — 50 ms blocks, −100 dB RMS):
```python
data, sr = sf.read("evidence/eligible_set.wav", dtype="float32", always_2d=True)
ratio, longest = silence_metrics(data.mean(axis=1), sr)
```
Output:
```
master: 1527.2 s, sr=48000
longest contiguous silent run: 0.000 s  (> 2.0 s threshold: PASS)
```

**Positive control** — same measurement on the old buggy `evidence/hermes_set.wav`
finds the documented defect, proving the measurement itself works:
```
OLD (buggy) master: 1307.3s | longest silent run=29.450s | min block RMS=0.00e+00 | blocks<1e-5: 589
NEW master:         1527.2s | longest silent run= 0.000s | min block RMS=7.03e-05 | blocks<1e-5: 0
```
(29.450 s vs Hermes' 29.40 s — within the 50 ms block size.)

## 6. §2b — ffmpeg optional, never fatal (acceptance #8)

Full suite under a PATH with **every** ffmpeg dir removed (two existed:
`…\WinGet\Links`, `…\hermes\tools\ffmpeg-9.0.1-win32-x64\bin`; verified
`shutil.which('ffmpeg') is None` inside the child):

```
Ran 44 tests in 2.435s
OK (skipped=3)
```
skips shown:
```
test_beat_aligned_overlap_within_contract ... skipped 'ffmpeg not installed'
test_cut_render_is_deterministic ... skipped 'ffmpeg not installed'
test_tempo_108_within_contract ... skipped 'ffmpeg not installed'
```

Direct `render_plan` call under that PATH (real subprocess, no mock):
```
ffmpeg on PATH: None
did not raise; report excerpt:
{
  "true_peak_dbtp": null,
  "integrated_lufs": null,
  "true_peak_measured_by": "unavailable: ffmpeg not on PATH",
  "peak_protection": "sample-peak pre-scale only (true peak not measured)",
  "clipping_count": 0,
  "duration_s": 23.995
}
```

## 7. Full suite + lint (acceptance #6)

```
python -m unittest discover -s tests -v   →   Ran 44 tests in 3.1s — OK
   (with ffmpeg on PATH: test_real_cache_never_selects_ineligible runs
    against the real v2 cache and passes — not skipped)
ruff check .            →   All checks passed!
ruff format --check .   →   24 files already formatted
```

## 8. Not verified / notes for Hermes

- **CI itself**: cannot run GitHub Actions locally. Verified equivalent: suite
  under a genuinely ffmpeg-less PATH exits 0 with the 3 expected skips —
  mirrors what the `Test` jobs (ubuntu 3.12/3.13, windows 3.12) will see.
  `verify.py`'s unmeasurable-peak path is exercised indirectly
  (`peak_ok is not False`); no dedicated test was added for it.
- **Cache migration**: bumping `ANALYSIS_VERSION` to 2 makes
  `AnalysisCache.get` treat v1 rows as misses — `analyze` recompute is
  transparent (no `--force`). `plan` on a still-v1 cache fails closed
  ("0 eligible… excluded by eligibility rule") until `analyze` reruns, or
  `--include-ineligible` is passed.
- **verify.py edit**: outside the card's explicit file lists but required by
  the §2b contract change (it consumes `ffmpeg_true_peak_dbtp`). 3-line
  honest-null handling; revert is trivial if you disagree.
- `plan_set` params after `records` are now keyword-only (PLR0917 fix); all
  existing callers already used keywords.
- `evidence/plan_eligible.json`, `evidence/eligible_set.wav`,
  `evidence/render_report_eligible.json` are new artifacts (wav gitignored);
  `evidence/plan.json` (buggy) left untouched for comparison.
