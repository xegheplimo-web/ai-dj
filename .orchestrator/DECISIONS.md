# DECISIONS — AI DJ

## 2026-10-22 — Khởi tạo
- D0: Dự án đặt tại `F:\ai-dj`, git repo `main`, Hermes sở hữu git (agent không commit).
- D0b: Python 3.13.15 làm runtime chính (3.14 thiếu wheel audio; 3.13 có torch/fastapi/mcp).
- D0c: Không cài Mixxx/driver MIDI trong vòng 1.

## 2026-10-22 — GPT đối chiếu GitHub trực tiếp (commit 604da3a)
Brief gửi: audit brief (ai-dj-audit-brief.md, 53 lines) qua ChatGPT CDP.
Verdict GPT: **CONFIRMED 9/9 claims**; Option A đúng cho eligible_set.wav spec conflict.

- **D10 (CONFIRMED):** Verifier full-master silence gate (không chỉ transition). 29.4s silent run trên master = FAIL. Đã implement DJ-P0-006.
- **D12 (CONFIRMED):** Two-tier silence gate: ≤ −90 dBFS / ≥ 0.5s → FAIL; ≤ −60 dBFS / ≥ 2.0s → FAIL. Anti-phase stereo với audio = NOT silence.
- **D13 (CONFIRMED):** Tri-state verdict — INCONCLUSIVE cho measurement thiếu; không bao giờ PASS khi thiếu.
- **D11b (NEW):** sounddevice/PortAudio optional import (CI Ubuntu không có libportaudio2). `player.py` try/except OSError → sd=None; play_master raise RuntimeError sạch. Áp dụng nguyên tắc D11 (optional external dependency không fatal).

## 2026-10-22 — DJ-P0-006b spec conflict resolution
**Vấn đề:** eligible_set.wav có master có vùng ≤ −60 dBFS nhưng < 2.0s, không có vùng nào vượt ngưỡng 2s → PASS hay FAIL?

**Phân tích:** Các tier đã công bố: Tier 1 (≤ −60 dBFS / ≥ 2.0s → FAIL) và Tier 2 (≤ −90 dBFS / ≥ 0.5s → FAIL). eligible_set.wav có điểm đến −60 dBFS → **FAIL tier 1**.

**Quyết định:** **Option A — FAIL.** GPT xác nhận trực tiếp (ChatGPT session ai-dj-audit): "Tiêu chuẩn hai-tier đã được công bố rõ ràng. eligible_set.wav có điểm đến -60 dBFS → FAIL tier 1." Option B (INCONCLUSIVE) bị từ chối: "Không có gì 'không đo được' — RMS đã đo được."

### D16 — Spec conflict đã giải quyết: eligible_set.wav → FAIL
Hai-tier gate tuân thủ. Verifier = technical gate (threshold cứng); G2b listening verdict = audio quality gate (phân biệt âm thanh rất nhỏ vs khoảng lặng bất thường — GPT đề xuất thêm).

### D16b — G3 BLOCKED cho đến khi GPT chấp nhận DJ-P0-007 listening verdict
GPT: "Tôi sẽ kiểm tra kỹ chất lượng transition ở DJ-P0-007 trước khi bạn mở rộng 60 phút."

## 2026-10-22 — CI fix (PortAudio)
CI run 39768311712 FAILED: "PortAudio library not found" trên Ubuntu. Fix: lazy import sounddevice trong player.py (D11b).
CI re-run 39771989534: **SUCCESS** (lint + ubuntu3.12 + ubuntu3.13 + windows + E2E). CodeQL 39771989494: **SUCCESS**.

- **D1 (GO):** Bỏ Mixxx ở P0. Nút thắt thật là *music understanding* và *transition quality*,
  không phải năng lực phát 2 file song song; Mixxx không giải quyết được nút thắt đó mà lại
  thêm 2 phụ thuộc ngoài + mất khả năng đo số học.
- **D2 (GO):** Kiến trúc **Pre-rendered Set → PCM Playback** cho P0: mọi quyết định âm nhạc tính
  trước; playback chỉ đọc block PCM đã dựng. KHÔNG làm DSP trong audio callback (PortAudio cấm
  allocation/filesystem I/O trong callback realtime).
- **D3 (GO):** Planner deterministic, confidence-aware. **KHÔNG đưa LLM vào P0** — LLM chỉ rerank
  ứng viên sau, qua validator deterministic, và chỉ khi A/B nghe chứng minh tốt hơn baseline
  rule-only. GPT: "LLM 38 tok/s không có nghĩa là LLM hiểu hòa âm tốt hơn thuật toán".
- **D4 (GO):** P0 = G0, G1 (mini-set 6–8 bài), G2a (synthetic timing), G2b (listening verdict).
  G3 (set 60 phút) là **P1 bắt buộc trước release** — GPT bác bỏ "20 bài" vì 20 bài × 150–330s
  dài hơn cả G3.
- **D5 (GO):** Không cài Mixxx / loopMIDI / driver MIDI trong P0.
- **D6:** Gate phải tách nguồn bằng chứng: **synthetic fixture** (beat timestamp biết trước) cho
  timing; **xác nhận thủ công** trên mẫu nhỏ cho corpus thật. Verifier không được lấy timing event
  do executor tự khai báo làm bằng chứng duy nhất.
- **D7:** Gate G2 phải đo thêm short-term loudness, true peak (≤ −1 dBTP), clipping, và bắt buộc
  có **listening verdict** — RMS + beat 20 ms KHÔNG thay được việc nghe.
- **D8:** Corpus phải có `recording_id` (bản thu) và `work_id` (tác phẩm) riêng; 55 file ≠ 55 bài
  độc lập (nhiều bản trùng/thử).
- **D9:** Timing luôn theo **sample index** trên timeline chung; wall clock chỉ để telemetry.
  Không dùng `time.sleep()` để quyết định thời điểm mix (20 ms = 960 sample ở 48 kHz).

### Bằng chứng đã sửa sau phản biện
- Đo lại bằng synthetic click track (ground truth biết trước): độ lệch beat thật là **0.20 ms**,
  không phải ~9 ms như phép đo trên nhạc thật. Con số 2% trong brief là nhiễu của beat detector
  trên nhạc Bolero, không phải sai số của rubberband.
- `ai_dj/timing.py` + `scripts/timing_probe.py` là harness đo độc lập (Hermes-owned, verification
  glue) — không dùng output của engine làm ground truth.

## 2026-10-23 — DJ-P2-CONTINUITY-002 hardening (GPT decision A + C + F + (a))

### D17 — Cue Boundary Is Not Phrase-Safe (transparent limitation, Flag #3 = F)

- Downbeat-aligned cue chỉ đảm bảo điểm cue nằm trên downbeat **ước lượng**
  (grid `estimated: true`, 4/4-assumed từ beat đầu tiên) — nó **không chứng minh**
  đó là ranh giới câu nhạc (phrase boundary).
- Continuity trimming có thể loại bỏ phần vocal hoặc outro quan trọng của bài hát.
- Anchor inclusion + D12 PASS **không đồng nghĩa** bảo toàn đầy đủ bài hát:
  gate chỉ chứng minh không có khoảng lặng vi phạm, không chứng minh người nghe
  nhận đủ nội dung quan trọng.
- Mọi anchor/track bị trim đáng kể phải báo rõ `cue_in_s`, `cue_out_s`, thời
  lượng bị loại và lý do — đã implement qua `cue_report` trong plan.json
  (DJ-P2-CONTINUITY-002).
- **G2b listening verdict vẫn bắt buộc** trước nghiệm thu âm nhạc — không tự
  động PASS từ JSON.
- Phrase-boundary model chỉ triển khai khi có bộ audio được đánh giá để đo độ
  chính xác thực tế (độ chính xác 8-bar/32-bar trên Bolero chưa được đo — nhận
  diện cấu trúc từ downbeat ước lượng tạo cảm giác an toàn giả).

Ví dụ thực tế: anchor "Sao Lòng Còn Thương" đã chuyển cue-out từ ~387.397 s
xuống ~351.759 s — rút ngắn ~35.64 s. Thay đổi đủ lớn để **bắt buộc xem xét
bằng nghe**; không thể chỉ dựa vào D12 PASS để khẳng định người nghe nhận đủ
phần quan trọng của bài hát.

### D18 — Verified-artifact gate & simulation honesty (Flag #2 = C)

- Pipeline hợp đồng: `PLAN` (simulation nhanh, prune) → `RENDER` (một master
  thật) → `VERIFY` (đọc audio thật) → `PASS` cho phép phát / `FAIL|INCONCLUSIVE`
  chặn phát. **Simulation KHÔNG chứng minh PASS D12** — chỉ dự báo tính khả
  thi để giảm render vô ích; `verify.py` giữ veto authority duy nhất.
- `play` chỉ chấp nhận master có verdict PASS hợp lệ, ràng buộc SHA-256 của
  cả `plan.json` và WAV (đổi 1 trong 2 file → PASS cũ mất hiệu lực).
- Fail-closed tuyệt đối (Flag #1 = A): corpus thiếu continuity payload →
  `plan` exit 5 (`CORPUS_UNMEASURED` toàn bộ / `ANALYSIS_REQUIRED` một phần
  + `missing_recording_ids`); **không** có waiver `--allow-unmeasured`.
