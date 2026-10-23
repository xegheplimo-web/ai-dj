# DJ-P2-BAR-001 — Bar-level evidence for octave resolution + ambiguity calibration

**Status:** READY
**Created:** 2026-10-23
**Created by:** Hermes (Lead Orchestrator)
**Parent:** `DJ-P2-OCTAVE-001` (PASS B1–B7, defect F3 open — see `D29`)
**Blocks:** `DJ-P2-BEAT-CLAIM-001` (O4/O3), G2b listening, G3-60MIN, PILOT 5 phút

## Context

`DJ-P2-OCTAVE-001` fixed the half-time direction: a fast tempo that was being halved is now
resolved correctly (T1 4/41 → 0/41, independently verified in `D29`). It also replaced the
unbounded `beat_env_ratio` with a bounded, scale-invariant `beat_env_score`, and made
irresolvable cases declare `octave_ambiguous` instead of guessing.

But the resolver only uses **the onset lattice**. That is not enough, and it fails in the
opposite direction.

## Problem — F3: the resolver DOUBLES slow material

Independently measured by the orchestrator (`scripts/orchestrator_adversarial_octave.py`,
`evidence/orchestrator_adversarial_octave.json`):

| probe | truth | detected | corrected | error | ambiguous |
|---|---:|---:|---:|---:|---|
| synthetic `slow_drum_70` | 70.0 | 139.9986 | **139.999** | **+99.999%** | True |
| real corpus `Gõ Cửa - Bolero Cover – Duy` | ~96 | 96.0743 | **192.149** | **+100%** | **False** (margin 0.1442) |

`slow_click` cases 60–96 BPM are all correct, and `slow_drum_80` / `slow_drum_90` are correct.
The failure is **material-dependent**, not a clean tempo cut-off.

The corpus case is the worse one: a Bolero ballad at ~96 BPM is doubled to 192 BPM with a
**decisive** margin (0.1442, well above the 0.08 ambiguity threshold), so the resolver reports
**no ambiguity at all**. 192 BPM is musically absurd for a Bolero.

**Root cause.** The scorer is
`acf_norm + 0.25*strength + 0.20*prior − 0.35*anti_penalty` over `{0.5, 1.0, 2.0}`. A slow track
whose onsets subdivide the beat (hi-hats on eighths, or a kick/snare alternation) presents an
onset lattice **identical** to the doubled tempo, so the lattice cannot separate them. The tempo
prior is too weak (weight 0.20) to break the tie, and there is **no bar-level / backbeat
information at all** — which is exactly what a human uses to say "this Bolero is 96, not 192".

### Second issue — the ambiguity boundary is fragile

Corpus rescan (41 tracks): margin min 0.0097 / p25 0.1001 / median 0.1448 / max 0.3234.
Distribution around the 0.08 threshold: **6 below · 8 inside 0.08–0.12 · 27 above 0.12**.
The 8 tracks just above the line can flip with any small scoring change. The threshold is
currently a constant with no calibration evidence behind it.

## Scope

1. **Add bar-level / backbeat evidence to the resolver.** The distinguishing signal between
   "70 BPM with eighths" and "140 BPM" is the **bar structure**: which onsets are strong (kick /
   downbeat) versus weak (snare / off-beat). Use onset-envelope accent at the candidate's
   downbeat positions — or a bar-level autocorrelation at 2×/4× the candidate period — so a
   candidate whose strong-onset alignment is inconsistent with the accent pattern is penalised.
   A candidate is only chosen ×2 when the doubled level actually explains the accents.
2. **Make a wrong ×2 non-silent.** If the chosen level cannot be distinguished from its
   neighbours, it must be `octave_ambiguous` — the same rule already applied to the halving
   direction. The `Gõ Cửa` case must not be able to return a confident ×2.
3. **Calibrate the ambiguity threshold against evidence, not a constant.** Measure the margin
   distribution on the corpus and on the synthetic tiers, and pick/document the boundary with the
   numbers behind it. If the boundary cannot be justified, say so rather than keeping 0.08.
4. **Do not regress the halving direction.** The 4 half-time cases (`click_168/174/178/180`) must
   stay correct, and `beat_env_score` must stay bounded and scale-invariant.

## Out of scope

- The quantization raster and the LS refinement (already verified — do not touch).
- `beat_env_score`'s definition (already verified correct).
- Planner, render, listening verdict, corpus annotation.
- Touching `.orchestrator/`, `docs/`, `scripts/`, `README.md`, or any config/dotfile.

## Acceptance criteria

Run `scripts/orchestrator_adversarial_octave.py` and
`scripts/orchestrator_independent_beat_verification.py` (the orchestrator's harnesses — **do not
modify them**, they are the independent check) and confirm:

| # | Criterion |
|---|---|
| **C1** | `slow_drum_70` resolves to ~70 BPM, **or** is explicitly `octave_ambiguous`. A confident 140 is a failure. |
| **C2** | The real corpus case `Gõ Cửa - Bolero Cover – Duy` resolves to ~96 BPM, **or** is explicitly `octave_ambiguous`. A confident 192 is a failure. |
| **C3** | The four half-time cases `click_168/174/178/180` stay correct (no regression). |
| **C4** | T1 (41 tempos) and T2 (8 musical patterns) keep **0** errors > 2%. |
| **C5** | All 3 negative cases stay `suspect`. |
| **C6** | The ambiguity threshold is documented with the measured margin distribution behind it. |
| **C7** | 151+ tests pass; `ruff check` + `ruff format --check` clean over the whole repo; D6 timing probe ≤ 20 ms; stale cache still fails closed with exit 5. |

## Definition of done

- Bar-level evidence implemented and covered by tests.
- C1 and C2 met (correct answer, or an honest `octave_ambiguous`).
- Threshold calibrated with recorded numbers.
- The orchestrator harnesses re-run and C1–C7 reported with actual numbers in `.agent_report.md`.
- No files modified outside the permitted write paths.

## Permitted write paths

You may **create or modify** only:

- source files under `ai_dj/`
- test files under `tests/`
- new files under `evidence/`
- `.agent_report.md`

**Do not modify or delete anything outside these paths.** In particular do not touch `scripts/`
(the orchestrator's independent harnesses live there — modifying them invalidates the
verification), `docs/`, `.orchestrator/`, `.github/`, `README.md`, `pyproject.toml`, or any
dotfile or config.

Do not create a virtualenv, do not `pip install`, do not run any setup wizard, and do not
scaffold `.devin/`, `AGENTS.md`, `CLAUDE.md`, `.editorconfig`, `.gitignore` or `.gitattributes`.

## Reporting

Write `.agent_report.md` with:

- what you changed, per file
- the adversarial probe before/after as a table (`slow_drum_70`, `Gõ Cửa`, the four half-time cases)
- the measured margin distribution and the threshold you chose, with the reasoning
- any claim you could **not** verify, stated as such
- explicit confirmation of which of C1–C7 you met

**`exit 0` is not `TASK_DONE`.** Report actual numbers. Independent verification is performed by
the orchestrator, so a self-report alone is never sufficient.
