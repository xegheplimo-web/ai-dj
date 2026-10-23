# DECISIONS — AI DJ

## 2026-10-22 — Khởi tạo
- D0: Dự án đặt tại `F:\ai-dj`, git repo `main`, Hermes sở hữu git (agent không commit).
- D0b: Python 3.13.15 làm runtime chính (3.14 thiếu wheel audio; 3.13 có torch/fastapi/mcp).
- D0c: Không cài Mixxx/driver MIDI trong vòng 1.

## 2026-10-22 — GPT đối chiếu GitHub trực tiếp (commit 604da3a)
Brief gửi: audit brief (ai-dj-audit-brief.md, 53 lines) qua ChatGPT CDP.
Verdict GPT: **CONFIRMED 9/9 claims**; Option A đúng cho eligible_set.wav spec conflict.

- **D10 (CONFIRMED):** Verifier full-master silence gate (không chỉ transition). 29.4s silent run trên master = FAIL. Đã implement DJ-P0-006.
- **D12 (CONFIRMED):** Two-tier silence gate: ≤ −90 dBFS / ≥ 0.5s → FAIL; ≤ −60 dBFS / ≥ 2.0s → FAIL. Anti-phase stereo với audio = NOT silence.
- **D13 (CONFIRMED):** Tri-state verdict — INCONCLUSIVE cho measurement thiếu; không bao giờ PASS khi thiếu.
- **D11b (NEW):** sounddevice/PortAudio optional import (CI Ubuntu không có libportaudio2). `player.py` try/except OSError → sd=None; play_master raise RuntimeError sạch. Áp dụng nguyên tắc D11 (optional external dependency không fatal).

## 2026-10-22 — DJ-P0-006b spec conflict resolution
**Vấn đề:** eligible_set.wav có master có vùng ≤ −60 dBFS nhưng < 2.0s, không có vùng nào vượt ngưỡng 2s → PASS hay FAIL?

**Phân tích:** Các tier đã công bố: Tier 1 (≤ −60 dBFS / ≥ 2.0s → FAIL) và Tier 2 (≤ −90 dBFS / ≥ 0.5s → FAIL). eligible_set.wav có điểm đến −60 dBFS → **FAIL tier 1**.

**Quyết định:** **Option A — FAIL.** GPT xác nhận trực tiếp (ChatGPT session ai-dj-audit): "Tiêu chuẩn hai-tier đã được công bố rõ ràng. eligible_set.wav có điểm đến -60 dBFS → FAIL tier 1." Option B (INCONCLUSIVE) bị từ chối: "Không có gì 'không đo được' — RMS đã đo được."

### D16 — Spec conflict đã giải quyết: eligible_set.wav → FAIL
Hai-tier gate tuân thủ. Verifier = technical gate (threshold cứng); G2b listening verdict = audio quality gate (phân biệt âm thanh rất nhỏ vs khoảng lặng bất thường — GPT đề xuất thêm).

### D16b — G3 BLOCKED cho đến khi GPT chấp nhận DJ-P0-007 listening verdict
GPT: "Tôi sẽ kiểm tra kỹ chất lượng transition ở DJ-P0-007 trước khi bạn mở rộng 60 phút."

## 2026-10-22 — CI fix (PortAudio)
CI run 39768311712 FAILED: "PortAudio library not found" trên Ubuntu. Fix: lazy import sounddevice trong player.py (D11b).
CI re-run 39771989534: **SUCCESS** (lint + ubuntu3.12 + ubuntu3.13 + windows + E2E). CodeQL 39771989494: **SUCCESS**.

- **D1 (GO):** Bỏ Mixxx ở P0. Nút thắt thật là *music understanding* và *transition quality*,
  không phải năng lực phát 2 file song song; Mixxx không giải quyết được nút thắt đó mà lại
  thêm 2 phụ thuộc ngoài + mất khả năng đo số học.
- **D2 (GO):** Kiến trúc **Pre-rendered Set → PCM Playback** cho P0: mọi quyết định âm nhạc tính
  trước; playback chỉ đọc block PCM đã dựng. KHÔNG làm DSP trong audio callback (PortAudio cấm
  allocation/filesystem I/O trong callback realtime).
- **D3 (GO):** Planner deterministic, confidence-aware. **KHÔNG đưa LLM vào P0** — LLM chỉ rerank
  ứng viên sau, qua validator deterministic, và chỉ khi A/B nghe chứng minh tốt hơn baseline
  rule-only. GPT: "LLM 38 tok/s không có nghĩa là LLM hiểu hòa âm tốt hơn thuật toán".
- **D4 (GO):** P0 = G0, G1 (mini-set 6–8 bài), G2a (synthetic timing), G2b (listening verdict).
  G3 (set 60 phút) là **P1 bắt buộc trước release** — GPT bác bỏ "20 bài" vì 20 bài × 150–330s
  dài hơn cả G3.
- **D5 (GO):** Không cài Mixxx / loopMIDI / driver MIDI trong P0.
- **D6:** Gate phải tách nguồn bằng chứng: **synthetic fixture** (beat timestamp biết trước) cho
  timing; **xác nhận thủ công** trên mẫu nhỏ cho corpus thật. Verifier không được lấy timing event
  do executor tự khai báo làm bằng chứng duy nhất.
- **D7:** Gate G2 phải đo thêm short-term loudness, true peak (≤ −1 dBTP), clipping, và bắt buộc
  có **listening verdict** — RMS + beat 20 ms KHÔNG thay được việc nghe.
- **D8:** Corpus phải có `recording_id` (bản thu) và `work_id` (tác phẩm) riêng; 55 file ≠ 55 bài
  độc lập (nhiều bản trùng/thử).
- **D9:** Timing luôn theo **sample index** trên timeline chung; wall clock chỉ để telemetry.
  Không dùng `time.sleep()` để quyết định thời điểm mix (20 ms = 960 sample ở 48 kHz).

### Bằng chứng đã sửa sau phản biện
- Đo lại bằng synthetic click track (ground truth biết trước): độ lệch beat thật là **0.20 ms**,
  không phải ~9 ms như phép đo trên nhạc thật. Con số 2% trong brief là nhiễu của beat detector
  trên nhạc Bolero, không phải sai số của rubberband.
- `ai_dj/timing.py` + `scripts/timing_probe.py` là harness đo độc lập (Hermes-owned, verification
  glue) — không dùng output của engine làm ground truth.

## 2026-10-23 — DJ-P2-CONTINUITY-002 hardening (GPT decision A + C + F + (a))

### D17 — Cue Boundary Is Not Phrase-Safe (transparent limitation, Flag #3 = F)

- Downbeat-aligned cue chỉ đảm bảo điểm cue nằm trên downbeat **ước lượng**
  (grid `estimated: true`, 4/4-assumed từ beat đầu tiên) — nó **không chứng minh**
  đó là ranh giới câu nhạc (phrase boundary).
- Continuity trimming có thể loại bỏ phần vocal hoặc outro quan trọng của bài hát.
- Anchor inclusion + D12 PASS **không đồng nghĩa** bảo toàn đầy đủ bài hát:
  gate chỉ chứng minh không có khoảng lặng vi phạm, không chứng minh người nghe
  nhận đủ nội dung quan trọng.
- Mọi anchor/track bị trim đáng kể phải báo rõ `cue_in_s`, `cue_out_s`, thời
  lượng bị loại và lý do — đã implement qua `cue_report` trong plan.json
  (DJ-P2-CONTINUITY-002).
- **G2b listening verdict vẫn bắt buộc** trước nghiệm thu âm nhạc — không tự
  động PASS từ JSON.
- Phrase-boundary model chỉ triển khai khi có bộ audio được đánh giá để đo độ
  chính xác thực tế (độ chính xác 8-bar/32-bar trên Bolero chưa được đo — nhận
  diện cấu trúc từ downbeat ước lượng tạo cảm giác an toàn giả).

Ví dụ thực tế: anchor "Sao Lòng Còn Thương" đã chuyển cue-out từ ~387.397 s
xuống ~351.759 s — rút ngắn ~35.64 s. Thay đổi đủ lớn để **bắt buộc xem xét
bằng nghe**; không thể chỉ dựa vào D12 PASS để khẳng định người nghe nhận đủ
phần quan trọng của bài hát.

### D18 — Verified-artifact gate & simulation honesty (Flag #2 = C)

- Pipeline hợp đồng: `PLAN` (simulation nhanh, prune) → `RENDER` (một master
  thật) → `VERIFY` (đọc audio thật) → `PASS` cho phép phát / `FAIL|INCONCLUSIVE`
  chặn phát. **Simulation KHÔNG chứng minh PASS D12** — chỉ dự báo tính khả
  thi để giảm render vô ích; `verify.py` giữ veto authority duy nhất.
- `play` chỉ chấp nhận master có verdict PASS hợp lệ, ràng buộc SHA-256 của
  cả `plan.json` và WAV (đổi 1 trong 2 file → PASS cũ mất hiệu lực).
- Fail-closed tuyệt đối (Flag #1 = A): corpus thiếu continuity payload →
  `plan` exit 5 (`CORPUS_UNMEASURED` toàn bộ / `ANALYSIS_REQUIRED` một phần
  + `missing_recording_ids`); **không** có waiver `--allow-unmeasured`.

### D19 — Beat grid bị lượng tử hóa thô hơn hợp đồng nghiệm thu (P0, 2026-10-23)

**Phát hiện (Hermes, kiểm chứng độc lập — xem `docs/audit-2026-10-23-hermes.md` §2):**

`analyzer.py` dùng `librosa.onset.onset_strength` + `beat_track` với `hop_length`
mặc định **512** ở `ANALYSIS_SR = 22050` → **frame period = 23.2200 ms**.
`beat_frames` là chỉ số frame nguyên, nên mọi `beat_times` bị snap về bội số của
23.22 ms, và `median_ibi_s` **luôn là bội số nguyên của 23.22 ms**.

Hệ quả đo được trên **42/42 track** (sai số snap **0.000 ms**):
`bpm_corrected` chỉ nhận giá trị trên một thang rời rạc —
172.27 / 161.50 / 152.00 / 143.55 / 136.00 / 129.20 / 123.05 / 117.45 / 107.67 /
99.38 / 92.29 / 89.10 / 83.35. Trong corpus: **161.50 × 12 bài, 172.27 × 8, 152.00 × 7**.
Ở vùng tempo này bước nhảy **≈ 10 BPM (~6%)**.

**`23.22 ms > 20 ms` → lưới nhịp thô hơn chính hợp đồng G2a.** Không thể nghiệm thu
căn nhịp 20 ms bằng một lưới chỉ phân giải được 23.22 ms.

Tinh chỉnh tempo bằng hồi quy LSQ (`beat_index → time`) trên `beat_times_s` đã lưu:
**42/42 track lệch khỏi giá trị thang**, tới **−5.58 BPM** (`huan luyen`) và
**+3.945 BPM** (`Untitled Session 6_mixdown`). Giá trị tinh chỉnh không nằm trên thang
→ giá trị đang lưu là **artifact lượng tử hóa**, không phải tempo thật.

**Hệ quả nghiêm trọng nhất — quyết định `beat_crossfade` không đáng tin ở biên:**
`dj_p2_miniset` cặp 0 có chênh BPM **theo thang 5.88%** (lọt `BEATMATCH_BPM_TOL = 6%`
→ gán `bpm_matched`, conf 0.635) nhưng chênh BPM **THẬT 11.41%** — vượt tolerance gấp đôi.
Master render có median deviation **27.7 ms**, đúng như dự đoán.

Phần lớn `beat_crossfade` khác hiển thị chênh **0.00%** chỉ vì hai bài rơi **cùng một ô
frame** — cơ chế quyết định **không phân biệt được** "0.00% vì thật sự bằng" với
"0.00% vì cùng bị snap".

**Hệ quả kiến trúc:** phương án O4 (validate nguồn trước render) + O3 (thắt planner) mà
GPT đề xuất đều **dựa trên giả định lưới nhịp đáng tin**. Giả định đó đã bị phủ định.
**Sửa lượng tử hóa phải làm TRƯỚC hoặc CÙNG LÚC với O4/O3** — nếu không, validator dù
độc lập đến đâu vẫn kết luận sai vì so với một lưới sai ~10 BPM.

**Hướng khắc phục (chưa triển khai):**
1. Giảm `hop_length` tầng beat/onset 512 → 128 (frame period 5.81 ms).
2. Tinh chỉnh tempo bằng LSQ trên chuỗi beat (gần như miễn phí, dùng dữ liệu đã tính).
3. Tái dựng lưới từ tempo tinh chỉnh + ước lượng phase, thay vì giữ `beat_frames` đã snap.
4. Thêm gate: từ chối `beat_crossfade` khi `frame_period > ngưỡng hợp đồng`.

### D20 — Harmonic compatibility: gộp nhầm hai quan hệ khác bản chất

`planner.py::key_distance()` trả `1` cho **mọi** cặp cùng mode cách 1 semitone, và
`_choose_transition()` coi `kdist <= 1` là `key_compatible`. Hàm **gộp chung**:
- relative major/minor (thật sự tương thích — chung key signature) → 1 ✅
- **cùng mode cách 1 semitone** (không chung key signature; chồng lên nhau sẽ đập phách) → 1 ❌

Kiểm chứng: `D# minor → D minor = 1`, `C minor → C# minor = 1`, `F# minor → G minor = 1`,
`E minor → F minor = 1`. `key_compatible` đã được dùng **19 lần** trong các plan đã commit,
trong đó `Gõ Cửa (D# minor) → Kiếp Đỏ Đen (D minor)` là **tuyên bố sai**.

Nguồn: GPT phát hiện; Hermes kiểm chứng độc lập và xác nhận. Là **blocker G3**.

### D21 — Eligibility D10 chỉ là heuristic im lặng; tách "audio integrity" khỏi "content suitability"

D10 đo im lặng **digital** (−100 dBFS, mono 22.05 kHz) nên không phân biệt được bản thu
luyện tập liên tục với bài hát liên tục: **40/42 eligible**, gồm 5× `Untitled Session
*_mixdown` (một bản đã vào mini-set đã nghiệm thu) và `âm thanh huấn luyện ok` (517.3 s).

Ngoài ra **42 dòng chỉ có 33 `work_id`** — 8 work có nhiều bản ghi (`Gió Về Miền Xuôi` ×3).
Sau khi trừ trùng lặp và file không phải bài hát: **~26 bài hát riêng biệt**.

Chốt (theo GPT, Hermes đồng ý): tách hai lớp —
- **Audio integrity** (file phát được + đạt continuity contract) = hard gate
- **Content suitability** (có phù hợp làm bài hát trong set DJ) = curation gate

Không dùng tên file làm hard classifier; rà soát thủ công 33 work thay vì huấn luyện ML
(corpus quá nhỏ). Autonomous production set chỉ nhận `music_track` + D10/D12; file nghi vấn
chỉ vào set qua `--include-track <id>` **kèm approval ràng buộc source SHA-256**, và plan
phải ghi rõ override + lý do. `--include-ineligible` giữ ngoài release mode.

### D22 — M7/M8 chưa đạt nhưng task đã đánh dấu `done` (closure violation)

`DJ-P2-MINISET-G2B-001` yêu cầu M7 (verdict người nghe, ghi lỗi + timestamp) và M8 (lưu
listening report). Trên đĩa **chỉ có template** — không verdict, không người nghe, không
lỗi, không timestamp. `TASKS.json` ghi `status=done`.

Đây là **closure violation**, không phải tài liệu cũ. Trạng thái đúng phải là
`TECHNICAL_PASS / LISTENING_PENDING / RELEASE_BLOCKED`. Chỉ đóng card khi M7/M8 có artifact
hợp lệ; **không dùng template thay verdict**.

### D23 — `DJ-PILOT-VIETMIX-005` bất khả thi với thư viện hiện tại

GPT chốt pilot 5 phút Việt Mix/Nonstop kèm điều kiện: *thiếu nguồn thì phải báo thiếu nguồn,
không tự lôi Bolero hoặc file luyện tập vào*. Kiểm chứng thư viện: **~35 cover Bolero/trữ
tình + 5 DAW session mixdown + 2 audio luyện tập/không xác định + 0 bản Việt Mix/Nonstop/
Vinahouse/EDM**. Thư viện 100% là cover do người dùng thu.

→ **Pilot bất khả thi vì thiếu nguồn.** Điều kiện tiên quyết: có audio Việt Mix/Nonstop
hợp lệ trong thư viện. Không được hạ chuẩn bằng cách dùng Bolero.

### D24 — Điều kiện mở G3 (chốt với GPT 2026-10-23)

G3-60MIN = **HOLD**. Sáu điều kiện GO:
1. Source alignment validation + harmonic compatibility PASS (D19, D20)
2. Corpus suitability được kiểm toán; autonomous planner không tự chọn non-song (D21)
3. Mini-set có listening verdict **thật** + SHA binding hợp lệ (D22)
4. D12 / synthetic timing / peak / clipping / integrity đều PASS
5. Mọi điều kiện M1–M8 đối chiếu với evidence
6. Control-plane ledger không có trạng thái DONE sai

### D25 — Thứ tự triển khai sửa lại sau phát hiện lượng tử hóa (GPT phán quyết 2026-10-23)

Sau khi Hermes trình bằng chứng D19, GPT **APPROVED** đổi thứ tự và **bắt buộc tách card**:

| Hạng mục | Quyết định |
|---|---|
| Sửa beat-grid quantization | **P0 — triển khai đầu tiên** |
| Card riêng `DJ-P2-BEAT-GRID-001` | **APPROVED** |
| O4 + O3 | giữ định hướng, triển khai **sau** beat-grid |
| Hard gate `frame_period > 20 ms` | **REJECT** — thay bằng **quality gate** |
| Việt Mix 5 phút | **BLOCKED — thiếu nguồn hợp lệ** |
| All-In-One-Infer | **DEFER** — chưa benchmark/cài |
| G3-60MIN | **HOLD** |

Chuỗi phụ thuộc: **P0 `DJ-P2-BEAT-GRID-001`** → **P1 `DJ-P2-BEAT-CLAIM-001`** →
P2 render + listening độc lập → G3-60MIN.

**Hiệu chỉnh của GPT đối với kết luận của Hermes (Hermes chấp nhận):** Hermes gọi 42 giá
trị BPM sau LSQ là "BPM thật" — **chưa đủ cơ sở**. LSQ ước lượng chu kỳ chính xác hơn mức
lượng tử hóa một frame, nhưng **không tự chứng minh** beat detector nhận đúng beat, không bỏ
beat, không nhầm half/double time. Muốn xác nhận phải **đối chiếu với audio hoặc beat
landmarks độc lập**. Đây đúng là tiêu chuẩn chống circular-validation mà dự án đã đặt ra
(D6) — Hermes đã vi phạm chính nguyên tắc đó khi gọi LSQ là ground truth.

→ Mục tiêu P0 vì thế là **khôi phục độ chính xác VÀ đánh giá độ tin cậy của beat grid**,
không chỉ thay công thức BPM bằng LSQ.

**Lý do tách card:** đo riêng được mức cải thiện BPM/beat timestamps; so sánh planner cũ/mới
trên cùng dữ liệu; nếu O4/O3 gây regression thì không phải rollback cả bộ phân tích nhịp;
review xác định được lỗi phát sinh từ analyzer hay planner.

### D26 — Beat Truth & Octave Integrity (GPT phán quyết 2026-10-23, APPROVED WITH SCOPE REVISION)

Hermes chuyển từ **suy luận số học** sang **known-answer test**: sinh click track ở BPM
biết trước (`ai_dj.timing.synthesize_click_track`) rồi hỏi analyzer báo mấy. Truth quyết
định **TRƯỚC** khi phân tích → không circular. Sweep 41 mốc BPM 88→180:

| Chỉ số | Giá trị |
|---|---|
| Ca sai > 2% | **13/41** |
| Ca sai mà confidence ≥ 0.5 | **13/13** |
| Sai số tối đa | **50.5%** |
| `IBI (frames)` | **luôn nguyên** ở mọi dòng |

**GPT xác nhận 3 lớp lỗi + phát hiện lớp thứ tư:**

| # | Lớp lỗi | Trạng thái |
|---|---|---|
| a | **Quantization bias** — snap về thang rời rạc | CONFIRMED |
| b | **Octave error** — `resolve_octave` chọn sai (truth 166.686 → báo 83.354, ratio 0.50; truth 178/180 → 89.103) | CONFIRMED |
| c | **`tempo_confidence` vô dụng** — đo độ đều (`ibi_cv`), không đo tính đúng; 0.9 trên lỗi 2.5%, 0.63 trên lỗi 50% | CONFIRMED |
| d | **Beat-phase / beat-grid integrity** — BPM đúng nhưng vị trí beat/downbeat sai | **CHƯA chứng minh hay bác bỏ** |

**Hiệu chỉnh kỹ thuật của GPT (Hermes kiểm chứng ĐÚNG):** Hermes mô tả click track là
"sine burst" — **sai**. `synthesize_click_track()` ghi `y[i:j] = 1.0` trong 15 ms → đó là
**rectangular pulse**. Fixture T1 của Hermes do đó **overfit một dạng transient duy nhất**.
Bắt buộc bổ sung: sine burst có envelope, percussive click, onset mềm.

**Quyết định:**
1. Giữ `DJ-P2-BEAT-GRID-001` là **một card duy nhất**, mở rộng gồm octave resolution +
   beat-grid validation. **Không tách card thứ ba** cho octave error.
2. Thay LSQ một mình bằng **Multi-Hypothesis Octave Resolution**: duy trì {B/2, B, 2B},
   đánh giá từng giả thuyết trên onset envelope, và **được phép trả `OCTAVE_AMBIGUOUS` /
   `UNVERIFIED`** thay vì tự chọn khi không đủ bằng chứng.
3. Giữ kiến trúc confidence-aware, nhưng **bỏ `tempo_confidence >= 0.5` làm hard gate** —
   thay bằng hai tầng xác minh. GPT từ chối dùng "khoảng cách tới bucket" làm cơ chế.
4. Mở rộng known-answer thành **3 tầng**: T1 controlled synthetic · T2 musical synthetic
   (kick/snare/hat, syncopation, swing) · T3 annotated real music. Ở T3 **không** lấy
   LSQ/librosa/model mới làm ground truth.
5. **Tách hai gate**: `timing_probe.py` chỉ kiểm chứng FFmpeg rubberband — **không** thay
   thế bài kiểm thử analyzer. Không được hiểu nhầm "0.204 ms" thành "beat tracking chính xác 0.204 ms".
6. **Nghiêm cấm planner phát hành claim `beatmatched` khi đầu vào còn `UNVERIFIED`.**

**Giới hạn vật lý (phải chấp nhận):** cùng một chuỗi onset có thể biểu diễn 83 BPM (eighth
notes) hoặc 166 BPM (quarter notes). **Timing một mình không luôn xác định được beat người
nghe cảm nhận.** Đây là lý do `OCTAVE_AMBIGUOUS` là output hợp lệ, không phải lỗi.

**Thứ tự bắt buộc (D26):**
1. Hermes bảo đảm Devin permissions + completion gate hoạt động
2. Devin CLI sửa quantization + octave resolution + beat-grid validation
3. Hermes chạy lại known-answer độc lập (gồm negative cases + T3 real annotated)
4. Chỉ khi P0 PASS mới mở O4/O3 + harmonic fix
5. Hoàn thành G2b listening + eligibility + kiểm tra lại master trước G3

**Về lane Devin (Q5):** whitelist trong prompt **đúng hướng nhưng chưa đủ**. GPT xác nhận
`ask` rule có thể thắng `allow`/`bypass` ở cấp tổ chức → **không** nên ép flag nguy hiểm.
GPT **không** xác minh được flag/ENV nào override được `ForceAsk` → **không khuyến nghị
thêm flag chưa chứng minh**. Bổ sung: workspace preflight (tạo sẵn config), permission policy
có scope, và **independent completion verification** — tuyệt đối không dùng `exit 0` làm
`TASK_DONE`.

**Trạng thái còn lại:** G3-60MIN **HOLD** · Việt Mix 5 phút **BLOCKED (thiếu nguồn)** ·
All-In-One-Infer **DEFER**.

### D27 — Kết quả verify độc lập card DJ-P2-BEAT-GRID-001: PARTIAL PASS

Hermes chạy lại known-answer **độc lập, từ ngoài worktree của agent** (agent đã xoá script
của Hermes trong `scripts/` — xem §3). Harness: `evidence/orchestrator_independent_verification.{json,md}`.

**Đã PASS (Hermes tự đo, không tin báo cáo agent):**

| # | Tiêu chí | Đo được |
|---|---|---|
| A1 | `median_ibi_s` rời raster 23.22 ms | **0/41** còn trên raster |
| A2 | `bpm_resolution` < 1 BPM | max **0.00061** trên cả 41 |
| A3 | Quality metric mọi track | có `beat_quality` đủ 41 |
| A4 | v3 fail-closed | **exit 5**, `CORPUS_UNMEASURED`, missing 32 |
| A5 | Test cũ | **139 test OK** (skip 1) |
| A6 | ruff | `All checks passed!` / 58 file formatted |
| A7 | D6 timing probe | **0.2037 ms** |
| A8 | render + verify | PASS, D12 sạch |
| A9 | Before/after | **38/41** đổi >0.5 BPM, \|Δ\| median **1.738**, max **16.321** |

**Cải thiện thật (known-answer, truth biết trước):**

| Tầng | v3 sai >2% | v4 sai >2% | v3 max | v4 max |
|---|---|---|---|---|
| T1 click 41 mốc | **13/41** | **4/41** | 50.50% | 50.003% |
| T2 musical 8 pattern | **5/8** | **0/8** | 49.99% | **0.058%** |

Sửa dứt điểm: `offladder_166_686` 83.354→**166.783**; `four_on_floor_126` 83.354→**126.001**;
`swing_120`/`half_time_120` 117.454→**120.0**. Ca negative bị bắt đúng: `neg_pure_tone` có
`tempo_confidence` = **0.9** (sai) nhưng quality block bắt qua `beat_env_ratio 1.07 < 2.0`.
**Metric confidence cũ sẽ chấp nhận nó.**

**CHƯA ĐẠT — 2 khiếm khuyết:**

**F1 — octave ambiguity CHƯA sửa, và thất bại IM LẶNG (vi phạm D26 §7.6 B1).**
4 ca T1 còn lại đều sai đúng một nửa, và **tất cả** bị báo `status: 'ok'` với `reasons: []`:

| case | truth | reported | ratio | status | reasons |
|---|---:|---:|---:|---|---|
| click_168.000 | 168.000 | 83.995 | 0.5 | `ok` | `[]` |
| click_174.000 | 174.000 | 87.004 | 0.5 | `ok` | `[]` |
| click_178.000 | 178.000 | 88.997 | 0.5 | `ok` | `[]` |
| click_180.000 | 180.000 | 90.003 | 0.5 | `ok` | `[]` |

D26 §7.1 yêu cầu Multi-Hypothesis Octave Resolution — **chưa triển khai**. Agent tự khai
trong mục Limitations (nó chưa từng thấy D26 — lỗi quy trình của Hermes, xem §3).

**F2 — `beat_env_ratio` không bị chặn (khiếm khuyết MỚI do verify này phát hiện).**

| vật liệu | `beat_env_ratio` |
|---|---|
| synthetic click / drum pattern | **7.2×10⁹ – 1.9×10¹⁰** |
| corpus nhạc thật | 4.1 – 10.2 |
| pure tone / white noise | 1.07 – 1.24 |

Metric = `median(envelope tại beat frame) / median(envelope)`. Với vật liệu percussive thưa,
median tổng thể ≈ 0 → tỉ số nổ ~9 bậc độ lớn. Ngưỡng `< 2.0` cho đáp án đúng trên 3 ca
negative, nhưng metric **không scale-invariant** — thực chất chỉ kiểm "median envelope có gần 0
không", độ lớn không mang nghĩa. Báo cáo của agent hiệu chuẩn trên corpus (4.1–10.2) và
**chưa bao giờ đo ca synthetic nơi nó nổ**.

→ Card sau phải thay bằng score **bị chặn**, ví dụ so onset strength tại beat frame với
**percentile cao** của envelope + epsilon floor.

**§3 — Lỗi quy trình của Hermes (không phải của agent):**
1. Card + D19–D26 **chưa commit** ở main khi tạo worktree → `git worktree add` từ HEAD
   `b8b3cd8` **không mang theo**; `DECISIONS.md` trong worktree dừng ở **D18**. Agent phải đọc
   card từ main repo.
2. D26 §7 (octave resolution, benchmark 3 tầng) được thêm vào card **sau khi** agent đã chạy
   → agent không bao giờ thấy.
3. Prompt ghi "you may write to exactly these paths and nothing else" → agent hiểu đúng là
   `scripts/` ngoài phạm vi và **xoá script known-answer của Hermes** ở đó. Harness phải tạo
   lại **ngoài** worktree.

**Bài học bắt buộc áp dụng từ nay:**
- **Commit card + decisions TRƯỚC khi tạo worktree** — worktree chỉ chứa nội dung đã commit.
- **Đóng băng card trước khi launch agent**; mọi bổ sung sau đó phải là **card mới**, không
  sửa card đang chạy.
- Whitelist phải ghi rõ **"không sửa và không xoá file ngoài danh sách"**, và file của
  orchestrator phải nằm **ngoài worktree** của agent.
- **Không bao giờ** dùng `exit 0` làm `TASK_DONE` (D26 §Q5) — vòng này `git diff` rỗng ở lần
  chạy 1 và verdict phải hạ xuống PARTIAL PASS ở lần chạy 2.

**Kết luận:** đóng D19 (quantization) — **đã sửa và kiểm chứng độc lập**. Card
`DJ-P2-BEAT-GRID-001` **không được close**; mở card kế tiếp cho octave resolution + sửa
`beat_env_ratio`. **O4/O3 tiếp tục HOLD** cho đến khi octave PASS.

### D28 — Corpus KHÔNG có lưới nhịp tempo-không-đổi trên 39/41 track

Hermes đọc chéo `evidence/beat_grid_before_after.json` (đo độc lập trên cache v4) sau khi thấy
`tempo_unstable` bật 28/41. Định nghĩa trong `ai_dj/analysis/beatgrid.py`:
`residual_std_ms` = std của LS-fit residual (thời điểm beat phát hiện vs đường tempo không đổi);
ngưỡng `RESIDUAL_STD_WARN_MS = 20.0` → vượt thì `residual_loose`.

**Đo được trên 41 track corpus:**

| metric | min | p25 | **median** | p75 | max |
|---|---|---|---|---|---|
| `residual_std_ms` | 15.97 | 81.17 | **137.79** | 219.74 | 1182.09 |
| `residual_max_ms` | — | — | — | — | **1182** |
| `phase_drift_ms` | −230.3 | −25.35 | **3.63** | 63.87 | 801.40 |
| `residual_growth_ratio` | 0.436 | 0.742 | **0.994** | 1.772 | 5.134 |
| `ibi_drift_pct` | −3.85 | 0.00 | **0.00** | 0.00 | 3.49 |
| `coverage_ratio` | 0.949 | 0.986 | 0.993 | 0.999 | 1.029 |
| `beat_env_ratio` | 3.399 | 5.011 | 6.102 | 7.738 | 9.730 |

**39/41 track `status: suspect`**; chỉ **2/41** sạch: `CÂU HẸN CÂU THỀ - COVER DUY`
(residual 16.37 ms, drift −1.41 ms, growth 0.774) và `Thế Sự -. Cover Duywav`
(residual 15.97 ms, drift 3.63 ms, growth 1.256).

**Đọc kỹ — đừng kết luận quá mức:**
- `residual_growth_ratio` median ≈ **1.0** và `ibi_drift_pct` median **0.00** → **không có drift
  hệ thống** (đầu và cuối track tempo khớp nhau).
- Nhưng `residual_std_ms` median **138 ms** → beat phát hiện bị **pha tán phi hệ thống** quanh đường
  tempo không đổi. Tại 160 BPM một nhịp = 375 ms, 138 ms ≈ **37% nhịp** — quá lớn để gọi là "lệch
  nhẹ".
- **KHÔNG tách được** "detector sai" hay "bài không có tempo ổn định" chỉ từ metric nội tại — đúng
  cảnh báo D26 (không có annotation độc lập). Giải thích khả dĩ nhất: corpus là **cover live có
  rubato/tempo tự do** (Bolero, thu giọng), nên lưới tempo-không-đổi vốn không tồn tại.

**Hệ quả kế hoạch:** đây là **chặn corpus suitability** cho PILOT 5 phút, độc lập với chặn thiếu
nguồn (GPT đã BLOCKED vì 0 bài Việt Mix). Beatmatch giữa 2 track không có grid ổn định là không
có nghĩa. **Cần annotation độc lập** (người đánh dấu beat trên vài track corpus) trước khi kết
luận detector sai — nếu annotation cho thấy beat thật thì đó là lỗi detector thứ hai (F3, cần
card riêng); nếu annotation cũng pha tán thì corpus không dùng được cho beatmatch.

**`beat_env_ratio` corpus = 3.4–9.7** (median 6.1) — xác nhận lại F2: trên synthetic nổ lên
7.2×10⁹, trên corpus chỉ ~6. Khoảng cách ~9 bậc độ lớn.

### D29 — Verify độc lập DJ-P2-OCTAVE-001: PASS B1–B7, phát hiện F3 (resolver NHÂN ĐÔI tempo chậm)

Hermes chạy 3 probe độc lập từ ngoài worktree: harness known-answer, **probe đối kháng tempo
thấp** (dải T1 của agent bắt đầu ở 88 BPM nên đầu thấp chưa từng được test), và **rescan toàn
corpus 41 track** (cache v4 stale theo thiết kế dưới v5 nên không có cache v5 để đọc).
Evidence: `evidence/orchestrator_verify_octave.{json,md}`, `orchestrator_adversarial_octave.json`,
`orchestrator_corpus_v5_scan.json`, + 4 harness script trong `scripts/orchestrator_*.py`
(chúng là công cụ verify của dự án — đặt cạnh `scripts/timing_probe.py`, KHÔNG để trong
`evidence/`: CI chạy `ruff check .` và bắt lint).

**PASS B1–B7 (Hermes tự đo):**

| # | Đo được |
|---|---|
| B1 | 4 ca half-time nay resolve ×2 **đúng** — không còn ca 50% nào bị đánh dấu verified |
| B2 | T1 **0/41** sai, max error **0.031%** |
| B3 | T2 **0/8** sai, max error **0.058%** |
| B4 | Negative **3/3** suspect |
| B5 | `beat_env_score` bounded **[0.5009, 1.0]** (trước 7.2×10⁹–1.9×10¹⁰) |
| B6 | **151 test OK** (skip 1) |
| B7 | ruff sạch · probe **0.2037 ms** · stale cache **exit 5** |

**Sửa đúng — 4 ca mục tiêu:** `click_168` 83.995→**167.991** · `174` 87.004→**174.008** ·
`178` 88.997→**177.995** · `180` 90.003→**180.006**.

**F2 đã sửa đúng cách:** `beat_env_score` = ảnh đơn điệu `r/(1+r)` của ratio cũ, bounded `[0,1)`,
**scale-invariant chính xác**, ngưỡng cũ `≥2.0` ánh xạ **chính xác** sang `≥2/3`. Không phải
đổi ngưỡng cho vừa — đây là bản sửa đúng.

**F3 MỚI — resolver NHÂN ĐÔI vật liệu chậm, và không phải lúc nào cũng flag:**

Synthetic (probe đối kháng 16 ca, 1 fail):

| case | truth | detected | corrected | error | ambig |
|---|---:|---:|---:|---:|---|
| `slow_drum_70` | 70.0 | 139.9986 | **139.999** | **+99.999%** | True |

`slow_click` 60–96 **đúng hết**; `slow_drum_80`/`90` đúng. Lỗi **phụ thuộc vật liệu**, không phải
ngưỡng tempo sạch.

Nhạc THẬT — **tệ hơn, và im lặng** (rescan 41 track):

| track | detected | corrected | margin | ambig |
|---|---:|---:|---:|---|
| `Gõ Cửa - Bolero Cover – Duy` | 96.0743 | **192.149** | **0.1442** | **False** |

Bolero ~96 BPM bị nhân đôi lên **192 BPM** với margin **dứt khoát** (0.1442 ≫ ngưỡng 0.08) → resolver
**không báo ambiguous**. 192 BPM cho Bolero là vô nghĩa. Track có `status: suspect` nhưng flag đó
đến từ **residual metric (D28)**, không phải từ octave resolver.

**Rescan corpus v5:** 41/41 phân tích OK · **ambiguous 6/41 (14.6%)** · **nhân đôi 1/41** ·
giữ ×1 40/41 · suspect 39/41 (do D28 residual, **không** do octave) · margin min 0.0097 / p25 0.1001 /
**median 0.1448** / max 0.3234 · phân bố: **6 dưới 0.08 · 8 trong 0.08–0.12 (mong manh) · 27 trên 0.12** ·
`residual_std_ms` median **137.8** (không đổi — D28 nguyên giá trị).

**Root cause F3:** resolver chấm `{0.5, 1.0, 2.0}` bằng ACF onset-envelope + comb-tooth strength +
tempo prior yếu (log-domain, centered 128 BPM, **weight 0.20**) − anti-penalty. Nó **chỉ dùng onset
lattice**. Track chậm mà onset chia nhỏ beat (hi-hat mỗi móc đơn, hoặc kick/snare xen kẽ) trình ra
lattice **giống hệt** tempo nhân đôi → lattice không tách được. Prior quá yếu để phá thế hoà, và
**hoàn toàn không có thông tin bar/backbeat** — đúng thứ con người dùng để nói "Bolero này 96, không
phải 192".

**Tác động thực tế:** octave fix **không** làm corpus vô dụng (6/41 ambiguous, 1/41 doubled). Vấn đề
corpus trội vẫn là **D28**. Nhưng **8 track nằm trong 0.08–0.12** → biên ambiguity mong manh, một thay
đổi scoring nhỏ có thể đẩy chúng qua lại.

**Kết luận:** card `DJ-P2-OCTAVE-001` **đạt tiêu chí B1–B7 như đã viết** → đóng card, ghi PASS kèm
finding. **Hướng half-time (tempo nhanh bị chia đôi): ĐÃ SỬA.** **Hướng nhân đôi (tempo chậm bị gấp
đôi): CHƯA SỬA** — phụ thuộc vật liệu, sống sót trên nhạc thật, và ở ca corpus **không hề bị flag
ambiguous**. **O4/O3 tiếp tục HOLD.** Card kế tiếp: bổ sung **bằng chứng cấp bar/backbeat** cho resolver
+ hiệu chuẩn lại biên ambiguity trên corpus (8 track sát ngưỡng) trước khi planner tin tempo của vật
liệu chậm.


## 2026-10-23 — Adopt ACE-Step 1.5 for AI-remix capability (GPT verdict verified)
**Nguồn:** `docs/gpt-ace-step-remix-reply-2026-10-23.md` (GPT recommendation, dữ liệu kiểm chứng).

- **D30 (GO — gated micro-probe):** Áp dụng **ACE-Step 1.5** (`git+https://github.com/ace-step/ACE-Step-1.5`, MIT) làm music-gen engine cho PoC remix. Kiến trúc **Option Y (stem + generated BGM)**: tách vocal stem của 1 bài Bolero trong `F:\DỸ LIỆU\ok` (rights-cleared) → ACE-Step sinh BGM Vinahouse (~128–135 BPM, key-compatible) → cân chỉnh BPM/key bằng `rubberband` (KHÔNG dùng analyzer D28 grid — 39/41 suspect) → mix vocal+BGM → master (ebur128) → verify (BPM librosa, true-peak, vocal clarity, structure, listening).
- **Install cô lập:** venv `F:/ace-venv` (py3.13, KHÔNG --system-site-packages) vì ACE-Step pin `torch==2.7.1+cu128` trùng/xung đột vs `torch 2.8.0+cu129` của py3.13 hệ thống (do ai-dj). Không ăn vào `F:/ai-dj-venv`.
- **Micro-probe (cổng đóng duy nhất):** cài đặt + sinh thử ≤30s trên RTX 3090 (CUDA) phải PASS trước khi dispatch lane Devin. Nếu fail → STOP, không cài ACE-Step; fallback offline-stretch (rubberband) — không phải remix thật (§20).
- **Độc lập với D19/D28:** PoC không bao cấp tiếp beat grid corpus — BPM đo trên OUTPUT bằng librosa trực tiếp. Parallel & non-blocking vs P0 quantization fix.
- **Lưu ý:** ACE-Step không bảo đảm giữ vocal gốc nếu dùng native cover mode → vì thế mới dùng stem-split (Option Y). Nếu stem separation làm mất điều khiển vocal, PoC chuyển sang Option X (native cover) với acceptance thu hẹp (không giữ vocal gốc).
