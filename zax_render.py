"""Phase 0(.5) map renderer for Lionheart .zax files.

A single command: `.zax` in, PNG out. No UI, no third-party libraries.

Renders the "Tree List" scenery of a map from directly overhead using the game's own
Environments/* sprite art (decoded via mdl16_format.decode_icon), painter's-algorithm
sorted by Position Y, placed with:

    screen_x = entity.Position X - sprite.hotspot_x   (hotspot exactly as stored)
    screen_y = entity.Position Y - sprite.hotspot_y

Underneath that, `Plasma Ground=CPlasmaTileMap` terrain is drawn: the `Elevations` byte
at each grid vertex selects a ground texture, bilinearly blended between each tile's four
corners (as the engine does), then modulated by the `Light Overlay` vertex-colour grid.

See docs/map-editor-design.md, "The rendering model" and "Terrain", for the full spec
this implements, the evidence behind it, and the one step (byte -> texture index) that is
inferred rather than read out of the binary. This module does not redesign anything from
that doc.

Usage:
    python zax_render.py "<path to .zax>" out.png [--scale 0.5] [--no-terrain]
                                                  [--texture-mode single|elevation]

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

# Flat dark background colour (RGBA). Used when terrain is disabled/unavailable.
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

    def fill_row(self, y: int, row_rgba: bytes) -> None:
        """Overwrite one full scanline with `row_rgba` (width*4 bytes). Used by terrain
        rendering, which produces one opaque row at a time."""
        stride = self.width * 4
        base = y * stride
        self.pixels[base: base + stride] = row_rgba

    def fill_span(self, y: int, x: int, row_rgba: bytes) -> None:
        """Overwrite part of a scanline, starting at column `x`. Lets terrain rendering
        redraw just a region without disturbing the rest of the canvas."""
        base = (y * self.width + x) * 4
        self.pixels[base: base + len(row_rgba)] = row_rgba

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
# Terrain (Plasma Ground = CPlasmaTileMap)
# ---------------------------------------------------------------------------
#
# See docs/map-editor-design.md, "Terrain", for the full derivation. Three data sources,
# all on a `Width/64 + 1` by `Height/64 + 1` vertex grid:
#   - `Texture 0..N-1`: 128x128 raw-16bpp ground tiles, sampled with wraparound.
#   - `Elevations Row N`: 1 byte per vertex. Despite the name this is the TEXTURE INDEX,
#     not a height -- the deserialiser reads these rows into the plane the engine samples
#     to pick a tile's texture. Scaled here as `byte * Num Textures // 256`, which is the
#     one step inferred rather than read from the binary (see the doc).
#   - `Light Overlay Row N`: 3 bytes (R,G,B) per vertex; 128 is neutral. Modulates the
#     result: out = clamp(texel * light / 128).
#
# Both the index and the light are bilinearly interpolated between each tile's four
# corner vertices. That is not cosmetic smoothing -- the engine does exactly this
# (FUN_005ed990), and picking one texture per cell instead makes any mapping look like
# blocky noise regardless of whether the mapping is correct.

GRID_CELL = 64  # world units between adjacent Light Overlay / Elevation vertices


def texture_path_for_name(data_root: Path, name: str) -> Path:
    # Texture names use forward slashes for subdirectories, e.g. "Rethgorad/grnd3".
    return data_root / "Cache" / "Textures" / (name + ".frm16")


def load_ground_texture(data_root: Path, name: str) -> dict | None:
    """Decode a ground texture (raw-16bpp .frm16) to the same dict shape as
    mdl16_format.decode_icon. Returns None on any load/decode failure."""
    try:
        data = texture_path_for_name(data_root, name).read_bytes()
    except OSError:
        return None
    try:
        return decode_icon(data)
    except Exception:
        return None


def parse_light_grid(node: ResourceNode, grid_cols: int, grid_rows: int):
    """Parse `Light Overlay Row 0..grid_rows-1` into a grid_rows x grid_cols list of
    (r,g,b) tuples. Returns None if any row is missing, mismatched in length, or not
    valid hex -- modulation is then skipped rather than the render failing."""
    grid = []
    for ry in range(grid_rows):
        raw = node.get(f"Light Overlay Row {ry}")
        if not isinstance(raw, str):
            return None
        hexstr = raw.strip()
        if len(hexstr) != grid_cols * 6:  # 3 bytes/vertex, 2 hex chars/byte
            return None
        try:
            b = bytes.fromhex(hexstr)
        except ValueError:
            return None
        grid.append([(b[3 * c], b[3 * c + 1], b[3 * c + 2]) for c in range(grid_cols)])
    return grid


def parse_elevation_grid(node: ResourceNode, grid_cols: int, grid_rows: int):
    """Parse `Elevations Row 0..grid_rows-1` into a grid_rows x grid_cols list of ints.
    Returns None on any missing/mismatched/invalid row, so texture selection falls back
    to Texture 0 rather than the render failing."""
    grid = []
    for ry in range(grid_rows):
        raw = node.get(f"Elevations Row {ry}")
        if not isinstance(raw, str):
            return None
        hexstr = raw.strip()
        if len(hexstr) != grid_cols * 2:  # 1 byte/vertex
            return None
        try:
            grid.append(list(bytes.fromhex(hexstr)))
        except ValueError:
            return None
    return grid


def render_terrain(canvas: Canvas, data_root: Path, plasma_node: ResourceNode,
                   elevation_textures: bool = False,
                   region: tuple[int, int, int, int] | None = None) -> dict:
    """Tile `Texture 0` across the whole canvas, modulated by the light overlay
    (bilinear between grid vertices), writing directly into `canvas`. Meant to run
    before any entity sprites are blitted, so terrain ends up underneath them.

    Returns a summary dict: texture name, grid dimensions, whether light modulation
    was applied, and a `note` on anything that fell back.
    """
    summary = {
        "texture": None, "grid_cols": None, "grid_rows": None,
        "light_modulation": False, "note": None,
    }

    try:
        num_tex = int(plasma_node.get("Num Textures") or 0)
    except ValueError:
        num_tex = 0
    tex_names = [plasma_node.get(f"Texture {i}") for i in range(max(num_tex, 1))]
    tex_names = [t for t in tex_names if t]
    if not tex_names:
        summary["note"] = "no 'Texture N' fields on Plasma Ground"
        return summary
    summary["texture"] = tex_names[0]

    # Load every declared texture; fall back to texture 0 for any that fail so a single
    # bad entry degrades one region rather than the whole map.
    loaded = [load_ground_texture(data_root, t) for t in tex_names]
    if loaded[0] is None:
        summary["note"] = f"failed to load/decode texture {tex_names[0]!r}"
        return summary
    loaded = [t if t is not None else loaded[0] for t in loaded]
    summary["textures_loaded"] = sum(1 for t in loaded if t is not None)

    tex_w, tex_h = loaded[0]["width"], loaded[0]["height"]
    # Per-channel row arrays of plain ints (not (r,g,b,a) tuples) to keep the hot loop
    # cheap, doubled so a 64px cell slice at any tiling phase is a plain slice.
    def channel_rows(tex, ch):
        return [[px[ch] for px in row] * 2 for row in tex["rows"]]
    tex_r = [channel_rows(t, 0) for t in loaded]
    tex_g = [channel_rows(t, 1) for t in loaded]
    tex_b = [channel_rows(t, 2) for t in loaded]

    width, height = canvas.width, canvas.height
    grid_cols = width // GRID_CELL + 1
    grid_rows = height // GRID_CELL + 1
    summary["grid_cols"] = grid_cols
    summary["grid_rows"] = grid_rows

    light_grid = parse_light_grid(plasma_node, grid_cols, grid_rows)
    if light_grid is None:
        summary["note"] = "Light Overlay missing or dimensions didn't match the grid; modulation skipped"
    summary["light_modulation"] = light_grid is not None

    # Texture selection from the elevation byte.
    #
    # Confirmed in Lionheart.exe: the elevation rows load into the plane at object offset
    # 0x2c40 (`LEA ECX,[ESI+0x2c40]` before the read in the deserialiser), and that same
    # plane is what FUN_005ed3e0 samples to pick a tile's texture. So elevation IS the
    # selector.
    #
    # Crucially the engine does NOT pick one texture per cell. FUN_005ed990 reads the
    # tile's FOUR corner values and interpolates across the tile, so the index varies
    # per pixel and regions blend smoothly into each other. Choosing per cell instead
    # makes any mapping look like blocky noise -- that mistake previously led to this
    # approach being written off as disproven.
    elev_grid = None
    if len(loaded) > 1:
        elev_grid = parse_elevation_grid(plasma_node, grid_cols, grid_rows)
    if elev_grid and not elevation_textures:
        elev_grid = None  # caller asked for flat single-texture ground
    summary["texture_selection"] = (
        "elevation index, 4-corner blend" if elev_grid else "single (Texture 0)")
    n_tex = len(loaded)
    tex_choice = (
        [[min(e * n_tex // 256, n_tex - 1) for e in row] for row in elev_grid]
        if elev_grid else None)

    FX = [i / GRID_CELL for i in range(GRID_CELL)]  # bilinear x-weights, reused every cell

    # `region` limits the redraw to a rectangle, leaving the rest of the canvas alone.
    # Terrain painting needs it: a full redraw costs 2.4s on Test Pocket and 9.7s on
    # Gate District, while a brush-sized region is ~0.09s whatever the map size. With
    # region=None the loops below cover the whole canvas exactly as before.
    if region is None:
        rx0, ry0, rx1, ry1 = 0, 0, width, height
    else:
        rx0, ry0, rx1, ry1 = region
        rx0 = max(0, min(width, int(rx0)))
        rx1 = max(rx0, min(width, int(rx1)))
        ry0 = max(0, min(height, int(ry0)))
        ry1 = max(ry0, min(height, int(ry1)))
    span_w = rx1 - rx0
    if span_w <= 0 or ry1 <= ry0:
        return summary
    alpha_row = b"\xff" * span_w

    for y in range(ry0, ry1):
        ty = y % tex_h
        if tex_choice is None:
            reps = -(-(rx1 + tex_w) // tex_w)  # ceil, with room for the phase offset
            ph = rx0 % tex_w
            tiled_r = (tex_r[0][ty] * reps)[ph:ph + span_w]
            tiled_g = (tex_g[0][ty] * reps)[ph:ph + span_w]
            tiled_b = (tex_b[0][ty] * reps)[ph:ph + span_w]
        else:
            # Per-pixel index, bilinearly blended between the tile's four corner values,
            # matching FUN_005ed990. Done per cell so the interpolation weights and the
            # texture rows are hoisted out of the inner loop.
            gy = min(y // GRID_CELL, len(tex_choice) - 1)
            gy1 = min(gy + 1, len(tex_choice) - 1)
            fy = (y - gy * GRID_CELL) / GRID_CELL
            top_row, bot_row = tex_choice[gy], tex_choice[gy1]
            rows_r = [t[ty] for t in tex_r]
            rows_g = [t[ty] for t in tex_g]
            rows_b = [t[ty] for t in tex_b]
            tiled_r, tiled_g, tiled_b = [], [], []
            last = len(top_row) - 1
            for cx in range(rx0 // GRID_CELL, grid_cols):
                x0 = cx * GRID_CELL
                if x0 >= rx1:
                    break
                span = min(GRID_CELL, width - x0)
                if x0 + span <= rx0:
                    continue
                # Clip this cell to the region; k stays the cell-relative index so the
                # blend weights and texture phase are unchanged by clipping.
                k_lo = max(0, rx0 - x0)
                k_hi = min(span, rx1 - x0)
                c0, c1 = min(cx, last), min(cx + 1, last)
                # vertical lerp at the cell's two vertex columns
                left = top_row[c0] + (bot_row[c0] - top_row[c0]) * fy
                right = top_row[c1] + (bot_row[c1] - top_row[c1]) * fy
                delta = right - left
                ph = x0 % tex_w
                for k in range(k_lo, k_hi):
                    ti = int(left + delta * FX[k] + 0.5)
                    if ti < 0:
                        ti = 0
                    elif ti >= n_tex:
                        ti = n_tex - 1
                    tp = ph + k
                    tiled_r.append(rows_r[ti][tp])
                    tiled_g.append(rows_g[ti][tp])
                    tiled_b.append(rows_b[ti][tp])

        if light_grid is not None:
            ry0 = min(y // GRID_CELL, grid_rows - 2)
            fy = (y - ry0 * GRID_CELL) / GRID_CELL
            top = light_grid[ry0]
            bottom = light_grid[ry0 + 1]

            lr_row = [0.0] * span_w
            lg_row = [0.0] * span_w
            lb_row = [0.0] * span_w
            for cx in range(rx0 // GRID_CELL, grid_cols - 1):
                x0 = cx * GRID_CELL
                x1 = min(x0 + GRID_CELL, width)
                if x0 >= rx1:
                    break
                if x1 <= rx0:
                    continue
                n = x1 - x0
                tl_r, tl_g, tl_b = top[cx]
                tr_r, tr_g, tr_b = top[cx + 1]
                bl_r, bl_g, bl_b = bottom[cx]
                br_r, br_g, br_b = bottom[cx + 1]
                # vertical lerp at the two vertex columns bounding this cell
                l_r = tl_r + (bl_r - tl_r) * fy
                l_g = tl_g + (bl_g - tl_g) * fy
                l_b = tl_b + (bl_b - tl_b) * fy
                r_r = tr_r + (br_r - tr_r) * fy
                r_g = tr_g + (br_g - tr_g) * fy
                r_b = tr_b + (br_b - tr_b) * fy
                d_r, d_g, d_b = r_r - l_r, r_g - l_g, r_b - l_b
                # Clip to the region and shift into region-relative indices, so the
                # weights themselves are identical to an unclipped render.
                k_lo = max(0, rx0 - x0)
                k_hi = min(n, rx1 - x0)
                dst_lo = x0 + k_lo - rx0
                dst_hi = x0 + k_hi - rx0
                lr_row[dst_lo:dst_hi] = [l_r + d_r * FX[k] for k in range(k_lo, k_hi)]
                lg_row[dst_lo:dst_hi] = [l_g + d_g * FX[k] for k in range(k_lo, k_hi)]
                lb_row[dst_lo:dst_hi] = [l_b + d_b * FX[k] for k in range(k_lo, k_hi)]

            row_bytes = bytearray(span_w * 4)
            row_bytes[0::4] = bytes(min(255, int(t * l / 128)) for t, l in zip(tiled_r, lr_row))
            row_bytes[1::4] = bytes(min(255, int(t * l / 128)) for t, l in zip(tiled_g, lg_row))
            row_bytes[2::4] = bytes(min(255, int(t * l / 128)) for t, l in zip(tiled_b, lb_row))
            row_bytes[3::4] = alpha_row
        else:
            row_bytes = bytearray(span_w * 4)
            row_bytes[0::4] = bytes(tiled_r)
            row_bytes[1::4] = bytes(tiled_g)
            row_bytes[2::4] = bytes(tiled_b)
            row_bytes[3::4] = alpha_row

        canvas.fill_span(y, rx0, row_bytes)

    return summary


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
    parser = argparse.ArgumentParser(description="Render a Lionheart .zax map to a PNG.")
    parser.add_argument("zax_path", help="path to the .zax map file")
    parser.add_argument("out_png", help="output PNG path")
    parser.add_argument("--scale", type=float, default=1.0, help="downsample factor for the output PNG (default 1.0)")
    parser.add_argument(
        "--no-terrain", action="store_true",
        help="skip Plasma Ground terrain rendering; reproduces phase-0 flat-background output",
    )
    parser.add_argument("--texture-mode", choices=["single", "elevation"],
                        default="elevation",
                        help="ground texture selection: 'elevation' (default) treats the "
                             "elevation byte as a texture index and blends it between each "
                             "tile's four corners, as the engine does; 'single' tiles "
                             "Texture 0 across the whole map")
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

    terrain_summary = None
    if not args.no_terrain:
        plasma_node = root.get("Plasma Ground")
        if isinstance(plasma_node, ResourceNode):
            terrain_summary = render_terrain(canvas, data_root, plasma_node, elevation_textures=args.texture_mode == 'elevation')
        else:
            terrain_summary = {"note": "no 'Plasma Ground' node on the map root"}

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
    if args.no_terrain:
        print("terrain: disabled (--no-terrain)")
    elif terrain_summary is None:
        print("terrain: (unexpected) not rendered")
    elif terrain_summary.get("texture") is None:
        print(f"terrain: not rendered ({terrain_summary['note']})")
    else:
        print(
            f"terrain: {terrain_summary.get('textures_loaded', 1)} texture(s), "
            f"selection={terrain_summary.get('texture_selection', 'single (Texture 0)')}, "
            f"grid={terrain_summary['grid_cols']}x{terrain_summary['grid_rows']}, "
            f"light modulation={'yes' if terrain_summary['light_modulation'] else 'no'}"
        )
        if terrain_summary["note"]:
            print(f"  note: {terrain_summary['note']}")
    print(f"elapsed: {elapsed:.2f}s")
    print(f"output dimensions: {out_w}x{out_h}  (canvas {canvas_width}x{canvas_height}, scale {args.scale})")
    print(f"wrote {args.out_png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
