# capture-pipeline

Screen + audio capture → AI analysis via OpenAI (vision, whisper, summary).

## Quick start

```bash
# 1. Dependencies
uv sync

# 2. API key
echo 'OPENAI_API_KEY=sk-...' > .env

# 3. macOS only: system audio loopback
brew install --cask blackhole-2ch
# Reboot, then in Audio MIDI Setup.app:
#   + → Create Multi-Output Device   (BlackHole 2ch + Speakers)
#   + → Create Aggregate Device      (BlackHole 2ch + Microphone)
# In System Settings → Sound → Output: select Multi-Output Device

# 4. Launch
./run.sh
```

## Architecture

```
capture.py       — ScreenCapture (mss, daemon thread), AudioCapture (sounddevice, callback thread)
processor.py     — VisionProcessor (GPT-4o), AudioProcessor (Whisper), Translator, Summarizer
main.py          — Pipeline orchestrator: async loop, queues, signal handling, CLI
transcribe_mov.py — Offline MOV → audio extract (afconvert) → transcribe + diarize
run.sh           — Launch wrapper with defaults
.env             — OPENAI_API_KEY (loaded via python-dotenv)
output/          — frame_*.txt, audio_*.txt, summary_*.md
```

## Key commands

```bash
./run.sh                           # live capture + screenshot + transcribe + summary
./run.sh --list-devices            # show audio devices
./run.sh --screen-interval 2       # faster screenshots
./run.sh --audio-device "BlackHole 2ch"  # override audio device

uv run python transcribe_mov.py --compress video.mov           # offline transcription
uv run python transcribe_mov.py --compress --translate video.mov  # + translation
```

## Models

| Purpose | Default | Flag |
|---|---|---|
| Screenshot → description | gpt-4o | `--vision-model` |
| Audio → transcript | whisper-1 | `--whisper-model` |
| Period summary | gpt-4o | `--summary-model` |
| Translation | gpt-4o | `--translate-to` |

## Conventions

- Python 3.11+, `uv` for package management
- `pyproject.toml` (PEP 621), no setup.py
- `aiofiles` for async file I/O
- Capture is sync (threads), processing is async (asyncio)
- All API calls via `httpx.Timeout(120s)`, max 1 retry
- Signal handlers for graceful shutdown (SIGINT/SIGTERM)
