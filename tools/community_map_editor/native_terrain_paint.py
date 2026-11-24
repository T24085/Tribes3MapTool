"""Intake masks and continuous color weights for native terrain."""

from hashlib import sha256
import re
import struct
import zlib

from terrain_grid import CELLS, VERTICES


MAX_PAINT_LAYERS = 32
MAX_MASK_SAMPLES = 4 * 1024 * 1024
MAX_PAINT_BYTES = 64 * 1024 * 1024
MAX_PAINT_PNG_BYTES = MAX_PAINT_BYTES + 1024 * 1024
PAINT_FILE = re.compile(r"terrain-alpha-(?:0[0-9]|[12][0-9]|3[01])\.png")


def decode_mask(raw, *, size=None, maximum=MAX_MASK_SAMPLES):
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Terrain weights must be an 8-bit grayscale PNG.")
    chunks = []
    offset = 8
    while offset + 12 <= len(raw) and len(chunks) < 4096:
        length, kind = struct.unpack_from(">I4s", raw, offset)
        end = offset + length + 12
        if end > len(raw):
            break
        data = raw[offset + 8:end - 4]
        if zlib.crc32(raw[offset + 4:end - 4]) != int.from_bytes(raw[end - 4:end], "big"):
            raise ValueError("Terrain weight PNG checksum does not match.")
        chunks.append((kind, data))
        offset = end
    if (offset != len(raw) or len(chunks) < 3 or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13
            or chunks[-1] != (b"IEND", b"") or any(kind != b"IDAT" for kind, _ in chunks[1:-1])):
        raise ValueError("Invalid terrain weight PNG layout.")
    width, height, *layout = struct.unpack(">IIBBBBB", chunks[0][1])
    if (layout != [8, 0, 0, 0, 0] or not (1 <= width <= 64 * CELLS and 1 <= height <= 64 * CELLS)
            or width * height > maximum
            or size is not None and (width, height) != size):
        raise ValueError("Invalid terrain weight PNG dimensions or color mode.")
    stride = width + 1
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(b"".join(data for _, data in chunks[1:-1]), height * stride + 1)
    except zlib.error as error:
        raise ValueError("Invalid compressed terrain weights.") from error
    if (len(pixels) != height * stride or not decoder.eof or decoder.unused_data
            or any(pixels[row * stride] for row in range(height))):
        raise ValueError("Invalid terrain weight rows.")
    return width, height, b"".join(pixels[row * stride + 1:(row + 1) * stride] for row in range(height))


def axis_samples(tile, vertices, mask_side):
    values = []
    for vertex in range(tile * CELLS, tile * CELLS + VERTICES):
        point = min(vertex, vertices - 1) * (mask_side - 1) / (vertices - 1)
        left = int(point)
        values.append((left, min(left + 1, mask_side - 1), point - left))
    return values


class NativeTerrainPaint:
    def __init__(self, folder, header, choices, palette, fallback):
        from intake_contract import bounded, intake_file_limit

        paint = header["terrain_paint"]
        self.width, self.height = paint["vertices"]
        self.fallback = palette.index(fallback)
        self.masks = []
        self.mask_sizes = {}
        for slot, _, surface in sorted(choices):
            name = paint["masks"][str(slot)]
            path = (folder / name).resolve(strict=True)
            if path.parent != folder:
                raise ValueError("Terrain paint mask is outside the intake.")
            raw = bounded(path, intake_file_limit(name, header["format"]))
            if sha256(raw).hexdigest() != header["files"][name]:
                raise ValueError("Terrain paint mask changed after validation.")
            width, height, alpha = decode_mask(raw)
            self.mask_sizes[slot] = (width, height)
            self.masks.append((palette.index(surface or fallback), width, height, alpha))
        self.channels = tuple(sorted({self.fallback, *(row[0] for row in self.masks)}))

    def tile_weights(self, col, row):
        pixels = VERTICES**2
        weights = [[float(channel == self.fallback)] * pixels for channel in range(3)]
        by_size = {}
        for channel, width, height, mask in self.masks:
            dimensions = width, height
            if dimensions not in by_size:
                xs = axis_samples(col, self.width, width)
                ys = [(lo * width, hi * width, part) for lo, hi, part in axis_samples(row, self.height, height)]
                by_size[dimensions] = xs, ys
            xs, ys = by_size[dimensions]
            for y, (low, high, v) in enumerate(ys):
                for x, (left, right, u) in enumerate(xs):
                    top = mask[low + left] * (1 - u) + mask[low + right] * u
                    bottom = mask[high + left] * (1 - u) + mask[high + right] * u
                    opacity = (top * (1 - v) + bottom * v) / 255
                    if opacity:
                        idx = y * VERTICES + x
                        for weight in weights:
                            weight[idx] *= 1 - opacity
                        weights[channel][idx] += opacity
        return list(zip(*weights))
