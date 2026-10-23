# AI-DJ-P3-ACE-001 — ACE-Step 1.5 Remix PoC (Option Y: stem + generated Vinahouse BGM)
Owner: Hermes (Lead Orchestrator / Technical Owner)
Type: Capability PoC (Phase P3 — AI Music Producer extension)
Status: READY (gated on micro-probe — see §5)
Priority: P3 (after D30 adopted 23/10; parallel & non-blocking vs P0 quantization D19)
Executor: Devin CLI (`launch-agent.sh devin`, worktree `task/AI-DJ-P3-ACE-001`, branch `feat/ace-remix-poc`)
Refs: `docs/gpt-ace-step-remix-reply-2026-10-23.md` (D30); skill `ai-dj-project` §7 evidence conventions; `DECISIONS.md` D19/D20/D21/D23/D28.

## 1. Goal
Prove **AI-generated Vinahouse remix** of one rights-cleared Bolero from `F:\DỸ LIỆU\ok`, produced via Option Y architecture, and **independently verified** (BPM librosa, true-peak/loudness ebur128, vocal clarity RMS, structure, listening verdict). Extends AI DJ → "AI DJ + AI Music Producer" without replacing ai-dj's verifier or beat grid.

## 2. Architecture (FROZEN — D30)
1. Pick ONE eligible Bolero in `F:\DỸ LIỆU\ok` (88–110 BPM target; residual_std_ms small; NOT the 96→192 doubling case unless documented).
2. Stem-split → **vocal stem** (demucs/spleeter; keep original vocalist).
3. ACE-Step 1.5 generate a Vinahouse BGM (target ~128–135 BPM, key compatible with input).
4. Align vocal stem to BGM: **librosa** time-stretch + pitch-shift as primary (librosa 1.0.0 already in ai-dj-venv) — **rubberband-cli / ffmpeg `rubberband` filter optional** quality boost if present. **Do NOT use analyzer D28 grid** (39/41 suspect) — measure alignment on the OUTPUT.
5. Mix vocal + BGM → master.
6. Master/verify: ffmpeg ebur128 (loudness, true peak, clipping), librosa BPM on OUTPUT, vocal-presence RMS on vocal stem, duration/structure, listening verdict (M7-style).

## 3. Ownership SCOPE (whitelist — Devin may touch ONLY these)
- CREATE/MODIFY: `ai_dj/remix.py` (new module: stem/ace/align/mix/master/verify); `ai_dj/cli.py` (add `remix` subcommand); `scripts/ace_poc_*.py` (micro-probe harness, siting `scripts/` per D27); `evidence/ace_poc_*` (gitignored media + JSON evidence); `docs/ace-poc-report.md`; `.orchestrator/cards/AI-DJ-P3-ACE-001.md`.
- READ-ONLY / shared (do NOT modify): `.orchestrator/`, `analysis_cache.sqlite`, `evidence/kdd_remix_*`, `ai_dj/analysis/*`, `tests/`, `ai_dj/timing.py`, `scripts/timing_probe.py`, `scripts/orchestrator_*`.
- **Do NOT modify or delete anything outside the above.** Harness/verifier lives OUTSIDE the worktree (run via `PYTHONPATH=F:/ai-dj`).
- **KHÔNG tạo/ghi file trong `.devin/`** (ForceAsk → chết lane); nếu cần config, mô tả trong docs. `.devin/` được gitignore.

## 4. Deliverables (stems per skill §7 — unique per set)
- `evidence/ace_poc_master.wav` (GITIGNORED — media never committed)
- `evidence/ace_poc_master.verify.json` (auto sidecar — different stem from full report)
- `evidence/ace_poc_verification.json` (FULL report)
- `evidence/ace_poc_render_report.json` (duration, true peak, clipping, underflows)
- `evidence/ace_poc_plan.json` (input track sha256, target BPM/key, stem choice, alignment)
- `evidence/ace_poc_vocal.json` (vocal stem RMS + presence metric)
- `docs/ace-poc-report.md` (real command output pasted)

## 5. Acceptance (ALL required — micro-probe gates the lane)
- [S1] **Micro-probe PASS (Hermes-run):** isolated venv `F:/ace-venv` installs ACE-Step 1.5 from GitHub on py3.13, `import ace_step` OK, a ≤30s generation runs on CUDA (RTX 3090) and emits a real WAV. → lane Devin ONLY after S1 PASS.
- [S2] Stem-split of chosen Bolero yields a vocal stem (RMS > silence floor, duration within 5% of input).
- [S3] Output master: BPM 126–136 (librosa, measured on OUTPUT WAV); key compatible with input; loudness −9 to −11 LUFS; true-peak < −1 dBTP; 0 clipped samples; 0 underflows.
- [S4] Vocal present & intelligible (vocal stem RMS ratio in expected band; listening verdict).
- [S5] Structure: ≥1 clean 8-/16-bar phrase region; output ≥30s.
- [S6] `ruff check .` + `ruff format --check .` clean over FULL repo; `python -m pytest tests/ -q` green (new tests included); no `.devin/` writes.
- [S7] Diff scope ⊆ whitelist (§3); `git status` only the listed files in the worktree.

## 6. Verify commands (Hermes independent)
```bash
cd F:/ai-dj
# micro-probe (Hermes):
F:/ace-venv/Scripts/python -m ace_step.generate --help        # or repo CLI
# lane outputs:
sha256sum evidence/ace_poc_master.wav
ffmpeg -i evidence/ace_poc_master.wav -af ebur128=framing=1 -f null - 2>evidence/ace_poc_ebur128.txt
python3 -c "import librosa; t,_=librosa.load('evidence/ace_poc_master.wav',sr=48000); print('bpm',librosa.beat.tempo(onset_envelope=librosa.onset.onset_strength(t,sr=48000))[0])"
python -m pytest tests/ -q
ruff check . && ruff format --check .
```

## 7. Constraints
- Devin CLI only. `--permission-mode dangerous` + `--respect-workspace-trust=false` (throwaway worktree).
- **Commit card + D30 + doc to `main` BEFORE `git worktree add`** (§3 D27 lesson). Worktree = fresh `task/AI-DJ-P3-ACE-001` off origin/main tip (repo currently 1 behind — fetch+ff first).
- One card = one lane on one worktree. No parallel same-worktree runs.
- No PyPI `ace-step` (0.1.0). Install from GitHub.
- Evidence media gitignored; commit JSON + report only.
- No secrets/logs with tokens.

## 8. Stop conditions
- Micro-probe S1 fails (install/CUDA/missing model) → **STOP, do not dispatch lane.** Re-enter maintenance (§20): classify ENVIRONMENT/MODEL/TOOL; preserve micro-probe log; report + decide fallback (offline rubberband stretch — not a real Vinahouse remix).
- ≥2 same-root-cause fails in the lane → halt, §20 maintenance.
- 25' lane budget exhausted → surface table + stop.

## 9. Pre-dispatch baseline (Hermes)
Snapshot `git -C F:/ai-dj status --short` before worktree add; diff the lane's changed-file list against §3 whitelist before review.
