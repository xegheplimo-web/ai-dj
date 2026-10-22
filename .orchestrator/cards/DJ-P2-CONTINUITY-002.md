# DJ-P2-CONTINUITY-002 — Final Contract Hardening

- **Priority**: P0
- **Nguồn**: GPT decision 2026-10-23 (`docs/gpt-decision-hardening-2026-10-23.md`) — quyết định **A + C + F + (a)**
- **Trạng thái**: todo
- **Phụ thuộc**: DJ-P2-CONTINUITY-001 (đã có code, GPT HOLD)

---

## 0. Bối cảnh

DJ-P2-CONTINUITY-001 đã triển khai continuity contract (commit `61da7f6`, CI xanh). GPT verdict **HOLD** với 3 điểm. GPT đã chốt phương án hardening: **A + C + F + (a)**.

---

## 1. Điều khoản bắt buộc (mã hoá quyết định GPT)

### C1 — Fail-closed tuyệt đối (Flag #1 = A)

- **Không có** `--allow-unmeasured` waiver trong runtime thông thường.
- Kiểm tra tính đầy đủ continuity payload **tại đầu vào CLI `plan`, trước beam search**.
- Toàn bộ corpus hợp lệ thiếu metric → `CORPUS_UNMEASURED`.
- Một phần thiếu → `ANALYSIS_REQUIRED` + số lượng + `missing_recording_ids`.
- **Không** đánh đồng dữ liệu thiếu với track thật sự không khả thi về âm thanh.
- Exit code **5** = dữ liệu chưa phân tích; giữ exit **4** = `PLAN_INFEASIBLE`.
- Contract lỗi:
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

### C2 — Hợp đồng regression G8 (xử lý mâu thuẫn GPT phát hiện)

- **G8 lịch sử**: golden output của legacy trên **v2** snapshot — chỉ để đối chiếu khi cần (lưu trữ, không phải cổng).
- **G8 hiện hành**: cùng một **v3** corpus snapshot + cùng thuật toán/tham số/seed → cùng `plan.json` byte-for-byte.
- **Không** thêm waiver vào production chỉ để làm bài test cũ PASS.

### C3 — Verify-by-render (Flag #2 = C), KHÔNG render hai lần

- Không render trong beam search hay mỗi lần thử lập kế hoạch.
- Quy trình: `PLAN` (simulation nhanh, prune) → `RENDER` (master thật một lần) → `VERIFY` (đọc audio thật) → `PASS` cho phát / `FAIL|INCONCLUSIVE` chặn phát.
- `play` **chỉ** chấp nhận master có verdict PASS hợp lệ.
- **Ràng buộc kết quả verify với SHA-256 của cả `plan.json` và WAV.** Đổi 1 trong 2 file → PASS cũ mất hiệu lực.
- **Không** thay max-of-contributors bằng worst-case phase lower bound trong vòng này.
- Sửa phát biểu hợp đồng: **simulation KHÔNG chứng minh PASS D12**, chỉ dự báo khả thi để giảm render vô ích.

### C4 — Test đối kháng predictor

- Thêm test cho phase cancellation, gain, fade, time-stretch, block boundary → định lượng false-positive/false-negative.
- Không đòi predictor khớp tuyệt đối renderer.
- Mọi ca **predictor PASS nhưng verifier FAIL** → ghi lại làm **regression fixture**.

### C5 — Limitation minh bạch (Flag #3 = F)

- Ghi **D17 — Cue Boundary Is Not Phrase-Safe** vào `DECISIONS.md`:
  - Downbeat-aligned cue chỉ nằm trên downbeat ước lượng, **không chứng minh** là ranh giới câu nhạc.
  - Continuity trimming có thể loại vocal/outro quan trọng.
  - Anchor inclusion + D12 PASS **không đồng nghĩa** bảo toàn đầy đủ bài hát.
  - Mọi anchor bị trim đáng kể phải báo `cue_in_s`, `cue_out_s`, thời lượng bị loại, lý do.
  - G2b listening verdict bắt buộc trước nghiệm thu âm nhạc.
  - Phrase-boundary model chỉ làm khi có bộ audio được đánh giá.

---

## 2. Roadmap (Flag #4 = a) — KHÔNG nhảy thẳng G3

1. **Đóng DJ-P2-CONTINUITY** (card này): fail-closed + strict cache + verified-artifact gate + regression + CI.
2. **Mở khóa DJ-P0-007 + DJ-P1-ANCHOR-001**: demo 15–20 phút có bài "Sao Lòng Còn Thương"; kiểm chứng D12, anchor identity, thời lượng nội dung giữ được, chất lượng transition.
3. **G2b listening verdict**: người nghe đánh giá; không tự động PASS từ JSON.
4. **G3 60 phút**: chỉ mở khi mini-set nghiệm thu cả kỹ thuật và âm nhạc.

---

## 3. Gate nghiệm thu

| Gate | Tiêu chí |
|---|---|
| **H1** | `plan` trên corpus thiếu continuity data → exit **5** + structured error (`ANALYSIS_REQUIRED` hoặc `CORPUS_UNMEASURED`), không tạo plan |
| **H2** | Toàn bộ corpus hợp lệ nhưng thiếu data → `CORPUS_UNMEASURED`; một phần → `ANALYSIS_REQUIRED` + IDs |
| **H3** | Sau khi `analyze` lại, `plan` chạy bình thường |
| **H4** | G8 hiện hành: 2 lần chạy trên **cùng v3** snapshot → `plan.json` byte-identical (sha256) |
| **H5** | `play` từ chối master không có verdict PASS hợp lệ (hoặc verdict ràng buộc SHA khác plan/WAV hiện tại) |
| **H6** | verify verdict ràng buộc SHA-256 của `plan.json` + WAV; đổi 1 file → PASS mất hiệu lực |
| **H7** | Test đối kháng: phase cancellation / gain / fade / time-stretch / block boundary; mọi ca predictor-PASS-verifier-FAIL → regression fixture |
| **H8** | `DECISIONS.md` có **D17** đầy đủ các ý (cue ≠ phrase, trimming mất nội dung, anchor+D12 ≠ bảo toàn, báo cue/trim, G2b bắt buộc) |
| **H9** | Toàn bộ test PASS; `ruff check .` clean; `ruff format --check .` clean |
| **H10** | `verify.py` thresholds KHÔNG đổi (diff rỗng) |

---

## 4. Ràng buộc

- **KHÔNG** commit/push (Hermes sở hữu git)
- **KHÔNG** ghi trong `.devin/` (ForceAsk → chết phiên)
- **KHÔNG** đổi ngưỡng `verify.py`
- **KHÔNG** thêm `--allow-unmeasured` waiver (GPT bác bỏ)
- **KHÔNG** thêm dependency mới nếu không cần
- **KHÔNG** đổi schema bảng SQLite (version payload JSON)
- Chuẩn hoá line-ending: `sed -i 's/\r$//'` + kiểm `open(p,'rb').read().count(b'\r')`
- Báo cáo cuối **dán output thật** (số test, JSON mẫu, file đổi) — "tests pass" không kèm output là không chấp nhận

## 5. Đầu ra

- Code + test trong worktree (dirty, chưa commit)
- `evidence/dj_p2_continuity002_*.json`
- Báo cáo markdown: giải thích C1–C5 + bằng chứng
