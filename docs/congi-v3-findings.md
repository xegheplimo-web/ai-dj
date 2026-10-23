# "Còn Gì Kiếp Sau" — v3 (AI-DJ-P3-ACE-004): apply GPT feedback

## What changed (Option Y, re-mix)
1. **P1 (BGM)**: seed-pick 3 candidates with GPT's "dynamic contrast" caption;
   `seed=99` wins — its raw BGM has a real **51 dB** breakdown→drop bar arc
   (drop −6.9 dBFS, breakdown −32.5 dBFS, program −16.9 LUFS).
2. **P2 (vocal mix)**: sidechain duck of the BGM under the vocal envelope
   (min_gain 0.45, 15 ms attack / 200 ms release) — the kick/bass "pump" that
   makes a Vinahouse mix breathe. Vocal sits at −2.0 dB vs BGM (plan target).
3. **Master**: loudnorm (TP-aware) → −10.4 LUFS / −1.1 dBTP.

## Gate results (v3)
PASS 9/9 — bpm 126–136 ✓ · key compatible ✓ · loudness −11..−9 LUFS ✓ ·
true_peak < −1 dBTP ✓ · no clipping ✓ · no underflows ✓ · duration ≥30 s ✓ ·
phrase ≥8 bars ✓ · vocal present ✓.

## v1 → v2 → v3 progression (bar-RMS std of the mastered 60 s)
| version | caption | seed-pick | sidechain | bar-RMS std | verdict |
|---|---|---|---|---|---|
| v1 (baseline)   | default    | ✗ | ✗ | 7.45 dB | PASS |
| v2 (caption)    | GPT        | ✗ | ✗ | 8.20 dB | PASS |
| v3 (final)      | GPT        | ✓ (seed 99) | ✓ | 8.48 dB | PASS |

Trend: each applied fix lifts measured dynamic contrast (+13 % end-to-end).
v3 is the **least-flat in-gate master** → the honest "less chán" result.

## D12 finding — the dynamic-arc vs LUFS-gate tension (verified, not assumed)
The seed-99 BGM carries a genuine 51 dB musical arc. Two loudnorm regimes
were tested to see how much survives into the master:

| loudnorm | mastered bar-RMS std | program LUFS | gate |
|---|---|---|---|
| LRA=11 (default, v3) | 8.48 dB | −10.4 LUFS | PASS |
| LRA=50 (preserve arc) | 10.11 dB | −13.0 LUFS | **FAIL** (below −11) |

LRA=50 recovers ~1.6 dB more arc but drives program to −13.0 LUFS, breaking the
`loudness_−9_−11_lufs` gate. The 51 dB arc is simply **too wide** to fit a −10 LUFS
program target without compression. ⇒ within the gates, v3 (LRA=11, std 8.48)
is the optimal master. A true breakdown→drop structure that survives mastering
requires **multi-section DAW arrangement** (distinct stems per section) — outside
the reach of a single ACE-Step clip + loudnorm normalizer.

## GPT feedback → which items were actionable here
GPT's 3 priorities:
- **P0 arrangement break↔drop** — *partially* addressed: seed-99's BGM self-generated
  a breakdown→drop arc, but mastering compresses it (D12). Full P0 needs DAW.
- **P1 regenerate BGM** — DONE (GPT caption + seed-pick).
- **P2 vocal mix (sidechain/reverb)** — sidechain DONE (duck); plate reverb skipped
  (no reverb plugin in ace-venv; librosa has none).

## Artifacts (gitignored media: WAV/MP3; JSON committed)
- `evidence/congi_kiep_sau/v3/ace_poc_master.wav` (−10.4 LUFS, −1.1 dBTP)
- `evidence/congi_kiep_sau/v3/ace_poc_master.mp3` (256 kbps, 1.92 MB)
- `evidence/congi_kiep_sau/v3/ace_poc_verification.json` (PASS 9/9)
- stems: `evidence/congi_kiep_sau/stems/htdemucs/source/{vocals,drums,bass,other}.wav` (281.12 s)
