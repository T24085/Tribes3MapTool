"""Bake a sparse top-down height mask for stationary shelter geometry."""

import math

from minimap import rgb_png

CELL_CM = 125
MAX_AXIS = 512
PADDING_CM = 20


def roof_mask(bounds, sample):
    if not bounds:
        return None
    x0, y0, z0 = (min(pair[0][axis] for pair in bounds) for axis in range(3))
    x1, y1, z1 = (max(pair[1][axis] for pair in bounds) for axis in range(3))
    origin = (x0 - PADDING_CM, y0 - PADDING_CM)
    size = (x1 + PADDING_CM - origin[0], y1 + PADDING_CM - origin[1])
    bottom, top = z0 - PADDING_CM, z1 + PADDING_CM
    width, height = (min(MAX_AXIS, max(2, math.ceil(span / CELL_CM))) for span in size)
    dx, dy = size[0] / width, size[1] / height
    candidates = set()

    def indices(low, high, offset, step, length):
        first = max(0, math.ceil((low - offset) / step - 0.5))
        last = min(length - 1, math.floor((high - offset) / step - 0.5))
        return range(first, last + 1)

    for lower, upper in bounds:
        for row in indices(lower[1], upper[1], origin[1], dy, height):
            for col in indices(lower[0], upper[0], origin[0], dx, width):
                candidates.add(row * width + col)
    pixels = bytearray(width * height * 3)
    found = False
    span = top - bottom
    for index in sorted(candidates):
        row, col = divmod(index, width)
        x, y = origin[0] + (col + .5) * dx, origin[1] + (row + .5) * dy
        hit = sample(x, y, bottom, top)
        if hit is not None and bottom <= hit <= top:
            level = round((hit - bottom) * 65535 / span)
            # Blue distinguishes a roof at minimum height from open sky.
            pixels[index * 3:index * 3 + 3] = bytes((level >> 8, level & 255, 255))
            found = True
    if not found:
        return None
    return rgb_png(pixels, width, height), origin, size, bottom, span
