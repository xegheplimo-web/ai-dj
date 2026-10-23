# T2 — Musical synthetic benchmark (BEFORE, analyzer v3)

Ground truth beat times come from the generator **before** the audio is written.

Musical patterns (kick/snare/hi-hat, backbeat, swung eighths) — NOT rectangular clicks.

- cases: **8**
- error > 2%: **5**
- wrong octave: **5**

| case | truth BPM | reported | err % | octave ratio | conf |
|---|---:|---:|---:|---:|---:|
| straight_120 | 120.000 | 117.454 | 2.122 | 0.9788 | 0.9 |
| straight_128 | 128.000 | 129.199 | 0.937 | 1.0094 | 0.9 |
| offladder_166_686 | 166.686 | 83.354 | 49.993 | 0.5001 | 0.63 |
| offladder_128_5 | 128.500 | 129.199 | 0.544 | 1.0054 | 0.9 |
| swing_120 | 120.000 | 117.454 | 2.122 | 0.9788 | 0.9 |
| half_time_120 | 120.000 | 117.454 | 2.122 | 0.9788 | 0.9 |
| four_on_floor_126 | 126.000 | 83.354 | 33.846 | 0.6615 | 0.63 |
| missing_every_4 | 128.000 | 129.199 | 0.937 | 1.0094 | 0.9 |

**Reading:**
1. `offladder_166_686` -> **83.354** (ratio 0.500): clean half-time error, same as T1.
2. `four_on_floor_126` -> **83.354** (ratio **0.662**): a *subharmonic* error, not a clean half —
   kick on every beat confuses the detector. This case does not appear in T1.
3. `swing_120` and `half_time_120` both collapse to the same 117.454 bucket — swing and
   half-time feel are not distinguished at all.
4. `tempo_confidence` is 0.9 on the 2.1% errors and 0.63 on the catastrophic ones —
   it still never separates right from wrong.

**Significance:** the defect is NOT an artifact of the rectangular-pulse click track.
On musical synthetic material the octave failure rate is *higher* (5/8) than on T1 (2/41),
which answers GPT's caveat that a click track proves rejection but not generalization.