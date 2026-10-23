# DJ-P2-OCTAVE-001 — Multi-Hypothesis Octave Resolution + bounded envelope score

**Status:** READY
**Created:** 2026-10-23
**Created by:** Hermes (Lead Orchestrator)
**Parent:** `DJ-P2-BEAT-GRID-001` (PARTIAL PASS — see `D27`)
**Blocks:** `DJ-P2-BEAT-CLAIM-001` (O4/O3), G2b listening, G3-60MIN

## Context

Card `DJ-P2-BEAT-GRID-001` fixed the quantization defect (D19) and was independently
verified, but landed at **PARTIAL PASS** (`D27`). The tempo estimator now recovers off-ladder
tempos to ≤0.06%, but two defects remain. Neither may be waived.

## Problem

### F1 — octave ambiguity is not resolved, and it fails silently

D26 §7.1 requires **Multi-Hypothesis Octave Resolution**. It is not implemented. The known-answer
benchmark (orchestrator-run, `evidence/orchestrator_independent_verification.json`) shows 4 of 41
controlled cases still wrong by exactly one half:

| case | truth BPM | reported | ratio | `status` | `reasons` |
|---|---:|---:|---:|---|---|
| `click_168.000` | 168.000 | 83.995 | 0.5 | **`ok`** | **`[]`** |
| `click_174.000` | 174.000 | 87.004 | 0.5 | **`ok`** | **`[]`** |
| `click_178.000` | 178.000 | 88.997 | 0.5 | **`ok`** | **`[]`** |
| `click_180.000` | 180.000 | 90.003 | 0.5 | **`ok`** | **`[]`** |

The halved grid is **self-consistent**: residual std is ~1.6 ms and coverage ~0.96, both *better*
than many correct cases. No within-track metric can flag it, because the wrong grid explains the
audio as well as the right one. This violates **D26 §7.6 B1** ("no ~50% half-time case may be
marked verified").

The failure mode is the dangerous one: a **confident wrong tempo with no warning**, which the
planner would then build transitions around.

### F2 — `beat_env_ratio` is numerically unbounded

`beat_env_ratio = median(onset envelope at beat frames) / median(onset envelope)`. Measured by
the orchestrator's independent run:

| material | `beat_env_ratio` |
|---|---|
| synthetic click / drum pattern | **7.2e9 – 1.9e10** |
| corpus (real music) | 4.1 – 10.2 |
| pure tone / white noise | 1.07 – 1.24 |

For sparse percussive material the overall envelope median is ~0, so the ratio explodes by ~9
orders of magnitude. The `< 2.0` threshold happens to give the right answer on the three negative
cases, but the metric is **not scale-invariant** and its magnitude carries no meaning: it
effectively tests "is the envelope median near zero", not "is there transient support at the beat
frames". The v4 report calibrated it on corpus values and never measured the synthetic cases where
it blows up.

## Scope

1. **Implement Multi-Hypothesis Octave Resolution** (D26 §7.1). For every candidate tempo, evaluate
   `{B/2, B, 2B}` — a tempo-prior model plus an autocorrelation / onset-envelope check — and
   choose the level that best explains the onset structure. Return the chosen level, and record
   `octave_diagnosis` for each level evaluated.
2. **Make the half-time case non-silent** (D26 §7.6 B1). When the chosen level cannot be
   distinguished from its neighbours, the track must be returned as `OCTAVE_AMBIGUOUS` or
   `UNVERIFIED` — **never** `ok`. An unverifiable answer is a valid output; a silently wrong one
   is not. Do not invent confidence to avoid the ambiguity.
3. **Replace `beat_env_ratio` with a bounded, scale-invariant score.** Compare onset strength at
   beat frames against a high percentile of the envelope with an epsilon floor, so the value is
   finite, bounded, and comparable across material. Keep the negative cases flagged
   (`suspect`), including `neg_pure_tone` where `tempo_confidence` = 0.9.
4. **Keep the v3 fail-closed behaviour.** A corpus record without the continuity payload must
   still produce `plan` exit 5 with `CORPUS_UNMEASURED`.

## Out of scope

- Changing the tempo *raster* or the LS refinement (already verified, do not regress).
- Planner changes, render changes, the listening verdict.
- Touching `.orchestrator/`, `docs/`, `README.md`, or any config/dotfile.

## Acceptance criteria

Run the orchestrator harness `independent_beat_verification.py` (T1 41 tempos + T2 8 musical
patterns + 3 negative cases) and confirm:

| # | Criterion |
|---|---|
| **B1** | No ~50% half-time case is returned as `ok` / verified. Every ambiguous case carries an explicit `OCTAVE_AMBIGUOUS` / `UNVERIFIED` status or an explicit suspect reason. |
| **B2** | T1 controlled synthetic: **0** cases wrong by > 2% **or** flagged ambiguous. (A correct answer, or a declared cannot-determine, both count as passing.) |
| **B3** | T2 musical synthetic: stays at **0** errors > 2% (no regression). |
| **B4** | Negative cases: all three flagged `suspect`. |
| **B5** | `beat_env_ratio` (or its replacement) is finite and bounded across T1, T2 and the negative cases — report the observed min/max in the report. |
| **B6** | All 139 existing tests still pass; new tests added for the octave hypothesis and the bounded metric. |
| **B7** | `ruff check` and `ruff format --check` clean; D6 timing probe still ≤ 20 ms; v3 cache still fails closed with exit 5. |

## Definition of done

- Octave resolution implemented and covered by tests.
- Half-time failures non-silent (B1).
- Envelope score bounded and reported (B5).
- Harness re-run and B1–B7 recorded with actual numbers in `.agent_report.md`.
- No files modified outside the permitted write paths.

## Permitted write paths

You may **create or modify** only:

- source files under `ai_dj/`
- test files under `tests/`
- new files under `evidence/`
- `.agent_report.md`

**Do not modify or delete anything outside these paths.** In particular do not delete or alter
`scripts/`, `docs/`, `.orchestrator/`, `.github/`, `README.md`, `pyproject.toml`, or any
dotfile or config. Other agents own those.

## Reporting

Write `.agent_report.md` with:

- what you changed, per file
- the harness numbers before and after, as a table
- any claim you could **not** verify, stated as such
- explicit confirmation of which of B1–B7 you met

**`exit 0` is not `TASK_DONE`.** Do not claim completion on exit code alone; report the actual
numbers.
