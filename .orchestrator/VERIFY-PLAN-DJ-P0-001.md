# VERIFY PLAN — DJ-P0-001 (Hermes-owned, độc lập với Devin)

Nguyên tắc: **Devin báo DONE không phải bằng chứng.** Hermes tự chạy lại mọi gate trên
`F:/ai-dj-venv` (không dùng venv của Devin) và đo lại từ artifact, không từ log do Devin khai.

## 0. Chuẩn bị
- venv: `F:/ai-dj-venv/Scripts/python.exe` (librosa 1.0.0, soundfile 0.14, sounddevice 0.5.6) — verified.
- ffmpeg: `ffmpeg -hide_banner -filters | grep rubberband` → present (9.0.1 full build).
- Chạy từ `F:/ai-dj`, branch `main`, `git status --short` trước khi verify.

## 1. Soi phạm vi (trước khi tin)
- `git status --short` + `git diff --stat` — chỉ được chạm whitelist của card
  (`ai_dj/**`, `tests/**`, `.orchestrator/cards/**`).
- Phạm vi bị cấm mà phải nghiệm thu: `.orchestrator/*` (ngoài cards), `.github/**`, `README.md`,
  `docs/**`, `LICENSE`, `requirements.txt`, `pyproject.toml`, `ai_dj/timing.py`,
  `scripts/timing_probe.py`.
- **KHÔNG được có commit** (Hermes sở hữu git) — nếu Devin commit thì báo lại, không tự merge.
- File rác ngoài phạm vi (`.serena/`, `__pycache__`, venv) → không commit kèm.

## 2. Gate tự động (chạy thật, dán output)
1. `ruff check .` → All checks passed
2. `ruff format --check .` → all files already formatted
3. `python -m compileall -q ai_dj scripts tests`
4. `python -m unittest discover -s tests -v` → OK, không FAIL/ERROR
5. `python scripts/timing_probe.py --bpm 120 --tempo 1.08` → exit 0 (harness cũ phải vẫn xanh)

## 3. Gate E2E thật (không phải unit test)
6. `python -m ai_dj.cli scan --dir "F:\DỮ LIỆU\ok"` → JSON, exit 0, đếm được file
7. `python -m ai_dj.cli analyze --dir "F:\DỮ LIỆU\ok"` → cache sqlite, idempotent
   (chạy 2 lần → cùng kết quả; BPM đã hiệu chỉnh octave phải nằm trong khoảng hợp lý)
8. `python -m ai_dj.cli plan --set-name mini --tracks 6` → `evidence/plan.json`;
   **determinism: chạy 2 lần → byte-identical** (sha256)
9. `python -m ai_dj.cli render --plan evidence/plan.json --out evidence/mini_set.wav` → WAV thật
   + `evidence/render_report.json`
10. `python -m ai_dj.cli verify --plan evidence/plan.json --master evidence/mini_set.wav` → exit 0
    + `evidence/verification.json`

## 4. Kiểm chứng ĐỘC LẬP (Hermes tự đo, không hỏi Devin)
- Đọc `evidence/mini_set.wav` bằng `ai_dj/timing` + `soundfile`: duration, true peak (dBTP),
  clipping count (số sample ở ±1.0), số khoảng lặng (>0.5s dưới −60 dBFS).
- **Yêu cầu hợp đồng:** true peak ≤ −1 dBTP, clipping = 0, không khoảng lặng, beat deviation ≤ 20 ms.
- **Chống xác nhận có lợi:** nếu `evidence/verification.json` tự khai PASS mà phép đo của Hermes
  cho kết quả khác → **FAIL**, bằng chứng là phép đo của Hermes.
- **Revert check:** tạo worktree tại commit trước fix, chạy test MỚI của Devin trên đó — phải
  thấy FAIL đúng lỗi cũ rồi mới tin test có tác dụng.

## 5. Đánh giá của người (G2b — không thể tự thay)
- Nghe `evidence/mini_set.wav` tại 3 điểm chuyển bài, ghi nhận: có beatmatch giữ được nhịp không,
  có khoảng trống/khớp sai không, có clipping nghe được không.
- Chỉ G2a (số học) tự động được; **G2b phải có người nghe** → báo Sếp kèm file để nghe.

## 6. Sau khi PASS
- Hermes commit (message có acceptance + bằng chứng), push → CI phải xanh.
- Ghi kết quả vào `.orchestrator/VERIFICATION.md` + cập nhật `PROJECT_STATE.md`.
- Báo Sếp: file, chỉ số đo, giới hạn còn lại.
