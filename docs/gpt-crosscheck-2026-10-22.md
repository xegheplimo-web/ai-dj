# Cross-check — GPT assessment vs repo (2026-10-22)

Every claim in `docs/gpt-assessment-2026-10-22.md` verified against source,
artifacts and runtime by Hermes. GPT read the repo at commit `604da3a`.

| # | GPT claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `_silence_check()` only fails on silence inside transition windows → master can hold a 29.4 s hole and still `pass: true` | **CONFIRMED** | `verify.py` L61-81: `max_run_ms` updated only when `in_tr[i]`; `silence_ok = max_run_ms <= MAX_SILENT_RUN_MS`. Runtime proof: master with 29.45 s hole → `pass: True`, `silence_ok: true`, `silent_hops_total: 589` |
| 2 | DJ-P0-002 code present but acceptance incomplete (no real-corpus re-scan / re-render) | **CONFIRMED then CLOSED** | Hermes re-ran: analyze excludes exactly the 2 measured non-songs (0.218 / 0.319); plan selects no ineligible track; re-render has **no** silent run > 2 s |
| 3 | Unmeasurable true peak must be INCONCLUSIVE, not PASS | **CONFIRMED (D13)** | `verify.py` L206-207: `peak_ok = None` but `hard_checks` uses `peak_ok is not False` → verdict still PASS |
| 4 | Downbeat grid assumes 4/4, not confirmed | **CONFIRMED** | `analyzer.py` L49 `_BEATS_PER_BAR = 4`; L244 `downbeat_estimated: True` |
| 5 | Corpus: 41 files → 40 recordings → 31 works | **CONFIRMED** | `evidence/corpus_report.json` and Hermes' own scan both report 41 / 40 / 31 |
| 6 | All 6 demo tracks had `tempo_stretch = 1.0` → time-stretch never exercised on real music | **PARTIALLY** | Plan has no `segments`; only transition 0 is a `beat_crossfade` with `target_bpm`. The other 4 are plain `crossfade`/`cut` → the demo indeed never stretched real audio |
| 7 | `main` unprotected | **CONFIRMED** | `GET /branches/main/protection` → 404 |
| 8 | README / verification ledger stale | **CONFIRMED** | README still lists G0/G1/G3 as ☐; control plane refreshed 2026-10-22 |
| 9 | Recovery (DJ-P0-005) must be redefined — pre-render means deleting a source file mid-playback cannot affect audio | **CONFIRMED (adopted)** | With a pre-rendered master the playback path never reads the corpus; recovery must cover planning-time file errors, render failure, corrupt master, device loss, buffer interruption |

## Claims adopted as decisions

- **D12** — full-master continuity gate (not transition-only). Card `DJ-P0-006`.
- **D13** — `pass: null` + `verdict: "INCONCLUSIVE"` when a required measurement is missing. Card `DJ-P0-006`.
- **D14** — DJ-P0-005 redefined per claim 9.
- **Execution order adopted** — close DJ-P0-002/006 → quality mini-set → G3 60-min → G1/G5 playback+recovery → G2b/G6 listening gate → then P1 (reference-mix understanding).
- **Architecture** — GPT agrees with D1/D2/D3: keep the Python DSP engine, keep pre-render for v1, do not add Mixxx / MIDI / LLM now.

## Not adopted

- GPT's P1 proposal "Reference Mix Understanding" is accepted as a **P1 direction**, not scheduled now — it depends on the G2b/G6 listening gate existing first.
