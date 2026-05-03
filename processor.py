"""
Processing pipeline: frames → vision model, audio → whisper, merge → summary.
Uses OpenAI API by default; swap providers as needed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import wave
from dataclasses import dataclass, field
from datetime import datetime

from openai import AsyncOpenAI

from capture import ScreenFrame, AudioChunk


@dataclass
class FrameDescription:
    captured_at: float
    description: str
    width: int
    height: int


@dataclass
class TimedSegment:
    start: float  # seconds from chunk start
    end: float
    text: str
    speaker: str = ""


@dataclass
class AudioTranscript:
    captured_at: float
    text: str
    language: str
    duration: float
    translation: str = ""
    segments: list[TimedSegment] = field(default_factory=list)


@dataclass
class SegmentSummary:
    start_time: float
    end_time: float
    frames: list[FrameDescription] = field(default_factory=list)
    transcripts: list[AudioTranscript] = field(default_factory=list)

    @property
    def texts(self) -> str:
        parts = []
        for t in self.transcripts:
            parts.append(t.text)
            if t.translation:
                parts.append(f"[{t.language}→ru] {t.translation}")
        return "\n".join(parts)


class VisionProcessor:
    """Sends captured frames to a vision-capable LLM for screen description."""

    SYSTEM = (
        "You are a screen activity analyst. Describe what is visible on the screen: "
        "which applications are running, what content is shown (code, documents, browser, terminal), "
        "any notable UI elements, dialogs, errors, or activity.  "
        "Be concise — 3-4 sentences max.  "
        "If the screen is mostly static or unchanged, say so."
    )

    def __init__(self, client: AsyncOpenAI, model: str = "gpt-4o"):
        self._client = client
        self._model = model

    async def describe(self, frame: ScreenFrame) -> FrameDescription:
        png_bytes = frame.to_png_bytes()
        b64 = base64.b64encode(png_bytes).decode()
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "low",
                            },
                        },
                    ],
                },
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return FrameDescription(
            captured_at=frame.captured_at,
            description=response.choices[0].message.content or "",
            width=frame.width,
            height=frame.height,
        )


class AudioProcessor:
    """Transcribes audio via OpenAI Whisper API. Swap to local whisper for offline use."""

    def __init__(self, client: AsyncOpenAI, model: str = "whisper-1"):
        self._client = client
        self._model = model

    async def transcribe(self, chunk: AudioChunk) -> AudioTranscript:
        wav_bytes = _samples_to_wav(chunk.samples, chunk.sample_rate)
        duration = len(chunk.samples) / chunk.sample_rate
        wav_file = io.BytesIO(wav_bytes)
        wav_file.name = "audio.wav"

        response = await self._client.audio.transcriptions.create(
            model=self._model,
            file=wav_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
        segments = [
            TimedSegment(start=s.start, end=s.end, text=s.text.strip())
            for s in (response.segments or [])
        ]
        return AudioTranscript(
            captured_at=chunk.captured_at,
            text=response.text.strip(),
            language=response.language or "unknown",
            duration=duration,
            segments=segments,
        )


class Translator:
    """Translates transcripts between languages via GPT model."""

    def __init__(self, client: AsyncOpenAI, model: str = "gpt-4o", target_lang: str = "Russian"):
        self._client = client
        self._model = model
        self._target = target_lang

    async def translate(self, text: str, source_lang: str) -> str:
        if not text:
            return ""
        # Skip if already in target language
        if source_lang.lower().startswith(self._target.lower()[:2]):
            return ""
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": f"Translate to {self._target}. Return ONLY the translation, no explanations.",
                },
                {"role": "user", "content": text},
            ],
            max_tokens=len(text) * 2,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""


class Summarizer:
    """Merges frame descriptions and audio transcripts into a curated summary."""

    SYSTEM = (
        "You are a session summarizer. You receive screen descriptions and speech transcripts "
        "from a desktop activity recording. Produce a structured summary:\n\n"
        "1. **Activity**: What the user was doing (coding, browsing, presenting, etc.)\n"
        "2. **Topics discussed**: Key topics from speech and visible content\n"
        "3. **Tools visible**: Apps, editors, terminals, websites\n"
        "4. **Notable events**: Errors, searches, decisions, TODO items\n"
        "5. **Summary**: 2-4 sentence overall summary\n\n"
        "Use markdown. Infer links or repo names if visible."
    )

    def __init__(self, client: AsyncOpenAI, model: str = "gpt-4o"):
        self._client = client
        self._model = model

    async def summarize(self, segment: SegmentSummary) -> str:
        time_range = f"{datetime.fromtimestamp(segment.start_time):%H:%M:%S} — {datetime.fromtimestamp(segment.end_time):%H:%M:%S}"

        frames_text = "\n\n".join(
            f"[{datetime.fromtimestamp(f.captured_at):%H:%M:%S}] {f.description}"
            for f in segment.frames
        )
        transcript_text = segment.texts or "(no speech detected)"

        prompt = (
            f"**Time range**: {time_range}\n\n"
            f"**Screen activity log**:\n{frames_text}\n\n"
            f"**Transcribed speech**:\n{transcript_text}"
        )

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.4,
        )
        return response.choices[0].message.content or ""


def _samples_to_wav(samples: "np.ndarray", sample_rate: int) -> bytes:
    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        # Convert float32 [-1,1] to int16
        int_samples = (samples * 32767).clip(-32768, 32767).astype(np.int16)
        wf.writeframes(int_samples.tobytes())
    return buf.getvalue()
