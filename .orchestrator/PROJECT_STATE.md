# AI DJ — PROJECT STATE

**Mục tiêu:** AI DJ tự chủ chạy Windows 11, open source — tự chọn bài, tự chuyển bài đúng nhịp,
phát liên tục set 60 phút, có bằng chứng đo được để nghiệm thu.

**Máy:** Windows 11 · RTX 3090 24GB · ffmpeg 9.0.1 (có rubberband) · Python 3.13.15 · Node v24

**Thư viện nhạc:** `F:\DỮ LIỆU\ok\` (+ `ok music\`) — 55 file WAV/MP3, cover Bolero/tình ca Việt, 150–330s.

## Trạng thái
- [ ] G0 Khởi động + CLI
- [ ] G1 Phát liên tục 20 bài không can thiệp
- [ ] G2 Đo được: lệch beat < 20ms, không khoảng lặng, RMS liên tục
- [ ] G3 Set 60 phút tự chọn bài
- [ ] G4 Thích nghi (đổi mood/thể loại/energy theo yêu cầu)
- [ ] G5 Recovery (lỗi 1 track/API không ngắt tiếng)
- [ ] G6 Nghiệm thu (bản ghi âm + log quyết định + nghe kiểm tra)

## Quyết định kiến trúc
| # | Quyết định | Trạng thái |
|---|---|---|
| D1 | Engine: Python tự chứa (ffmpeg rubberband + sounddevice), không Mixxx ở vòng 1 | CHƯA CHỐT — chờ phản biện GPT |
| D2 | AI đưa quyết định âm nhạc offline; timing do engine deterministic | CHƯA CHỐT |
