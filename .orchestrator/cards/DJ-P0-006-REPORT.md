# DJ-P0-006 — Report: full-master continuity gate + INCONCLUSIVE verdict

**Coder:** Devin CLI · **Date:** 2026-10-22
**Status:** implemented & verified locally. Not committed (Hermes owns git).

Environment used for all commands: `F:\ai-dj` on Windows (Git Bash),
project venv `F:\ai-dj-venv\Scripts\python.exe` = Python 3.13.15
(`ai_dj` installed editable), ruff 0.17.0 on PATH.

---

## 1. What changed

| File | Change |
|---|---|
| `ai_dj/verify.py` | **D12** — `_silence_check` now gates silence over the **whole** master: hops overlapping a deliberate region are masked out before runs are measured, and `silence_ok` is `max_silent_run_ms <= MAX_SILENT_RUN_MS` on what remains. Deliberate regions (new helpers `_silent_runs`, `_allowed_silence_regions_s`): the renderer's own fade-in (`[0, MASTER_FADE_IN_S]`) and fade-out (`[len-MASTER_FADE_OUT_S, len]`) edges, plus optional plan-declared `allowed_silent_gaps_s` (`[[start_s, end_s), ...]`, master timeline; malformed entries ignored — none exist today). Silence block now reports `max_silent_run_ms` + `worst_region_s` (the gate value + offender), `silent_hops_total`, `allowed_silent_regions_s` (echoed for evidence), and `transition_windows` `{max_silent_run_in_transitions_ms, worst_region_s}` kept as diagnostics. **D13** — `verify_master` builds `required_checks` as tri-state (True/False/None=unmeasured): any measured failure → `pass: false`/`FAIL`; else any unmeasured → `pass: null`/`INCONCLUSIVE`; else `pass: true`/`PASS`. Report gains `verdict` + `unmeasured_checks`. |
| `ai_dj/cli.py` | `cmd_verify`: generic exception from `verify_master` (e.g. corrupt/unreadable WAV) → exit 2 with JSON error; `pass is None` → exit 2. Codes now: 0 = PASS, 1 = FAIL, 2 = INCONCLUSIVE/usage-error. |
| `tests/test_engine.py` | New `TestVerifier` (5 tests) + helpers `_tone_wav` (continuous 220 Hz stereo tone with optional zeroed gaps) and `_verify_plan`. Covers: hole outside transitions fails + names region; fade-edge + plan-declared gaps exempt; clean master `pass: true` (ffmpeg-gated); no-ffmpeg → `pass`/`verdict` INCONCLUSIVE with no fabricated dBTP; CLI exit codes 2/2/1/2 (missing master, corrupt master, hole, no-ffmpeg). |

`ai_dj/render.py`, `ai_dj/timing.py`, `scripts/timing_probe.py`,
`.orchestrator/INTERFACES.md`, `README.md`, `docs/**`, `LICENSE`,
`requirements.txt`, `pyproject.toml` untouched.

Design notes for Hermes:

- **Threshold unchanged** — `MAX_SILENT_RUN_MS = 100.0` now applies to the
  whole master, not only transitions. `SILENCE_PEAK = 1e-4` is near-digital
  silence; both corpus masters measure 0.0 s or 29.45 s, nothing in between,
  so 100 ms is a safe strict contract. If a real set ever needs a longer
  allowance it's a one-constant tweak (D12's open question for GPT).
- **Precedence**: a *measured* failure beats unmeasured — if ffmpeg is absent
  **and** the master has a hole, verdict is FAIL (the failure is known, not
  inconclusive). `synthetic_alignment_check` erroring (e.g. no ffmpeg → no
  rubberband) counts as *unmeasured*, not a beat failure.
- Boundary semantics: a silent run is only exempt for the part inside an
  allowed region — a run starting 50 ms before the fade-out still counts as a
  50 ms run (masked-hop granularity), then `<= 100 ms` decides.
- `evidence/verification.json` (committed in `ceb6952`) still shows the
  original false-PASS — left as the historical proof.

## 2. Acceptance 1 — regression on the proven defect

```
$ python -m ai_dj.cli render --plan evidence/hermes_plan1.json --out evidence/dj_p0_006_holey.wav
```

Render report (excerpt): `duration_s: 1307.261` (plan 1307.266),
`true_peak_dbtp: -1.2`, `clipping_count: 0`, exit 0.

```
$ python -m ai_dj.cli verify --plan evidence/hermes_plan1.json --master evidence/dj_p0_006_holey.wav --out evidence/dj_p0_006_verification.json
```

Output excerpt:

```json
"silence": {
  "max_silent_run_ms": 29450.0,
  "worst_region_s": [90.0, 119.45],
  "silent_hops_total": 589,
  "allowed_silent_regions_s": [[0.0, 0.05], [1304.261, 1307.261]],
  "transition_windows": {
    "max_silent_run_in_transitions_ms": 0.0,
    "worst_region_s": null
  },
  "silence_ok": false
}
```

```json
"pass": false,
"verdict": "FAIL",
"unmeasured_checks": []
```

`EXIT=1`. The gate names exactly the hole Hermes measured independently:
29.45 s @ 90.0 s (was `pass: true` before). True peak `-1.2 ≤ -1.0 dBTP`,
clipping 0 — the only failure is the hole.

## 3. Acceptance 2 — no regression on the clean master

```
$ python -m ai_dj.cli verify --plan evidence/plan_eligible.json --master evidence/eligible_set.wav --out evidence/dj_p0_006_verification_eligible.json
```

Output excerpt:

```json
"silence": {"max_silent_run_ms": 0.0, "worst_region_s": null,
            "silent_hops_total": 0, "silence_ok": true, ...}
```

```json
"pass": true,
"verdict": "PASS",
"unmeasured_checks": []
```

`EXIT=0`. Independent measurement of the same file: longest silent run
**0.0 s** (scan: 50 ms hops, peak < 1e-4). Note `beat_deviation_transitions[1].within_threshold: false` is real-music onset diagnostics (p95 80.6 ms) — not a gate check, per the existing note.

## 4. Acceptance 3 — INCONCLUSIVE (ffmpeg removed from PATH)

```
$ PATH=/usr/bin /f/ai-dj-venv/Scripts/python.exe -m ai_dj.cli verify --plan evidence/plan_eligible.json --master evidence/eligible_set.wav --out evidence/dj_p0_006_verification_noffmpeg.json
```

(`PATH=/usr/bin` → `shutil.which("ffmpeg")` is `None`; verified before the run.)

Output excerpt:

```json
"true_peak": {"dbtp": null, "limit_dbtp": -1.0,
              "measured_by": "unavailable: ffmpeg not on PATH",
              "integrated_lufs": null, "ok": null}
"synthetic_alignment_check": {"probe": "synthetic_click_tracks",
  "error": "RuntimeError: ffmpeg not found on PATH (required for tempo stretch)"}
```

```json
"pass": null,
"verdict": "INCONCLUSIVE",
"unmeasured_checks": ["true_peak", "synthetic_alignment"]
```

`EXIT=2`. No fabricated true-peak number; every measurable check still ran
and passed. Before this change the same run ended `pass: true`
(`peak_ok is not False`) — the false-PASS class D13 removes.

## 5. Acceptance 4 — tests, lint, format

```
$ python -m unittest discover -s tests
Ran 49 tests in 4.899s
OK
```

(44 before + 5 new in `TestVerifier`; ffmpeg-dependent tests ran, ffmpeg is
installed here.)

```
$ ruff check ai_dj tests scripts
All checks passed!
$ ruff format --check ai_dj tests scripts
14 files already formatted
```

New tests were written red-first: against the old code the hole test saw
`pass: True` (`assertIs(report["pass"], False)` → `True is not False`), the
inconclusive test saw `pass: False` instead of `None`, and the rest failed on
missing `verdict`/`max_silent_run_ms` keys.

## 6. Acceptance 5 — transition windows still reported

`silence.transition_windows` carries `max_silent_run_in_transitions_ms` +
`worst_region_s` on every report (0.0/null on both masters above) — same data
as before, now explicitly diagnostic detail.

## 7. Not verified / caveats

- No real master with a *short* (< 3 s) internal hole was run; the 100 ms
  boundary itself is covered by unit fixtures only.
- `allowed_silent_gaps_s` is a new optional plan field — verified via unit
  test, never produced by `ai_dj.planner` (by design: "none exist today").
- Report JSONs written to `evidence/dj_p0_006_verification*.json`;
  `evidence/dj_p0_006_holey.wav` (376 MB, gitignored like other evidence WAVs).
