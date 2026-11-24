"""Decode local Tribes bitmaps and keyed palette colors."""

from io import BytesIO
import struct


MAX_SIDE = 4096
UNKEYED = 0xffffffff


def _chunks(data, start, *, aligned):
    while start < len(data):
        if start + 8 > len(data):
            raise ValueError("Truncated bitmap or palette chunk.")
        tag, size = struct.unpack_from("<4sI", data, start)
        end = start + 8 + size
        if end > len(data):
            raise ValueError("Truncated bitmap or palette data.")
        yield tag, data[start + 8:end]
        start = end + (size % 2 if aligned else 0)


def palette_banks(data):
    if data.startswith(b"PL98") and len(data) >= 52:
        count = struct.unpack_from("<I", data, 4)[0]
        if not 1 <= count <= 256 or 52 + count * 1032 > len(data):
            raise ValueError("Invalid Tribes palette bank count.")
        banks = {}
        for offset in range(52, 52 + count * 1032, 1032):
            key = struct.unpack_from("<I", data, offset + 1024)[0]
            if key in banks:
                raise ValueError("A Tribes palette repeats a bank key.")
            banks[key] = data[offset:offset + 1024]
        return banks
    if data.startswith(b"RIFF") and len(data) >= 24 and data[8:12] == b"PAL ":
        end = struct.unpack_from("<I", data, 4)[0] + 8
        if end > len(data):
            raise ValueError("Truncated RIFF palette.")
        chunks = dict(_chunks(data[:end], 12, aligned=True))
        colors = chunks.get(b"data", b"")
        if len(colors) >= 8:
            version, count = struct.unpack_from("<HH", colors)
            if version == 0x300 and 1 <= count <= 256 and len(colors) == 4 + 4 * count:
                return {UNKEYED: colors[4:]}
    raise ValueError("Unsupported or invalid Tribes palette.")


def _dimensions(width, height):
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        raise ValueError("Terrain bitmap dimensions exceed the supported size.")


def _phoenix(data, *, allow_mask):
    # Darkstar's size hint can omit trailing metadata. Validate the chunks themselves.
    if len(data) < 36 or not 0 < struct.unpack_from("<I", data, 4)[0] <= len(data):
        raise ValueError("Invalid Tribes PBMP bitmap size.")
    chunks = list(_chunks(data, 8, aligned=False))
    by_tag = {tag.lower(): content for tag, content in chunks}
    if len(by_tag) != len(chunks) or len(by_tag.get(b"head", b"")) != 20:
        raise ValueError("Invalid Tribes PBMP bitmap header.")
    count, width, height, depth, attributes = struct.unpack("<5I", by_tag[b"head"])
    _dimensions(width, height)
    unsupported = 0x76 if allow_mask else 0x77
    if count != len(chunks) - 1 or depth != 8 or attributes & unsupported:
        raise ValueError("Unsupported Tribes PBMP depth or blend attributes.")
    stride = (width + 3) & ~3
    pixels = by_tag.get(b"data", b"")
    if len(pixels) < stride * height:
        raise ValueError("Truncated Tribes PBMP pixel rows.")
    key = by_tag.get(b"pidx", struct.pack("<I", UNKEYED))
    if len(key) != 4:
        raise ValueError("Invalid Tribes PBMP palette key.")
    indexes = b"".join(pixels[start:start + width] for start in range(0, stride * height, stride))
    colors = palette_banks(b"RIFF" + struct.pack("<I", len(by_tag[b"riff"])) + by_tag[b"riff"])[UNKEYED] if b"riff" in by_tag else None
    return (width, height), indexes, struct.unpack("<I", key)[0], colors, bool(attributes & 1)


def bitmap_png(data, find_palette, *, allow_mask=False):
    """Return RGBA PNG, a palette receipt, and the decoded opacity mode."""
    from PIL import Image

    receipt = None
    color_key = False
    try:
        if data.startswith(b"PBMP"):
            size, indexes, key, colors, color_key = _phoenix(data, allow_mask=allow_mask)
            image = Image.frombytes("P", size, indexes)
            if colors is None:
                colors, receipt = find_palette(key)
        else:
            image = Image.open(BytesIO(data), formats=("BMP", "PNG"))
            _dimensions(*image.size)
            colors = None
            if data.startswith(b"BM") and struct.unpack_from("<H", data, 6)[0] == 0xf5f7:
                key = struct.unpack_from("<H", data, 8)[0]
                if key != 0xffff:
                    if image.mode != "P":
                        raise ValueError("A keyed Windows bitmap needs indexed pixels.")
                    colors, receipt = find_palette(key)
        if colors is not None:
            entries = len(colors) // 4
            if image.getextrema()[1] >= entries:
                raise ValueError("The artwork refers to an index outside its palette.")
            image.putpalette(b"".join(colors[i:i + 3] for i in range(0, len(colors), 4)))
        rgba = image.convert("RGBA")
        if color_key:
            rgba.putalpha(Image.frombytes("L", image.size, indexes.translate(bytes([0] + [255] * 255))))
        alpha = rgba.getchannel("A")
        low, high = alpha.getextrema()
        mode = "opaque"
        if (low, high) != (255, 255):
            if not allow_mask:
                raise ValueError("Terrain artwork needs opaque pixels.")
            bins = alpha.histogram()
            if sum(bins[1:255]):
                raise ValueError("Partially translucent interior materials need review.")
            mode = "masked"
        output = BytesIO()
        rgba.save(output, format="PNG")
        return output.getvalue(), receipt, mode
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Unreadable Tribes artwork.") from exc


def artwork_png(data, find_palette):
    png, receipt, _ = bitmap_png(data, find_palette)
    return png, receipt
