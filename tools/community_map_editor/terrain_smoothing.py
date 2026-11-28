"""Smooth terrain between its height samples by subdividing each cell (Catmull-Rom).

Source terrain is a height grid drawn as flat triangles, so slopes show facets between samples (4.9 m apart
on Katabatic). UE3 terrain tessellated smoothly between samples; this does the same on the mesh, so the
ground players ski on is smooth too.

Catmull-Rom passes exactly through every source sample, so each original height stays where the source put
it, and a point on a cell edge depends only on samples along that edge, so neighbouring tiles still meet.
"""

ASCEND_FACTOR = 2  # Each source cell becomes 2 x 2 cells.


def catmull_rom(p0, p1, p2, p3, t):
    """The Catmull-Rom curve through p1 (t = 0) and p2 (t = 1)."""
    t2 = t * t
    return 0.5 * (2 * p1 + (p2 - p0) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                  + (3 * (p1 - p2) + p3 - p0) * t2 * t)


def smooth_grid(height, size, factor):
    """Heights of a (size * factor + 1)^2 vertex grid from height(x, y) on the (size + 1)^2 source grid.

    ``height`` is called with x and y from 0 to size. Beyond the edge the slope continues in a straight
    line, so every vertex comes from this tile's samples alone and a straight slope stays straight.
    """
    if factor == 1:
        return [height(x, y) for y in range(size + 1) for x in range(size + 1)]
    rows = {}

    def row(y):
        if y not in rows:
            if y < 0:
                rows[y] = [2 * a - b for a, b in zip(row(0), row(1))]
            elif y > size:
                rows[y] = [2 * a - b for a, b in zip(row(size), row(size - 1))]
            else:
                rows[y] = [height(x, y) for x in range(size + 1)]
        return rows[y]

    def sample(samples, x):
        if x < 0:
            return 2 * samples[0] - samples[1]
        if x > size:
            return 2 * samples[size] - samples[size - 1]
        return samples[x]

    def along(samples, x, t):
        if t == 0:
            return samples[x]
        return catmull_rom(sample(samples, x - 1), samples[x], sample(samples, x + 1), sample(samples, x + 2), t)

    out = []
    width = size * factor + 1
    for fy in range(width):
        y, ty = divmod(fy, factor)
        ty /= factor
        if y == size:
            y, ty = size - 1, 1.0
        lines = [row(y - 1), row(y), row(y + 1), row(y + 2)]
        for fx in range(width):
            x, tx = divmod(fx, factor)
            tx /= factor
            if x == size:
                x, tx = size - 1, 1.0
            columns = [along(line, x, tx) for line in lines]
            out.append(columns[1] if ty == 0 else catmull_rom(*columns, ty) if ty < 1 else columns[2])
    return out


def bilinear(values, size, factor, fx, fy):
    """A per-vertex value at subdivided vertex (fx, fy), linearly between the source vertices."""
    x, tx = divmod(fx, factor)
    y, ty = divmod(fy, factor)
    tx, ty = tx / factor, ty / factor
    x1, y1 = min(size, x + 1), min(size, y + 1)
    stride = size + 1
    a, b = values[y * stride + x], values[y * stride + x1]
    c, d = values[y1 * stride + x], values[y1 * stride + x1]
    return [(1 - ty) * ((1 - tx) * p + tx * q) + ty * ((1 - tx) * r + tx * s) for p, q, r, s in zip(a, b, c, d)]
