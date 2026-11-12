"""Read Obsidian Excalidraw notes and render their pen strokes to images."""
import json, math, re
from pathlib import Path

import lzstring
from PIL import Image, ImageDraw

MAX_SIDE = 2000        # pixels; longest side of each image sent to the model
MIN_SCALE = 0.6        # below this, handwriting gets too small to read -> split into tiles
PAD = 40               # drawing units of white space around the content


def load_scene(path):
    """Return the Excalidraw scene dict from an .excalidraw.md (or .excalidraw) file."""
    text = Path(path).read_text(encoding="utf-8")
    if path.suffix == ".excalidraw":
        return json.loads(text)
    m = re.search(r"```compressed-json\s*\n(.*?)\n```", text, re.S)
    if m:
        raw = re.sub(r"\s+", "", m.group(1))
        return json.loads(lzstring.LZString().decompressFromBase64(raw))
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError(f"no Excalidraw data in {path}")


def strokes(scene):
    """Visible freedraw/line elements as lists of absolute points, with width and color."""
    out = []
    for e in scene.get("elements", []):
        if e.get("isDeleted") or e.get("type") not in ("freedraw", "line", "arrow", "draw"):
            continue
        x, y = e["x"], e["y"]
        ang = e.get("angle", 0) or 0
        pts = [(x + px, y + py) for px, py in e.get("points", [])]
        if ang:   # rotate around the element's centre
            cx, cy = x + e.get("width", 0) / 2, y + e.get("height", 0) / 2
            c, s = math.cos(ang), math.sin(ang)
            pts = [(cx + (px - cx) * c - (py - cy) * s, cy + (px - cx) * s + (py - cy) * c) for px, py in pts]
        if pts:
            out.append({"pts": pts, "w": e.get("strokeWidth", 1) or 1, "color": e.get("strokeColor", "#000000"),
                        "pressures": e.get("pressures") or []})
    return out


def bbox(strks):
    xs = [p[0] for s in strks for p in s["pts"]]
    ys = [p[1] for s in strks for p in s["pts"]]
    return min(xs), min(ys), max(xs), max(ys)


def _draw(strks, box, scale):
    x0, y0, x1, y1 = box
    W, H = max(1, int((x1 - x0) * scale)), max(1, int((y1 - y0) * scale))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    for s in strks:
        pts = [((px - x0) * scale, (py - y0) * scale) for px, py in s["pts"]]
        if not any(0 <= px <= W and 0 <= py <= H for px, py in pts):
            continue
        color = s["color"] if s["color"] not in ("transparent", "") else "#000000"
        # Excalidraw pen strokes are thin in drawing units; keep them legible at any scale.
        w = max(2, int(round(s["w"] * 2.2 * max(scale, 0.8))))
        if len(pts) == 1:
            px, py = pts[0]
            d.ellipse([px - w / 2, py - w / 2, px + w / 2, py + w / 2], fill=color)
        else:
            d.line(pts, fill=color, width=w, joint="curve")
    return img


def render(scene):
    """Render a scene to one or more PIL images (tiles, top-to-bottom then left-to-right)."""
    strks = strokes(scene)
    if not strks:
        return []
    x0, y0, x1, y1 = bbox(strks)
    x0, y0, x1, y1 = x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD
    w, h = x1 - x0, y1 - y0
    scale = min(MAX_SIDE / w, MAX_SIDE / h, 2.0)
    if scale >= MIN_SCALE:
        return [_draw(strks, (x0, y0, x1, y1), scale)]

    # Big canvas: tile at a readable scale, with overlap so lines cut at an edge appear whole in one tile.
    scale = MIN_SCALE
    tile = MAX_SIDE / scale
    step = tile * 0.85
    tiles = []
    ty = y0
    while ty < y1:
        tx = x0
        while tx < x1:
            box = (tx, ty, min(tx + tile, x1), min(ty + tile, y1))
            inside = [s for s in strks if any(box[0] <= px <= box[2] and box[1] <= py <= box[3] for px, py in s["pts"])]
            if inside:
                tiles.append(_draw(strks, box, scale))
            tx += step
        ty += step
    return tiles
