#!/usr/bin/env python3
"""Transcribe handwriting in Obsidian Excalidraw drawings with a vision model on OpenRouter.

For each Excalidraw drawing in the vault (any note with `excalidraw-plugin:` in its frontmatter), the pen strokes are rendered to PNG and sent to the
model; the text is written to a companion note next to the drawing:

    Notes/Drawing 2026-11-08 10.49.09.excalidraw.md     (your drawing, never modified)
    Notes/transcripts/Drawing 2026-11-08 10.49.09.transcript.md     (written by this script)

Only new or changed drawings are sent, so re-running is cheap on the free daily quota.

API key: $OPENROUTER_API_KEY, or the file ~/.config/excalidraw-ocr/openrouter.key
"""
import argparse, base64, hashlib, io, json, os, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime
from pathlib import Path

import render

DEFAULT_VAULT = Path(os.environ.get("EXCALIDRAW_OCR_VAULT", Path.home() / "Notes"))
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl:free"
FALLBACK_MODEL = "google/gemma-4-31b-it:free"
KEY_FILE = Path.home() / ".config/excalidraw-ocr/openrouter.key"
STATE_FILE = Path.home() / ".local/state/excalidraw-ocr/state.json"
API = "https://openrouter.ai/api/v1/chat/completions"
MIN_INTERVAL = 4.0        # seconds between requests (free models allow ~20/min)

PROMPT = """These images are handwritten notes, drawn with a pen in a note-taking app. \
If there are several images, they are overlapping tiles of one large page, in reading order \
(top to bottom, then left to right); text near tile edges may appear twice, so include it once.

Transcribe the handwriting exactly as written, as Markdown:
- Keep headings and bullet points (a dash or dot at the start of a line is a bullet). When a sentence
  wraps onto the next line only because the page ran out of room, join it into one line.
- Keep the writer's wording and spelling; do not summarize, correct, or add anything.
- Use [?] for a word you cannot read, or [word?] for your best guess.
- If something is a drawing or diagram rather than text, describe it briefly in *italics* on its own line.

Output only the transcription, with no preamble."""


def api_key():
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text().strip()
    if not k:
        sys.exit(f"No OpenRouter API key. Put it in {KEY_FILE} (chmod 600) or set OPENROUTER_API_KEY.")
    return k


def png_b64(img):
    buf = io.BytesIO()
    img.convert("L").save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def call_model(key, model, images, retries=4):
    content = [{"type": "text", "text": PROMPT}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64(im)}"}} for im in images]
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                       "temperature": 0}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "X-Title": "excalidraw-ocr"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            if "error" in data:
                raise RuntimeError(data["error"].get("message", str(data["error"])))
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if not text:
                raise RuntimeError("empty response")
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (429, 502, 503) and attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f"    {e.code} from OpenRouter, retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(10)
                continue
            raise RuntimeError(f"network error: {e}") from None


def strokes_hash(scene):
    """Changes only when the visible drawing changes (not when Obsidian re-saves metadata)."""
    s = render.strokes(scene)
    return hashlib.sha256(json.dumps([(x["pts"], x["w"], x["color"]) for x in s]).encode()).hexdigest()[:16]


def is_drawing(path):
    """Excalidraw notes are recognised by their frontmatter, whatever the file is named
    (Obsidian lets you rename "Drawing ... .excalidraw.md" to anything)."""
    if path.name.endswith(".transcript.md"):
        return False
    if path.name.endswith((".excalidraw.md", ".excalidraw")):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(2000)
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    return "excalidraw-plugin:" in (head[:end] if end > 0 else head)


TRANSCRIPT_DIR = "transcripts"     # created next to each drawing


def transcript_path(drawing):
    stem = drawing.name.removesuffix(".md").removesuffix(".excalidraw")
    return drawing.parent / TRANSCRIPT_DIR / (stem + ".transcript.md")


def migrate_old_transcript(drawing):
    """Earlier versions wrote the transcript right beside the drawing; move it into transcripts/."""
    stem = drawing.name.removesuffix(".md").removesuffix(".excalidraw")
    old, new = drawing.with_name(stem + ".transcript.md"), transcript_path(drawing)
    if old.exists() and not new.exists():
        new.parent.mkdir(exist_ok=True)
        old.rename(new)
        return True
    return False


def write_transcript(drawing, vault, text, model, tiles):
    link = drawing.relative_to(vault).as_posix().removesuffix(".md")
    out = transcript_path(drawing)
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        "---\n"
        f"source: \"[[{link}]]\"\n"
        f"transcribed: {datetime.now().isoformat(timespec='minutes')}\n"
        f"model: {model}\n"
        "tags: [handwriting-transcript]\n"
        "---\n"
        f"> Transcribed from [[{link}|the drawing]]"
        f"{f' ({tiles} tiles)' if tiles > 1 else ''}. Edits here are overwritten when the drawing changes.\n\n"
        f"{text}\n", encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, help="drawings to process (default: every drawing in the vault)")
    ap.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--fallback-model", default=FALLBACK_MODEL, help="tried if the main model fails ('' = none)")
    ap.add_argument("--force", action="store_true", help="re-transcribe even if the drawing hasn't changed")
    ap.add_argument("--limit", type=int, default=0, help="process at most N drawings this run")
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent, save tile PNGs, no API calls")
    ap.add_argument("--watch", type=float, default=0, metavar="MIN", help="keep running, re-checking every MIN minutes")
    ap.add_argument("--after", default=os.environ.get("EXCALIDRAW_OCR_AFTER", ""),
                    help="shell command to run after new transcripts are written, e.g. a Nextcloud "
                         "'occ files:scan' when writing straight into Nextcloud's data folder")
    a = ap.parse_args()

    vault = a.vault.expanduser().resolve()
    key = None if a.dry_run else api_key()
    while True:
        run(a, vault, key)
        if not a.watch:
            break
        time.sleep(a.watch * 60)


def run(a, vault, key):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        state = {}

    drawings = [p.expanduser().resolve() for p in a.paths] if a.paths else sorted(
        p for p in vault.rglob("*.md")
        if ".obsidian" not in p.parts and ".trash" not in p.parts and is_drawing(p))

    todo = []
    for d in drawings:
        try:
            scene = render.load_scene(d)
        except Exception as e:
            print(f"skip (unreadable): {d.relative_to(vault)}: {e}")
            continue
        h = strokes_hash(scene)
        rel = d.relative_to(vault).as_posix()
        if not a.dry_run and migrate_old_transcript(d):
            print(f"moved transcript into {transcript_path(d).relative_to(vault)}")
        if not a.force and state.get(rel) == h and transcript_path(d).exists():
            continue
        todo.append((d, rel, scene, h))

    if a.limit:
        todo = todo[: a.limit]
    print(f"{len(drawings)} drawings, {len(todo)} new or changed", flush=True)

    last_call = 0.0
    done = failed = 0
    for d, rel, scene, h in todo:
        tiles = render.render(scene)
        if not tiles:
            print(f"  {rel}: empty, skipped")
            state[rel] = h
            continue
        print(f"  {rel}: {len(tiles)} image{'s' if len(tiles) > 1 else ''}", flush=True)
        if a.dry_run:
            outdir = Path("dry-run") / Path(rel).with_suffix("")
            outdir.mkdir(parents=True, exist_ok=True)
            for i, t in enumerate(tiles):
                t.save(outdir / f"tile{i + 1}.png")
            print(f"    saved tiles to {outdir}/")
            continue

        text, model_used = None, None
        for model in [a.model] + ([a.fallback_model] if a.fallback_model else []):
            wait = MIN_INTERVAL - (time.time() - last_call)
            if wait > 0:
                time.sleep(wait)
            last_call = time.time()
            try:
                text, model_used = call_model(key, model, tiles), model
                break
            except Exception as e:
                print(f"    {model} failed: {e}", flush=True)
        if text is None:
            failed += 1
            continue
        out = write_transcript(d, vault, text, model_used, len(tiles))
        state[rel] = h
        STATE_FILE.write_text(json.dumps(state, indent=1))
        done += 1
        print(f"    -> {out.relative_to(vault)} ({len(text)} chars, {model_used})", flush=True)

    STATE_FILE.write_text(json.dumps(state, indent=1))
    if todo and not a.dry_run:
        print(f"done: {done} transcribed, {failed} failed")
    if done and a.after:
        r = subprocess.run(a.after, shell=True)
        print(f"after-command exited {r.returncode}")


if __name__ == "__main__":
    main()
