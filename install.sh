#!/usr/bin/env bash
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

ATOMIC_HOME="$HOME/.atomic"
BIN_DIR="$HOME/.local/bin"
VENV="$ATOMIC_HOME/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "  ${CYAN}atomic${RESET} installer"
echo ""

# ── Python ───────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "  ${RED}python3 not found.${RESET} Install from https://python.org then re-run."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10) ]]; then
    echo -e "  ${RED}Python 3.10+ required${RESET} (found ${PY_VER}). Upgrade and re-run."
    exit 1
fi

echo -e "  Python  ${GREEN}${PY_VER}${RESET}"

# ── GPU detection ─────────────────────────────────────────────────────────────
detect_gpu() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "metal"
    elif command -v nvidia-smi &>/dev/null 2>&1; then
        echo "cuda"
    else
        echo "cpu"
    fi
}

GPU=$(detect_gpu)
case $GPU in
    metal) echo -e "  GPU     ${GREEN}Apple Metal${RESET}" ;;
    cuda)  echo -e "  GPU     ${GREEN}NVIDIA CUDA${RESET}" ;;
    cpu)   echo -e "  GPU     ${YELLOW}CPU only${RESET}  ${DIM}(no GPU found — inference will be slower)${RESET}" ;;
esac

echo ""

# ── venv ──────────────────────────────────────────────────────────────────────
if [[ -d "$VENV" ]]; then
    echo -e "  ${DIM}Reusing existing venv at $VENV${RESET}"
else
    echo -e "  ${DIM}Creating venv at $VENV ...${RESET}"
    python3 -m venv "$VENV"
fi

PIP="$VENV/bin/pip"
"$PIP" install --upgrade pip --quiet

# ── llama-cpp-python ──────────────────────────────────────────────────────────
echo -e "  ${DIM}Installing llama-cpp-python [${GPU}] — this may take a few minutes ...${RESET}"

case $GPU in
    metal)
        CMAKE_ARGS="-DGGML_METAL=on" "$PIP" install "llama-cpp-python" --quiet --upgrade
        ;;
    cuda)
        CMAKE_ARGS="-DGGML_CUDA=on" "$PIP" install "llama-cpp-python" --quiet --upgrade
        ;;
    *)
        "$PIP" install "llama-cpp-python" --quiet --upgrade
        ;;
esac

# ── atomic ────────────────────────────────────────────────────────────────────
echo -e "  ${DIM}Installing atomic ...${RESET}"
"$PIP" install -e "$SCRIPT_DIR" --quiet

# ── global wrapper ────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/atomic" << WRAPPER
#!/usr/bin/env bash
exec "$VENV/bin/atomic" "\$@"
WRAPPER
chmod +x "$BIN_DIR/atomic"

# ── PATH ──────────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    SHELL_RC=""
    if [[ "$SHELL" == *"zsh"* ]]; then
        SHELL_RC="$HOME/.zshrc"
    elif [[ "$SHELL" == *"bash"* ]]; then
        SHELL_RC="$HOME/.bash_profile"
    fi

    if [[ -n "$SHELL_RC" ]]; then
        echo '' >> "$SHELL_RC"
        echo '# atomic' >> "$SHELL_RC"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
        echo -e "  ${DIM}Added ~/.local/bin to PATH in $SHELL_RC${RESET}"
    else
        echo -e "  ${YELLOW}Add this to your shell profile:${RESET}"
        echo -e "  ${DIM}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
    fi

    export PATH="$BIN_DIR:$PATH"
fi

echo ""
echo -e "  ${GREEN}Done!${RESET}"
echo ""
echo -e "  Run ${CYAN}atomic${RESET} to start."
echo -e "  ${DIM}(open a new terminal if 'atomic' is not found)${RESET}"
echo ""
