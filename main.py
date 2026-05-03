"""
Orchestrator: ties capture + processing with async backpressure.
Screen capture runs in a daemon thread, audio capture in the sd callback thread,
processing is async (vision + whisper run concurrently), summary runs per segment.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import aiofiles
from openai import AsyncOpenAI

from capture import ScreenCapture, AudioCapture, ScreenFrame, AudioChunk
from processor import (
    VisionProcessor,
    AudioProcessor,
    Translator,
    Summarizer,
    FrameDescription,
    AudioTranscript,
    SegmentSummary,
)


class _DummyAudio:
    """Fallback when audio device is unavailable."""

    def start(self): pass
    def stop(self): pass
    def get(self, timeout: float | None = None):
        return None


class Pipeline:
    """Orchestrates capture → process → summarize in a single async loop."""

    def __init__(
        self,
        screen_interval: float = 5.0,
        audio_chunk_duration: float = 30.0,
        audio_silence_threshold: float = 0.01,
        audio_silence_duration: float = 2.0,
        audio_sample_rate: int = 16000,
        audio_device: str | int | None = None,
        vision_model: str = "gpt-4o",
        whisper_model: str = "whisper-1",
        summary_model: str = "gpt-4o",
        output_dir: str = "./output",
        segment_duration: float = 300.0,  # summarize every 5 min
        translate: bool = False,
        translate_to: str = "Russian",
    ):
        import httpx

        if api_key := os.getenv("OPENAI_API_KEY"):
            self._client = AsyncOpenAI(
                api_key=api_key,
                timeout=httpx.Timeout(120.0, connect=10.0),
                max_retries=1,
            )
        else:
            self._client = AsyncOpenAI(
                timeout=httpx.Timeout(120.0, connect=10.0),
                max_retries=1,
            )

        self._screen = ScreenCapture(interval=screen_interval)
        self._audio = AudioCapture(
            sample_rate=audio_sample_rate,
            chunk_duration=audio_chunk_duration,
            silence_threshold=audio_silence_threshold,
            silence_duration=audio_silence_duration,
            device=audio_device,
        )
        self._vision = VisionProcessor(self._client, model=vision_model)
        self._whisper = AudioProcessor(self._client, model=whisper_model)
        self._summarizer = Summarizer(self._client, model=summary_model)
        self._translator = Translator(self._client, model=summary_model, target_lang=translate_to) if translate else None
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._segment_duration = segment_duration

        self._running = False
        self._frames: list[FrameDescription] = []
        self._transcripts: list[AudioTranscript] = []
        self._last_summary_at = time.time()
        self._summary_count = 0
        self._on_event: callable = lambda *a, **kw: None  # hook for server events

    async def run(self):
        """Main loop: capture + async process until interrupted."""
        self._running = True
        loop = asyncio.get_running_loop()

        # Start capture in threads to avoid blocking event loop
        print("[pipeline] starting screen capture...")
        await loop.run_in_executor(None, self._screen.start)
        print("[pipeline] screen capture ok, starting audio...")
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, self._audio.start),
                timeout=10.0,
            )
            print("[pipeline] audio capture started")
        except asyncio.TimeoutError:
            print("[pipeline] WARNING: audio device timed out — audio capture disabled")
            self._audio = _DummyAudio()

        frame_task = asyncio.create_task(self._process_frames())
        audio_task = asyncio.create_task(self._process_audio())

        try:
            while self._running:
                await asyncio.sleep(1.0)
                now = time.time()
                if now - self._last_summary_at >= self._segment_duration:
                    await self._summarize_segment(now)
        finally:
            self._running = False
            frame_task.cancel()
            audio_task.cancel()
            self._screen.stop()
            self._audio.stop()
            try:
                await asyncio.wait_for(
                    asyncio.gather(frame_task, audio_task, return_exceptions=True),
                    timeout=5.0,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    def stop(self):
        self._running = False
        self._screen.stop()
        self._audio.stop()

    async def _process_frames(self):
        """Consume screen frames from the capture queue, send to vision model."""
        while self._running:
            frame: ScreenFrame | None = await asyncio.get_running_loop().run_in_executor(
                None, self._screen.get, 1.0
            )
            if frame is None:
                continue
            try:
                desc = await self._vision.describe(frame)
                self._frames.append(desc)
                await self._on_event("vision", {
                    "captured_at": desc.captured_at,
                    "description": desc.description,
                    "width": desc.width,
                    "height": desc.height,
                })
                print(
                    f"[vision] {time.strftime('%H:%M:%S', time.localtime(desc.captured_at))} "
                    f"{desc.description[:120]}..."
                )
                await self._save_frame(desc)
            except Exception as e:
                print(f"[vision] error: {e}")

    async def _process_audio(self):
        """Consume audio chunks from the capture queue, send to whisper."""
        while self._running:
            chunk: AudioChunk | None = await asyncio.get_running_loop().run_in_executor(
                None, self._audio.get, 1.0
            )
            if chunk is None:
                continue
            try:
                transcript = await self._whisper.transcribe(chunk)
                if transcript.text:
                    # Translate if enabled and language differs from target
                    if self._translator:
                        translation = await self._translator.translate(
                            transcript.text, transcript.language
                        )
                        if translation:
                            transcript.translation = translation
                    self._transcripts.append(transcript)
                    await self._on_event("audio", {
                        "captured_at": transcript.captured_at,
                        "text": transcript.text,
                        "language": transcript.language,
                        "duration": transcript.duration,
                        "translation": transcript.translation,
                    })
                    ts = time.strftime('%H:%M:%S', time.localtime(transcript.captured_at))
                    print(f"[audio] {ts} [{transcript.language}] \"{transcript.text[:100]}...\"")
                    if transcript.translation:
                        print(f"[trans] {ts} \"{transcript.translation[:100]}...\"")
                    await self._save_transcript(transcript)
            except Exception as e:
                print(f"[audio] error: {e}")

    async def _summarize_segment(self, now: float):
        if not self._frames and not self._transcripts:
            return

        segment = SegmentSummary(
            start_time=self._last_summary_at,
            end_time=now,
            frames=list(self._frames),
            transcripts=list(self._transcripts),
        )
        try:
            summary = await self._summarizer.summarize(segment)
            self._summary_count += 1
            await self._save_summary(summary, segment.start_time, segment.end_time)
            await self._on_event("summary", {
                "number": self._summary_count,
                "start": segment.start_time,
                "end": segment.end_time,
                "text": summary,
            })
            print(f"\n[summary #{self._summary_count}]\n{summary}\n")
        except Exception as e:
            print(f"[summary] error: {e}")

        # Reset accumulators
        self._frames.clear()
        self._transcripts.clear()
        self._last_summary_at = now

    async def _save_frame(self, desc: FrameDescription):
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(desc.captured_at))
        path = self._output_dir / f"frame_{ts}.txt"
        async with aiofiles.open(path, "w") as f:
            await f.write(f"{ts}\n{desc.description}\n")

    async def _save_transcript(self, transcript: AudioTranscript):
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(transcript.captured_at))
        path = self._output_dir / f"audio_{ts}.txt"
        content = f"{ts} [{transcript.language}]\n{transcript.text}\n"
        if transcript.translation:
            content += f"\n[translation]\n{transcript.translation}\n"
        async with aiofiles.open(path, "w") as f:
            await f.write(content)

    async def _save_summary(self, summary: str, start: float, end: float):
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(start))
        path = self._output_dir / f"summary_{self._summary_count:03d}_{ts}.md"
        async with aiofiles.open(path, "w") as f:
            await f.write(summary)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Screen + audio capture → AI summary pipeline")
    parser.add_argument("--screen-interval", type=float, default=5.0)
    parser.add_argument("--audio-chunk-duration", type=float, default=30.0)
    parser.add_argument("--audio-silence-threshold", type=float, default=0.01)
    parser.add_argument("--audio-silence-duration", type=float, default=2.0)
    parser.add_argument("--audio-sample-rate", type=int, default=16000)
    parser.add_argument("--audio-device", type=str, default=None)
    parser.add_argument("--vision-model", default="gpt-4o")
    parser.add_argument("--whisper-model", default="whisper-1")
    parser.add_argument("--summary-model", default="gpt-4o")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--segment-duration", type=float, default=300.0)
    parser.add_argument("--translate", action="store_true", help="Translate transcripts to target language")
    parser.add_argument("--translate-to", default="Russian", help="Target language for translation (default: Russian)")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        from capture import list_audio_devices

        list_audio_devices()
        return

    pipeline = Pipeline(
        screen_interval=args.screen_interval,
        audio_chunk_duration=args.audio_chunk_duration,
        audio_silence_threshold=args.audio_silence_threshold,
        audio_silence_duration=args.audio_silence_duration,
        audio_sample_rate=args.audio_sample_rate,
        audio_device=args.audio_device,
        vision_model=args.vision_model,
        whisper_model=args.whisper_model,
        summary_model=args.summary_model,
        output_dir=args.output_dir,
        segment_duration=args.segment_duration,
        translate=args.translate,
        translate_to=args.translate_to,
    )

    def _sig_handler(sig, frame):
        print("\n[shutdown] stopping pipeline...")
        pipeline.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    print(f"[pipeline] starting (screenshot every {args.screen_interval}s, summarize every {args.segment_duration}s)")
    print(f"[pipeline] output dir: {args.output_dir}")
    await pipeline.run()
    print("[pipeline] stopped.")


if __name__ == "__main__":
    asyncio.run(main())
