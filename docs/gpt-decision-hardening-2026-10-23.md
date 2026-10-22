# GPT Decision — Final Contract Hardening (DJ-P2-CONTINUITY-002)

**Date**: 2026-10-23 · **Thread**: https://chatgpt.com/c/6ada4129-7f64-83ec-bf98-c3976f61a2d3
**Context**: verdict HOLD trên DJ-P2-CONTINUITY-001 (`61da7f6`) → GPT chọn phương án hardening.
**Source**: raw ChatGPT reply (archived verbatim below).

---

## Quyết định tóm tắt

**GO triển khai hardening có giới hạn: A + C + F + (a)** — một phương án thống nhất, không phải bốn lựa chọn độc lập.

| Câu hỏi | Quyết định | Nguyên tắc |
|---|---|---|
| 1. Missing continuity | **A — Fail-closed tuyệt đối** | Không có dữ liệu thì không được lập kế hoạch trong autonomous mode |
| 2. Simulation vs proof | **C — Verify-by-render** | Plan nhanh; bắt buộc verify WAV trước khi cho phát hoặc công nhận hoàn thành |
| 3. Phrase-safe | **F — Hoãn phrase-boundary engine** | Công khai giới hạn, bắt buộc kiểm chứng bằng nghe |
| 4. Roadmap | **(a) — Mở khóa mini-set trước** | Nghiệm thu 15–20 phút rồi mới tiến tới 60 phút |

---

## 1. Flag #1 — A: Fail-closed tuyệt đối, KHÔNG thêm `--allow-unmeasured`

Bác bỏ waiver trong runtime thông thường: `--allow-unmeasured` rất dễ thành đường bypass khi chương trình báo lỗi, đưa hệ thống quay lại đúng tình trạng trước khi sửa D10 ↔ D12.

**Cách triển khai chốt:**
- Kiểm tra tính đầy đủ của continuity payload ngay tại đầu vào CLI `plan`, trước beam search.
- Nếu **toàn bộ** corpus hợp lệ thiếu metric → trả `CORPUS_UNMEASURED`.
- Nếu **một phần** thiếu → trả `ANALYSIS_REQUIRED`, kèm số lượng và recording IDs.
- Không đánh đồng dữ liệu thiếu với track thật sự không khả thi về mặt âm thanh.
- Sau khi chạy lại `analyze`, CLI lập kế hoạch bình thường.
- **Exit code 5** cho lỗi dữ liệu chưa được phân tích; giữ **exit code 4** cho `PLAN_INFEASIBLE`.

**Contract lỗi (JSON):**
```json
{
  "error": "ANALYSIS_REQUIRED",
  "reason": "missing_continuity_data",
  "analysis_version_required": 3,
  "continuity_version_required": 1,
  "missing_recording_ids": ["rec-example"],
  "message": "Re-analyze the corpus before planning."
}
```

### Vấn đề G8 phải xử lý đồng thời

Không thể đồng thời yêu cầu strict planner từ chối v2 cache **và** yêu cầu chính strict planner tạo output byte-identical trên v2 cache. Hợp đồng regression chốt lại:

- **G8 lịch sử**: lưu golden output của legacy implementation trên v2 snapshot để đối chiếu khi cần.
- **G8 hiện hành**: cùng một **v3** corpus snapshot, cùng thuật toán, tham số và seed → cùng một `plan.json` byte-for-byte.
- Không thêm waiver vào production chỉ để làm bài test cũ PASS.

---

## 2. Flag #2 — C: Verify-by-render, nhưng KHÔNG render hai lần

Không render master trong mỗi lần beam search hay mỗi lần người dùng thử lập kế hoạch. Với kiến trúc pre-render, chỉ cần đưa verifier thành **cổng bắt buộc của quy trình phát nhạc**:

```
PLAN      → continuity simulation nhanh, loại ứng viên không khả thi
RENDER    → tạo master WAV một lần
VERIFY    → D12 + required checks; đọc audio thật, không tin kết quả mô phỏng
  PASS        → cho phép phát
  FAIL/INCONCLUSIVE → chặn phát
```

- `plan` chỉ tạo candidate plan.
- `render` tạo một master thực.
- `verify` quyết định master có đủ điều kiện kỹ thuật để phát.
- `play` chỉ chấp nhận master có verdict PASS hợp lệ.
- **Ràng buộc kết quả verify với SHA-256 của cả `plan.json` và WAV.** Nếu một trong hai file thay đổi, kết quả PASS cũ mất hiệu lực.

**Có cần sửa max-of-contributors?** Không cần thay bằng worst-case phase lower bound trong vòng hardening này. Predictor có thể sai khi cộng tín hiệu, nhưng verifier cuối cùng kiểm tra waveform thực tế. Lower bound bảo thủ quá mức có nguy cơ loại nhiều transition tốt.

**Bắt buộc**: sửa phát biểu hợp đồng — **simulation không chứng minh chắc chắn PASS D12**, chỉ dự báo tính khả thi để giảm số render vô ích.

- Thêm test đối kháng cho phase cancellation, gain, fade, time-stretch, block boundary để định lượng false-positive/false-negative của predictor. Không đòi predictor khớp tuyệt đối với renderer.
- Mọi trường hợp **predictor PASS nhưng verifier FAIL** phải được ghi lại làm regression fixture.

---

## 3. Flag #3 — F: Hoãn phrase-boundary engine, giới hạn minh bạch

Không triển khai phrase-boundary layer ngay. Với Bolero, nhận diện cấu trúc 8-bar/32-bar từ downbeat ước lượng có thể tạo cảm giác an toàn giả. Analyzer hiện dùng 4/4-assumed, chưa có mô hình xác nhận cấu trúc câu nhạc.

**Limitation ghi vào DECISIONS.md — D17: Cue Boundary Is Not Phrase-Safe:**
- Downbeat-aligned cue chỉ đảm bảo điểm cue nằm trên downbeat ước lượng, **không chứng minh** đó là ranh giới câu nhạc.
- Continuity trimming có thể loại bỏ phần vocal hoặc outro quan trọng.
- Anchor inclusion và D12 PASS **không đồng nghĩa** với bảo toàn đầy đủ bài hát.
- Mọi anchor bị trim đáng kể phải báo rõ `cue_in_s`, `cue_out_s`, thời lượng bị loại và lý do.
- G2b listening verdict vẫn bắt buộc trước nghiệm thu âm nhạc.
- Phrase-boundary model chỉ triển khai khi có bộ audio được đánh giá để đo độ chính xác thực tế.

**Ví dụ thực tế**: anchor hiện tại đã chuyển cue-out từ ~387.397s xuống 351.759s — rút ngắn ~35.64s. Đây là thay đổi đủ lớn để **bắt buộc xem xét bằng nghe**; không thể chỉ dựa vào D12 PASS để khẳng định người nghe nhận đủ phần quan trọng của bài hát.

Hoãn phrase engine **không có nghĩa hoãn toàn bộ DJ-P1-ANCHOR-001**: có thể nghiệm thu phần planner kỹ thuật; G7 và chất lượng âm nhạc kiểm chứng riêng.

---

## 4. Roadmap — (a): không nhảy thẳng G3

- **Bước 1 — Đóng DJ-P2-CONTINUITY**: fail-closed, strict cache, verified-artifact gate, regression tests, CI.
- **Bước 2 — Mở khóa DJ-P0-007 + DJ-P1-ANCHOR-001**: dựng lại demo 15–20 phút có bài "Sao Lòng Còn Thương". Kiểm chứng D12, anchor identity, thời lượng nội dung được giữ và chất lượng transition.
- **Bước 3 — G2b listening verdict**: người nghe đánh giá vocal, phrasing, chuyển nhạc, tính liền mạch. Không tự động đánh dấu PASS từ JSON.
- **Bước 4 — G3 60 phút**: chỉ mở khi mini-set đã được nghiệm thu cả kỹ thuật và âm nhạc.

Nhảy thẳng lên 60 phút không giúp chứng minh chất lượng — chỉ khiến mỗi chu kỳ phát hiện và sửa lỗi tốn thời gian hơn.

---

## 5. Task duy nhất giao Devin CLI

**DJ-P2-CONTINUITY-002 — Final Contract Hardening** (Execution contract · Hermes Lead Orchestrator / Devin CLI only)
