# DJ-P1-ANCHOR-001 — Anchor-Aware Set Planning

**Status:** todo · **Priority:** P1 · **Assignee:** devin-cli · **Verified by:** hermes
**Source:** GPT review 2026-10-22 → `docs/gpt-review-anchor-2026-10-22.md`
**Ref:** HEAD b22eeb6 · **GPT verdict:** GO — "0/8 gate được xác nhận"

## Problem

Sếp yêu cầu phát một bài cụ thể ("Sao Lòng Còn Thương – Trọng Hải"). Greedy planner
không đảm bảo bài đó được chơi, và `plan_set()` không có force-include. Đây là HAI bài
toán tối ưu khác nhau:

1. "slot tiếp theo nên chọn bài nào?" → greedy arc giải được
2. "người dùng muốn nghe bài X → xây set TỐT NHẤT xung quanh X" → CHƯA giải được

## Decision (GPT): Option B — Anchor-Aware Planning

Giữ interface `--include-track` của Option A, nhưng implement **anchor-aware constrained
search** thay vì "ép bài vào slot rồi greedy".

Lý do: đây là AI-DJ tự chủ, không phải trình phát ngẫu nhiên. Khi người dùng yêu cầu một
bài, AI phải **vừa phát đúng bài đó, vừa tự tìm cách phối nhạc phù hợp**.

### Bác bỏ
- **Option A nguyên bản** (force include + greedy): đảm bảo bài xuất hiện nhưng KHÔNG
  đảm bảo hàng xóm phù hợp → không chọn nguyên bản.
- **Option C** (giữ greedy): không đáp ứng yêu cầu → loại.

## Scope (7 mục)

1. `plan_set(..., include_ids: list[str] | None = None)` — bản đầu hỗ trợ **đúng một
   anchor**; từ chối nhiều anchor bằng lỗi rõ ràng (KHÔNG âm thầm bỏ bớt).
2. CLI `--include-track <recording_id>` → ánh xạ sang track ID hợp lệ trong cache.
3. **Anchored beam search**: beam width 64, tie-break deterministic theo track ID,
   không lặp bài.
4. Tối ưu **toàn chuỗi** theo energy arc + BPM + key + khả năng chuyển bài; tái sử
   dụng scorer và ràng buộc `_choose_transition` hiện hành.
5. Ghi vào `plan.json`: `request`, `planner.algorithm`, `planner.beam_width`,
   `planner.seed`, `anchor_resolution`, provenance (fingerprint / SHA-256 nguồn).
6. **KHÔNG anchor → đường greedy legacy giữ nguyên byte-identical** (regression).
7. Từ chối: track bị D10 exclude, thiếu file nguồn, hoặc không có plan khả thi →
   **lỗi có cấu trúc, KHÔNG fallback âm thầm** sang bài khác.

## Cost function

```
J(S) = Σ_i C_arc(s_i, i) + Σ_{i=1..n-1} C_transition(s_{i-1}, s_i) + C_constraint(S)
```

- `C_arc`: độ phù hợp energy arc.
- `C_transition`: tổng rủi ro BPM, key, beat-grid, phrase, vocal overlap, âm lượng.
- `C_constraint`: vi phạm hard constraint — không được tồn tại trong plan hợp lệ.

**KHÔNG** dùng `kd <= 1` làm điều kiện bắt buộc cho mọi transition (key detection có sai
số). Nhưng khi có nhiều phương án khả thi, hàng xóm key-compatible + phrase tốt phải được
ưu tiên.

**Quan trọng:** planner chọn phương án chi phí tốt nhất; **renderer vẫn phải xác nhận có
kỹ thuật chuyển bài phù hợp**. Không được biến transition có key clash thành beat-crossfade
chỉ vì kế hoạch đạt điểm thấp.

## Acceptance gates (8) — GPT: "0/8 gate được xác nhận"

- [ ] **G1** Bài Sao Lòng Còn Thương xuất hiện **đúng một lần** trong plan
- [ ] **G2** Planner chủ động đánh giá ĐOẠN TUYỆT và các hàng xóm tương thích
- [ ] **G3** Cùng input/seed/nguồn → kết quả **deterministic**
- [ ] **G4** Không anchor → **legacy regression không đổi**
- [ ] **G5** Anchor không tồn tại hoặc bị exclude → **lỗi rõ ràng**
- [ ] **G6** Render thành công, **không** silence/dropout/clip bất thường
- [ ] **G7** Verifier độc lập xác nhận nội dung bài anchor **thực sự nghe được**
- [ ] **G8** Hermes tự chạy lại tests + kiểm tra bằng chứng (không chỉ nhận PASS của Devin)

## Deliverable cho "Sao Lòng Còn Thương"

Demo nghe được ~15 phút (KHÔNG phải plan 60 phút rồi báo thành công):

- Anchor xuất hiện **ngay đầu hoặc rất sớm**
- Giữ được **phần giọng hát chính**
- Ít nhất **một** chuyển bài sang track khác phù hợp
- Không cắt ngang câu hát bất hợp lý

| Chỉ tiêu | Điều kiện đạt |
|---|---|
| Anchor inclusion | 100% |
| Audible anchor | Xác nhận độc lập từ WAV render |
| Transition timing | Căn theo beat/downbeat, phrase hợp lệ |
| Silence bất thường | Không có |
| True peak | ≤ −1.0 dBTP |
| Vocal overlap | Không chồng lời gây khó nghe ngoài chủ đích |
| Musical quality | Nghe đánh giá và lưu verdict |

**Output:** `plan.json` + `master.wav` + `verification.json` + listening verdict.

> Các phép đo waveform/loudness chỉ xác nhận **một phần** chất lượng. Việc bài Bolero có
> được phối tự nhiên hay không vẫn cần một lượt nghe thực tế.

## Technical flags (GPT)

1. **`BEATMATCH_BPM_TOL` là hard gate trong `_choose_transition` (dòng 203) nhưng là soft
   penalty trong planner cost.** 161.499 vs 172.266 = **6.25% > 6%** → KHÔNG BAO GIỜ là
   beat_crossfade từ opener đó. Đừng đọc penalty nhỏ (0.0020) là "beatmatch hợp lệ".
2. **Bolero có nguy cơ nhận nhầm double-time.** 161.499 ≈ 2 × 80.750. `octave_diagnosis:
   agree` KHÔNG phải bằng chứng cuối cùng về nhịp cảm nhận. Cần kiểm tra onset / beat-grid /
   downbeat cho anchor.

## Do NOT

- KHÔNG sửa trọng số `key_clash` toàn cục (0.25) để ép một bài được chọn — đó là sửa kết
  quả của một trường hợp, không phải sửa thuật toán.
- KHÔNG giữ greedy làm giải pháp cho requested-track.
- KHÔNG đánh đồng "xuất hiện trong JSON" với "đã phát nhạc thành công".
- KHÔNG tạo/ghi file trong `.devin/`.
- KHÔNG commit/push (Hermes sở hữu git).
