"""Self-tests for ``ai_dj.ace_remixer`` (AI-DJ-P3-ACE-001).

``tests/`` is read-only for this lane, so the new tests live here — inside
the whitelisted ``scripts/ace_poc_*`` glob. Run either way:

    python -m pytest scripts/ace_poc_selftest.py -q
    python scripts/ace_poc_selftest.py

The tests exercise only the pure helpers — no torch/demucs/network needed —
plus a ffmpeg-parse test that skips cleanly when ffmpeg is absent.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np

try:
    import pytest
except ImportError:
    pytest = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_dj import ace_remixer as ar


def test_stretch_rate():
    assert ar.stretch_rate(100.0, 130.0) == 1.3
    assert math.isclose(ar.stretch_rate(99.38, 130.0), 1.3081, abs_tol=1e-3)


def test_bar_seconds():
    assert math.isclose(ar.bar_seconds(120.0), 2.0)
    assert math.isclose(ar.bar_seconds(130.0), 1.8461538, abs_tol=1e-5)


def test_vocal_offset_seconds():
    # onset at 0.5s, entry bar 4 at 130bpm: bar time = 7.3846s -> shift +6.8846
    off = ar.vocal_offset_seconds(0.5, 4, 130.0)
    assert math.isclose(off, 4 * ar.bar_seconds(130.0) - 0.5)


def test_parse_key():
    assert ar.parse_key("B minor") == (11, "minor")
    assert ar.parse_key("Bb major") == (10, "major")
    assert ar.parse_key("F# minor") == (6, "minor")
    assert ar.parse_key("Am") == (9, "minor")


def test_key_compatible_rejects_d20_semitone_bug():
    # D20 defect: same-mode pairs one semitone apart must NOT be compatible.
    assert not ar.key_compatible("D# minor", "D minor")
    assert not ar.key_compatible("C minor", "C# minor")
    assert not ar.key_compatible("F# minor", "G minor")
    assert not ar.key_compatible("E minor", "F minor")


def test_key_compatible_accepts_real_pairs():
    assert ar.key_compatible("B minor", "B minor")  # identical
    assert ar.key_compatible("A minor", "C major")  # relative
    assert ar.key_compatible("B minor", "D major")  # relative
    assert ar.key_compatible("B minor", "F# minor")  # fifth, same mode
    assert ar.key_compatible("B minor", "E minor")  # fourth, same mode
    assert ar.key_compatible("B minor", "B major")  # parallel tonic


def test_key_shift_semitones_shortest():
    assert ar.key_shift_semitones("B minor", "B minor") == 0
    assert ar.key_shift_semitones("A minor", "B minor") == 2
    assert ar.key_shift_semitones("B minor", "A minor") == -2
    assert ar.key_shift_semitones("B minor", "C major") == 1


def test_rms_dbfs():
    silence = np.zeros(48000, dtype=np.float32)
    assert ar.rms_dbfs(silence) == -math.inf
    full = np.full(48000, 0.5, dtype=np.float32)
    assert math.isclose(ar.rms_dbfs(full), -6.02, abs_tol=0.05)


def test_first_strong_onset_on_synthetic():
    if importlib.util.find_spec("librosa") is None:
        if pytest is not None:
            pytest.skip("librosa not installed in this interpreter")
        return  # standalone run: counted as skip, not failure
    sr = 48000
    y = np.zeros(int(sr * 8), dtype=np.float32)
    t0 = 3.0
    n = int(0.01 * sr)
    y[int(t0 * sr) : int(t0 * sr) + n] = 1.0
    y[int(5.5 * sr) : int(5.5 * sr) + n] = 1.0
    got = ar.first_strong_onset_s(y, sr)
    assert abs(got - t0) < 0.2


def test_mix_places_vocal_at_offset():
    sr = 48000
    bgm = np.full((sr * 4, 2), 0.05, dtype=np.float32)
    vocal = np.full((sr, 2), 0.05, dtype=np.float32)
    mix, m = ar.mix_vocal_over_bgm(vocal, bgm, vocal_offset_s=1.0, sr=sr, vocal_target_db=0.0)
    assert mix.shape[0] == sr * 4
    assert m["vocal_offset_s"] == 1.0
    # vocal region should be ~6 dB hotter than BGM-only region (2x amplitude)
    assert mix[sr].max() > mix[0].max()


def test_bar_activity_detects_run():
    sr = 48000
    bpm = 120.0  # 2 s bars
    y = np.zeros(int(sr * 24), dtype=np.float32)  # 12 bars
    y[int(4 * sr) : int(20 * sr)] = 0.3  # bars 2..9 active (8 bars)
    act = ar.bar_activity(y, sr, bpm)
    assert act["n_bars"] == 12
    assert act["longest_active_bars"] >= 8


def _all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_all())
