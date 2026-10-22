# AI DJ

[![CI](https://github.com/xegheplimo-web/ai-dj/actions/workflows/ci.yml/badge.svg)](https://github.com/xegheplimo-web/ai-dj/actions/workflows/ci.yml)
[![CodeQL](https://github.com/xegheplimo-web/ai-dj/actions/workflows/codeql.yml/badge.svg)](https://github.com/xegheplimo-web/ai-dj/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Autonomous AI DJ engine for **Windows 11**: the AI decides the music (track order,
transition type, cue points, energy arc), and a deterministic engine executes the
timing — so musical decisions are made offline while beat alignment is done with
sample accuracy.

> Status: **P0 — vertical slice**. The engine is not built yet; what exists today is
> the verification harness and the CI contract that every future gate must satisfy.

## Why this design

Two claims drive the architecture, both measured on the target machine:

1. **Musical decisions are offline.** No LLM or analysis runs inside the audio path.
2. **Timing is deterministic and measurable.** A synthetic click track with beat
   positions known *before* processing is the ground truth — the engine cannot be
   validated against its own output (no circular validation).

Measured baseline (2026-10-22, Windows 11 / RTX 3090 / Python 3.13):

| Item | Result | How measured |
|---|---|---|
| FFmpeg `rubberband` tempo ×1.08 | beat deviation **0.20 ms** (contract ≤ 20 ms) | `scripts/timing_probe.py` |
| Audio stack | librosa 1.0.0, numba 0.68, sounddevice 0.5.6 | `pip list` |
| Output stream | opens on Realtek / FxSound / NVIDIA HDMI | `sounddevice.OutputStream` |

## Layout

```
ai_dj/            engine package (timing verification today; analyzer/planner/renderer next)
scripts/          verification probes (CLI)
tests/            unit + end-to-end tests
evidence/         machine-readable verification evidence (JSON)
.orchestrator/    project state, decisions, verification ledger
```

## Gates

| Gate | Contract | Status |
|---|---|---|
| G0 | CLI + Unicode corpus scan + stable cache/replay | ☐ |
| G1 | Produce and play a 6–8 track mini-set without manual intervention | ☐ |
| G2a | Synthetic beat timing within 20 ms; no dropout/clipping | ✅ harness in place |
| G2b | Listening verdict on transitions (no forced beatmatch of incompatible tracks) | ☐ |
| G3 | 60-minute set, self-selected, no intervention (P1 — required before release) | ☐ |
| G4–G6 | Live adaptation, advanced recovery, UI | ☐ |

## Develop

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\timing_probe.py --bpm 120 --tempo 1.08
```

`ffmpeg` with the `rubberband` filter must be on `PATH` for the timing gate
(the probe skips with a clear message when it is absent).

## CI

- `ci.yml` — lint (ruff check + format + compileall), unit tests on Ubuntu
  Python 3.11–3.13 and Windows Python 3.12, and an **E2E** job that runs the
  synthetic ground-truth timing probe on real FFmpeg and uploads the evidence.
- `codeql.yml` — Python code scanning (push/PR + weekly).
- `dependabot.yml` — GitHub Actions + pip.

## License

MIT — see [LICENSE](LICENSE).
