# DECISIONS — AI DJ

## 2026-10-22 — Khởi tạo
- D0: Dự án đặt tại `F:\ai-dj`, git repo `main`, Hermes sở hữu git (agent không commit).
- D0b: Python 3.13.15 làm runtime chính (3.14 thiếu wheel audio; 3.13 có torch/fastapi/mcp).
- D0c: Không cài Mixxx/driver MIDI trong vòng 1.

## 2026-10-22 — Phản biện kiến trúc chéo với GPT (thread "AI chơi DJ")
Brief đã gửi: `docs/gpt-brief-2026-10-22.md` (§A sự thật đo được, §B 4 câu hỏi chéo).
Verdict GPT: **CONDITIONAL GO — engine Python P0**.

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
