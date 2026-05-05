"""
Minimal-latency capture: raw GPU framebuffer via mss, PCM audio via sounddevice.
No encoding, no transmuxing — frames stay raw BGRA, audio stays raw float32.
"""

from __future__ import annotations

import time
import threading
import queue
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import mss
import mss.tools
import sounddevice as sd
from PIL import Image


@dataclass
class ScreenFrame:
    """Raw screen capture: BGRA numpy array stored as PNG bytes (lossless, fast)."""

    bgra: np.ndarray
    captured_at: float
    width: int
    height: int

    def to_png_bytes(self) -> bytes:
        import io

        # mss returns BGRA; PIL needs RGB — strip alpha, reverse BGR → RGB
        bgr = self.bgra[:, :, :3]
        rgb = bgr[:, :, ::-1]
        img = Image.fromarray(rgb)
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=1)
        return buf.getvalue()


@dataclass
class AudioChunk:
    samples: np.ndarray  # float32, shape (n, channels)
    sample_rate: int
    captured_at: float


class ScreenCapture:
    """Grabs the primary monitor at a configurable interval — only when the
    previous frame has been consumed (backpressure via queue.get)."""

    def __init__(self, interval: float = 5.0, monitor_index: int = 0):
        self._interval = interval
        self._monitor_index = monitor_index
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[ScreenFrame | None] = queue.Queue(maxsize=1)

    def start(self):
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=False)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)

    def get(self, timeout: float | None = None) -> ScreenFrame | None:
        return self._queue.get(timeout=timeout)

    def _loop(self):
        while self._running:
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[self._monitor_index]
                    while self._running:
                        start = time.monotonic()
                        try:
                            img = sct.grab(monitor)
                        except Exception as e:
                            print(f"[screen-grab] error: {e}, retrying in 2s...")
                            time.sleep(2)
                            break  # reopen mss context
                        frame = ScreenFrame(
                            bgra=np.array(img, dtype=np.uint8),
                            captured_at=time.time(),
                            width=img.width,
                            height=img.height,
                        )
                        # Drop oldest if consumer is slow (queue maxsize=1)
                        try:
                            self._queue.put_nowait(frame)
                        except queue.Full:
                            try:
                                self._queue.get_nowait()
                            except queue.Empty:
                                pass
                            self._queue.put_nowait(frame)
                        elapsed = time.monotonic() - start
                        sleep_for = self._interval - elapsed
                        if sleep_for > 0:
                            self._stop_event.wait(timeout=sleep_for)
                        if self._stop_event.is_set():
                            break
                    if self._stop_event.is_set():
                        break
            except Exception as e:
                print(f"[screen-capture] fatal: {e}, restarting in 5s...")
                time.sleep(5)


class AudioCapture:
    """Captures system audio via loopback (BlackHole/macOS, PulseAudio/Linux).
    Accumulates raw PCM into chunks. Splits on silence or max duration."""

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_duration: float = 30.0,
        silence_threshold: float = 0.01,
        silence_duration: float = 2.0,
        device: str | int | None = None,
    ):
        self._sample_rate = sample_rate
        self._chunk_duration = chunk_duration
        self._silence_threshold = silence_threshold
        self._silence_duration = silence_duration
        self._device = device
        self._running = False
        self._stream: sd.InputStream | None = None
        self._queue: queue.Queue[AudioChunk] = queue.Queue()
        self._buffer: list[np.ndarray] = []
        self._silence_samples = 0
        self._silence_max = int(silence_duration * sample_rate)
        self._chunk_max_samples = int(chunk_duration * sample_rate)

    def start(self, on_chunk: Callable[[AudioChunk], None] | None = None):
        self._running = True
        self._on_chunk = on_chunk

        # Auto-detect channels: aggregate device may have mic + loopback
        try:
            info = sd.query_devices(self._device)
            channels = max(info["max_input_channels"], 1)
        except Exception:
            channels = 1
        self._input_channels = channels

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=channels,
            device=self._device,
            callback=self._callback,
            dtype="float32",
            latency="low",
        )
        self._stream.start()

    def stop(self):
        self._running = False
        self._emit_chunk()              # flush remaining buffered audio
        if self._stream is not None:
            stream = self._stream
            self._stream = None
            stream.stop()
            stream.close()

    def get(self, timeout: float | None = None) -> AudioChunk | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _callback(self, indata: np.ndarray, frames: int, _time, status):
        if status:
            print(f"[audio] status: {status}")
        if not self._running:
            raise sd.CallbackStop

        # Mix all channels → mono (aggregate device: system audio + mic)
        mono = np.mean(indata, axis=1) if indata.ndim > 1 else indata
        self._buffer.append(mono.copy())

        rms = np.sqrt(np.mean(mono**2))
        if rms < self._silence_threshold:
            self._silence_samples += frames
        else:
            self._silence_samples = 0

        total_samples = sum(len(b) for b in self._buffer)

        # Split on silence
        if self._silence_samples >= self._silence_max and total_samples > self._sample_rate:
            self._emit_chunk()
        # Or split on max duration
        elif total_samples >= self._chunk_max_samples:
            self._emit_chunk()

    def _emit_chunk(self):
        if not self._buffer:
            return
        samples = np.concatenate(self._buffer)
        self._buffer.clear()
        self._silence_samples = 0

        # Skip near-silence chunks to avoid Whisper hallucinating "you"
        rms = float(np.sqrt(np.mean(samples**2)))
        if rms < self._silence_threshold * 1.5:
            return

        chunk = AudioChunk(
            samples=samples.astype(np.float32),
            sample_rate=self._sample_rate,
            captured_at=time.time(),
        )
        self._queue.put(chunk)
        if self._on_chunk:
            self._on_chunk(chunk)


def list_audio_devices():
    """Print available audio devices for device selection."""
    print(sd.query_devices())
