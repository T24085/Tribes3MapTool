"""Decode Vengeance tangent-space normal maps into linear RGBA PNGs."""

from io import BytesIO
from math import sqrt

from vengeance_terrain import RGBA8_TEXTURE
from vengeance_terrain_layers import texture_png


CXV8U8, DXT5N, BC5 = 12, 13, 14
NORMAL_FORMATS = RGBA8_TEXTURE | {CXV8U8: (1, 2), DXT5N: (4, 16), BC5: (4, 16)}


def normal_png(kind, width, height, raw):
    from PIL import Image

    if kind in RGBA8_TEXTURE:
        return texture_png(kind, width, height, raw)
    size = width, height
    try:
        if kind == CXV8U8:
            # Vengeance stores already biased X/Y bytes in its CxV8U8 mip.
            x, y = raw[::2], raw[1::2]
        elif kind == DXT5N:
            pixels = Image.frombytes("RGBA", size, raw, "bcn", (3, "DXT5"))
            x, y = (pixels.getchannel(channel).tobytes() for channel in ("A", "G"))
        elif kind == BC5:
            pixels = Image.frombytes("RGB", size, raw, "bcn", (5, "BC5"))
            x, y = (pixels.getchannel(channel).tobytes() for channel in ("R", "G"))
        else:
            raise ValueError("Unknown tangent-space texture encoding.")
        if len(x) != width * height or len(y) != len(x):
            raise ValueError("Invalid tangent-space texture dimensions.")
        squared = [(value / 127.5 - 1) ** 2 for value in range(256)]
        z = bytes(round(127.5 * (1 + sqrt(max(0, 1 - squared[a] - squared[b])))) for a, b in zip(x, y))
        bands = [Image.frombytes("L", size, data) for data in (x, y, z)]
        image = Image.merge("RGBA", (*bands, Image.new("L", size, 255)))
        buffer = BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()
    except (ValueError, OSError) as error:
        raise ValueError("The Vengeance normal map could not be decoded.") from error
