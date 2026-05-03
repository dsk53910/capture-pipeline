# capture-pipeline

Screen + audio capture → AI analysis via OpenAI (GPT-4o, Whisper).

Records your desktop activity: screenshots are described, audio is transcribed, and periodic summaries are generated in Markdown.

## Install

### Option A: Homebrew (recommended)

```bash
brew tap dsk53910/capture
brew install capture-pipeline
```

### Option B: Install script

```bash
git clone https://github.com/dsk53910/capture-pipeline.git
cd capture-pipeline
./install.sh
```

### Option C: Manual

```bash
brew install --cask blackhole-2ch        # audio loopback driver
brew install uv                          # Python package manager
git clone https://github.com/dsk53910/capture-pipeline.git
cd capture-pipeline
uv sync
echo 'OPENAI_API_KEY=sk-...' > .env
```

## Audio setup (macOS, one-time)

After installing BlackHole and rebooting:

1. Open **Audio MIDI Setup.app**
2. `+` → **Create Multi-Output Device** → check `BlackHole 2ch` + your speakers
3. `+` → **Create Aggregate Device** → check `BlackHole 2ch` + your microphone
4. **System Settings → Sound → Output** → select `Multi-Output Device`

```
System Output → Multi-Output (BlackHole 2ch + Speakers)   ← sound goes here
Capture Input → Aggregate (BlackHole 2ch + Microphone)    ← captured from here
```

## Launch

```bash
./start.sh       # TUI mode (server + control panel, one button)
./run.sh         # headless mode (CLI only)
```

## Usage

### TUI mode

```
./start.sh
```

Opens a terminal UI with:
- **Device dropdown** — choose audio input device live
- **Model selection** — pick GPT-4o / GPT-4o-mini / Whisper
- **Translation toggle** — auto-translate speech to Russian (or any language)
- **Interval sliders** — screenshot frequency, summary period
- **Live event log** — screenshots, transcripts, summaries streaming
- **Config auto-save** — settings persist to `pipeline_config.yaml`

Press **Ctrl+C** to stop.

### Headless mode

```bash
./run.sh                                      # defaults from run.sh
./run.sh --screen-interval 2                  # faster screenshots
./run.sh --audio-device "BlackHole 2ch"       # override device
./run.sh --list-devices                       # show available audio inputs
./run.sh --translate --translate-to English   # translate to English
```

### Offline transcription

```bash
# Transcribe MOV files with timestamps and speaker labels
uv run python transcribe_mov.py --compress --translate video.mov
```

| Flag | Effect |
|------|--------|
| `--compress` | Boost quiet voice (for phone calls) |
| `--translate` | Translate to target language |
| `--translate-to Russian` | Target language (default: Russian) |

Output:
```
[   0.0s] 🎙️ Спикер 1: Hello, how are you?
[   2.5s] 🎙️ Спикер 2 (тихий): I'm fine, thanks.
```

## Models

| Purpose | Model | Override |
|---|---|---|
| Screenshot → description | `gpt-4o` | `--vision-model` |
| Audio → transcript | `whisper-1` | `--whisper-model` |
| Period summary | `gpt-4o` | `--summary-model` |
| Translation | `gpt-4o` | `--translate-to` |

## Output

```
output/
├── frame_20260501_143000.txt      # per-screenshot descriptions
├── audio_20260501_143015.txt      # per-chunk transcripts (+ translation)
└── summary_001_20260501_143000.md # 5-minute Markdown summaries
```

## Architecture

```
capture.py         — ScreenCapture (mss), AudioCapture (sounddevice)
processor.py       — VisionProcessor, AudioProcessor, Translator, Summarizer
main.py            — Pipeline orchestrator (headless CLI)
pipeline_server.py — HTTP :8730, event buffer, device management
pipeline_tui.py    — Textual TUI: settings panel + live log
transcribe_mov.py  — Offline MOV → audio extract → transcribe + speaker labels
start.sh           — One-button launch (server + TUI)
run.sh             — Headless launch
install.sh         — macOS installer
```

## Requirements

- macOS (Linux experimental)
- Python 3.11+
- OpenAI API key
- BlackHole 2ch (audio loopback, macOS only)

## Uninstall

```bash
# Homebrew
brew uninstall capture-pipeline
brew untap dsk53910/capture
brew uninstall --cask blackhole-2ch   # optional

# Manual
rm -rf capture-pipeline/
```

User data (`.env`, `output/`) is kept intact.

## License

MIT
