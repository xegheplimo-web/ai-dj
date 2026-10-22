# GPT Review — Khoảng trống D10↔D12 & Hợp đồng Liên tục Âm thanh

- **Ngày**: 2026-10-22
- **Kênh**: ChatGPT (Plus, `attong39@gmail.com`) — thread `https://chatgpt.com/c/6ada4129-7f64-83ec-bf98-c3976f61a2d3`
- **Tiêu đề thread**: "Phân tích kiến trúc AI DJ"
- **Prompt**: brief kiến trúc đầy đủ (6117 ký tự) — `A.` sự thật đã xác minh, `B.` 4 câu hỏi
- **Commit đối chiếu**: `35417213ec791177eafbc1b2ea2037551e7f1c32`
- **Mô hình GPT tự khai**: đã đọc trực tiếp mã nguồn trên GitHub tại commit 35417213

---

## VERDICT: **HOLD** — chưa đủ điều kiện nghiệm thu

> "AI-DJ đã có nền tảng kỹ thuật tương đối tốt, nhưng chưa đủ điều kiện để khẳng định
> có thể tự động tạo một DJ set đạt chất lượng nghe thực tế. Vấn đề quan trọng nhất
> không phải thuật toán beam search mà là planner hiện chưa biết liệu audio được chọn
> có thực sự đáp ứng điều kiện phát liên tục hay không."

---

## Quyết định (1 quyết định duy nhất mỗi câu)

| # | Câu hỏi | Quyết định GPT | Ưu tiên |
|---|---|---|---|
| 1 | Khoảng trống D10↔D12 | **B mở rộng — Unified Audio Continuity Contract** | **P0** |
| 2 | Track "audio" mở đầu | **Không chọn "audio"** làm opener trừ khi đo lại đoạn mở đầu đạt **opener audibility gate** | **P0** |
| 3 | Thứ tự roadmap | **(i) sửa D10↔D12 trước**; bảo toàn code Devin chưa commit, rồi mới tích hợp | **P0** |
| 4 | G7 "anchor nghe được" | **Bắt buộc listening verdict**; fingerprint/cross-correlation chỉ chứng minh nhận dạng tín hiệu | **P1** |

---

## Câu 1 — Unified Audio Continuity Contract

**Không sửa D12 để hợp thức hóa FAIL, không giữ nguyên lỗi với lý do "đặc tính Bolero".**

Phản biện các phương án:

| Phương án | Đánh giá GPT |
|---|---|
| **A** — Nới D12 | **Bác bỏ.** Điều chỉnh tùy tiện sau khi có FAIL là nguy cơ làm yếu tiêu chuẩn nghiệm thu. |
| **B** — Analyzer + planner | **Chọn**, nhưng phải thêm **hard constraint** và phân tích **theo cue segment**; penalty đơn thuần không đủ. |
| **C** — Chỉ dùng metric −100 | **Bác bỏ.** Thực nghiệm đã chỉ ra false eligibility đối với "audio". |
| **D** — Chấp nhận FAIL | **Bác bỏ để nghiệm thu.** Có thể dùng cho nghe thử có gắn nhãn, nhưng không được biến thành PASS. |

### Phân biệt khái niệm GPT chỉ ra

- **D10** = loại bỏ recording kém chất lượng / không phù hợp để tạo set → **bộ lọc corpus**
- **D12** = kiểm tra master phát ra có đáp ứng hợp đồng liên tục → **kiểm tra sản phẩm**

Hai cái không nhất thiết cùng tiêu chuẩn eligibility. **Nhưng planner phải biết trước điều kiện D12 để không tạo kế hoạch chắc chắn thất bại.**

→ Giữ D10 làm bộ lọc corpus, bổ sung **lớp render-feasibility độc lập**.

### Luồng kiến trúc GPT chốt

```
Analyzer
  → Đo silence ĐA NGƯỠNG, RMS tuyệt đối + vùng âm lượng thấp
Segment feasibility
  → Kiểm tra cue_in → cue_out, stretch, gain, overlap, anchor
Anchor-aware beam search
  → Loại phương án CHẮC CHẮN vi phạm hard gate; tối ưu cost trên ứng viên còn lại
Render → Independent D12 verify
  → D12 GIỮ QUYỀN PHỦ QUYẾT kết quả thực tế
```

### Contract đề xuất (GPT cung cấp, giá trị `null` phải tính lại từ WAV)

```json
{
  "continuity_version": 1,
  "block_ms": 50,
  "channels": "all_channels_rms",
  "silence_intervals": {
    "digital_minus90": [],
    "low_level_minus60": []
  },
  "opener": {
    "window_s": 30,
    "max_low_level_run_s": null,
    "audibility_ok": null
  }
}
```

> **Quan trọng**: lưu **intervals**, không chỉ `longest_run_s`. Khi planner thay đổi cue,
> đoạn silence có thể bị loại khỏi phần phát hoặc nằm trong overlap được track khác lấp âm hợp lệ.

### Quy tắc hard constraint

- Ứng viên chỉ được chọn nếu đoạn audio dự kiến phát **không chứa vi phạm D12 không xử lý được**,
  hoặc planner có phương án chuyển tiếp mà renderer thực hiện và kiểm chứng được.
- **Anchor bắt buộc**: ưu tiên cue hợp lệ không chứa im bất thường. **Không tự cắt giữa câu hát,
  không chèn âm nền để đánh lừa D12.** Nếu không tồn tại → trả `PLAN_INFEASIBLE` kèm khoảng vi phạm + nguyên nhân.
- "Không có kế hoạch hợp lệ tốt hơn một kế hoạch đẹp trên JSON nhưng chắc chắn tạo WAV lỗi."

### Chi phí thay đổi (GPT chỉ ra)

Thêm trường vào payload JSON trong SQLite **không bắt buộc đổi cấu trúc bảng** — chỉ cần
**version hóa kết quả phân tích** và đảm bảo cache cũ bị coi là **stale**.

---

## Câu 2 — Opener "audio"

**Quyết định: không chọn "audio" làm opener** trong trường hợp hiện tại, trừ khi đo lại
chính đoạn mở đầu chứng minh đạt audibility gate.

- Lý do **không phải** vì tên file hay nhạc Bolero chậm, mà vì thuật toán chọn opener mới đánh giá
  **energy trung bình của track**, chưa có thông tin về **phân bố âm lượng theo thời gian**.
- Cần kiểm tra **30 giây đầu của đoạn phát thực tế**: khoảng im liên tục, RMS tuyệt đối, phần âm thanh hữu ích.
- GPT **không chọn** cách đơn giản là thêm hệ số RMS vào toàn bộ cost: "Một opener hoàn toàn im lặng
  trong thời gian dài vẫn có thể thắng nếu các cost khác đủ thấp."

### GPT sửa lại luận điểm của Hermes (chỉ ra 2 số khác nhau)

- `26.45s` = longest run dưới −60 dBFS của **source** "audio"
- `[110.65, 125.85]` = khoảng lỗi **15.2s trong master**

→ Hai số này **chưa chứng minh 30s đầu master bị im**. Muốn kết luận chính xác, phải **đo cửa sổ mở đầu
của master** hoặc đoạn đã áp cue.

### Gate ban đầu GPT đặt

Theo đúng D12: **không có run vi phạm digital/low-level tại opener**. G2b sẽ đánh giá tiếp xem mở đầu
có đủ hấp dẫn về âm nhạc — thay vì coi mức âm lượng đạt ngưỡng là đồng nghĩa với một phần mở màn hay.

---

## Hai phát hiện kỹ thuật quan trọng

### (1) Sửa ngưỡng âm học — Hermes đã nhầm hướng

> "Thay −60 → −50 dBFS **không phải nới lỏng** ngưỡng. Nó làm detector **nghiêm ngặt hơn** vì nhiều
> đoạn âm thanh sẽ bị xếp vào vùng low-level hơn. **Chỉ hạ ngưỡng xuống −70 dBFS** mới làm detector bớt nhạy."

**Hermes xác nhận GPT đúng.** Nâng ngưỡng (−60 → −50) mở rộng vùng bắt buộc ≤ ngưỡng → nhiều đoạn
bị tính là low-level hơn → detector **chặt hơn**. Hạ ngưỡng (−60 → −70) thu hẹp vùng → **lỏng hơn**.
→ Phương án A trong brief của Hermes (đề xuất −60 → −50 với ý "nới lỏng") **sai hướng**.

### (2) Penalty trong planner không bảo đảm giải quyết được lỗi

> "Với anchor bắt buộc, nếu đoạn 3.9 giây gần như im lặng vẫn nằm trong phần phát và không có track
> khác lấp được, planner có thể tạo một kế hoạch tối ưu về cost nhưng **chắc chắn FAIL D12**."

→ Đây là lý do phải dùng **hard constraint** (loại ứng viên) chứ không chỉ **penalty** (giảm cost).

---

## Phát hiện MỚI của GPT (Hermes đã kiểm chứng — ĐÚNG CẢ HAI)

> "Analyzer đo silence trên tín hiệu **mono 22,05 kHz**, còn D12 đo RMS **đa kênh** trên master **48 kHz**.
> Vì vậy, ngay cả khi đồng bộ ngưỡng, hai tầng vẫn có thể **bất đồng**."

**Xác minh của Hermes trên source:**

| GPT claim | Bằng chứng | Kết luận |
|---|---|---|
| Analyzer mono 22.05 kHz | `analyzer.py:34 ANALYSIS_SR = 22050`; `:180 librosa.load(..., sr=ANALYSIS_SR, mono=True)` | ✅ **XÁC NHẬN** |
| Verify đa kênh ở sr gốc (48 kHz) | `verify.py:412 sf.read(..., dtype="float32", always_2d=True)` | ✅ **XÁC NHẬN** |

→ **Hai gate đo HAI TÍN HIỆU KHÁC NHAU.** Đồng bộ ngưỡng một mình **không đủ** — phải đồng bộ
cả tín hiệu đo (sample rate + số kênh) hoặc ghi rõ điều kiện nghiệm thu.

---

## Câu 3 — Thứ tự roadmap

**(i) sửa D10↔D12 trước**; bảo toàn code Devin chưa commit, rồi mới tích hợp. → **P0**

## Câu 4 — G7 "anchor nghe được"

**Bắt buộc listening verdict.** Fingerprint/cross-correlation chỉ chứng minh **nhận dạng tín hiệu**
(signal present), không chứng minh **người nghe nghe thấy được theo nghĩa chất lượng**. → **P1**

---

## Hành động phát sinh

| # | Task | Nguồn |
|---|---|---|
| 1 | **DJ-P2-CONTINUITY-001** — Unified Audio Continuity Contract (P0) | GPT quyết định Câu 1 |
| 2 | Đồng bộ tín hiệu đo giữa analyzer ↔ verify (mono 22.05k vs multi-ch 48k) | GPT phát hiện mới |
| 3 | Thêm **opener audibility gate** (cửa sổ 30s) | GPT quyết định Câu 2 |
| 4 | Đo lại cửa sổ mở đầu của **master** (không chỉ source) | GPT sửa luận điểm |
| 5 | `PLAN_INFEASIBLE` có cấu trúc cho anchor bắt buộc | GPT quyết định Câu 1 |
| 6 | G2b listening verdict framework | GPT quyết định Câu 4 |

---

## Trạng thái chấp nhận

- **DJ-P0-007** và **DJ-P1-ANCHOR-001** đều **KHÔNG được chấp nhận** cho tới khi
  Unified Audio Continuity Contract được thi hành và verify PASS.
- Verdict này **không** được ghi đè bằng việc nới ngưỡng hoặc chấp nhận FAIL.
