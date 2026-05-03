#!/bin/bash
set -euo pipefail

echo "============================================"
echo "  capture-pipeline installer"
echo "============================================"
echo ""

# ---- macOS check ----
if [[ "$(uname)" != "Darwin" ]]; then
    echo "[!] macOS only. Linux support is experimental — install manually."
    exit 1
fi

# ---- Homebrew ----
if ! command -v brew &>/dev/null; then
    echo "[!] Homebrew not found. Install from https://brew.sh"
    exit 1
fi

# ---- BlackHole audio driver ----
if ! system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole"; then
    echo "[1/4] Installing BlackHole 2ch (audio loopback)..."
    brew install --cask blackhole-2ch
    echo ""
    echo "[*] REBOOT REQUIRED — BlackHole won't appear until restart."
    echo "[*] After reboot, re-run this script to continue."
    echo ""
    read -p "Press Enter to reboot now (or Ctrl+C to reboot later)..."
    sudo shutdown -r now
    exit 0
else
    echo "[1/4] BlackHole 2ch ✓"
fi

# ---- UV package manager ----
if ! command -v uv &>/dev/null; then
    echo "[2/4] Installing uv..."
    brew install uv
else
    echo "[2/4] uv ✓"
fi

# ---- Python + project ----
echo "[3/4] Installing capture-pipeline..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
uv sync
uv tool install --force --python python3 "$SCRIPT_DIR" 2>/dev/null || \
    uv pip install -e "$SCRIPT_DIR" 2>/dev/null || true

# ---- .env check ----
if [ ! -f .env ]; then
    echo "[4/4] No .env found — creating template."
    cat > .env <<'EOF'
# OpenAI API key (required)
OPENAI_API_KEY=sk-your-key-here
EOF
    echo "[*] Edit .env and add your OpenAI API key:"
    echo "    nano .env"
else
    echo "[4/4] .env ✓"
fi

echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "  Audio setup (Audio MIDI Setup.app):"
echo "    1. Multi-Output Device: BlackHole 2ch + Speakers"
echo "    2. Aggregate Device:  BlackHole 2ch + Microphone"
echo "    3. System Settings → Sound → Output → Multi-Output"
echo ""
echo "  Launch:"
echo "    ./start.sh     # TUI mode"
echo "    ./run.sh       # headless mode"
echo ""
