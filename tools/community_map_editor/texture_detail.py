"""Merge authored tangent-space normals and scalar surface detail for review."""

from functools import lru_cache
from math import sqrt
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from texture_batches import revision_inputs, save_revision, texture_png


CHANNELS = {0: ("L",), 2: ("R", "G", "B"), 4: ("L", "A"), 6: ("R", "G", "B", "A")}


def load_image(path):
    metadata = texture_png(path)
    with Image.open(path) as image:
        if image.is_animated or image.getbands() != CHANNELS[metadata["channels"]]:
            raise ValueError("Use a single grayscale or RGB(A) PNG for surface detail.")
        return image.copy()


def resize_data(image, size):
    if image.size == size:
        return image
    if "A" not in image.getbands():
        return image.resize(size, Image.Resampling.BILINEAR)
    # Resample data channels independently. Alpha is not a color weight here.
    return Image.merge(image.mode, [band.resize(size, Image.Resampling.BILINEAR) for band in image.split()])


def tile_detail(image, size, repeats):
    if repeats == 1:
        return resize_data(image, size)
    width, height = size
    xs = [round(i * width / repeats) for i in range(repeats + 1)]
    ys = [round(i * height / repeats) for i in range(repeats + 1)]
    tiled = Image.new(image.mode, size)
    scaled = {}
    for left, right in zip(xs, xs[1:]):
        for top, bottom in zip(ys, ys[1:]):
            dimensions = right - left, bottom - top
            if dimensions not in scaled:
                scaled[dimensions] = resize_data(image, dimensions)
            tiled.paste(scaled[dimensions], (left, top))
    return tiled


@lru_cache(maxsize=65536)
def tangent_xy(red, green):
    def decode(sample):
        return (sample - 128) / (128 if sample < 128 else 127)

    x, y = decode(red), decode(green)
    radial = x * x + y * y
    if radial > 1:
        length = sqrt(radial)
        return x / length, y / length, 0.0
    return x, y, sqrt(1.0 - radial)


def normal_byte(value):
    return min(255, max(0, round(128 + value * (128 if value < 0 else 127))))


def reorient(base_red, base_green, detail_red, detail_green, amount):
    bx, by, bz = tangent_xy(base_red, base_green)
    dx, dy, _ = tangent_xy(detail_red, detail_green)
    ux, uy = -dx * amount, -dy * amount
    uz = sqrt(max(0, 1 - ux * ux - uy * uy))
    tz = bz + 1
    # Reoriented normal mapping. Keep the upper hemisphere sampled by BC5.
    dot = bx * ux + by * uy + tz * uz
    rx, ry, rz = bx * dot - ux * tz, by * dot - uy * tz, max(0, tz * dot - uz * tz)
    length = sqrt(rx * rx + ry * ry + rz * rz)
    if length < 1e-9:
        return 128, 128, 255
    return normal_byte(rx / length), normal_byte(ry / length), normal_byte(rz / length)


def merge_normals(base, detail, amount):
    result = Image.new("RGB", base.size)
    width, height = base.size
    for y in range(0, height, 64):
        box = (0, y, width, min(height, y + 64))
        first = base.crop(box).convert("RGB").tobytes()
        second = detail.crop(box).tobytes()
        pixels = bytearray(len(first))
        for offset in range(0, len(first), 3):
            pixels[offset:offset + 3] = reorient(first[offset], first[offset + 1],
                                                 second[offset], second[offset + 1], amount)
        strip = Image.frombytes("RGB", (width, box[3] - y), bytes(pixels))
        result.paste(strip, (0, y))
    if "A" in base.getbands():
        result.putalpha(base.getchannel("A"))
    return result


def scalar_channel(image, channel):
    if channel == "grayscale":
        if "L" in image.getbands():
            return image.getchannel("L")
        red = image.getchannel("R")
        if any(ImageChops.difference(red, image.getchannel(key)).getbbox() for key in ("G", "B")):
            raise ValueError("Choose a source channel for a packed detail texture.")
        return red
    if channel not in image.getbands():
        raise ValueError("The detail PNG does not contain the selected source channel.")
    return image.getchannel(channel)


def merge_mask(base, detail, target, operation, amount):
    channels = list(base.getbands())
    if target not in channels:
        raise ValueError("Choose an existing mask channel.")
    result = list(base.split())
    slot = channels.index(target)
    if operation == "roughness":
        weights = detail.point([round(128 + (value - 128) * amount) for value in range(256)])
        result[slot] = ImageChops.add(result[slot], weights, offset=-128)
    else:
        weights = detail.point([round(255 + (value - 255) * amount) for value in range(256)])
        result[slot] = ImageChops.multiply(result[slot], weights)
    return Image.merge(base.mode, result)


def merge_revision(library, draft, root, row, source, *, operation, strength, repeats,
                   source_channel="grayscale", target_channel=None):
    size = tuple(row["target"])
    if type(strength) is not int or not 1 <= strength <= 100:
        raise ValueError("Choose a detail strength from 1 to 100 percent.")
    if type(repeats) is not int or not 1 <= repeats <= min(64, *size):
        raise ValueError("Choose a tile count from 1 to 64 that fits both image dimensions.")
    allowed = ("normal",) if row["role"] == "normal" else ("roughness", "occlusion") if row["role"] == "masks" else ()
    if operation not in allowed:
        raise ValueError("Select a normal map or a mask texture for this operation.")

    original, base = revision_inputs(root, row)
    image = resize_data(load_image(base), size)
    detail = load_image(Path(source).resolve(strict=True))
    if operation == "normal":
        if any(mode not in ("RGB", "RGBA") for mode in (image.mode, detail.mode)):
            raise ValueError("Tangent-space normal maps must be RGB or RGBA.")
        adjusted = merge_normals(image, tile_detail(detail.convert("RGB"), size, repeats), strength / 100)
    else:
        detail = tile_detail(scalar_channel(detail, source_channel), size, repeats)
        adjusted = merge_mask(image, detail, target_channel, operation, strength / 100)
    return save_revision(library, draft, root, row, original, adjusted)


def tile_edges(image):
    """Return opposite-edge mean and peak channel differences on the 8-bit scale."""
    width, height = image.size
    result = []
    for first, second in (
        ((0, 0, 1, height), (width - 1, 0, width, height)),
        ((0, 0, width, 1), (0, height - 1, width, height)),
    ):
        with ImageChops.difference(image.crop(first), image.crop(second)) as difference:
            stats = ImageStat.Stat(difference)
            result.append((sum(stats.mean) / len(stats.mean), max(high for _, high in stats.extrema)))
    return result
