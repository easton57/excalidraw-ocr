# excalidraw-ocr

Transcribes handwriting in Obsidian Excalidraw drawings using a vision model on OpenRouter.
Each drawing's pen strokes are rendered to PNG and the transcription is written to a
companion note in a `transcripts/` folder next to the drawing. Drawings are never modified,
and only new or changed drawings are sent.

## Setup

Put your OpenRouter API key in `~/.config/excalidraw-ocr/openrouter.key` (chmod 600),
or set `OPENROUTER_API_KEY`.

`run.sh` creates `.venv` and installs dependencies on first run (uses `uv` if available).

## Usage

```sh
./run.sh                    # all drawings in the vault
./run.sh --watch 10         # keep running, re-check every 10 minutes
./run.sh --force PATH...    # redo specific drawings
./run.sh --dry-run          # render to ./dry-run/ without calling the API
```

The vault defaults to `~/Notes`; override it with `--vault` or
`EXCALIDRAW_OCR_VAULT`. See `./run.sh --help` for all options.

## Running as a service

`excalidraw-ocr.service` is a systemd unit that runs `--watch 10`. Install instructions
are in the comments at the top of the file.
