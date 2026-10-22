# DJ-P0-006b — Report: two-tier silence, artifact integrity, exit codes

**Coder:** Devin CLI · **Date:** 2026-10-22
**Status:** implemented & verified locally. Not committed (Hermes owns git).

Environment: `F:\ai-dj` on Windows (Git Bash), venv
`F:\ai-dj-venv\Scripts\python.exe` = Python 3.13.15 (`ai_dj` editable),
ruff 0.17.x, ffmpeg on PATH (winget Links).

---

## 1. What changed

| File | Change |
|---|---|
| `ai_dj/verify.py` | **Two-tier D12 gate.** `_silence_check` now takes the full multi-channel array (no more `data.mean(axis=1)` mono mixdown — Rule A). `_block_rms_dbfs` computes non-overlapping 50 ms blocks with `RMS = sqrt(sum_c sum_n x[n,c]^2 / (N*C))` via `einsum` (no squared copy). Two tiers via `_tier_report`: `digital_silence` ≤ −90 dBFS fails on a run ≥ 0.5 s; `low_level` ≤ −60 dBFS fails on a run ≥ 2.0 s. Run lengths are compared **in samples** at the master rate with `>=` (`(e-s)*blk >= round(min_run_s*sr)` — Rule C, inclusive boundary). `silence_ok = digital.ok and low.ok`. **Exemption** unchanged in mechanism, restated per Rule B: only blocks overlapping the renderer's declared `MASTER_FADE_IN_S`/`MASTER_FADE_OUT_S` sample ranges or plan-declared `allowed_silent_gaps_s` are masked; everything is still counted in the report. **New diagnostics:** `channel_rms_dbfs` (per-channel, whole file) + `dead_channels` (channel RMS ≤ −90 dBFS — flag only, not a gate), `short_term_3s` `{min_dbfs, at_s}` (3 s sliding-window energy, diagnostic only). `transition_windows` kept as diagnostic. `max_silent_run_ms`/`worst_region_s`/`silent_hops_total`/`allowed_silent_regions_s` kept (digital-tier values) for report continuity. **Artifact integrity (Gap 2).** `verify_master` no longer raises on missing/corrupt master — it returns `_artifact_fail_report`: `checks.artifact {exists, decodable, error, ok:false}`, `pass:false`, `verdict:"FAIL"`, all downstream checks listed in `unmeasured_checks`. Normal reports now include `checks.artifact {ok:true, channels, frames}` and `artifact` is in `required_checks`. |
| `ai_dj/cli.py` | Exit-code contract (Gap 3): PASS → 0, FAIL → 1, `pass is None` (INCONCLUSIVE) → **3** (was 2), usage/environment errors → 2. Removed the `FileNotFoundError` handler (missing master is a FAIL report now, not an exception). Module docstring updated. Usage errors emit `{"error": ...}` on stderr — never a verdict-shaped report. |
| `tests/test_engine.py` | `_tone_wav` extended with `regions=[(s,e,dbfs)]` (sine at RMS dbfs, `None` = zeros) and `mode` (`same`/`antiphase`/`dead_right`). New tests (7): low-level −60.5 dBFS 2 s FAIL; inclusive boundaries (exactly 0.5 s / 2.0 s fail, 0.45 s / 1.95 s pass); anti-phase stereo NOT silence; dead-channel flag; missing master → FAIL report; corrupt master → FAIL report; no-ffmpeg + hole → FAIL. `test_verify_cli_exit_codes` rewritten for the 0/1/3/2 contract incl. argparse usage error. Hole regression test extended to assert **both** tiers fire. |

Not touched: `ai_dj/timing.py`, `scripts/timing_probe.py`, `.orchestrator/INTERFACES.md`, `README.md`, `docs/**`, `LICENSE`, `requirements.txt`, `pyproject.toml`, `ai_dj/render.py`, `ai_dj/player.py`. No new dependencies.

## 2. Design notes for Hermes

- **Dead channel is flag-only** — the card says "report per-channel RMS and flag a dead channel separately"; I read "flag" as diagnostic, not gate. `dead_channels` lists channel indices with whole-file RMS ≤ −90 dBFS. Easy to promote to FAIL if intended.
- **`allowed_silent_gaps_s` kept** — Rule B's "exempt only samples inside the fade-in/out the renderer actually declared" is read as fixing the *edge* exemption to the renderer's real fade ranges (not arbitrary head/tail seconds). Plan-declared gaps remain a separate deliberate-silence mechanism (existing test relies on it). If the strict reading is intended — fades only, no declared gaps — say so and I'll remove it.
- **Unreadable plan → exit 2.** "Required artifact" in Gap 2 is the master under verification. A plan that can't be parsed is an invocation error (argparse convention); verification can't start, so nothing is measured.
- **`verify_master` writes `--out` even for artifact-FAIL** — a FAIL report is valid evidence; only usage errors emit no report.
- **Block-granularity caveat:** detection is per-50 ms-block RMS, so a silence span that starts/ends mid-block is quantized to block boundaries (a partial loud block lifts that block's RMS — conservative, never widens the hole).
- **float64 fix found during verification:** the 3 s short-term diagnostic originally `cumsum`'d float32 energies; differences of ~1e2 prefix sums catastrophically cancelled quiet windows into the −240 dB floor (visible as a bogus `min_dbfs:-240` on `eligible_set.wav`). Now `np.cumsum(ms, dtype=np.float64)`.
- **Toolchain hook:** the session-start `project-toolchain-setup` hook was **not** run — card §6 forbids any writes under `.devin/` and `AGENTS.md` (including a `.dismissed` marker). Left untouched; documented here instead of `docs/dev-toolchain.md` since `docs/**` is out of scope.

## 3. §7 Acceptance — commands and outputs

### 1. `old_buggy.wav` (`evidence/dj_p0_006_holey.wav`, 29.45 s hole) → FAIL, both tiers

```
$ python -m ai_dj.cli verify --plan evidence/hermes_plan1.json --master evidence/dj_p0_006_holey.wav --out evidence/dj_p0_006b_verification_holey.json
EXIT=1
```

```json
"verdict": "FAIL", "pass": false,
"digital_silence": {"ok": false, "worst_region_s": [90.0, 119.45],
                    "max_run_s": 29.45, "violating_runs_s": [[90.0, 119.45]],
                    "silent_blocks": 589}
"low_level":       {"ok": false, "worst_region_s": [89.25, 121.95],
                    "max_run_s": 32.7,
                    "violating_runs_s": [[89.25, 121.95], [777.45, 784.0]]}
"max_silent_run_ms": 29450.0, "worst_region_s": [90.0, 119.45]
```

Digital tier names the same `[90.0, 119.45]` Hermes measured. The −60 dBFS
tier catches the hole too (wider — it also pulls in the quiet edges) **plus a
second previously-invisible run `[777.45, 784.0]` (6.55 s ≤ −60 dBFS)** in the
same master — a real finding the single-threshold check missed.

### 2. 2 s stretch at −60 dBFS → FAIL

Fixture: 15 s stereo tone, `[6.0, 8.0)` replaced by a sine at −60.5 dBFS RMS
(PCM_24). Generation script in §4 below; verified via CLI and unit test
`test_low_level_near_silence_fails`.

```
$ python -m ai_dj.cli verify --plan <fix>/low60_plan.json --master <fix>/low60.wav --out <fix>/low60_v.json
low60 EXIT=1 → verdict FAIL | dig ok:True | low ok:False worst [6.0, 8.0] | dead []
```

The digital tier stays `ok:true` (−60.5 ≫ −90): it's the **low-level** tier
that fails — i.e. real audio above digital silence is now gated.

### 3. Exactly 0.5 s and exactly 2.0 s → consistent (inclusive) classification

Fixtures: `[6.0, 6.5)` zeros (exactly 10 blocks) and `[6.0, 8.0)` at −60.5 dBFS
(exactly 40 blocks). Unit test also asserts 0.45 s / 1.95 s pass.

```
dig_0p5 EXIT=1 → FAIL | dig ok:False worst [6.0, 6.5] | low ok:True (0.5 s < 2 s)
low_2p0 EXIT=1 → FAIL | dig ok:True | low ok:False worst [6.0, 8.0]
```

`>=` on sample counts: `10*2400 >= 24000`, `40*2400 >= 96000` — boundary is a
violation by contract.

### 4. Anti-phase stereo → NOT silence

```
antiphase EXIT=0 → PASS | dig ok:True | low ok:True | dead [] | channel_rms_dbfs [-9.03, -9.03]
```

`L = -R` cancels to zero in a mono mixdown (the old code flagged the whole file
silent); multi-channel RMS keeps the real −9 dBFS program. A `dead_right`
fixture (L tone, R = 0) → `dead_channels: [1]`, `silence_ok` still true
(flag-only).

### 5. `evidence/eligible_set.wav` → **FAIL — SPEC CONFLICT, see §5**

```
$ python -m ai_dj.cli verify --plan evidence/plan_eligible.json --master evidence/eligible_set.wav --out evidence/dj_p0_006b_verification_eligible.json
EXIT=1
```

```json
"verdict": "FAIL", "pass": false
"digital_silence": {"ok": true, "max_run_s": 0.0}
"low_level": {"ok": false, "max_run_s": 6.6,
              "violating_runs_s": [[629.6, 635.8], [1009.15, 1015.75]]}
"short_term_3s": {"min_dbfs": -82.29, "at_s": 631.2}
"channel_rms_dbfs": [-22.75, -22.81], "dead_channels": []
```

Under the spec'd constants this master **cannot PASS** — the gate is working
as specified; the fixture contradicts acceptance #5. Details in §5.

### 6. ffmpeg absent → INCONCLUSIVE, exit 3, no fabricated peak

Clean fixture (`clean.wav`, 15 s continuous tone — no real clean master exists
in `evidence/` anymore, see §5):

```
$ PATH=/usr/bin /f/ai-dj-venv/Scripts/python.exe -m ai_dj.cli verify --plan <fix>/clean_plan.json --master <fix>/clean.wav --out <fix>/clean_noffmpeg_v.json
EXIT=3
```

```json
"verdict": "INCONCLUSIVE", "pass": null,
"true_peak": {"dbtp": null, "ok": null, "measured_by": "unavailable: ffmpeg not on PATH"}
"unmeasured_checks": ["true_peak", "synthetic_alignment"]
```

### 7. ffmpeg absent **and** a hole → FAIL, exit 1

```
$ PATH=/usr/bin /f/ai-dj-venv/Scripts/python.exe -m ai_dj.cli verify --plan evidence/hermes_plan1.json --master evidence/dj_p0_006_holey.wav --out <fix>/holey_noffmpeg_v.json
EXIT=1
```

```json
"verdict": "FAIL", "pass": false,
"unmeasured_checks": ["true_peak", "synthetic_alignment"],
"silence": {"silence_ok": false, "digital_silence": {"worst_region_s": [90.0, 119.45]}}
"true_peak": {"dbtp": null}
```

Unmeasured checks are still listed, but a measured FAIL is never masked.

### 8. Corrupt master → FAIL, exit 1 · Missing master → FAIL, exit 1

```
$ python -m ai_dj.cli verify --plan <fix>/corrupt_plan.json --master <fix>/corrupt.wav --out <fix>/corrupt_v.json
EXIT=1 → "verdict":"FAIL", artifact {"exists":true,"decodable":false,
         "error":"LibsndfileError: ... Format not recognised.","ok":false}

$ python -m ai_dj.cli verify --plan <fix>/clean_plan.json --master <fix>/nonexistent.wav --out <fix>/missing_v.json
EXIT=1 → "verdict":"FAIL", artifact {"exists":false,"decodable":false,
         "error":"master not found: ...","ok":false}
```

Both write a FAIL report to `--out` and print the report JSON to stdout.

### 9. CLI usage error → exit 2, no verdict

```
$ python -m ai_dj.cli verify --plan <fix>/clean_plan.json            # missing --master
usage: ai_dj verify [-h] --plan PLAN --master MASTER [--out OUT]
ai_dj verify: error: the following arguments are required: --master
EXIT=2                        # argparse usage text on stderr, stdout empty

$ python -m ai_dj.cli verify --plan <fix>/no_plan.json --master <fix>/clean.wav
EXIT=2
STDOUT: []
STDERR: {"error": "cannot read plan: [Errno 2] No such file or directory: ..."}
```

`{"error": ...}` on stderr — no `verdict` key anywhere.

### 10. Full suite + lint

```
$ python -m unittest discover -s tests
Ran 56 tests in 7.5s
OK

$ python -m ruff check ai_dj tests scripts
All checks passed!
$ python -m ruff format --check ai_dj tests scripts
14 files already formatted
```

49 → 56 tests (+7). New tests were written RED-first: against the old code
`low60` returned `pass:true` (KeyError on `low_level`), anti-phase flagged the
whole file silent (`silence_ok:false`), missing/corrupt raised (exit 2), and
INCONCLUSIVE mapped to 2.

## 4. Fixture generation (reproducible)

```python
import json, numpy as np, soundfile as sf

SR = 48000


def tone(n, dbfs=-9.0):
    t = np.arange(n) / SR
    a = np.sqrt(2) * 10 ** (dbfs / 20)
    return a * np.sin(2 * np.pi * 220 * t)


# clean/low60/dig_0p5/low_2p0/antiphase/dead_r = 15 s stereo stereo files;
# low60:  d[int(6*SR):int(8*SR)] = tone(2*SR, -60.5)
# plans:  {"master_sr":48000,"tracks":[],"transitions":[],
#          "timeline":{"total_samples":15*SR,"total_duration_s":15.0}}
```

Fixtures live under `%TEMP%\djp006b_pqw2bpq1\` (ephemeral); verification JSONs
also written to `evidence/dj_p0_006b_verification_{holey,eligible}.json`.

## 5. SPEC CONFLICT — acceptance #5 cannot hold under the spec'd constants

`eligible_set.wav` contains two long low-level runs (50 ms multi-channel RMS):

| Region (s) | Length | Depth |
|---|---|---|
| 629.6 – 635.8 | 6.2 s | down to ≈ −83 dBFS/block |
| 1009.15 – 1015.75 | 6.6 s | down to ≈ −82 dBFS/block |

Both are mid-track content (track 1 ends its span at 663.9 s; track 2 at
1043.63 s) — source-audio quiet stretches ~22 s before the next transition,
not inside fades, transitions, or any declared gap (`plan_eligible.json` has
no `allowed_silent_gaps_s`). Under tier 2 (≤ −60 dBFS ≥ 2.0 s → FAIL) they are
violations, so `eligible_set.wav` → FAIL exit 1, while #5 expects PASS.
Acceptance #2 and #5 are mutually exclusive for this fixture; I implemented
the constants exactly as spec'd rather than weaken the gate.

Hermes' options:
1. **Accept the FAIL as correct** — the stretches are real ≤ −60 dBFS content;
   #5's premise (eligible_set is "clean") was measured under the old −80 dBFS
   peak check which saw 0 silent hops.
2. **Declare them deliberate** — add `"allowed_silent_gaps_s": [[629.6,635.8],
   [1009.15,1015.75]]` to `plan_eligible.json`; verifier then PASSes (the
   exemption mechanism is in place and tested). I did not hand-edit the plan:
   it is the engine's declared intent and falsifying it would mask the finding.
3. **Retune the tier** — to pass this file the level would need to sit below
   ≈ −83 dBFS (e.g. −85) or min run > 6.6 s.

## 6. Not verified / caveats

- No real master with a *partially* quiet transition or a genuine dead channel
  was run; those paths are covered by unit fixtures only.
- The low-level tier found `eligible_set.wav` failing; whether that is a
  verifier win (real gaps the old check missed — it also found a third run
  `[777.45,784.0]` in the holey master) or a fixture/spec miscalibration is
  Hermes' call (§5).
- `min_dbfs` for `short_term_3s` reports the min over all 3 s window starts on
  the 50 ms block grid (1-block hop); a sub-3 s file reports whole-file energy.
- Report JSONs: `evidence/dj_p0_006b_verification_{holey,eligible}.json`
  (uncommitted — Hermes owns git).
