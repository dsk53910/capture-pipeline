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

# 4. Launch (one button)
./start.sh
```

## UI (new)

```
./start.sh          → launches server + TUI, Ctrl+C to stop both
./run.sh            → headless mode (CLI only, full args)
```

```
start.sh
├── pipeline_server.py &    (background HTTP :8730)
└── pipeline_tui.py         (Textual TUI, foreground)

TUI features:
  - Live device dropdown (no system settings needed)
  - Model selection (vision, whisper, summary)
  - Translation toggle + target language
  - Gain / overlap / interval sliders
  - Contextual tips during capture
  - Config auto-saves to pipeline_config.yaml
```

## Architecture

```
capture.py       — ScreenCapture (mss, daemon thread), AudioCapture (sounddevice, callback thread)
processor.py     — VisionProcessor (GPT-4o), AudioProcessor (Whisper), Translator, Summarizer
main.py          — Pipeline orchestrator: async loop, queues, signal handling, CLI
pipeline_server.py — HTTP server wrapping Pipeline, event buffer (TUI polls /events)
pipeline_tui.py  — Textual TUI: settings panel, live log, tips, one-button control
transcribe_mov.py — Offline MOV → audio extract (afconvert) → transcribe + diarize
run.sh           — Headless launch wrapper with defaults
start.sh         — Server + TUI launch (one button)
.env             — OPENAI_API_KEY (loaded via python-dotenv)
pipeline_config.yaml — Auto-saved settings
output/          — frame_*.txt, audio_*.txt, summary_*.md
```

## Key commands

```bash
./start.sh                         # TUI mode (server + control panel)
./run.sh                           # headless mode (CLI only)
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
