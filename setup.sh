#!/usr/bin/env bash
# setup.sh — run once to create the project virtualenv
# Never installs anything globally. All deps stay in .venv/
set -euo pipefail

PYTHON_MIN="3.11"
VENV_DIR=".venv"

echo "==> Checking Python version..."
PYTHON_BIN=$(command -v python3.11 || command -v python3.12 || command -v python3 || true)

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.11+ not found. Install via: brew install python@3.11"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "    Found Python $PYTHON_VERSION at $PYTHON_BIN"

# Minimal version check
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]; }; then
    echo "ERROR: Python 3.11+ required, found $PYTHON_VERSION"
    echo "Install via: brew install python@3.11"
    exit 1
fi

echo "==> Creating virtualenv at $VENV_DIR ..."
"$PYTHON_BIN" -m venv "$VENV_DIR"

echo "==> Installing core dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e ".[dev]"

# macOS-only deps (pyobjc) — skip in CI/Linux
if [[ "$(uname)" == "Darwin" ]]; then
    echo "==> Installing macOS-specific dependencies (pyobjc)..."
    "$VENV_DIR/bin/pip" install --quiet -e ".[macos]"
fi

echo ""
echo "==> Setup complete."
echo ""
echo "    Activate the venv with:"
echo "    source .venv/bin/activate"
echo ""
echo "    Then run tests:"
echo "    pytest"
echo ""
echo "    Then install the track command:"
echo "    pip install -e ."
echo "    track --help"
