# Development plan

## ✅ Done

- [x] **Server** — `pipeline_server.py`: HTTP API on :8730, event buffer (TUI polls `/events?since=N`)
- [x] **TUI** — `pipeline_tui.py`: Textual control panel, settings + live log, timer
- [x] **One-button launch** — `start.sh`: server (bg) + TUI (fg), kill via lsof, Ctrl+C/quit stops both
- [x] **Config persistence** — `pipeline_config.yaml` auto-saves from TUI
- [x] **Audio device dropdown** — live selection from sounddevice query
- [x] **Model selection** — vision, whisper, summary in TUI
- [x] **Translation toggle** — on/off + target language in TUI
- [x] **Interval controls** — screen interval, segment duration in TUI
- [x] **MOV transcription** — `transcribe_mov.py` with timestamps, speaker labels (RMS), compression
- [x] **BGRA→RGB fix** — mss returns BGRA, converted to RGB for PIL/GPT-4o
- [x] **Start/Stop reliability** — 11 bugs fixed (event loop, race conditions, thread join, task cleanup)
- [x] **Microphone capture fix** — per-channel RMS max (not mean) for aggregate device silence detection
- [x] **Stop button visual state** — variant + refresh() on enable/disable
- [x] **Keyboard bindings** — Ctrl+C, Ctrl+Q to quit
- [x] **English UI** — all labels, buttons, status translated from Russian
- [x] **Audio noise filter** — RMS check in `_emit_chunk`, skips near-silence chunks
- [x] **Stereo/multi-channel mix** — aggregate device channels mixed to mono
- [x] **Flush audio buffer on stop** — last seconds not lost
- [x] **Formula fix** — `Dir.chdir` instead of `system chdir:`, no cask dependency

---

## Server + TUI (next)

- [ ] **Live tips** — contextual advice: quiet voice → increase gain, silence → check device
- [ ] **Audio gain slider** — apply gain multiplier to audio samples
- [ ] **Audio chunk overlap** — add overlap between chunks to avoid speech loss at boundaries

---

## Pipeline

### 🔴 High

- [ ] **Session-based output** — `output/2026-05-01_1430/` with `index.md`
- [ ] **On-demand screenshot** — hotkey or HTTP endpoint for instant frame

### 🟡 Medium

- [ ] **Monitor/window selection** — `--monitor N` or capture specific window
- [ ] **Adaptive screenshot interval** — faster on activity, slower when idle
- [ ] **Pause/resume** — TUI button or HTTP endpoint
- [ ] **Session metadata** — config dump, total duration, token usage per session

### 🟢 Low

- [ ] **Re-summarization** — take existing `summary_*.md`, produce daily/weekly meta-summary

---

## Transcription (`transcribe_mov.py`)

### 🔴 High

- [ ] **Speaker diarization (pyannote 3.1)** — proper model instead of RMS heuristic (requires `HF_TOKEN`)
- [ ] **Audio preprocessing** — `noisereduce` + multiband compressor before Whisper

### 🟡 Medium

- [ ] **Local Whisper (faster-whisper)** — CTranslate2, 3-4× faster, no API cost
- [ ] **Confidence scores** — per-segment `[0.92]` from Whisper
- [ ] **Chunk overlap + dedup** — 2s overlap, remove duplicates at boundaries

### 🟢 Low

- [ ] **Batch processing** — glob patterns, progress bar, skip existing `.txt`
- [ ] **Export formats** — SRT subtitles, JSON with segments

---

## Rust layer (future)

Capture in Rust via `pyo3` — lower latency. Not critical — current Python perf is adequate.
