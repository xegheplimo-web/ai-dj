# Brief P0 — AI DJ (cập nhật sau khi P0 xanh CI)

Gửi: 2026-10-22 · Thread: "AI chơi DJ"
Trạng thái: P0 vertical slice XANH (CI 5/5), 3 lỗi thật đã sửa.

---

## §A — SỰ THẬT ĐO ĐƯỢC (chỉ số Hermes tự đo, có lệnh)

**Repo:** github.com/xegheplimo-web/ai-dj — public, MIT, branch `main`, 2780 LOC Python.
**CI:** 5/5 xanh — lint+format, test py3.12/3.13 ubuntu, py3.12 windows, E2E timing gate, CodeQL (run 39748627985).

**Pipeline:** scan → analyze (sqlite cache v2, idempotent) → plan (deterministic, seed-pinned)
→ render (ffmpeg rubberband, deterministic) → play (sounddevice ring buffer)
→ verify (ffmpeg ebur128 độc lập, không dùng code của repo).

| Hạng mục | Kết quả | Cách đo |
|---|---|---|
| Lệch beat (synthetic click, ground truth biết trước) | **0.0 ms** (hợp đồng ≤ 20 ms) | `ai_dj.timing` trên click track đi qua render thật |
| Master 1307.261 s | true peak **−1.2 dBFS**, clip **0**, **−20.1 LUFS**, LRA 8.8 | ffmpeg ebur128 trên file đĩa |
| analyze | **idempotent** — sha256 cache + summary giống hệt 2 lần chạy | sha256 |
| plan | **deterministic** — sha256 giống hệt, seed = 0 | sha256 |
| render | **deterministic** — sha256 giống hệt 2 lần | sha256 |
| play | 96000/96000 frame, **0 underflow** | sounddevice |
| Corpus | 41 file → 40 recording → **39 eligible** (2 loại: im lặng dài 21.8 % / 31.9 %) | luật D10 (run > 2 s) |
| Re-render sau lọc | **không còn im lặng run > 2 s** (bản cũ: 29.45 s) | `silence_metrics` |
| ffmpeg-less | 38 test OK; `render_plan` không raise; báo `unavailable` (không giả 0.0) | PATH không có ffmpeg |
| Lệch onset nhạc thật (diagnostic) | median 39 ms, p95 60.5 ms | so grid của plan |

**Lỗi Hermes bắt được, agent bỏ sót:**
1. Wheel `ai_dj` thiếu cả package `ai_dj/analysis` → `pip install` crash khi `ai_dj analyze`.
2. `render_plan` raise khi thiếu ffmpeg → 3 CI test job đỏ.
3. Corpus chứa file không phải bài hát → planner chọn làm bài mở đầu → master im lặng 29.4 s.

**Chưa làm:** set 60 phút · phát 20 bài không can thiệp · recovery · thích nghi mood.

---

## §B — LỰA CHỌN + CÂU HỎI CHÉO

**Bối cảnh:** corpus 39 bài eligible, thuần cover một ca sĩ (Bolero / tình ca Việt), 100–578 s
→ ~195 phút tương liệu cho set 60 phút.

**1 — Thứ tự hoàn thiện.** Theo bạn, thứ tự tối ưu để đạt "AI DJ tự chủ thật" là gì?
- (a) G3 set 60 phút → G1 phát 20 bài → G5 recovery → G4 mood
- (b) Sửa beat alignment trên nhạc thật trước (39–60 ms)
- (c) Mở rộng corpus trước
- (d) Khác — nêu rõ

**2 — Tính đúng đắn của claim "chuyển bài đúng nhịp".** Contract 20 ms hiện chỉ đo trên
synthetic click (ground truth biết trước); nhạc thật lệch 39–60 ms. Claim có vững chưa?
Nên đo thêm gì để chứng minh chất lượng chuyển bài thật?

**3 — Corpus.** 39 bài một nghệ sĩ: set 60 phút có "có nghĩa" không, hay corpus mới là chặn
thật? Rủi ro khi corpus hẹp (lặp, không đa dạng) và cách xử lý?

**4 — Kiến trúc dài hạn.** Pre-rendered → PCM playback giới hạn tính linh hoạt (G4 mood,
yêu cầu trực tiếp). Nên chuyển sang real-time mixer sớm hơn, hay giữ nguyên và tách phần
"quyết định âm nhạc" khỏi phần "phát"?

**Tiêu chí quyết:** thứ tự theo (1) phụ thuộc, (2) rủi ro, (3) chi phí, (4) khả năng rollback.
Một phương án tối ưu + lý do, không cần nhiều phương án.
