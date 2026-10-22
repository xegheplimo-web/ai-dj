# DJ-P2-MINISET-G2B-001 — Anchor-Preserved 15–20 Minute Mini-Set

- **Priority**: P0
- **Nguồn**: GPT review 2026-10-23 (`docs/gpt-review-miniset-2026-10-23.md`) — **UNLOCKED**
- **Trạng thái**: todo
- **Phụ thuộc**: DJ-P2-CONTINUITY-002 (ACCEPTED, `bccbf03`)

---

## 0. Bối cảnh

GPT đã **ACCEPT** DJ-P2-CONTINUITY-002 và **UNLOCK** bước 2: dựng mini-set 15–20 phút có bài
anchor "Sao Lòng Còn Thương", render WAV thật, verify, rồi G2b human listening.
**G3-60MIN vẫn HOLD** tới khi mini-set nghiệm thu cả kỹ thuật lẫn âm nhạc.

Nguyên tắc GPT chốt: *"bảo vệ nội dung bài anchor ngay từ planner, thay vì để G2b quyết định
có chấp nhận một đoạn cắt lớn hay không."*

---

## 1. Điều khoản bắt buộc

### C1 — Structural Anchor Preservation (M3)
- Planner **phải đặt bài anchor ở vị trí cuối set** khi được yêu cầu giữ nội dung anchor.
- Không cho continuity optimizer tự ý cắt phần kết anchor chỉ để làm D12 PASS.
- Nếu giữ nội dung anchor không thể đồng thời thỏa D12 → trả về `PLAN_INFEASIBLE` có lý do rõ ràng.
- **KHÔNG** tự động sửa ngưỡng D12, **KHÔNG** thêm silent-gap exemption, **KHÔNG** giấu phần cắt.
- `--include-track` hiện chỉ bảo đảm anchor **xuất hiện** — cần bổ sung ràng buộc vị trí cuối + bảo toàn nội dung.

### P1 — Kiểm tra độ đầy đủ continuity payload
- `has_continuity_payload()` hiện chỉ kiểm tra `continuity_version` + `block_rms_dbfs` không rỗng → payload 1 block vẫn lọt gate.
- Cần kiểm tra `block_ms`, sample rate, giá trị **hữu hạn (finite)** và **khớp thời lượng audio**.
- Đây là hardening quan trọng nhất còn thiếu ở C1.

### P2 — Cue report phản ánh TOÀN BỘ phần bị loại
- `cue_report` hiện chỉ xuất hiện khi cue được **điều chỉnh**; default cue vẫn cắt intro/outro nhưng không báo cáo.
- Với mini-set, mỗi track phải ghi: source duration, retained duration, tổng head/tail trim so với **file gốc**, phần trim bổ sung, lý do.
- Ghi rõ `removed_tail_s` hiện đo **trim bổ sung**, không phải tổng so với audio gốc.

### P3 — Phân biệt artifact integrity với verifier provenance (ghi nhận, không chữ ký)
- Không tuyên bố SHA-256 là bằng chứng **chống giả mạo** hay chứng minh WAV render từ chính plan.
- Ghi rõ D12 cho phép `allowed_silent_gaps_s` khai báo trong plan → phải kiểm soát exemption đó.
- `sha256_file()` dùng `read_bytes()` (nạp cả WAV vào RAM) → chuyển hash **theo chunk** — **trước G3**, không phải blocker mini-set.

### C4 — Không cố định false-negative vĩnh viễn
- Regression test adversarial hiện yêu cầu ≥1 false-negative + giữ phân loại cũ.
- Đổi thành: fixture lưu **trường hợp lịch sử**, cho phép cải thiện predictor + cập nhật baseline **có kiểm soát**.

---

## 2. Gate nghiệm thu (M1–M8)

| Gate | Tiêu chí |
|---|---|
| **M1 — Duration** | Master thực **900–1.200 giây** (15–20 phút) |
| **M2 — Anchor** | Đúng recording ID `rec-36bd9cb09f26f5b8`, không thay bản ghi / duplicate |
| **M3 — Preservation** | Anchor ở **cuối set**; không mất nội dung quan trọng; tổng trim khai báo |
| **M4 — D12** | Real WAV PASS cả digital silence lẫn low-level silence |
| **M5 — Audio integrity** | True peak, clipping, sample rate + các required checks PASS |
| **M6 — Playback** | Bound verdict PASS, phát trọn file, **0 underflows** |
| **M7 — Listening** | G2b có verdict người nghe, ghi lỗi + timestamp |
| **M8 — Traceability** | Lưu plan, master SHA-256, verification JSON, cue report, listening report |

> **Quy tắc FAIL cứng**: một lỗi làm mất câu hát, cắt cụt kết bài hoặc chuyển nhạc gượng ép
> nghiêm trọng là **FAIL, dù D12 vẫn PASS**.

> **Quy tắc sửa lỗi**: nếu render/verify FAIL, giữ nguyên bằng chứng thất bại, sửa đúng nguyên
> nhân, tạo candidate mới. **KHÔNG sửa verifier để hợp thức hóa master.**

---

## 3. Ràng buộc

- **KHÔNG** commit/push (Hermes sở hữu git)
- **KHÔNG** ghi trong `.devin/` (ForceAsk → chết phiên)
- **KHÔNG** đổi ngưỡng `verify.py`; **KHÔNG** thêm silent-gap exemption
- **KHÔNG** tự ý sửa ngưỡng D12 hay `verify.py` để master PASS
- **KHÔNG** thêm dependency mới; **KHÔNG** đổi schema SQLite
- Line-ending: `sed -i 's/\r$//'` + kiểm `open(p,'rb').read().count(b'\r') == 0`
- Báo cáo cuối **dán output thật** (số test, exit code, JSON mẫu, file đổi)

## 4. Đầu ra

- Code + test trong worktree (dirty, chưa commit)
- Master WAV + `plan.json` + `verify` JSON + cue report (JSON tracked; WAV gitignored)
- `evidence/dj_p2_miniset_*.json` + báo cáo markdown
