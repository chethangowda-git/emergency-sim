#!/usr/bin/env bash
# Linux/macOS launcher: creates .venv if missing, installs requirements, opens the dashboard.
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      echo "$py"; return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  PY="$(find_python)" || { echo "Python 3.11 or newer is required: https://www.python.org/downloads/"; exit 1; }
  echo "Creating virtual environment with $PY ..."
  "$PY" -m venv .venv
fi

HASH="$(.venv/bin/python -c "import hashlib; print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())")"
if [ "$(cat .venv/.requirements.sha 2>/dev/null || true)" != "$HASH" ]; then
  echo "Installing requirements (first time takes a few minutes) ..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  echo "$HASH" > .venv/.requirements.sha
fi

URL="http://localhost:8501"
( sleep 4; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) >/dev/null 2>&1 &
echo "Starting dashboard at $URL (close this window to stop it) ..."
exec .venv/bin/python -m streamlit run dashboard/app.py --server.headless true --server.port 8501
