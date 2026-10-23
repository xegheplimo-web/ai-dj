# Hermes independent verification — DJ-P2-OCTAVE-001 (analyzer v5)

Run by the **orchestrator**, from **outside** the agent's worktree. Ground truth decided
before analysis in every tier. Three probes: the shared known-answer harness, an adversarial
low-tempo probe (the agent's own range started at 88 BPM), and a full-corpus rescan.

## PASS — card criteria B1-B7 (orchestrator-measured)

| # | Criterion | Measured |
|---|---|---|
| B1 | no ~50% half-time case marked verified | met — the 4 cases now resolve ×2 correctly |
| B2 | T1 0 errors > 2% | **0/41**, max error **0.031%** |
| B3 | T2 0 errors > 2% | **0/8**, max error **0.058%** |
| B4 | 3 negatives flagged suspect | **3/3** |
| B5 | envelope score bounded | **`beat_env_score` in [0.5009, 1.0]** (was 7.2e9–1.9e10) |
| B6 | tests pass | **151 tests OK** (skipped=1) |
| B7 | ruff / probe / fail-closed | clean · **0.2037 ms** · stale cache → **exit 5** |

### T1 before -> after (the 4 target cases)

| case | v4 (before) | v5 (after) |
|---|---:|---:|
| click_168.000 | 83.995 | **167.991** |
| click_174.000 | 87.004 | **174.008** |
| click_178.000 | 88.997 | **177.995** |
| click_180.000 | 90.003 | **180.006** |

The `beat_env_score` replacement is a genuine fix: it is the monotone image r/(1+r) of the old
ratio, bounded to [0,1) and exactly scale-invariant, and the old `>= 2.0` gate maps exactly to
`>= 2/3`. Observed range across all probes: 0.5009 - 1.0.

## F3 — the resolver DOUBLES slow material, and does not always flag it

### Synthetic (adversarial probe, 16 cases, 1 failure)

| case | truth | detected | corrected | error | ambiguous |
|---|---:|---:|---:|---:|---|
| `slow_drum_70` | 70.0 | 139.9986 | **139.999** | **+99.999%** | True |

All `slow_click` cases 60-96 BPM are correct; `slow_drum_80` and `slow_drum_90` are correct.
The failure is material-dependent, not a clean tempo cut-off.

### Real corpus — WORSE, and silent (full rescan, 41 tracks)

| track | detected | corrected | margin | ambiguous |
|---|---:|---:|---:|---|
| `Gõ Cửa - Bolero Cover – Duy` | 96.0743 | **192.149** | **0.1442** | **False** |

A Bolero ballad at ~96 BPM is doubled to 192 BPM with a **decisive** margin (0.1442 well above
the 0.08 ambiguity threshold), so the octave resolver reports no ambiguity at all. 192 BPM is
musically absurd for a Bolero. The track is `status: suspect`, but that flag comes from the
residual metric (D28), not from the octave resolver.

### Corpus rescan summary (41 tracks, v5)

| metric | value |
|---|---|
| analysed | 41/41 |
| **ambiguous** | **6/41 (14.6%)** |
| **doubled (×2)** | **1/41** |
| kept ×1 | 40/41 |
| suspect | 39/41 (dominated by D28 residual, not octave) |
| margin min / p25 / median / max | 0.0097 / 0.1001 / 0.1448 / 0.3234 |
| margin distribution | 6 below 0.08 · **8 in 0.08-0.12 (fragile)** · 27 above 0.12 |
| residual_std_ms median | 137.8 (unchanged — D28 stands) |

### Root cause of F3

The resolver scores `{0.5, 1.0, 2.0}` using onset-envelope autocorrelation + comb-tooth strength
+ a weak log-domain tempo prior centred at 128 BPM (weight 0.20) - an anti-penalty. It uses
**only the onset lattice**. A slow track whose onsets subdivide the beat (hi-hats on eighths,
or a kick/snare alternation) presents a lattice identical to the doubled tempo, so the lattice
cannot separate them. The tempo prior is too weak to break the tie, and there is no bar-level /
backbeat information at all — which is exactly what a human uses to say "this Bolero is 96, not
192".

### Practical impact

The octave fix does **not** make the corpus unusable: only 6/41 ambiguous and 1/41 doubled.
The dominant corpus problem remains D28 (39/41 suspect, median residual 138 ms), which is a
separate, pre-existing issue. But 8 more tracks sit inside 0.08-0.12, so the ambiguity boundary
is fragile and a small scoring change could move them either way.

## Verdict

**PASS on the card's stated criteria B1-B7**, independently verified, with one new defect found
outside the card's fixture set:

- the half-time direction (fast tempo halved) is **FIXED**;

- the doubling direction (slow tempo doubled) is **NOT fixed** — it is material-dependent, it
  survives on real music, and on the corpus case it is not even flagged ambiguous;

- the ambiguity flag is honest but the boundary is fragile (8/41 within 0.08-0.12).

A follow-up card must add bar-level / backbeat evidence to the resolver before the planner can
trust the tempo of slow material.