"""
Extract audio from MOV files → transcribe via Whisper → optional translation.
Uses macOS built-in afconvert (no ffmpeg needed).
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import AsyncOpenAI

from processor import AudioProcessor, AudioTranscript, Translator, TimedSegment

load_dotenv()


def compress_audio(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Dynamic range compressor: lifts quiet parts, limits loud peaks.
    Good for phone calls where one speaker is much quieter."""
    # Envelope: smoothed absolute amplitude
    window = int(sample_rate * 0.020)  # 20ms window
    if window < 1:
        window = 1
    # Pad for same-length convolution
    envelope = np.convolve(np.abs(samples), np.ones(window) / window, mode="same")
    envelope = np.maximum(envelope, 1e-10)

    # Target: reduce dynamic range to ~15dB above noise floor
    peak_env = np.percentile(envelope, 95)
    noise_floor = np.percentile(envelope, 10)
    target_range = 0.18  # ~15dB

    # Compression ratio: boost quiet, leave loud
    gain = np.where(
        envelope < peak_env * 0.3,
        target_range / (envelope + noise_floor),  # boost quiet
        1.0 / np.maximum(envelope / (peak_env * 0.3), 1.0),  # limit loud
    )
    # Smooth gain changes to avoid artifacts
    gain = np.convolve(gain, np.ones(window) / window, mode="same")
    gain = np.clip(gain, 0.5, 10.0)  # safety limits

    compressed = samples * gain
    # Prevent clipping
    peak = np.max(np.abs(compressed))
    if peak > 0.95:
        compressed = compressed * 0.95 / peak
    return compressed.astype(np.float32)


def label_speakers(
    segments: list[TimedSegment],
    samples: np.ndarray,
    sample_rate: int,
) -> list[TimedSegment]:
    """Assign speaker labels based on per-segment RMS energy.
    Louder segments → Speaker 1, quieter → Speaker 2.
    Works well for phone calls recorded on one end."""
    if not segments:
        return segments

    rms_values = []
    for seg in segments:
        start_idx = int(seg.start * sample_rate)
        end_idx = int(seg.end * sample_rate)
        seg_samples = samples[start_idx:end_idx]
        rms = float(np.sqrt(np.mean(seg_samples**2))) if len(seg_samples) > 0 else 0.0
        rms_values.append(rms)

    # Separate into two clusters by RMS
    threshold = np.median(rms_values) if rms_values else 0
    labeled = []
    for seg, rms in zip(segments, rms_values):
        speaker = "🎙️ Спикер 1" if rms >= threshold else "🎙️ Спикер 2 (тихий)"
        labeled.append(TimedSegment(start=seg.start, end=seg.end, text=seg.text, speaker=speaker))

    return labeled


def format_timed_output(segments: list[TimedSegment]) -> str:
    """Format segments with timestamps and speaker labels."""
    lines = []
    for seg in segments:
        ts = f"[{seg.start:6.1f}s]"
        sp = f" {seg.speaker}: " if seg.speaker else " "
        lines.append(f"{ts}{sp}{seg.text}")
    return "\n".join(lines)


async def transcribe_file(
    client: AsyncOpenAI,
    mov_path: Path,
    whisper_model: str = "whisper-1",
    translate: bool = False,
    translate_to: str = "Russian",
    compress: bool = False,
) -> AudioTranscript:
    processor = AudioProcessor(client, model=whisper_model)
    translator = Translator(client, model="gpt-4o", target_lang=translate_to) if translate else None

    # Extract audio to WAV via macOS afconvert
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    subprocess.run(
        [
            "afconvert",
            "-f", "WAVE",
            "-d", "LEI16@16000",
            str(mov_path),
            str(wav_path),
        ],
        check=True,
        capture_output=True,
    )

    # Read WAV samples
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.readframes(wf.getnframes())
        raw = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sample_rate = wf.getframerate()
        num_channels = wf.getnchannels()

    wav_path.unlink(missing_ok=True)

    # Deinterleave if stereo — pick the best channel (phone calls: both same)
    if num_channels == 2:
        print(f"  [stereo detected: {len(raw) / sample_rate:.0f}s per channel]")
        left = raw[0::2]
        right = raw[1::2]
        # Quick check: which channel has more energy (less silence)?
        l_rms = float(np.sqrt(np.mean(left**2)))
        r_rms = float(np.sqrt(np.mean(right**2)))
        channel_samples = [left] if l_rms >= r_rms else [right]
        print(f"  [using channel {'L' if l_rms >= r_rms else 'R'} (RMS {max(l_rms, r_rms):.4f})]")
    else:
        channel_samples = [raw]

    from capture import AudioChunk

    all_segments: list[TimedSegment] = []
    total_duration = 0.0
    lang_counts: dict[str, int] = {}

    for ch_idx, samples in enumerate(channel_samples):
        ch_label = ["L", "R"][ch_idx] if len(channel_samples) == 2 else ""
        if ch_label:
            print(f"  --- channel {ch_label} ---")

        # Keep uncompressed copy for RMS-based speaker labeling
        raw_samples = samples.copy()

        if compress:
            print(f"  [compressing...]")
            samples = compress_audio(samples, sample_rate)

        chunk_samples = 12 * 60 * sample_rate
        ch_duration = 0.0

        for offset in range(0, len(samples), chunk_samples):
            segment_slice = samples[offset : offset + chunk_samples]
            raw_slice = raw_samples[offset : offset + chunk_samples]
            chunk = AudioChunk(samples=segment_slice, sample_rate=sample_rate, captured_at=offset / sample_rate)
            transcript = await processor.transcribe(chunk)
            if transcript.text:
                # Label speakers using uncompressed audio for accurate RMS
                labeled = label_speakers(transcript.segments, raw_slice, sample_rate)
                # Adjust timestamps: offset within chunk + chunk start offset
                chunk_start = offset / sample_rate
                for seg in labeled:
                    seg.start += chunk_start + ch_duration
                    seg.end += chunk_start + ch_duration
                all_segments.extend(labeled)
                lang_counts[transcript.language] = lang_counts.get(transcript.language, 0) + 1
                print(f"  chunk {len(all_segments)} segments: [{transcript.language}] {transcript.text[:80]}...")
            ch_duration += len(segment_slice) / sample_rate

        total_duration = max(total_duration, ch_duration)

    if not all_segments:
        return AudioTranscript(captured_at=0.0, text="", language="unknown", duration=0.0)

    merged_lang = max(lang_counts, key=lang_counts.get) if lang_counts else "unknown"

    # Build formatted output with timestamps and speakers
    timed_text = format_timed_output(all_segments)
    plain_text = " ".join(s.text for s in all_segments)

    merged = AudioTranscript(
        captured_at=0.0,
        text=plain_text,
        language=merged_lang,
        duration=total_duration,
        segments=all_segments,
    )

    # Translate merged text
    if translator and merged.text:
        translation = await translator.translate(merged.text, merged.language)
        if translation:
            merged.translation = translation

    # Store timed text for output
    merged._timed_text = timed_text

    return merged


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Transcribe MOV files")
    parser.add_argument("files", nargs="*", help="MOV files to transcribe")
    parser.add_argument("--whisper-model", default="whisper-1")
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--translate-to", default="Russian")
    parser.add_argument("--compress", action="store_true", help="Compress dynamic range (for quiet voices)")
    args = parser.parse_args()

    client = AsyncOpenAI()

    mov_files = [Path(f) for f in args.files] if args.files else sorted(Path(".").glob("*.mov"))
    if not mov_files:
        print("No MOV files found")
        return

    for mov in mov_files:
        print(f"\n{'='*60}\n{mov.name}\n{'='*60}")
        try:
            transcript = await transcribe_file(
                client,
                mov,
                whisper_model=args.whisper_model,
                translate=args.translate,
                translate_to=args.translate_to,
                compress=args.compress,
            )
            # Print timed output
            timed = getattr(transcript, "_timed_text", "") or transcript.text
            print(timed[:2000])

            # Save to .txt
            out = mov.with_suffix(".txt")
            content = f"File: {mov.name}\nLang: {transcript.language}\nDuration: {transcript.duration:.1f}s\n\n"
            content += timed + "\n"
            if transcript.translation:
                content += f"\n--- Translation ({args.translate_to}) ---\n{transcript.translation}\n"
            out.write_text(content)
            print(f"Saved → {out}")

        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
