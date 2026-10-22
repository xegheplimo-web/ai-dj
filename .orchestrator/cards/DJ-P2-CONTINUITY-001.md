# DJ-P2-CONTINUITY-001 — Unified Audio Continuity Contract

- **Priority**: P0
- **Nguồn**: GPT verdict 2026-10-22 (`docs/gpt-review-continuity-2026-10-22.md`) — **VERDICT: HOLD**
- **Thread**: https://chatgpt.com/c/6ada4129-7f64-83ec-bf98-c3976f61a2d3
- **Trạng thái**: todo
- **Phụ thuộc**: không

---

## 1. Bối cảnh

GPT: *"planner hiện chưa biết liệu audio được chọn có thực sự đáp ứng điều kiện phát liên tục hay không."*

Đã đo và xác nhận **HAI** khoảng trống độc lập:

### Lỗi 1 — Lệch ngưỡng D10 ↔ D12

| Gate | File | Ngưỡng | Track "audio" |
|---|---|---|---|
| D10 (analyzer) | `ai_dj/analysis/analyzer.py:45` | **−100 dBFS** / ≥2.0s | ratio 0.000, longest 0.00s ✅ |
| D12 (verifier) | `ai_dj/verify.py:53,55` | **−90 / −60 dBFS** | **26.45s tại ≤−60 → FAIL** |

Track "audio" vượt cửa D10 nhưng đổ mix khi D12 FAIL.

### Lỗi 2 — Hai gate đo HAI TÍN HIỆU KHÁC NHAU (GPT phát hiện, Hermes đã xác minh)

| Gate | Bằng chứng | Tín hiệu đo |
|---|---|---|
| D10 analyzer | `analyzer.py:34 ANALYSIS_SR = 22050`; `:180 librosa.load(..., sr=ANALYSIS_SR, mono=True)` | **mono 22.05 kHz** |
| D12 verifier | `verify.py:412 sf.read(..., dtype="float32", always_2d=True)` | **đa kênh ở sr gốc (48 kHz)** |

→ Đồng bộ ngưỡng một mình **không đủ**; phải đồng bộ cả **tín hiệu đo** hoặc ghi rõ
điều kiện nghiệm thu cho phép lệch.

### Hệ quả

- `DJ-P0-007` và `DJ-P1-ANCHOR-001` đều **FAIL D12** (đoạn im **trong track**, không phải tại transition)
- Lỗi **tái diễn**: mọi set từ corpus này có nguy cơ FAIL

---

## 2. Mục tiêu

Thi hành **Unified Audio Continuity Contract**: planner biết trước điều kiện D12 để
**không tạo kế hoạch chắc chắn thất bại**, trong khi D12 giữ nguyên quyền phủ quyết.

**Giữ nguyên (KHÔNG đổi để hợp thức hóa FAIL):**
- `DIGITAL_SILENCE_DBFS = -90.0`, `LOW_LEVEL_DBFS = -60.0`, các `min_run_s` trong `verify.py`
- D10 vẫn là **bộ lọc corpus**

---

## 3. Yêu cầu kỹ thuật

### 3.1 Analyzer — đo silence đa ngưỡng, lưu **intervals**

Bổ sung vào kết quả phân tích (payload JSON, **không đổi schema bảng SQLite** — xem §3.5):

```json
{
  "continuity_version": 1,
  "block_ms": 50,
  "channels": "all_channels_rms",
  "silence_intervals": {
    "digital_minus90": [[start_s, end_s], ...],
    "low_level_minus60": [[start_s, end_s], ...]
  },
  "opener": {
    "window_s": 30,
    "max_low_level_run_s": null,
    "audibility_ok": null
  }
}
```

- **Bắt buộc lưu intervals, không chỉ `longest_run_s`** — khi planner đổi cue, đoạn silence
  có thể bị loại khỏi phần phát hoặc nằm trong overlap được track khác lấp âm hợp lệ.
- `opener` đo trên **cửa sổ 30s đầu** của đoạn phát thực tế (cue đã áp).
- Giá trị `null` là **chưa tính** — phải tính từ WAV, không được mặc định hoặc đoán.

### 3.2 Đo trên tín hiệu khớp với master

- Đo silence theo **đa kênh** (all-channels RMS) tại **sample rate đầu ra của render**, khớp với
  `verify.py:412`. Giữ `ANALYSIS_SR = 22050 mono` cho các metric khác (BPM/key/energy) — nhưng
  **metric continuity phải dùng tín hiệu khớp master**, hoặc ghi rõ điều kiện nghiệm thu cho phép lệch.
- Nếu chọn cách giữ 2 tín hiệu: phải **đo lại và in ra** độ lệch thực tế giữa hai cách đo trên
  toàn bộ corpus, kèm kết luận có đáng coi là tương đương không.

### 3.3 Segment feasibility

Kiểm tra từng ứng viên trên **đoạn thực sự sẽ phát**: `cue_in → cue_out`, stretch, gain, overlap, anchor.
→ Xác định đoạn im nào **rơi vào phần phát**, cái nào bị loại, cái nào được overlap lấp.

### 3.4 Planner — **hard constraint** (không chỉ penalty)

- Loại ứng viên **chắc chắn vi phạm hard gate** khỏi beam search (GPT: penalty đơn thuần **không đủ** —
  một opener hoàn toàn im vẫn có thể thắng nếu cost khác đủ thấp).
- **Anchor bắt buộc**: ưu tiên cue hợp lệ không chứa im bất thường.
  - **KHÔNG tự cắt giữa câu hát. KHÔNG chèn âm nền để đánh lừa D12.**
  - Nếu không tồn tại cue phù hợp → trả **`PLAN_INFEASIBLE`** kèm khoảng vi phạm + nguyên nhân (structured, exit code riêng).
- Thêm **opener audibility gate**: không có run vi phạm digital/low-level trong 30s đầu đoạn phát.

### 3.5 Cache versioning — KHÔNG đổi schema bảng

- Thêm `continuity_version` vào **payload JSON** (không `ALTER TABLE`).
- Cache cũ (thiếu version) phải bị coi là **stale** → re-analyze.
- **Phải chứng minh**: cache cũ không dùng được ngầm; re-analyze sinh ra kết quả mới.

### 3.6 Đo lại cửa sổ mở đầu của **master**

GPT sửa luận điểm của Hermes: `26.45s` là của **source**, `[110.65, 125.85]` là lỗi **15.2s trong master** —
hai số khác nhau, **chưa chứng minh 30s đầu master bị im**. Phải đo cửa sổ mở đầu của **master**.

---

## 4. Gate nghiệm thu

| Gate | Tiêu chí | Cách kiểm |
|---|---|---|
| **G1** | Analyzer sinh `continuity_version`, intervals đa ngưỡng, `opener` đã tính (không null) | Chạy `analyze`, in JSON; assert schema |
| **G2** | Cache cũ (không version) bị coi stale → re-analyze; không dùng ngầm | Test: cache cũ → stale; re-analyze → mới |
| **G3** | Metric continuity đo trên tín hiệu khớp master (đa kênh, sr đầu ra) | Đối chiếu `verify.py:412`; in độ lệch đo nếu giữ 2 tín hiệu |
| **G4** | Planner loại ứng viên chắc chắn vi phạm hard gate (hard constraint, không chỉ penalty) | Test: track vi phạm → không vào plan |
| **G5** | `PLAN_INFEASIBLE` có cấu trúc, exit code riêng, kèm khoảng vi phạm + nguyên nhân | Test 3 nhánh: infeasible do silence / do cue / do anchor không hợp lệ |
| **G6** | Opener audibility gate: 30s đầu master không có run vi phạm | Đo trên **master đã render** (không phải source) |
| **G7** | Render + verify **PASS D12** trên plan có chứa anchor | Chạy plan → render → verify; đưa JSON |
| **G8** | Legacy regression: plan không có `include_ids` **byte-identical** trước/sau | 2 lần chạy, `cmp` sha256 |
| **G9** | Không nới ngưỡng D10/D12; D12 giữ quyền phủ quyết | Diff `verify.py` — không có thay đổi ngưỡng |
| **G10** | Toàn bộ 64+ test PASS, ruff clean, ruff format sạch | Chạy và dán output |

---

## 5. Ràng buộc

- **KHÔNG** commit / push — Hermes sở hữu git
- **KHÔNG** tạo/ghi file trong `.devin/` (ForceAsk → chết phiên); nếu cần config toolchain, chỉ mô tả trong docs
- **KHÔNG** nới ngưỡng D10/D12 để hợp thức hóa FAIL
- **KHÔNG** tự cắt giữa câu hát hoặc chèn âm nền để đánh lừa D12
- **KHÔNG** thêm dependency mới nếu không có nhu cầu thực tế
- **KHÔNG** đổi schema bảng SQLite (chỉ version hóa payload JSON)
- Báo cáo cuối cùng **phải dán output thật** (số test, JSON mẫu, danh sách file đổi) —
  "tests pass" không kèm output in ra là không chấp nhận
- Chuẩn hóa line-ending: `sed -i 's/\r$//'` sau khi ghi file + kiểm bằng Python (`open(p,'rb').read().count(b'\r')`)

## 6. Đầu ra

- Code + test trong worktree (dirty, chưa commit)
- `evidence/dj_p2_continuity_*.json` — kết quả đo, plan mới, verify mới
- Báo cáo markdown: giải thích contract, intervals, hard constraint, và **bằng chứng đo lại cửa sổ mở đầu master**
