#!/usr/bin/env bash
# Transcribe handwriting in Obsidian Excalidraw drawings (only new/changed ones).
#   ./run.sh                    all drawings in the vault (default ~/Notes,
#                               or set EXCALIDRAW_OCR_VAULT=/path/to/vault)
#   ./run.sh --watch 10         keep running, check every 10 minutes
#   ./run.sh --force PATH...    redo specific drawings
#   ./run.sh --dry-run          render to ./dry-run/ without calling the API
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$HERE/.venv/bin/python" ]; then
  if command -v uv >/dev/null; then
    uv venv -q "$HERE/.venv"
    uv pip install -q --python "$HERE/.venv/bin/python" -r "$HERE/requirements.txt"
  else
    python3 -m venv "$HERE/.venv"
    "$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt"
  fi
fi
exec "$HERE/.venv/bin/python" "$HERE/excalidraw_ocr.py" "$@"
