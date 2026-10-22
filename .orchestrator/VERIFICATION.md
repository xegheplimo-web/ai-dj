# VERIFICATION — AI DJ

## Baseline máy (đo 22/10/2026, trước khi viết code)
| Hạng mục | Kết quả | Cách đo |
|---|---|---|
| Audio stack py3.13 | librosa 1.0.0, numba 0.68, sounddevice 0.5.6, soundfile 0.14 | `pip list` |
| Output stream | Mở OK (Realtek/FxSound/NVIDIA HDMI) | `sd.OutputStream` |
| Time-stretch | ffmpeg `rubberband=tempo=1.08` trên 152.5s → 141.2s; tỉ lệ IBI 1.0588 vs 1.08 (sai số ~2%); 61× realtime | `ffmpeg` + librosa beat_track |
| Phân tích nhạc Việt | 5 file OK: duration/RMS/beat đọc được; tempo 92–144 BPM (nghi lỗi octave) | librosa |
| Mixxx | Chưa cài; không OSC chính thức; cần driver MIDI ảo | docs + `pip index pyrtmidi` (không có wheel) |

## Gates

| Run | Date | Task | Tests | Lint | Compile | CI | E2E | Evidence | Verifier |
|---|---|---|---|---|---|---|---|---|---|
| DJ-P0-006b-verify | 22/10 18:30 VN | Verifier two-tier silence + PortAudio fix | 56/56 ✓ (+7) | ruff ✓ | ✓ | ✓ 39771989534 | ✓ 0.20ms (timing_probe.json) | PASS: eligible_set.wav, FAIL: silent_set.wav | 2-tier gate operational |

_Chưa chạy — chờ implement._
