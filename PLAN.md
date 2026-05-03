# Development plan

## Pipeline (`main.py` + `capture.py` + `processor.py`)

### 🔴 High priority

- [ ] **Audio chunk overlap** — add 1s overlap between consecutive chunks to avoid speech loss at boundaries
- [ ] **Session-based output** — `output/2026-05-01_1430/` with `index.md`, not flat file list
- [ ] **On-demand screenshot** — keyboard hotkey or HTTP endpoint to trigger instant frame capture

### 🟡 Medium priority

- [ ] **Monitor/window selection** — `--monitor 1` or `--window "VS Code"` flag
- [ ] **Adaptive screenshot interval** — faster when active window changes, slower when idle
- [ ] **Pause/resume** — console key `p` or SIGUSR1 to toggle capture
- [ ] **Session metadata** — per-session config dump, total duration, token usage

### 🟢 Low priority

- [ ] **Re-summarization** — take existing `summary_*.md` files, produce daily/weekly meta-summary
- [ ] **config.yaml** — single config file instead of 15 CLI flags

---

## Transcription (`transcribe_mov.py`)

### 🔴 High priority

- [ ] **Speaker diarization (pyannote 3.1)** — replace RMS heuristic with proper model (requires `HF_TOKEN`)
- [ ] **Audio preprocessing** — `noisereduce` + multiband compressor before Whisper input

### 🟡 Medium priority

- [ ] **Local Whisper (faster-whisper)** — CTranslate2 backend, 3-4× faster, no API cost
- [ ] **Confidence scores** — expose Whisper segment confidence `[0.92]` in output
- [ ] **Chunk overlap + dedup** — 2s overlap between chunks, remove duplicate phrases at boundaries

### 🟢 Low priority

- [ ] **Batch processing** — glob patterns, progress bar, skip existing `.txt`
- [ ] **Export formats** — SRT subtitles, JSON with segments, Markdown table

---

## Rust layer (future)

Capture layer (`mss` + `sounddevice` equivalents) in Rust via `pyo3` bindings:
- Lower CPU/latency for screen grab and audio callback
- AI calls stay in Python (network-bound, no benefit from Rust)
- Not critical — current Python perf is adequate
