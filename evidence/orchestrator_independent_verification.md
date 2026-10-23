# Hermes independent verification — DJ-P2-BEAT-GRID-001 (analyzer v4)

Run by the **orchestrator**, from **outside** the agent's worktree (an earlier agent run
deleted orchestrator-owned scripts under `scripts/`). Ground truth decided before analysis.

## Before (v3) vs after (v4)

| Tier | v3 errors > 2% | v4 errors > 2% | v3 max err | v4 max err |
|---|---|---|---|---|
| T1 controlled synthetic (41 tempos) | **13/41** | **4/41** | 50.50% | 50.003% |
| T2 musical synthetic (8 patterns) | **5/8** | **0/8** | 49.99% | 0.058% |

T2 cases fixed outright: `offladder_166_686` 83.354 -> **166.783**; `four_on_floor_126`
83.354 -> **126.001**; `swing_120` / `half_time_120` 117.454 -> **120.0**.

## Confirmed PASS (orchestrator-measured)

| # | Criterion | Measured |
|---|---|---|
| A1 | `median_ibi_s` off the legacy 23.22 ms raster | **0/41** records still on raster |
| A2 | BPM resolution < 1 BPM | max **0.00061** on all 41 |
| A4 | v3 cache fails closed | **exit 5**, `CORPUS_UNMEASURED`, missing_count 32 |
| A5 | existing tests pass | **139 tests OK** (skipped=1) |
| A6 | ruff check + format | `All checks passed!` / `58 files already formatted` |
| A7 | D6 synthetic timing probe | **0.2037 ms** (contract <= 20) |
| A9 | corpus before/after | **38/41** changed > 0.5 BPM, \|delta\| median **1.738**, max **16.321** |

Negative-case detection works: `neg_pure_tone` has `tempo_confidence` = **0.9** (wrong) but the
quality block flags it via `beat_env_ratio 1.07 < 2.0` -> `status: suspect`. The old confidence
metric would have accepted it.

## FAILED / INCOMPLETE

### F1 — octave ambiguity is NOT fixed, and fails SILENTLY (violates D26 B1)

The 4 remaining T1 failures are all exact half-time, and every one is reported as `status: 'ok'`
with an **empty `reasons` list** — a confident wrong answer with no warning:

| case | truth BPM | reported | ratio | status | reasons |
|---|---:|---:|---:|---|---|
| click_168.000 | 168.000 | 83.995 | 0.5 | `ok` | `[]` |
| click_174.000 | 174.000 | 87.004 | 0.5 | `ok` | `[]` |
| click_178.000 | 178.000 | 88.997 | 0.5 | `ok` | `[]` |
| click_180.000 | 180.000 | 90.003 | 0.5 | `ok` | `[]` |

D26 §7.6 B1 requires that no ~50% half-time case be marked verified. It is marked `ok`.
D26 §7.1 requires Multi-Hypothesis Octave Resolution — not implemented. The agent disclosed
this as a known limitation (it never saw D26; see the process note below).

### F2 — `beat_env_ratio` is numerically unbounded (new defect found by this verification)

| material | `beat_env_ratio` |
|---|---|
| synthetic clicks / drum patterns | **7.2e9 – 1.9e10** |
| corpus (real music) | 4.1 – 10.2 |
| pure tone / white noise | 1.07 – 1.24 |

The metric is `median(envelope at beat frames) / median(envelope)`. For sparse percussive
material the overall median is ~0, so the ratio explodes by ~9 orders of magnitude. The
`< 2.0` threshold gives the right answer on the three negative cases, but the metric is not
scale-invariant: it effectively tests "is the envelope median near zero", and its magnitude
carries no meaning. The agent's report calibrated it on corpus values (4.1–10.2) and never
measured the synthetic cases where it blows up.

Suggested direction (for the follow-up card): use a bounded score, e.g. compare onset strength
at beat frames against a high percentile of the envelope with an epsilon floor, so the value
is scale-invariant and finite.

## Process failures (orchestrator side, not the agent's)

1. The card `DJ-P2-BEAT-GRID-001.md` and decisions D19–D26 were **uncommitted** in the main
   repo when the worktree was created, so `git worktree add` (from HEAD `b8b3cd8`) did not
   carry them. The worktree's `DECISIONS.md` ends at **D18**. The agent read the card from the
   main repo on its first run but proceeded from the task text on the second.
2. D26 §7 (octave resolution, three-tier benchmark) was added to the card **after** the agent
   was launched, so the agent never saw it.
3. The prompt whitelist said "you may write to exactly these paths and nothing else", which the
   agent correctly read as "`scripts/` is off-limits" — and it **deleted** the orchestrator's
   known-answer scripts there. The harness was re-created outside the worktree.

## Verdict

**PARTIAL PASS.** The quantization defect (D19) is genuinely fixed and independently confirmed:
T1 13/41 -> 4/41 failures, T2 5/8 -> 0/8, and the tempo estimator now recovers off-ladder
tempos to <=0.06%. The quality block correctly catches fabricated grids where the old
`tempo_confidence` did not.

But the card cannot be closed: octave ambiguity remains, it is marked `ok`, and D26 §7.1 is
unimplemented. A follow-up card must fix octave resolution and make the half-time case
non-silent before any planner work (O4/O3) proceeds.