# GPT Review — DJ-P2-CONTINUITY-002 (2026-10-23)

- **Thread**: https://chatgpt.com/c/6ada8372-62f8-83ec-9475-8616965762fc
- **Model**: ChatGPT High (extended thinking + web search + GitHub retrieval)
- **Đối tượng review**: commit `bccbf03` (cli.py, planner.py, continuity.py, verdict.py, player.py, verify.py, adversarial tests)

## Final decision

> **DJ-P2-CONTINUITY-002 = ACCEPTED; DJ-P2-MINISET-G2B-001 = UNLOCKED; G3-60MIN = HOLD.**

> Định hướng tiếp theo là chứng minh hệ thống không chỉ phát nhạc liên tục, mà còn giữ được
> cấu trúc âm nhạc và nội dung bài hát mà người nghe mong đợi. Đây chính là khoảng cách giữa
> một audio pipeline đạt kiểm thử kỹ thuật và một AI-DJ có thể tự chủ về mặt âm nhạc.

## 1. Đánh giá C1–C5

| Hạng mục | Verdict | Nhận xét |
|---|---|---|
| C1 — Fail-closed | **PASS có lưu ý** | Chặn v2/missing payload, phân biệt exit 5 với exit 4 |
| C2 — G8 determinism | **PASS** | Hợp đồng regression mới hợp lý, không nới production |
| C3 — Verify-by-render | **PASS có giới hạn** | Gate SHA hoạt động, verifier giữ quyền quyết định |
| C4 — Predictor adversarial | **ACCEPT** | Chấp nhận sai lệch vì final WAV luôn phải verify |
| C5 — D17/D18 | **PASS có lưu ý** | Giới hạn phrase-safe được công khai, nhưng trim report chưa đầy đủ |

Không còn blocker từ phạm vi H1–H10 buộc phải giữ HOLD cho việc bắt đầu mini-set.

GPT lưu ý: số 107 tests + CI xanh được ghi nhận từ bằng chứng Hermes cung cấp; GPT **chưa** tự chạy lại test trên Windows.

## 2. H7 — có cần nâng predictor ngay?

**Không. Giữ nguyên predictor hiện tại.** Phân biệt quan trọng:

- `phase_cancellation_constructed` dùng master **dựng thủ công** để chứng minh giới hạn lý thuyết.
- `gain_peak_scale_gap` là false-negative **tái hiện qua `render_plan` thật**.

→ Không được diễn giải 2/6 là tỷ lệ false-negative của hệ thống khi chạy nhạc thực tế.

Kiến trúc được chấp nhận: `PLAN (predict & prune) → RENDER (master thật) → VERIFY (final veto)`; PASS = được phép playback kỹ thuật, FAIL/INCONCLUSIVE = chặn playback.

**Sửa C4 sau mini-set**: regression test hiện yêu cầu phải còn ≥1 false-negative và giữ nguyên phân loại. Khi predictor được cải thiện, test đó không nên thất bại chỉ vì đã loại bỏ false-negative. Fixture lưu trường hợp lịch sử, nhưng cho phép cải thiện kiểm chứng + cập nhật baseline có kiểm soát.

## 3. D17 — quyết định xử lý trim 35,64 giây

GPT chọn **(c) Structural Anchor Preservation** — bảo vệ nội dung bài anchor ngay từ planner, thay vì để G2b quyết định có chấp nhận một đoạn cắt lớn hay không.

- Bài anchor dài 389,397 s; cue-out hiện chuyển tới 351,759 s.
- Tổng phần cuối bị loại so với file gốc: **37,638 s** (trim bổ sung 35,638 s + trim mặc định 2 s).
- `cue_report.removed_tail_s` hiện đo phần cắt **bổ sung** so với default cue, **không phải** tổng thời lượng bị cắt so với audio gốc → gây hiểu sai.

**Quy tắc mới cho mini-set:**
- Với bài anchor, planner phải đặt bài ở **vị trí cuối set**, ưu tiên giữ trọn phần kết có âm nhạc.
- Không cho continuity optimizer tùy ý cắt 35 s chỉ để làm D12 PASS.
- Nếu giữ nội dung anchor không thể đồng thời thỏa D12 → trả về `PLAN_INFEASIBLE` có lý do rõ ràng.
- Không tự động sửa ngưỡng D12, thêm silent-gap exemption hay giấu phần cắt.
- Cần bổ sung ràng buộc planner cho mini-set: `--include-track` hiện mới chỉ bảo đảm anchor **xuất hiện**, chưa bảo đảm **vị trí cuối** hay **mức độ bảo toàn nội dung**.

G2b sau đó phải nghe cả đoạn cuối bản gốc và phần tương ứng trong master. Người nghe vẫn giữ quyền phủ quyết nếu mất câu hát, câu nhạc hoặc đoạn kết quan trọng.

## 4. Ba cải tiến bắt buộc ghi nhận (P1–P3)

Không hủy kết quả C1–C5, nhưng phải đưa vào kiểm soát khi mở rộng.

### P1 — Kiểm tra độ đầy đủ của continuity payload
Hiện `has_continuity_payload()` chỉ kiểm tra `continuity_version` và `block_rms_dbfs` không rỗng. Payload chỉ chứa **một block** cũng vượt qua input gate. Cần kiểm tra `block_ms`, sample rate, giá trị hữu hạn (finite) và **khớp với thời lượng audio**. Đây là hardening có ý nghĩa thực tế nhất còn thiếu ở C1.

### P2 — Cue report phải phản ánh toàn bộ phần bị loại
`cue_report` hiện chỉ xuất hiện khi cue được điều chỉnh. Default cue vẫn cắt intro/outro nhưng **không phát sinh báo cáo**. Với mini-set, mỗi track cần ghi rõ: source duration, retained duration, tổng head/tail trim so với file gốc, phần trim bổ sung và lý do.

### P3 — Phân biệt artifact integrity với verifier provenance
SHA-256 chứng minh tệp khớp bản đã ghi nhận; **sidecar JSON hiện không được xác thực nguồn tạo**. Hơn nữa D12 cho phép `allowed_silent_gaps_s` khai báo trong plan → phải kiểm soát các exemption đó trong quy trình tự chủ. Với demo local đáng tin cậy, **chưa cần** hệ thống chữ ký mật mã, nhưng **không được tuyên bố** đây là bằng chứng chống giả mạo hay chứng minh WAV được render từ chính plan.

`sha256_file()` đang dùng `read_bytes()` đưa toàn bộ WAV vào bộ nhớ → chuyển sang hash theo chunk **trước G3 60 phút**, không phải blocker của mini-set.

## 5. Acceptance contract cho bước 2 — DJ-P2-MINISET-G2B-001

| Gate | Điều kiện nghiệm thu |
|---|---|
| **M1 — Duration** | Master thực từ **900–1.200 giây** (15–20 phút) |
| **M2 — Anchor** | Đúng recording ID, không bị thay bản ghi hoặc duplicate |
| **M3 — Preservation** | Anchor ở **cuối set**; không cắt mất nội dung quan trọng; tổng trim được khai báo |
| **M4 — D12** | Real WAV PASS cả digital silence và low-level silence |
| **M5 — Audio integrity** | True peak, clipping, sample rate và các required checks PASS |
| **M6 — Playback** | Bound verdict PASS, phát trọn file, **0 underflows** trong bài kiểm thử thiết bị |
| **M7 — Listening** | G2b có verdict của người nghe, ghi rõ lỗi và timestamp |
| **M8 — Traceability** | Lưu plan, master SHA-256, verification JSON, cue report và listening report |

G2b phải đánh giá: chuyển nhịp, hòa âm, chuyển vocal, năng lượng giữa bài, chất lượng kết thúc và **đặc biệt đoạn cuối anchor so với bản gốc**. Một lỗi làm mất câu hát, cắt cụt kết bài hoặc chuyển nhạc gượng ép nghiêm trọng là **FAIL, dù D12 vẫn PASS**.

Nếu render hoặc verify FAIL: Hermes giữ nguyên bằng chứng thất bại rồi cho Devin sửa đúng nguyên nhân và tạo candidate mới. **Không sửa verifier để hợp thức hóa master.**

## 6. Trả lời cuối

- **Q1 — Unlock step 2?** YES. GO cho mini-set và G2b, **HOLD cho G3**.
- **Q2 — Accept H7 false-negatives?** YES. Giữ predictor hiện tại, verify-by-render với veto tuyệt đối. Chưa tối ưu lại bound.
- **Q3 — Trim −35,64 s?** Chọn **(c)**. Bảo vệ anchor trong planner, đặt cuối set, không cho optimizer tự hy sinh phần kết quan trọng. G2b kiểm chứng bằng nghe, không dùng G2b làm lý do chấp nhận trim thiếu kiểm soát.
- **Q4 — Implement differently?** C1 cần kiểm tra đầy đủ block map; C4 không nên cố định false-negative mãi mãi; C5 cần báo toàn bộ trim từ source gốc; C3 cần ghi rõ giới hạn xác thực provenance.

## 7. Hành động của Hermes

1. Đóng DJ-P2-CONTINUITY-002 (ACCEPTED) — commit `bccbf03`, CI+CodeQL xanh.
2. Mở **DJ-P2-MINISET-G2B-001** (UNLOCKED) cho Devin CLI, gồm P1+P2 (P3 phần chữ ký hoãn; chunked-hash trước G3).
3. G3-60MIN giữ **HOLD** cho tới khi mini-set nghiệm thu cả kỹ thuật lẫn âm nhạc.
