"""Frame an imported playable area and render its terrain as a north-up PNG."""

import hashlib
import json
import math
from pathlib import Path
import re
import struct
import tempfile
import zlib

from intake_contract import MAX_MANIFEST_BYTES, bounded, validate_minimap
from terrain_grid import CELLS, VERTICES, TiledTerrain, height_range


def framing(bounds, rotation=0, scale=1, low=0.75, default=1, high=2):
    if not all(math.isfinite(v) for v in (rotation, scale, low, default, high)):
        raise ValueError("Minimap settings must be finite numbers.")
    if not (-360 <= rotation <= 360 and 0.1 <= scale <= 10
            and 0.05 <= low <= default <= high <= 32):
        raise ValueError("Use rotation −360 to 360°, scale 0.1 to 10, and ordered zoom values 0.05 to 32.")
    lower, upper = bounds
    width, height = [b - a for a, b in zip(lower, upper)]
    angle = math.radians(rotation)
    cosine, sine = abs(math.cos(angle)), abs(math.sin(angle))
    # The runtime uses the longer extent. Account for the footprint after rotation.
    extent = [1.10 * (width * cosine + height * sine),
              1.10 * (width * sine + height * cosine)]
    if max(extent) > 10_000_000:
        raise ValueError("The playable area exceeds the host's minimap extent limit.")
    return {
        "center": [(a + b) / 2 for a, b in zip(lower, upper)],
        "extent": [max(100, span) for span in extent],
        "scale": scale,
        "rotation_deg": rotation,
        "zoom": {"min": low, "default": default, "max": high},
        "image": "minimap.png",
        "image_bounds": bounds,
    }


def view_transform(settings, size):
    cx, cy = settings["center"]
    angle = math.radians(settings["rotation_deg"])
    c, s = math.cos(angle), math.sin(angle)
    relative_zoom = (2 + settings["zoom"]["default"]) / 3
    units = max(settings["extent"]) / settings["scale"] / relative_zoom / size
    mid = size / 2

    def project(x, y):
        dx, dy = x - cx, y - cy
        return mid + (dx * c - dy * s) / units, mid + (dx * s + dy * c) / units

    def world(x, y):
        dx, dy = (x - mid) * units, (y - mid) * units
        return cx + dx * c + dy * s, cy - dx * s + dy * c

    return project, world


def terrain_pixels(terrain, width, height, world):
    step = terrain["step_cm"]
    ox, oy, _ = terrain["origin_cm"]
    y_sign = terrain.get("y_sign", -1)
    if "tile_grid" in terrain:
        tiled = TiledTerrain(terrain)
        cells_x, cells_y = tiled.width, tiled.height
        hole_sets = [set(block["holes"]) for block in tiled.blocks]

        def elevations(x, y):
            row, local_y = divmod(y, CELLS)
            col, local_x = divmod(x, CELLS)
            if local_y * CELLS + local_x in hole_sets[tiled.grid[row][col]]:
                return None
            return tiled.sample(x, y), tiled.sample(x + 1, y), tiled.sample(x, max(0, y + y_sign))
    else:
        heights = terrain["height_m"]
        grid = VERTICES if len(heights) == VERTICES**2 else CELLS
        cells_x = cells_y = CELLS
        holes = set(terrain["holes"])

        def elevations(x, y):
            if y * CELLS + x in holes:
                return None
            ny = max(0, y + y_sign) if grid == VERTICES else (y + y_sign) % CELLS
            return heights[y * grid + x], heights[y * grid + (x + 1) % grid], heights[ny * grid + x]

    bottom, top = height_range(terrain)
    elevation_range = max(1, top - bottom)
    pixels = bytearray(width * height)
    for row in range(height):
        for column in range(width):
            x, y = world(column + 0.5, row + 0.5)
            ix = math.floor((x - ox) / step)
            iy = math.floor((y - oy) * y_sign / step)
            value = 24
            if 0 <= ix < cells_x and 0 <= iy < cells_y:
                samples = elevations(ix, iy)
                if samples is not None:
                    z, east, south = samples
                    relief = max(-28, min(28, (east + south - 2 * z) * 5))
                    value = int(94 + 115 * (z - bottom) / elevation_range + relief)
            pixels[row * width + column] = value
    return pixels


def terrain_png(terrain, bounds):
    (left, top), (right, bottom) = bounds
    span_x, span_y = right - left, bottom - top
    units = max(span_x, span_y) / 384
    width, height = max(1, round(span_x / units)), max(1, round(span_y / units))
    pixels = terrain_pixels(
        terrain, width, height,
        lambda x, y: (left + x * span_x / width, top + y * span_y / height),
    )
    return grayscale_png(pixels, 8, width, height)


def grayscale_png(pixels, depth, width=256, height=256):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

    stride = width * depth // 8
    rows = b"".join(b"\0" + pixels[i * stride:(i + 1) * stride] for i in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def export_host_minimap(manifest, settings, map_id, parent):
    """Write merge fragments in a new folder, leaving the draft and host untouched."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", map_id):
        raise ValueError("Enter the portable map ID (up to 40 letters, digits or underscores, starting with a letter).")
    validate_minimap(settings)
    source = bounded(manifest.parent / "minimap.png", 16 * 1024 * 1024)
    digest = hashlib.sha256(source).hexdigest()
    header = json.loads(bounded(manifest, MAX_MANIFEST_BYTES))
    if digest != header["files"]["minimap.png"]:
        raise ValueError("Minimap image no longer matches the intake. Reopen a verified intake.")
    if settings["image_bounds"] != header["minimap"]["image_bounds"]:
        raise ValueError("Image bounds no longer match the verified terrain artwork.")
    inventory = {"path": f"lua/{map_id}/minimap.png", "bytes": len(source), "sha256": digest}
    target = Path(parent) / f"{map_id}-minimap"
    with tempfile.TemporaryDirectory(dir=parent) as work:
        staged = Path(work) / "export"
        staged.mkdir()
        (staged / "minimap.png").write_bytes(source)
        for name, data in (
            ("map-minimap.json", {"minimap": settings}),
            ("manifest-minimap.json", {"files": [inventory]}),
        ):
            (staged / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            raise FileExistsError(f"{target.name} already exists. Choose another folder.")
        staged.rename(target)
    return target
