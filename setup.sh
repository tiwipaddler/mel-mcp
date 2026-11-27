#!/usr/bin/env bash
# Set up mel-mcp on macOS or Linux: Python environment, packages, settings, tests, search model.
#
#   ./setup.sh            set up, ready for your own data
#   ./setup.sh --starter  also download the Kenya starter pack and build its search index
#
# Safe to run again: finished steps are skipped and your data and .env are never replaced.

set -euo pipefail
cd "$(dirname "$0")"

STARTER=no
for arg in "$@"; do
  case "$arg" in
    --starter) STARTER=yes ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nSetup stopped: %s\n' "$1" >&2; exit 1; }

step "Finding Python 3.11 or 3.12"
# PyTorch, which the search model needs, doesn't support Python 3.13 on every computer.
PYTHON=""
for candidate in python3.12 python3.11 python3 \
    /opt/local/bin/python3.12 /opt/local/bin/python3.11 \
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
    /usr/local/bin/python3.12 /usr/local/bin/python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] not in ((3, 11), (3, 12)))' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  fail "Python 3.11 or 3.12 is needed. Install one, then run ./setup.sh again:
  macOS with MacPorts:   sudo port install python311
  macOS with Homebrew:   brew install python@3.11
  Ubuntu or Debian:      sudo apt install python3.11 python3.11-venv
  Anywhere:              https://www.python.org/downloads/"
fi
echo "Using $PYTHON ($("$PYTHON" --version))"

step "Creating the Python environment (venv)"
if [ -x venv/bin/python ]; then
  echo "venv already exists"
else
  "$PYTHON" -m venv venv
fi

step "Installing packages (the first time takes a few minutes)"
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt
venv/bin/python -m pip install --quiet -e . --no-deps

step "Creating your settings file (.env)"
if [ -f .env ]; then
  echo ".env already exists; left unchanged"
else
  cp .env.example .env
  echo "Created .env from .env.example"
fi

step "Running the tests"
venv/bin/python -m pytest -q || fail "some tests failed. Copy the output above into an issue."

step "Downloading the search model (about 400 MB, once)"
mkdir -p logs
if venv/bin/python -c "
from sentence_transformers import SentenceTransformer
from mel_mcp.config import settings
SentenceTransformer(settings.embedding_model)
" > logs/setup.log 2>&1; then
  echo "Search model ready"
else
  fail "the search model couldn't be downloaded (details in logs/setup.log).
Check your internet connection and run ./setup.sh again."
fi

if [ "$STARTER" = yes ]; then
  step "Loading the Kenya starter pack"
  venv/bin/mel-starter load
fi

cat <<'EOF'

Setup finished.

Next:
  1. In every new terminal, activate the environment first:
       source venv/bin/activate
  2. Connect your assistant (see guide/SETUP.md), for example:
       mel-mcp-config claude-code
EOF
if [ "$STARTER" = no ]; then
  echo "  3. Load your data (see guide/YOUR_DATA.md), or try the Kenya starter pack: ./setup.sh --starter"
fi
