# DJ-P2-CONTINUITY-001 — Unified Audio Continuity Contract

**Verdict: IMPLEMENTED — all 10 acceptance gates pass on real data.**

The card's premise, confirmed by direct measurement: analyzer D10 and verifier D12
measure *different signals at different thresholds*. D10 reads a mono 22.05 kHz
librosa mixdown at −100 dBFS (2 s runs); D12 re-reads the rendered master —
all-channel 50 ms RMS blocks at 48 kHz — and fails on ≥ 0.5 s ≤ −90 dBFS or
≥ 2.0 s ≤ −60 dBFS. Threshold sync alone cannot close that gap.

The fix implemented here: **one contract measured on the D12 signal class** —
per-block all-channel RMS at the render sample rate — stored in the analysis
payload, consumed by the planner as a *hard constraint*, with D12 keeping veto
authority after render. No threshold was softened; no cue was faked; no mid-lyric
cut or injected audio was used anywhere.

## Contract design

```
source WAV ──► render.load_audio_48k (the renderer's own decode path)
           ──► 50 ms all-channel RMS blocks (verify._block_rms_dbfs math)
           ──► silence_intervals + per-block dBFS + opener window  ──► payload JSON
                                                                         │
planner:   assess_segment(cue search, coverage zones) ──► hard prune      │
           anchored beam / greedy  ──► _build_tracks/_layout_master       │
           ──► find_master_violations (forward-simulated master blocks)   │
           ──► ban culprit + re-plan, or PLAN_INFEASIBLE (exit 4)         ▼
render:    unchanged                                     verify.py D12: unchanged
```

Two layers, deliberately:

1. **Selection-time** (`_candidate_feasible` → `continuity.assess_segment`):
   optimistic. Coverage zones at the segment edges (`head_cov`, `tail_cov`,
   derived from the *actual* incoming transition decision) may mask silent
   runs; interior runs force a cue trim to downbeat boundaries — a legitimate
   cue choice, never a splice inside a dead stretch and never inside a lyric.
   Candidates with no legal ≥ 30 s window are removed from the search *before
   cost is compared* — a penalty cannot keep a fully silent opener from winning.
2. **Commit-time** (`find_master_violations` → `_master_level_map`): exact.
   After an ordering is chosen, the master's 50 ms block map is forward-
   simulated: each block's predicted level is the max over contributing tracks
   of `source_block_dbfs + gain_db + fade_envelope_db` (the renderer's own
   fade curves, incl. `beat_crossfade` shift via `render._beat_shift_samples`,
   `echo_out` fraction, `cut` de-click, master edge fades). Runs matching D12
   semantics — same block size, same sample-count `>=` comparison, same exempt
   regions (renderer fades + `allowed_silent_gaps_s`) — are violations.
   Culprits are banned and the order rebuilt; an anchor that cannot be placed
   yields `PlanInfeasible`, never a quiet drop.

The predictor is **conservative by construction**: a block counts as audible
only when one contributor provably exceeds the threshold (independent signals
sum in power to ≥ the largest contributor). Boundary combinations can over-flag
(safe direction); under-flagging requires true cancellation across unrelated
program material.

## Analyzer payload schema (fresh records, `continuity_version: 1`)

```json
{
  "continuity_version": 1,
  "block_ms": 50,
  "channels": "all_channels_rms",
  "continuity_sr": 48000,
  "silence_intervals": {
    "digital_minus90": [[352.05, 352.6], [385.45, 389.35]],
    "low_level_minus60": [[351.05, 352.65], [385.4, 389.35]]
  },
  "opener": {
    "window_s": 30,
    "cue_in_s": 0.48762,
    "max_digital_run_s": 0.0,
    "max_low_level_run_s": 0.0,
    "audibility_ok": true
  },
  "block_rms_dbfs": ["…per-block floats…"]
}
```

(Real values above are the anchor `rec-36bd9cb09f26f5b8`'s stored payload.)

- `silence_intervals` keeps **all** maximal runs, not just violating-length
  ones — the planner needs run boundaries for stretch/gain/coverage reasoning.
- `block_rms_dbfs` is the per-block level array the assessment and the master
  simulation run on — needed because gain (`±12 dB`) and stretch (±6 %) change
  which runs violate on the master, and intervals alone can't re-derive that.
- `opener` is measured over the *default-cue* window `[cue_in, cue_in+30 s)` —
  `default_cue_points` is now the single cue rule shared by analyzer and
  planner (`planner._cue_points` delegates to it).
- `AnalysisCache.get` treats an `ok` payload whose `continuity_version` is
  absent or older as a **cache miss** → re-analyze, never a silent fallback.
  `ANALYSIS_VERSION` bumped 2 → 3. **No `ALTER TABLE`** — the version lives in
  the payload JSON; the table schema is untouched.

## Two-signal reality — measured corpus delta (G3)

Same 41 tracks, both meters. `d10` = mono 22.05 kHz −100 dBFS run metrics
(stored fields); `d12` = all-channel 48 kHz RMS at the contract thresholds:

```
rec-1d80838e4a40 | d10 0.0000/   0.2s | d12 dig  26.65s low  54.00s | Đam Mê (Cover Duy)
rec-cdb8894fb3bf | d10 0.3187/  46.9s | d12 dig  46.85s low  48.70s | huan luyen
rec-3177f4d0d6ca | d10 0.2179/  40.4s | d12 dig  40.35s low  40.50s | ĐỐI DIỆN NHÀ EM - DUY GỐC
rec-456190e736b3 | d10 0.0000/   0.0s | d12 dig   0.15s low  26.45s | audio
rec-093e6c9bf8c7 | d10 0.0000/   0.2s | d12 dig   0.35s low   9.50s | Untitled Session 32_mixdown
rec-52ef1d309f22 | d10 0.0000/   0.3s | d12 dig   8.95s low   9.50s | Untitled Session 35_mixdown
…
rec-36bd9cb09f26 | d10 0.0100/   3.9s | d12 dig   3.90s low   3.95s | Sao Lòng Còn Thương - Trọng Hải
--- 19/41 tracks contain a D12-tier run if played edge-to-edge
```

**19 of 41 tracks** carry a run that D12 would flag if the segment were played
edge-to-edge — while D10 scored most of them at ~0 % silence. Two different
signal classes *and* two different thresholds; syncing constants alone cannot
repair this. Equivalence of the new metric to the verifier's math is pinned by
test: `block_rms_dbfs` output is `assert_array_equal` to
`verify._block_rms_dbfs` on identical audio, and the mirrored constants equal
`verify.py`'s (`tests/test_continuity.py::test_block_rms_matches_verify`).

The two signals now coexist *deliberately*: mono 22.05 kHz stays for
BPM/key/energy/onset work (its D10 corpus-filter verdict is preserved); the
48 kHz all-channel path exists solely for continuity — measured through
`render.load_audio_48k`, i.e. on the exact signal the renderer feeds the mix.

## Acceptance gates — real output

### G1 — fresh analyze emits the contract payload

`analyze --corpus evidence/corpus_report.json` re-analyzed all 41 records
(`ANALYSIS_VERSION` 3). Anchor `rec-36bd9cb09f26f5b8` (Sao Lòng Còn Thương):

```
version: 1 block_ms: 50 channels: all_channels_rms sr: 48000
digital: [[352.05, 352.6], [385.45, 389.35]]
low_level: [[351.05, 352.65], [385.4, 389.35]]
opener: {'audibility_ok': True, 'cue_in_s': 0.48762,
         'max_digital_run_s': 0.0, 'max_low_level_run_s': 0.0, 'window_s': 30}
n_blocks: 7787 duration_s: 389.397
```

Evidence: `evidence/dj_p2_continuity_measure.json`. Unit coverage:
`test_analyzer_emits_continuity_fields`, `test_analyzer_flags_silent_source`.

### G2 — stale cache is re-analyzed, never silently reused

```
before: SELECT analysis_version, COUNT(*) …  →  [(2, 41)]
after:  …                                     →  [(3, 41)]
```

`test_stale_cache_is_reanalyzed` proves both layers: a v2 row is a miss, *and*
a v3 row whose payload lacks `continuity_version` is still a miss →
`analyze_corpus` recomputes and the refreshed payload then hits.

### G3 — same signal class as the verifier

`test_block_rms_matches_verify` — `np.testing.assert_array_equal` between
`continuity.block_rms_dbfs` and `verify._block_rms_dbfs` on the same stereo
array, plus constant equality (block 0.05 s, −90/0.5 s, −60/2.0 s, same floor).

### G4 — hard-constraint pruning

`_candidate_feasible` removes `infeasible` candidates inside both selectors
(`_anchored_order` beam and `_greedy_order`) before cost comparison; the
post-order `find_master_violations` then verifies the committed master and
bans/re-plans on any remaining culprit. Not a penalty: `infeasible` tracks are
never expanded in the beam. Unit coverage: `test_violating_track_is_pruned`,
`test_coverable_head_run_is_kept`, `test_uncoverable_run_is_infeasible`,
`test_gain_can_create_violation`, `test_uncovered_interior_run_flagged`,
`test_silent_window_with_silent_neighbour_flagged`,
`test_covered_tail_run_is_clean`.

### G5 — forced infeasible anchor → PLAN_INFEASIBLE + exit 4

Real corpus run — D10-ineligible anchor (`huan luyen`, silence ratio 0.319):

```json
{
  "error": "PLAN_INFEASIBLE",
  "reason": "anchor_ineligible",
  "message": "requested anchor 'rec-cdb8894fb3bf79df' is excluded by the eligibility rule: silence_run_ratio 0.319 > 0.02",
  "violations": []
}
EXIT=4
```

Real audio run — a 120 s WAV at −80 dBFS: D10-eligible (`eligible: True`,
ratio 0.0 — invisible to the −100 gate) but every block violates D12's −60 tier.
Forced as anchor (`evidence/dj_p2_continuity_infeasible.json`):

```json
{
  "error": "PLAN_INFEASIBLE",
  "reason": "anchor_infeasible",
  "message": "no feasible plan found containing anchor 'rec-lowbed': the anchor cannot be placed in any slot without violating the D12 continuity contract (uncoverable_silence)",
  "violations": [
    {
      "tier": "low_level_minus60",
      "range_s": [0.05, 118.0],
      "uncovered_s": [0.07, 118.0],
      "reason": "uncovered_silence_run"
    }
  ],
  "anchor_id": "rec-lowbed",
  "assessment_status": "infeasible"
}
EXIT_CODE = 4
```

Distinct exit code `4`, structured `violations` with segment ranges + reason,
human-readable `message`. Other structured reasons: `anchor_not_found`,
`anchor_unusable`, `anchor_too_short`, `anchor_deduplicated`,
`too_many_anchors`, `slot_exhausted`, `pool_exhausted`, `replan_exhausted`.

### G6 — opener gate measured on the rendered master

`continuity.master_opener_report` reads `evidence/dj_p2_continuity_master.wav`
**from disk** and applies `verify._block_rms_dbfs` / `_silent_runs` — D12's own
block logic — to the first 30 s (600 blocks). Only the renderer's declared
`MASTER_FADE_IN_S` region is exempt, mirroring `_allowed_silence_regions_s`:

```json
{
  "window_s": 30.0,
  "measured_on": "evidence/dj_p2_continuity_master.wav",
  "measured_with": "verify._block_rms_dbfs/_silent_runs (D12 block logic)",
  "blocks": 600,
  "tiers": {
    "digital_minus90":  {"threshold_dbfs": -90.0, "min_run_s": 0.5,
                         "max_run_s": 0.0,  "violating_runs_s": [], "ok": true},
    "low_level_minus60": {"threshold_dbfs": -60.0, "min_run_s": 2.0,
                          "max_run_s": 0.65, "violating_runs_s": [], "ok": true}
  },
  "audibility_ok": true
}
```

### G7 — anchored plan → render → verify PASSES D12

Same v3 cache, same `--include-track rec-36bd9cb09f26f5b8`, both worktrees:

| | legacy @ 3822710 | this worktree |
|---|---|---|
| `audio` cue | 35.085 → 308.94 | **161.332 → 293.477** |
| anchor cue_out | 387.397 | **351.759** (downbeat before the 352.05 s gap) |
| verify verdict | **FAIL** | **PASS** |

Legacy master FAIL detail (`/tmp` evidence, reproduced in this report):

```
digital ok: False  runs: [[619.65, 620.25]]
low    ok: False   max_run: 15.2  worst: [110.65, 125.85]
```

The 619.65 s master position is the anchor's 352.05 s source gap landing
in-slot; the 15.2 s low-level run is `audio`'s quiet stretch inside its old
segment. New master (`evidence/dj_p2_continuity_verification.json`):

```
verdict: PASS | pass: True
digital: {"ok": true, "max_run_s": 0.0, "violating_runs_s": []}
low:     {"ok": true, "max_run_s": 1.9, "worst_region_s": [830.7, 832.6]}
allowed: [[0.0, 0.05], [1637.992, 1640.992]]
```

Identical track order both ways — the only delta is legitimate cue movement.

### G8 — legacy regression, byte-identical

Both worktrees planned the **same v2 snapshot**
(`evidence/analysis_cache_v2_snapshot.sqlite`, pre-contract payloads — the
records carry no continuity data, so the contract measures nothing and changes
nothing):

```
$ sha256sum evidence/dj_p2_g8_plan_legacy.json evidence/dj_p2_g8_plan_new.json
a4fbb6f7d8dbcacbcaf94e82663767ad461de5f74c15429f3e34a711c2bb5da3 *dj_p2_g8_plan_legacy.json
a4fbb6f7d8dbcacbcaf94e82663767ad461de5f74c15429f3e34a711c2bb5da3 *dj_p2_g8_plan_new.json
$ cmp … ; echo → CMP: identical
```

Legacy worktree HEAD: `38227106da4485e53f5fadf31543bc45be3b9d42`. On the *v3*
cache the plans differ — deliberately and only in cues, shown in G7. The
unmeasured-payload pass-through is unit-tested
(`test_unmeasured_records_never_pruned`) plus the existing
`tests/test_anchor.py` `LEGACY_GOLDEN` byte-identity tests, which still pass.

### G9 — verify.py thresholds unchanged

```
$ git diff HEAD -- ai_dj/verify.py
(empty — verify.py is not in the diff at all)
```

`DIGITAL_SILENCE_DBFS=-90.0`, `DIGITAL_SILENCE_MIN_RUN_S=0.5`,
`LOW_LEVEL_DBFS=-60.0`, `LOW_LEVEL_MIN_RUN_S=2.0`, `SILENCE_BLOCK_S=0.05` —
all untouched. The contract mirrors them as constants in `continuity.py` and
pins the equality in tests.

### G10 — full suite + lint/format

```
$ python -m pytest tests
85 passed, 1 skipped in 8.96s      # 64 pre-existing + 21 new continuity tests

$ ruff check .
All checks passed!

$ ruff format --check .
41 files already formatted
```

Line-ending contract: `sed -i 's/\r$//'` applied to every written file; every
file verified `open(p,'rb').read().count(b'\r') == 0` (all listed below).

## Changed files

```
 M ai_dj/analysis/analyzer.py  | +34  continuity fields, ANALYSIS_VERSION 3,
                                   payload-version staleness in Cache.get
 M ai_dj/cli.py               |  +8  exit code 4 for PLAN_INFEASIBLE
 M ai_dj/planner.py           | +322 hard-constraint pruning (_candidate_feasible),
                                   _select_order re-planning loop, PlanInfeasible,
                                   _assess_order/_build_tracks/_build_transitions/
                                   _layout_master extraction (identical geometry)
?? ai_dj/continuity.py        | new  D12-mirror metric, cue rule, assess_segment,
                                   find_master_violations, master_opener_report
?? tests/test_continuity.py   | new  21 tests covering G1–G6, G8 behaviour
```

No `.devin/` writes. No commits/pushes. No `ALTER TABLE`. No `verify.py` edits.

## Evidence files

```
evidence/dj_p2_continuity_measure.json        anchor intervals + opener + master-opener report
evidence/dj_p2_continuity_plan.json           anchored plan (anchor cue trimmed to 351.759 s)
evidence/dj_p2_continuity_render_report.json  render report (472 MB master, 1641 s)
evidence/dj_p2_continuity_verification.json   verify report — verdict PASS
evidence/dj_p2_continuity_infeasible.json     G5 PLAN_INFEASIBLE payload + exit code
evidence/dj_p2_g8_plan_legacy.json            legacy @ 3822710 on v2 snapshot
evidence/dj_p2_g8_plan_new.json               new code on v2 snapshot (identical)
evidence/dj_p2_g8_plan_legacy_on_v3.json      legacy code on v3 cache (FAIL demo plan)
evidence/analysis_cache_v2_snapshot.sqlite    pre-contract cache used for G8 input
evidence/dj_p2_continuity_master.wav          rendered master (PASS)
evidence/dj_p2_legacy_master.wav              rendered legacy plan (FAIL)
```

## Notes & limits

- `plan` reads `all_records()`; v2 payloads pass through as `unmeasured` —
  never pruned, never falsely flagged (unknown blocks excluded from the sim
  scan). The contract flow is `analyze → plan`: analyze refreshes stale rows.
- Post-order simulation uses *planned* segment lengths; rubberband output can
  differ by a few samples — verify.py remains the authoritative gate (D12 is
  never bypassed or weakened by the predictor).
- `assess_segment` trims only at downbeat boundaries and requires ≥ 30 s of
  played segment; where no such window exists the verdict is `infeasible`
  (`uncoverable_silence` / `no_valid_cue`), never a faked cue.
