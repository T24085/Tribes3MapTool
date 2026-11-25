"""Full-resolution metric heightmaps for the private intake exchange."""

from hashlib import sha256
from io import BytesIO
import json
import re
import struct

from intake_contract import bounded
from minimap import grayscale_png
from terrain_grid import CELLS, VERTICES, TiledTerrain, height_range


RECEIPT = 'terrain-export.json'
MAX_TILES_PER_AXIS = 64
MAX_TILES = MAX_TILES_PER_AXIS**2
MAX_RECEIPT_BYTES = 512 * 1024
MAX_TILE_BYTES = 256 * 1024
MAX_TILE_TOTAL = 128 * 1024 * 1024
TILE_PATH = re.compile(r'assets/terrain/height-[0-9]{4}\.png')
DIGEST = re.compile(r'[a-f0-9]{64}')
PNG_HEADER = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000101000001011000000000')
IDENTITY = dict(kind='tribes-private-heightmaps', format=1, units='metres', tile_vertices=VERTICES)


def terrain_frame(terrain):
    low, high = height_range(terrain)
    scale = terrain['cm_per_m'] / 100
    return dict(origin_m=[value / 100 for value in terrain['origin_cm']],
                step_m=terrain['step_cm'] / 100, raster_y=terrain.get('y_sign', -1),
                height_offset_m=low * scale, height_step_m=(high - low) * scale / 65535)


def heightmap_png(heights, low, high):
    if low == high:
        pixels = bytes(2 * VERTICES**2)
    else:
        multiplier = 65535 / (high - low)
        samples = heights
        if len(heights) == CELLS**2:
            # The T2 raster stores 256 samples per axis and wraps at the final mesh vertex.
            samples = (heights[(y % CELLS) * CELLS + x % CELLS]
                       for y in range(VERTICES) for x in range(VERTICES))
        pixels = struct.pack(f'>{VERTICES**2}H', *(round((value - low) * multiplier) for value in samples))
    return grayscale_png(pixels, 16, VERTICES, VERTICES)


def write_heightmaps(bundle, terrain, intake_sha256):
    terrain_tiles = TiledTerrain(terrain) if 'tile_grid' in terrain else None
    low, high = height_range(terrain)
    grid, files, variants = [], {}, {}
    total = 0
    for y in range(terrain_tiles.rows if terrain_tiles else 1):
        row = []
        for x in range(terrain_tiles.cols if terrain_tiles else 1):
            variant = terrain_tiles.tile_key(x, y) if terrain_tiles else ()
            if variant not in variants:
                heights = (terrain_tiles.mesh_block(x, y) if terrain_tiles else terrain)['height_m']
                data = heightmap_png(heights, low, high)
                total += len(data)
                if total > MAX_TILE_TOTAL:
                    raise ValueError('The heightmaps exceed the private intake export size limit.')
                name = f'assets/terrain/height-{len(files):04d}.png'
                bundle.writestr(name, data)
                files[name] = sha256(data).hexdigest()
                variants[variant] = name
            row.append(variants[variant])
        grid.append(row)
    receipt = dict(IDENTITY, **terrain_frame(terrain), intake_sha256=intake_sha256,
                   tile_grid=grid, files=files)
    bundle.writestr(RECEIPT, json.dumps(receipt, separators=(',', ':'), allow_nan=False).encode('utf-8'))


def validate_receipt(raw, manifest, _header):
    receipt = json.loads(raw)
    identity = dict(IDENTITY, intake_sha256=sha256(manifest).hexdigest())
    if (not isinstance(receipt, dict) or receipt.keys() != {
            *identity, 'origin_m', 'step_m', 'raster_y', 'height_offset_m', 'height_step_m', 'tile_grid', 'files'}
            or any(receipt[key] != value for key, value in identity.items())
            or any(type(receipt[key]) is not int for key in ('format', 'tile_vertices'))):
        raise ValueError('Heightmaps do not belong to this private intake.')
    grid, files = receipt['tile_grid'], receipt['files']
    if (not isinstance(files, dict) or not 1 <= len(files) <= MAX_TILES
            or any(not TILE_PATH.fullmatch(path) or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                   for path, digest in files.items())
            or not isinstance(grid, list) or not 1 <= len(grid) <= MAX_TILES_PER_AXIS
            or not isinstance(grid[0], list) or not 1 <= len(grid[0]) <= MAX_TILES_PER_AXIS
            or any(not isinstance(row, list) or len(row) != len(grid[0]) or
                   any(not isinstance(path, str) or path not in files for path in row) for row in grid)
            or {name for row in grid for name in row} != files.keys()):
        raise ValueError('Invalid heightmap tile inventory.')
    return receipt


def verify_files(root, receipt, terrain):
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError('Install Pillow in the Python environment used by the map launcher.') from exc

    expected = terrain_frame(terrain)
    grid, files = receipt['tile_grid'], receipt['files']
    layout = terrain.get('tile_grid', [[0]])
    if (not isinstance(receipt['origin_m'], list) or any(
            type(value) not in (int, float) for value in (*receipt['origin_m'], receipt['step_m'],
                                                         receipt['height_offset_m'], receipt['height_step_m']))
            or type(receipt['raster_y']) is not int
            or any(receipt[key] != value for key, value in expected.items())
            or (len(grid), len(grid[0])) != (len(layout), len(layout[0]))):
        raise ValueError('Heightmap frame does not match the intake terrain.')
    total = 0
    for name, checksum in files.items():
        tile = root / name
        if not tile.is_file() or tile.resolve(strict=True) != tile:
            raise ValueError('Missing or linked heightmap tile: ' + name)
        data = bounded(tile, MAX_TILE_BYTES)
        total += len(data)
        if total > MAX_TILE_TOTAL or not data.startswith(PNG_HEADER):
            raise ValueError('Invalid 16-bit heightmap tile: ' + name)
        if sha256(data).hexdigest() != checksum:
            raise ValueError('Heightmap checksum mismatch: ' + name)
        try:
            with Image.open(BytesIO(data), formats=('PNG',)) as image:
                image.verify()
            with Image.open(BytesIO(data), formats=('PNG',)) as image:
                image.load()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValueError('Unreadable heightmap PNG: ' + name) from exc
