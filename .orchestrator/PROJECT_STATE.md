# AI DJ — PROJECT STATE

**Mục tiêu:** AI DJ tự chủ chạy Windows 11, open source — tự chọn bài, tự chuyển bài đúng nhịp,
phát liên tục set 60 phút, có bằng chứng đo được để nghiệm thu.

**Máy:** Windows 11 · RTX 3090 24GB · ffmpeg 9.0.1 (có rubberband) · Python 3.13.15 · Node v24

**Thư viện nhạc:** `F:\DỮ LIỆU\ok\` (+ `ok music\`) — 41 file quét được (WAV/MP3), cover Bolero/tình ca Việt, 100–578s.

**Repo:** https://github.com/xegheplimo-web/ai-dj — public, MIT, branch `main`, topics `dj audio beatmatching ai music`.
**CI:** `ci.yml` (lint + test py3.12/3.13 ubuntu + py3.12 windows + E2E timing gate) + `codeql.yml` + `dependabot.yml`.

## Trạng thái gate
- [x] **G0 Khởi động + CLI** — `ai_dj {scan,analyze,plan,render,play,verify}`, 36 test xanh, ruff sạch
- [ ] **G1** Phát liên tục 20 bài không can thiệp
- [~] **G2** Đo được: lệch beat < 20ms (**0.0 ms** trên synthetic ground truth ✓) · không khoảng lặng (**✓** master không còn run > 2s) · RMS liên tục (**CHƯA**)
- [~] **G3** Set 60 phút tự chọn bài — hiện render được **22 phút**; còn 60 phút
- [ ] **G4** Thích nghi (đổi mood/thể loại/energy theo yêu cầu)
- [ ] **G5** Recovery (lỗi 1 track không ngắt tiếng)
- [ ] **G6** Nghiệm thu (bản ghi âm + log quyết định + nghe kiểm tra)

## Quyết định kiến trúc
| # | Quyết định | Trạng thái |
|---|---|---|
| D0 | Dự án `F:\ai-dj`, repo `main`, Hermes sở hữu git (agent không commit) | CHỐT |
| D0b | Python 3.13.15 runtime chính | CHỐT |
| D0c | Không cài Mixxx/driver MIDI vòng 1 | CHỐT |
| D1 | Engine Python tự chứa, bỏ Mixxx ở P0 | CHỐT (GPT: CONDITIONAL GO) |
| D2 | Pre-rendered Set → PCM playback; không DSP trong audio callback | CHỐT |
| D3 | Planner deterministic, confidence-aware; LLM chỉ rerank sau qua validator | CHỐT |
| D5 | Không dùng driver MIDI (pyrtmidi không có wheel) | CHỐT |
| D6 | Synthetic click là ground truth; onset nhạc thật là diagnostic | CHỐT |
| D8 | `corpus.py` có `recording_id`/`work_id` | CHỐT |
| D10 | Corpus eligibility: im lặng trong run > 2s ≤ 2% (đo: bài thật 0.0–0.2%, file rác 21.8–31.9%) | CHỐT — đang triển khai (DJ-P0-002) |
| D11 | ffmpeg optional, không fatal; không báo 0.0 giả | CHỐT — đang triển khai (DJ-P0-002) |

## Đang chạy
- **DJ-P0-003** (next): set 60 phút từ corpus đã lọc

## Lỗi đã xác nhận
1. **Wheel thiếu `ai_dj/analysis/`** → `pip install ai-dj` crash khi `ai_dj analyze`. Sửa `pyproject.toml` (`packages`) — đã verify wheel chứa đủ 10 module. *Lỗi scaffolding của Hermes.*
2. **`render_plan` raise khi thiếu ffmpeg** → 3 CI test job đỏ (run 39747015283). Sửa trong DJ-P0-002.
3. **Corpus có file không phải bài hát** (`huan luyen.wav` 31.9%, `ĐỐI DIỆN NHÀ EM - DUY GỐC.wav` 21.8% im lặng dài) → planner chọn làm bài mở đầu → master có khoảng im lặng **29.4s**. Sửa trong DJ-P0-002.

## Ràng buộc
- Không token/secret lên chat hoặc repo.
- CI phải xanh thật; mọi claim phải có lệnh đo được.
- Coding agent không commit/push — Hermes sở hữu git.
