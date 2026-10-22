"""PCM playback of the rendered master via sounddevice (D2).

The PortAudio callback does no DSP and no filesystem I/O: a reader thread
prefetches PCM blocks from the master WAV into a bounded queue; the callback
only copies a ready block (or emits zeros and counts an underflow). Playback is
of the canonical master file — nothing is recomputed at play time.

Telemetry (frames emitted, underflows, device) is collected for
``playback_report.json`` — sample counters are the timing truth, wall clock is
only used for the optional progress print.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

BLOCK_FRAMES = 4096
QUEUE_DEPTH = 8


def _make_reader(
    master_wav: Path, block_frames: int, blocks: queue.Queue, stop: threading.Event
) -> threading.Thread:
    """Thread that streams PCM blocks into ``blocks``; None = EOF sentinel."""

    def reader() -> None:
        try:
            with sf.SoundFile(str(master_wav)) as f:
                for block in f.blocks(blocksize=block_frames, dtype="float32", always_2d=True):
                    while not stop.is_set():
                        try:
                            blocks.put(block, timeout=0.05)
                            break
                        except queue.Full:
                            continue
                    if stop.is_set():
                        break
        finally:
            while not stop.is_set():
                try:
                    blocks.put_nowait(None)
                    break
                except queue.Full:
                    continue

    return threading.Thread(target=reader, daemon=True, name="pcm-reader")


def _make_callback(blocks: queue.Queue, state: dict):
    """Realtime callback: copy a ready block or emit zeros + count underflow."""

    def callback(outdata: np.ndarray, frames: int, _time, status) -> None:
        if status.output_underflow:
            state["underflows"] += 1
        try:
            blk = blocks.get_nowait()
        except queue.Empty:
            state["underflows"] += 1
            outdata.fill(0)
            return
        if blk is None or state["eof"]:
            state["eof"] = True
            outdata.fill(0)
            raise sd.CallbackStop
        n = len(blk)
        outdata[:n] = blk
        state["frames_emitted"] += n
        if n < frames:
            outdata[n:] = 0.0
            state["eof"] = True
            raise sd.CallbackStop

    return callback


def play_master(
    master_wav: str | Path,
    block_frames: int = BLOCK_FRAMES,
    queue_depth: int = QUEUE_DEPTH,
    report_path: str | Path | None = None,
    progress: bool = False,
) -> dict:
    """Play ``master_wav`` to the default output device; return telemetry."""
    master_wav = Path(master_wav)
    if not master_wav.exists():
        raise FileNotFoundError(f"master not found: {master_wav}")

    with sf.SoundFile(str(master_wav)) as f:
        sr, channels, total_frames = f.samplerate, f.channels, len(f)

    blocks: queue.Queue = queue.Queue(maxsize=queue_depth)
    stop = threading.Event()
    state = {"frames_emitted": 0, "underflows": 0, "eof": False}
    reader_thread = _make_reader(master_wav, block_frames, blocks, stop)

    stream = sd.OutputStream(
        samplerate=sr,
        channels=channels,
        dtype="float32",
        blocksize=block_frames,
        callback=_make_callback(blocks, state),
    )
    reader_thread.start()
    try:
        stream.start()
        while stream.active:
            if progress:
                done = state["frames_emitted"]
                print(
                    f"\r{done}/{total_frames} frames "
                    f"({done / sr:.1f}s), underflows={state['underflows']}   ",
                    end="",
                    flush=True,
                )
            stop.wait(0.5 if progress else 0.2)
        if progress:
            print()
    except KeyboardInterrupt:
        stop.set()
        stream.abort()
    finally:
        if stream.active:
            stream.stop()
        stream.close()
        reader_thread.join(timeout=2.0)

    report = {
        "master_wav": str(master_wav),
        "total_frames": int(total_frames),
        "frames_emitted": state["frames_emitted"],
        "frames_matched": state["frames_emitted"] >= total_frames,
        "underflows": state["underflows"],
        "block_frames": block_frames,
        "queue_depth": queue_depth,
        "stopped_early": stop.is_set() and not state["eof"],
        "device_index": stream.device,
        "samplerate": sr,
        "channels": channels,
    }
    if report_path:
        rp = Path(report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
