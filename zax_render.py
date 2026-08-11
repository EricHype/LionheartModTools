"""Phase 0 map renderer for Lionheart .zax files.

A single command: `.zax` in, PNG out. No UI, no terrain, no third-party libraries.

Renders the "Tree List" scenery of a map from directly overhead using the game's own
Environments/* sprite art (decoded via mdl16_format.decode_icon), painter's-algorithm
sorted by Position Y, placed with:

    screen_x = entity.Position X - sprite.hotspot_x   (hotspot exactly as stored)
    screen_y = entity.Position Y - sprite.hotspot_y

See docs/map-editor-design.md, "The rendering model" and "Phase 0", for the full spec
this implements. This module does not redesign anything from that doc.

Usage:
    python zax_render.py "<path to .zax>" out.png [--scale 0.5]

Terrain (CPlasmaTileMap) is explicitly out of scope -- the background is a flat colour.
No third-party libraries: the PNG is hand-written with stdlib zlib + struct.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

from resource_format import ResourceNode, parse_resource_text
from mdl16_format import decode_icon, find_header

# Flat dark background colour (RGBA). Terrain is explicitly out of scope.
BACKGROUND = (24, 22, 28, 255)


# ---------------------------------------------------------------------------
# PNG writer -- stdlib only (zlib + struct), RGBA 8-bit, non-interlaced.
# ---------------------------------------------------------------------------

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def write_png(path, width: int, height: int, rgba: bytearray) -> None:
    """Write `rgba` (width*height*4 bytes, row-major top-to-bottom) as a PNG."""
    assert len(rgba) == width * height * 4, "pixel buffer size mismatch"

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for every scanline
        raw += rgba[y * stride: (y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    idat = zlib.compress(bytes(raw), 9)

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += _png_chunk(b"IHDR", ihdr)
    out += _png_chunk(b"IDAT", idat)
    out += _png_chunk(b"IEND", b"")
    Path(path).write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

class Canvas:
    def __init__(self, width: int, height: int, background: tuple[int, int, int, int]):
        self.width = width
        self.height = height
        r, g, b, a = background
        self.pixels = bytearray((r, g, b, a) * (width * height))

    def blit(self, screen_x: int, screen_y: int, icon: dict) -> None:
        """Composite `icon` (as returned by mdl16_format.decode_icon) at top-left
        corner (screen_x, screen_y), clipping against canvas bounds. Pixels with
        alpha 0 are transparent and left untouched.
        """
        w, h = icon["width"], icon["height"]
        rows = icon["rows"]

        x0 = max(0, screen_x)
        x1 = min(self.width, screen_x + w)
        y0 = max(0, screen_y)
        y1 = min(self.height, screen_y + h)
        if x0 >= x1 or y0 >= y1:
            return  # entirely off-canvas; clipped away, not an error

        canvas_width = self.width
        pixels = self.pixels
        for cy in range(y0, y1):
            sprite_row = rows[cy - screen_y]
            row_base = cy * canvas_width * 4
            for cx in range(x0, x1):
                r, g, b, a = sprite_row[cx - screen_x]
                if a == 0:
                    continue
                idx = row_base + cx * 4
                pixels[idx] = r
                pixels[idx + 1] = g
                pixels[idx + 2] = b
                pixels[idx + 3] = a

    def downsample(self, scale: float) -> tuple[int, int, bytearray]:
        """Nearest-neighbour resample. scale==1.0 returns the buffer unchanged."""
        if scale == 1.0:
            return self.width, self.height, self.pixels

        new_w = max(1, round(self.width * scale))
        new_h = max(1, round(self.height * scale))
        src = self.pixels
        src_w = self.width
        out = bytearray(new_w * new_h * 4)
        for oy in range(new_h):
            sy = min(self.height - 1, int(oy / scale))
            src_row_base = sy * src_w * 4
            out_row_base = oy * new_w * 4
            for ox in range(new_w):
                sx = min(src_w - 1, int(ox / scale))
                si = src_row_base + sx * 4
                oi = out_row_base + ox * 4
                out[oi: oi + 4] = src[si: si + 4]
        return new_w, new_h, out


# ---------------------------------------------------------------------------
# Map loading / entity extraction
# ---------------------------------------------------------------------------

def load_root(zax_path) -> ResourceNode:
    data = Path(zax_path).read_bytes()
    text = data.decode("latin-1")
    return parse_resource_text(text)


def iter_level_parts(root: ResourceNode):
    """Yield every `Level Part=` child ResourceNode of the top-level `Tree List`."""
    tree_list = root.get("Tree List")
    if tree_list is None:
        return
    for key, value in tree_list.fields:
        if key == "Level Part" and isinstance(value, ResourceNode):
            yield value


def sprite_path_for_model(data_root: Path, model: str) -> Path:
    # Model paths use forward slashes and may contain literal spaces; each segment
    # maps 1:1 onto a filesystem path component under Cache/Models.
    return data_root / "Cache" / "Models" / (model + ".mdl16")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Lionheart .zax map to a PNG (phase 0, no terrain).")
    parser.add_argument("zax_path", help="path to the .zax map file")
    parser.add_argument("out_png", help="output PNG path")
    parser.add_argument("--scale", type=float, default=1.0, help="downsample factor for the output PNG (default 1.0)")
    parser.add_argument(
        "--data-root",
        default=r"C:\Program Files (x86)\GOG Galaxy\Games\Lionheart - Legacy of the Crusader\data",
        help="game data root (contains Cache/Models/...)",
    )
    args = parser.parse_args()

    start = time.time()
    data_root = Path(args.data_root)

    root = load_root(args.zax_path)

    width_raw = root.get("Width")
    height_raw = root.get("Height")
    if width_raw is None or height_raw is None:
        print("error: top-level Width/Height not found on root CLayerSaveData", file=sys.stderr)
        return 1
    canvas_width = int(float(width_raw))
    canvas_height = int(float(height_raw))

    canvas = Canvas(canvas_width, canvas_height, BACKGROUND)

    # sprite cache: model path -> decoded icon dict, or None for a load that failed
    sprite_cache: dict[str, dict | None] = {}
    # for models whose load failed, why -- so every entity referencing that model
    # (not just the first) gets attributed to the right skip reason
    sprite_fail_reason: dict[str, str] = {}
    skip_reasons: Counter = Counter()

    entities_found = 0
    entities_drawn = 0

    # Collect (Position Y, screen_x, screen_y, model) for entities to draw, then sort
    # by ascending Position Y for the painter's algorithm.
    to_draw: list[tuple[float, str, ResourceNode]] = []

    for part in iter_level_parts(root):
        model = part.get("Model")
        pos_x_raw = part.get("Position X")
        pos_y_raw = part.get("Position Y")

        if not isinstance(model, str) or not model.startswith("Environments/"):
            continue  # not a renderable scenery entity
        if pos_x_raw is None or pos_y_raw is None:
            continue  # doesn't meet the "has both Position X and Position Y" bar
        if model.startswith("Editor/"):
            continue  # defensive; Environments/ prefix already excludes this

        entities_found += 1

        visible = part.get("Visible", "1")
        if visible == "0":
            skip_reasons["invisible (Visible=0)"] += 1
            continue

        try:
            pos_x = float(pos_x_raw)
            pos_y = float(pos_y_raw)
        except (TypeError, ValueError):
            skip_reasons["unparseable Position X/Y"] += 1
            continue

        to_draw.append((pos_y, model, part, pos_x))

    # Painter's algorithm: ascending Position Y.
    to_draw.sort(key=lambda t: t[0])

    for pos_y, model, part, pos_x in to_draw:
        if model not in sprite_cache:
            sprite_path = sprite_path_for_model(data_root, model)
            try:
                sprite_bytes = sprite_path.read_bytes()
            except OSError:
                sprite_cache[model] = None
                sprite_fail_reason[model] = "missing sprite file"
            else:
                try:
                    header = find_header(sprite_bytes)
                    if header is None:
                        sprite_cache[model] = None
                        sprite_fail_reason[model] = "unparseable header"
                    else:
                        sprite_cache[model] = decode_icon(sprite_bytes, header)
                except Exception:
                    sprite_cache[model] = None
                    sprite_fail_reason[model] = "sprite decode error"

        icon = sprite_cache[model]
        if icon is None:
            skip_reasons[sprite_fail_reason[model]] += 1
            continue

        screen_x = round(pos_x - icon["hotspot_x"])
        screen_y = round(pos_y - icon["hotspot_y"])
        canvas.blit(screen_x, screen_y, icon)
        entities_drawn += 1

    out_w, out_h, out_pixels = canvas.downsample(args.scale)
    write_png(args.out_png, out_w, out_h, out_pixels)

    elapsed = time.time() - start

    distinct_sprites_loaded = sum(1 for v in sprite_cache.values() if v is not None)
    total_skipped = sum(skip_reasons.values())

    print(f"entities found:  {entities_found}")
    print(f"entities drawn:  {entities_drawn}")
    print(f"skipped:         {total_skipped}")
    for reason, count in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
        print(f"  - {reason}: {count}")
    print(f"distinct sprites loaded: {distinct_sprites_loaded}")
    print(f"elapsed: {elapsed:.2f}s")
    print(f"output dimensions: {out_w}x{out_h}  (canvas {canvas_width}x{canvas_height}, scale {args.scale})")
    print(f"wrote {args.out_png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
