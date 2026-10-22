# DJ-P2-CONTINUITY-002 — Final Contract Hardening: implementation report

Hardening of the Unified Audio Continuity Contract per the GPT-approved plan
**A + C + F + (a)** (`docs/gpt-decision-hardening-2026-10-23.md`).

Environment: Python 3.13.15 (`F:/ai-dj-venv/Scripts/python.exe`), pytest 9.1.1,
ruff 0.17.0, ffmpeg on PATH, sounddevice 0.5.6.

Working tree is intentionally **dirty and uncommitted** (Hermes owns git).

---

## C1 — Fail-closed continuity input validation (Flag #1 = A, NO waiver)

`plan` now validates continuity-payload completeness of the *selectable pool*
(the same pool the search would use: `_eligible` → `_dedup_works`, honouring
`--include-ineligible`) **before** beam search:

- `ai_dj/continuity.py::has_continuity_payload` — single test shared with
  `assess_segment` (current `continuity_version` + non-empty `block_rms_dbfs`).
- `ai_dj/planner.py::unmeasured_corpus_error` — all missing →
  `CORPUS_UNMEASURED`; subset missing → `ANALYSIS_REQUIRED` with
  `missing_count` + `missing_recording_ids`.
- `ai_dj/cli.py::cmd_plan` — emits the structured error on stderr, **exit 5**
  (data-not-analyzed); exit 4 stays `PLAN_INFEASIBLE`. No `--allow-unmeasured`.

Real output — `python -m ai_dj.cli plan --cache <all-unmeasured.sqlite>`:

```json
{
  "error": "CORPUS_UNMEASURED",
  "reason": "missing_continuity_data",
  "analysis_version_required": 3,
  "continuity_version_required": 1,
  "missing_recording_ids": ["rec-1", "rec-2", "rec-3", "rec-4"],
  "missing_count": 4,
  "message": "Every usable corpus record lacks the continuity payload — run analyze before planning."
}
```
exit_code: **5**, plan file written: **false**

Real output — partial missing (`rec-2`, `rec-4` unmeasured):

```json
{
  "error": "ANALYSIS_REQUIRED",
  "reason": "missing_continuity_data",
  "analysis_version_required": 3,
  "continuity_version_required": 1,
  "missing_recording_ids": ["rec-2", "rec-4"],
  "missing_count": 2,
  "message": "Re-analyze the corpus before planning."
}
```
exit_code: **5**, plan file written: **false**

Evidence: `evidence/dj_p2_continuity002_c1_fail_closed.json` (subprocess-captured).
Tests: `TestFailClosedPlanInput` — 7 cases incl. unmeasured-anchor →
data error (not infeasible), stale v2 rows, ineligible exclusion not blocking.

## C2 — G8 re-chart on the v3 snapshot

- Legacy v2 golden (HEAD b22eeb6) moved out of `test_anchor.py` into
  `tests/fixtures/legacy_golden_plan_v2.json` — archived, reference only.
- Current gate: same v3 snapshot + algorithm/params/seed → byte-identical
  `plan.json`. Tested at both the planner level and the CLI level; **no**
  production waiver was added.

Real output — two `plan` runs on the same seeded v3 cache:

```
run_1 sha256: 0ebe89f47283df9901f57e3fda9c77b7c89dde21db67d7805a5730784ad3d586  exit 0
run_2 sha256: 0ebe89f47283df9901f57e3fda9c77b7c89dde21db67d7805a5730784ad3d586  exit 0
byte_identical: true
```

Evidence: `evidence/dj_p2_continuity002_g8_determinism.json`.
Tests: `TestG8CurrentContract`, `test_no_anchor_plan_is_byte_deterministic`.

## C3 — Verify-by-render playback gate (Flag #2 = C, no double render)

Pipeline is `PLAN → RENDER → VERIFY → PLAY`; `plan` renders nothing:

- New `ai_dj/verdict.py` — `verify` writes a `<master>.verify.json` sidecar
  recording the verdict **bound to SHA-256 of BOTH plan.json and the WAV**
  (`write_verdict`/`artifact_binding`; binding also rides on the verify report).
- `ai_dj/player.py::play_master` calls `require_play_gate` **before** any
  sounddevice use: missing file, unreadable/non-PASS verdict, unbound verdict,
  or either artifact changed → `PlaybackRefused` → CLI exit 1 with
  `PLAYBACK_REFUSED` + machine `reason`. Accepted reports carry `verify_gate`.
- `verify.py` untouched (thresholds and code unchanged — H10 diff is empty).
- Wording fixed: `planner.py` — "the simulation does NOT prove the render
  passes D12 — it predicts feasibility"; `verdict.py`/`DECISIONS.md` (D18).

Real output (minimal PASS-able plan → render exit 0 → verify PASS):

```json
verdict sidecar (master.verify.json):
{"verdict": "PASS", "pass": true, "verified_by": "ai_dj.verify",
 "plan_sha256": "c7e2f5315e92bca3f27f81732f2a08be14dc533957d83fd4749b767bf31363d9",
 "master_sha256": "33ec80eaa75182eeea46d10f61f746714e4dd01b12d6a45b6b650cd017a735c5"}
```

| play attempt | exit | reason |
|---|---|---|
| no verdict file | 1 | `verify_verdict_missing` |
| plan.json +1 byte after PASS | 1 | `verify_plan_changed` |
| master.wav 1 flipped byte after PASS | 1 | `verify_master_changed` |
| bound PASS intact | **0** | played — 480000 frames, 0 underflows |

Evidence: `evidence/dj_p2_continuity002_play_gate.json`.
Tests: `TestVerifiedArtifactGate` — 7 cases (missing/corrupt/non-PASS/stale/
accept-with-mocked-sounddevice).

## C4 — Adversarial predictor quantification

New `tests/test_predictor_adversarial.py` runs the predictor
(`find_master_violations`) against the real verifier
(`verify._silence_check`) on real renders; outcomes classified
agreement / false-positive / false-negative.

Measured summary (real run, `evidence/dj_p2_continuity002_adversarial.json`):

| case | harness | predictor | verifier | outcome |
|---|---|---|---|---|
| phase_cancellation_constructed | constructed master | PASS | FAIL (4.0 s digital hole) | **false_negative** |
| phase_cancellation_render | render_plan | PASS | PASS | agreement_clean |
| gain_peak_scale_gap | render_plan | PASS | FAIL (-60 dBFS 3.0 s run) | **false_negative** |
| fade_crossfade_quiet_pair | render_plan | FAIL (predicted 2.0 s) | PASS | false_positive |
| time_stretch | render+rubberband | FAIL (predicted 0.5 s) | PASS (real 0.4 s) | false_positive |
| block_boundary | render_plan | FAIL (predicted 10 blk) | PASS (real 9 blk) | false_positive |

- **2 false negatives** (predictor PASS, verifier FAIL) — both recorded as
  `regression_fixture: true` in `tests/fixtures/predictor_divergence.json`,
  pinned by `test_every_false_negative_is_a_recorded_fixture` and
  `test_outcomes_match_recorded_fixtures`.
- Root causes documented in the fixture: max-of-contributors is **phase-blind**
  and does not model the renderer's **post-mix peak-protection scale**; the
  conservative bound also over-flags (equal-power sum, stretch shrinking a
  hole, block-straddle dilution) — all in the *safe* direction.
- No planner/renderer agreement is required — the verifier is authoritative.

## C5 — D17: Cue Boundary Is Not Phrase-Safe (Flag #3 = F)

- `.orchestrator/DECISIONS.md` — **D17** added with all card points: cue is an
  *estimated* downbeat, not a proven phrase boundary; trimming may drop
  vocal/outro content; anchor inclusion + D12 PASS ≠ full preservation;
  significant trims must be reported; G2b listening verdict mandatory; phrase
  engine only after an evaluated audio set exists. **D18** records the
  verify-by-render gate + fail-closed corpus decisions.
- Transparency implemented: `planner._build_tracks` now attaches
  `cue_report` to any adjusted cue — `default_cue_in_s`/`default_cue_out_s`,
  final `cue_in_s`/`cue_out_s`, `removed_head_s`/`removed_tail_s`/
  `removed_total_s`, `reason: "uncovered_silence_run"`
  (tests: `TestCueTrimReporting`).

---

## Acceptance gates H1–H10 — real outputs

| Gate | Result | Evidence |
|---|---|---|
| H1/H2 | `plan` exit 5 + `CORPUS_UNMEASURED`/`ANALYSIS_REQUIRED`, no plan file | `dj_p2_continuity002_c1_fail_closed.json` |
| H3 | measured corpus → exit 0, plan written | `dj_p2_continuity002_g8_determinism.json` run_1 |
| H4 | two runs → identical sha256 `0ebe89f4…d586` | same file, `byte_identical: true` |
| H5 | play without verdict → `PLAYBACK_REFUSED/verify_verdict_missing` | `dj_p2_continuity002_play_gate.json` |
| H6 | plan/WAV byte change → `verify_plan_changed`/`verify_master_changed` | same file |
| H7 | 6 adversarial cases, 2 FN fixtured | `dj_p2_continuity002_adversarial.json` + `tests/fixtures/predictor_divergence.json` |
| H8 | D17 + D18 in `DECISIONS.md` | `.orchestrator/DECISIONS.md` |
| H9 | `107 passed in 11.46s`; `ruff check .` → All checks passed; `ruff format --check .` → 48 files already formatted | below |
| H10 | `git diff ai_dj/verify.py` → **empty** | below |

```text
$ pytest tests/ -x -q
107 passed in 11.46s
$ ruff check .
All checks passed!
$ ruff format --check .
48 files already formatted
$ git diff ai_dj/verify.py
(empty)
Line-ending audit: 16 written files, 0 carriage returns (byte check).
```

## Changed files (dirty, uncommitted)

```
 M .orchestrator/DECISIONS.md      D17 + D18
 M ai_dj/cli.py                   C1 input gate (exit 5) + verify->verdict write + PlaybackRefused
 M ai_dj/continuity.py            has_continuity_payload
 M ai_dj/planner.py               unmeasured_corpus_error, cue_report, wording fix
 M ai_dj/player.py                require_play_gate + verify_gate in report
 M tests/test_anchor.py           v2 golden archived; current determinism tests
?? ai_dj/verdict.py               SHA-256-bound verdict sidecar + play gate
?? tests/test_hardening.py        18 tests: C1/C2/gate/SHA-binding/cue_report
?? tests/test_predictor_adversarial.py  adversarial harness (5 categories)
?? tests/fixtures/legacy_golden_plan_v2.json   archived v2 golden (reference)
?? tests/fixtures/predictor_divergence.json    recorded outcomes incl. 2 FN fixtures
?? scripts/gen_continuity002_evidence.py       regenerates the evidence JSONs
?? evidence/dj_p2_continuity002_{c1_fail_closed,g8_determinism,play_gate,adversarial}.json
?? evidence/dj_p2_continuity002_report.md      this report
```

`.orchestrator/TASKS.json` is Hermes's dispatch bookkeeping (status flip),
untouched by the implementation.

## Constraints honoured

No commit/push · no `.devin/` writes · `verify.py` unchanged (empty diff) ·
no `--allow-unmeasured` · no new dependencies · SQLite schema unchanged
(payload-JSON versioning only) · all written files LF-clean (0 `\r`).
