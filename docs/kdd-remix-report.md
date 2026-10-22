# DJ-P2-KDD-REMIX-001 v2 — KDD Remix Master Report

Date: 2026-10-23. Working dir: `F:/ai-dj`. Python: `F:/ai-dj-venv/Scripts/python.exe` (3.13.15).
ffmpeg: `9.0.1-full_build` on PATH (`rubberband` filter present — required for `beat_crossfade` stretch and ebur128 true peak).

## Verdict

**PASS** — one verified DJ remix master containing `kdd` ("DUY MANH - Kiep Do Den").

- Set: `Gõ Cửa - Bolero Cover – Duy` → `DUY MANH - Kiep Do Den` → `Khóc Thầm`
- Duration: **899.336 s** (∈ [600, 900])
- Transitions: `beat_crossfade` conf **0.745** (≥ 0.7), `crossfade` conf **0.63**
- D12 silence gate: both tiers clean (digital: 0 silent blocks; low-level: 0 silent blocks)
- True peak: **-1.2 dBTP** (limit -1.0); clipping_count: **0**
- Verify verdict: **PASS**, exit 0, `unmeasured_checks: []`

## Planning

`plan` has no duration knob — total length falls out of the chosen tracks' cue
ranges. KDD's played segment is ~315 s and the corpus is mostly 300 s tracks,
so the search space was swept over `n_tracks {3,4}`, `energy_target`
`{None,0.1..0.8}`, `anchor_last {F,T}` (80 combos, direct `plan_set` calls).
**Exactly one configuration lands inside [600, 900]:**

`--tracks 3 --include-track kdd --energy-target 0.2` (any of 0.1–0.3; deterministic — `seed` is recorded but unused by the anchored beam).

All other combos: 906.6–1195.8 s. `n_tracks=4` cannot fit (min 1064.7 s).
This is also the only ordering that captures the single ≥ 0.7 transition KDD
can reach: `beat_crossfade` needs BPM within 6 % + key distance ≤ 1;
`Gõ Cửa` (92.285 BPM, D# minor) vs KDD (89.103 BPM, D minor) = 3.4 % / dist 1
→ conf `0.9 * (1 - 0.3*0.0345/0.06) ≈ 0.745`. No other corpus track is within
6 % of either neighbor, so the second transition tops out at the `crossfade`
ceiling `0.9 * 0.7 = 0.63` (reported honestly, per the task's fallback clause).

Real command output:

```
$ python -m ai_dj.cli plan --tracks 3 --include-track kdd --energy-target 0.2 --set-name kdd_remix --out evidence/kdd_remix_plan.json
{
  "written_to": "evidence\\kdd_remix_plan.json",
  "set_name": "kdd_remix",
  "track_count": 3,
  "total_duration_s": 899.336,
  "tracks": [
    {"index": 0, "title": "Gõ Cửa - Bolero Cover – Duy", "bpm": 92.285, "key": "D# minor", "tempo_stretch": 1.0, "gain_db": -0.09},
    {"index": 1, "title": "DUY MANH - Kiep Do Den", "bpm": 89.103, "key": "D minor", "tempo_stretch": 1.035711, "gain_db": -1.1},
    {"index": 2, "title": "Khóc Thầm", "bpm": 83.354, "key": "D minor", "tempo_stretch": 1.0, "gain_db": -1.38}
  ],
  "transitions": [
    {"index": 0, "type": "beat_crossfade", "duration_s": 10.403, "confidence": 0.745,
     "reasons": ["bpm_matched", "key_compatible", "bpm_diff_3.4%"]},
    {"index": 1, "type": "crossfade", "duration_s": 6.0, "confidence": 0.63,
     "reasons": ["key_compatible", "bpm_diff_6.5%"]}
  ],
  "anchor_resolution": {"requested_count": 1, "included_count": 1, "all_included": true,
    "anchors": [{"recording_id": "kdd", "slot_index": 1,
      "source_sha256": "60181887f9b97ac46470f47e4ab4d67a29f274ca42df996ab046ef3a84f8ebc3"}]}
}
```

KDD is tempo-stretched ×1.035711 (89.103 → 92.285 BPM) inside the beat-crossfade
— within the rubberband-safe ±6 % window (`MAX_STRETCH = 1.06`).

**Second valid configuration** (explored via a transient `pool_ids` pool
filter on `plan_set` — since reverted, see Worktree code state): restricting
the pool to `{kdd, rec-4abe167c7f9c8ea3 (Gõ Cửa), rec-f6dc34794985d5f3
(NỢ TÌNH)}` yields `NỢ TÌNH → KDD → Gõ Cửa` at **795.506 s** — KDD at native
tempo in the middle slot; `crossfade` 0.63 documented + `beat_crossfade`
0.739. Superseded by the adopted plan only because the adopted one keeps
every transition inside compatible keys.

## Render

```
$ python -m ai_dj.cli render --plan evidence/kdd_remix_plan.json --out evidence/kdd_remix_master.wav --report evidence/kdd_remix_render_report.json
{
  "out_wav": "evidence\\kdd_remix_master.wav",
  "master_sr": 48000,
  "total_samples_planned": 43168119,
  "total_samples_actual": 43168119,
  "duration_s": 899.336,
  "true_peak_dbtp": -1.2,
  "integrated_lufs": -15.0,
  "loudness_range_lu": 6.2,
  "true_peak_measured_by": "ffmpeg ebur128=peak=true",
  "peak_protection": "sample-peak pre-scale + ffmpeg true-peak re-measure",
  "peak_limit_dbtp": -1.0,
  "peak_scale_applied_db": -1.545,
  "clipping_count": 0,
  "segments": [
    {"index": 0, "recording_id": "rec-4abe167c7f9c8ea3", "tempo_stretch": 1.0,      "planned_samples": 14674127, "actual_samples": 14674127},
    {"index": 1, "recording_id": "kdd",                  "tempo_stretch": 1.035711, "planned_samples": 14592298, "actual_samples": 14592298},
    {"index": 2, "recording_id": "rec-f313e4624a307ca2", "tempo_stretch": 1.0,      "planned_samples": 14689038, "actual_samples": 14689038}
  ]
}
```

Master WAV: `evidence/kdd_remix_master.wav`, 259 008 758 bytes, PCM_24 stereo 48 kHz.

## Verify

`python -m ai_dj.cli verify --plan evidence/kdd_remix_plan.json --master evidence/kdd_remix_master.wav --out evidence/kdd_remix_verification.json` → **exit 0**

```json
{
  "pass": true,
  "verdict": "PASS",
  "unmeasured_checks": [],
  "checks": {
    "artifact":              {"decodable": true, "channels": 2, "frames": 43168119, "ok": true},
    "duration_matches_plan": {"planned_s": 899.336, "actual_s": 899.336, "tolerance_s": 1.0, "ok": true},
    "true_peak":             {"dbtp": -1.2, "limit_dbtp": -1.0, "measured_by": "ffmpeg ebur128=peak=true", "integrated_lufs": -15.0, "ok": true},
    "clipping":              {"count": 0, "ok": true},
    "silence": {
      "digital_silence": {"threshold_dbfs": -90.0, "min_run_s": 0.5, "silent_blocks": 0, "violating_runs_s": [], "ok": true},
      "low_level":       {"threshold_dbfs": -60.0, "min_run_s": 2.0, "silent_blocks": 0, "violating_runs_s": [], "ok": true},
      "channel_rms_dbfs": [-17.67, -17.76],
      "dead_channels": [],
      "short_term_3s": {"min_dbfs": -24.18, "at_s": 337.05},
      "silence_ok": true
    },
    "synthetic_alignment_check": {"probe": "synthetic_click_tracks_through_render_plan",
      "bpm_a": 100.0, "bpm_b": 104.0, "stretch_factor": 0.961538,
      "detected_beats_in_overlap": 10, "measured_ibi_ms": 600.0, "target_ibi_ms": 600.0,
      "deviation_ms": 0.0, "within_threshold": true, "threshold_ms": 20.0},
    "samplerate_matches": {"expected": 48000, "actual": 48000, "ok": true}
  },
  "artifact_binding": {
    "plan_sha256":   "7888c4c3865505652d7e4376aedaaaf9b77b2a8b2161337a52c764bb4847e373",
    "master_sha256": "ccf1201c40b2da69d6fd18013106babfa4eb563fa0fb111234a7dbf9de192dd9"
  }
}
```

Real-music onset deviation on the `beat_crossfade` window is `within_threshold: false`
(coverage 0.812, median 42.5 ms, p95 79.2 ms) — **diagnostic only**, never a gate;
the hard 20 ms beat contract is `synthetic_alignment_check` (0.0 ms deviation, PASS).
This is expected on live bolero/acoustic onsets and does not affect the verdict.

## Repo health (real output)

```
$ F:/ai-dj-venv/Scripts/python.exe -m pytest tests -q
........................................................................ [ 59%]
.................................................                        [100%]
121 passed in 10.92s
```

```
$ F:/ai-dj-venv/Scripts/python.exe -m ruff check .
All checks passed!
$ F:/ai-dj-venv/Scripts/python.exe -m ruff format --check .
52 files already formatted
```

**Worktree code state:** `git status` shows `M` on no engine file — the
adopted plan used only stock CLI flags (`--tracks`, `--include-track`,
`--energy-target`), so `ai_dj/` is at HEAD. A parallel session explored a
crate-filtered variant (`pool_ids` on `plan_set` + `plan --crate`, plus
`tests/test_remix.py`) which produced the second configuration below; that
code was subsequently reverted and the adopted deliverable does not depend
on it.

Data-side additions that the plan does depend on:

- `analysis_cache.sqlite` — `kdd` record analyzed in (av=3, cv=1): 89.103 BPM,
  D minor, 335.203 s, conf 0.9, ibi_cv 0.0313, 126 downbeats, eligible.
- `library/kdd.wav` — 48 kHz PCM_24 transcode of the m4a source; the plan's
  KDD `path` points here (`.m4a` is outside `scan_corpus` extensions).

## Cue points (`evidence/kdd_remix_cue_report.json`)

| # | recording_id | master_start_s | stretch | cue_report status | reason |
|---|--------------|---------------:|--------:|-------------------|--------|
| 0 | rec-4abe167c7f9c8ea3 | 0.0   | 1.0      | ok       | ok |
| 1 | kdd                  | 295.308 | 1.035711 | adjusted | uncovered_silence_run |
| 2 | rec-f313e4624a307ca2 | 593.314 | 1.0      | ok       | ok |

KDD's cue was adjusted by the planner's D12 assessment: `cue_out_s` 316.302 vs
default 334.703 — an 18.4 s tail trim (retained 314.863 s, 93.93 % of source)
to keep a source silent run out of the master. Declared, not hidden.

## Artifacts

| File | Role |
|------|------|
| `evidence/kdd_remix_plan.json` | plan (899.336 s, 3 tracks, KDD at index 1) |
| `evidence/kdd_remix_master.wav` | rendered master (PCM_24, 48 kHz stereo) |
| `evidence/kdd_remix_master.verify.json` | verdict sidecar — PASS, sha256-bound (play-gate token; canonical `.verify.json` name) |
| `evidence/kdd_remix_verification.json` | full verify report (`--out`; repo convention keeps it separate from the sidecar) |
| `evidence/kdd_remix_render_report.json` | render report |
| `evidence/kdd_remix_cue_report.json` | per-track cue audit |
| `docs/kdd-remix-report.md` | this report |

`evidence/kdd_remix_plan_t3.json` is the run-1 leftover (914.6 s — over the 900 s
cap, KDD last, 2× crossfade 0.63): superseded, kept for provenance and
regenerated byte-identically via `plan --tracks 3 --include-track kdd`
(no `--energy-target`, no `--crate`).

Also in the worktree: `library/kdd.wav` (KDD source transcode the plan's
`path` references).

## Concurrency note

Two sessions ran this card against the same `evidence/` paths and the same
worktree. The adopted plan/master pair above is the internally consistent,
sha-bound set that survived (`artifact_binding` in the verify report); it
re-derives byte-identically from the command in the Planning section. A
parallel code change (`plan_set(pool_ids=...)` + `plan --crate` +
`tests/test_remix.py`) was reverted during the race; `git status` on
`ai_dj/` is clean as of this report.

## Acceptance criteria

- [x] Plan duration ∈ [600, 900] — 899.336 s
- [x] ≥ 3 tracks, KDD included — 3 tracks, KDD index 1
- [x] Verify PASS on both D12 tiers — `digital_silence.ok` and `low_level.ok`
- [x] true_peak ≤ -1.0 dBTP — -1.2; clipping_count 0
- [x] Cue report for every track — 3/3
- [x] Preferred goal met: transition confidence ≥ 0.7 reached (0.745 beat_crossfade); second transition reported at its actual 0.63

Constraints honoured: no commit/push, nothing written under `.devin/`,
`ai_dj/verify.py` untouched, no `allowed_silent_gaps_s` in the plan, LF endings.
