"""Sample and place tiled heightfields shared by the reader and editor."""

import struct


CELLS = 256
VERTICES = CELLS + 1
MAX_PREVIEW_SIDE = 1025
EMPTY_LEVEL_MASK = 0b0011_1000


def _artwork_corners(flags):
    result = []
    for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
        if flags & 1:
            u, v = v, 1 - u
        if flags & 2:
            u = 1 - u
        if flags & 4:
            v = 1 - v
        result.append((u, v))
    return tuple(result)


ARTWORK_UV = tuple(_artwork_corners(flags) for flags in range(8))


def legacy_holes(flags):
    return [index for index, flag in enumerate(flags) if flag & EMPTY_LEVEL_MASK]


def footprint(terrain):
    grid = terrain.get("tile_grid")
    return (len(grid[0]) * CELLS, len(grid) * CELLS) if grid else (CELLS, CELLS)


def height_range(terrain):
    if "tile_grid" not in terrain:
        return min(terrain["height_m"]), max(terrain["height_m"])
    blocks = terrain["terrain_blocks"]
    return min(min(block["height_m"]) for block in blocks), max(max(block["height_m"]) for block in blocks)


class TiledTerrain:
    def __init__(self, terrain):
        self.grid = terrain["tile_grid"]
        self.blocks = terrain["terrain_blocks"]
        self.cols = len(self.grid[0])
        self.rows = len(self.grid)
        self.width, self.height = footprint(terrain)

    def _address(self, x, y):
        col = min(x // CELLS, self.cols - 1)
        row = min(y // CELLS, self.rows - 1)
        return self.blocks[self.grid[row][col]], x - col * CELLS, y - row * CELLS

    def sample(self, x, y):
        # On a shared edge the block in the direction of increasing grid index owns the sample.
        block, local_x, local_y = self._address(x, y)
        return block["height_m"][local_y * VERTICES + local_x]

    def tile_key(self, col, row):
        east = col + 1 < self.cols
        forward = row + 1 < self.rows
        return (self.grid[row][col], self.grid[row][col + 1] if east else None,
                self.grid[row + 1][col] if forward else None,
                self.grid[row + 1][col + 1] if east and forward else None)

    def mesh_block(self, col, row):
        ident, east, forward, _ = self.tile_key(col, row)
        block = self.blocks[ident]
        if east is None and forward is None:
            return block
        heights = block["height_m"].copy()
        x, y = col * CELLS, row * CELLS
        if east is not None:
            heights[CELLS::VERTICES] = [self.sample(x + CELLS, y + offset) for offset in range(VERTICES)]
        if forward is not None:
            heights[-VERTICES:] = [self.sample(x + offset, y + CELLS) for offset in range(VERTICES)]
        return dict(block, height_m=heights)


def preview_size(terrain):
    width, height = footprint(terrain)
    divisor = max(1, max(width, height) / (MAX_PREVIEW_SIDE - 1))
    return max(2, round(width / divisor) + 1), max(2, round(height / divisor) + 1)


def preview_samples(terrain):
    """Sample a normalized 16-bit overview. The exact floats remain in the intake."""
    tiled = TiledTerrain(terrain)
    width, height = preview_size(terrain)
    low, high = height_range(terrain)
    if low == high:
        return bytes(2 * width * height), width, height
    xs = [round(x * tiled.width / (width - 1)) for x in range(width)]
    ys = [round(y * tiled.height / (height - 1)) for y in range(height)]
    data = bytearray(2 * width * height)
    scale = 65535 / (high - low)
    for row, y in enumerate(ys):
        for col, x in enumerate(xs):
            struct.pack_into(">H", data, 2 * (row * width + col), round((tiled.sample(x, y) - low) * scale))
    return bytes(data), width, height
