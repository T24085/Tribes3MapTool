"""The MILF environment component: a map's sun, sky light, fog, exposure and grade.

Spec: T3BackEndServer docs/MILF_ENVIRONMENT.md (Tribes3-Modded/T3BackEndServer#146). It is engine
neutral. Each source game has its own converter (environment_ascend.py first; Tribes 1, Tribes 2
and Vengeance follow) that produces this component with that game's default factors, and each
engine has one adapter that applies it (environment_ue5.py for the draft editor). Units and axes
follow MILF: metres, degrees, linear colour, right-handed with +X east, +Y north and Z up.
"""

import math
import struct
import zlib

FORMAT = 1
GAMES = ("tribes1", "tribes2", "vengeance", "ascend", "hand_made")
PARTS = {"format", "sun", "sky", "fog", "exposure", "grade", "provenance"}


def finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def colour(value):
    return isinstance(value, list) and len(value) == 3 and all(finite(item, 0, 1) for item in value)


def exposure_scale(ev100):
    """Camera exposure multiplier for an EV100 value: 1 / (1.2 x 2^EV100)."""
    return 1 / (1.2 * 2 ** ev100)


def ev100_for_scale(scale):
    return math.log2(1 / (1.2 * scale))


def to_milf_axes(vector):
    """Unreal (left-handed, +Y right) to MILF (right-handed, +Y north): flip Y."""
    x, y, z = vector
    return [x, -y, z]


def from_milf_axes(vector):
    return to_milf_axes(vector)


def validate(component):
    """Raise ValueError unless the component matches format 1 of the spec."""
    if not isinstance(component, dict) or component.get("format") != FORMAT or component.keys() - PARTS:
        raise ValueError("Invalid MILF environment component.")
    sun = component.get("sun")
    if sun is not None:
        direction = sun.get("direction") if isinstance(sun, dict) else None
        if (not isinstance(sun, dict) or sun.keys() - {"direction", "color", "illuminance_lux"}
                or direction is not None and (not isinstance(direction, list) or len(direction) != 3
                                              or not all(finite(v, -1, 1) for v in direction)
                                              or abs(math.hypot(*direction) - 1) > 1e-3)
                or "color" in sun and not colour(sun["color"])
                or "illuminance_lux" in sun and not finite(sun["illuminance_lux"], 0, 200000)):
            raise ValueError("Invalid MILF environment sun.")
    sky = component.get("sky")
    if sky is not None and (not isinstance(sky, dict) or sky.keys() - {"color", "intensity"}
                            or "color" in sky and not colour(sky["color"])
                            or "intensity" in sky and not finite(sky["intensity"], 0, 16)):
        raise ValueError("Invalid MILF environment sky.")
    fog = component.get("fog")
    ranges = {"density": (0, 5), "start_m": (0, 100000), "height_m": (-100000, 100000),
              "height_falloff": (0, 2), "max_opacity": (0, 1)}
    if fog is not None and (not isinstance(fog, dict) or fog.keys() - ranges.keys() - {"color", "sun_color"}
                            or "color" in fog and not colour(fog["color"])
                            or "sun_color" in fog and not colour(fog["sun_color"])
                            or any(not finite(fog[key], *ranges[key]) for key in fog.keys() & ranges.keys())):
        raise ValueError("Invalid MILF environment fog.")
    exposure = component.get("exposure")
    if exposure is not None and (not isinstance(exposure, dict) or exposure.keys() != {"mode", "ev100"}
                                 or exposure["mode"] != "fixed" or not finite(exposure["ev100"], -10, 20)):
        raise ValueError("Invalid MILF environment exposure.")
    grade = component.get("grade")
    if grade is not None:
        bloom = grade.get("bloom") if isinstance(grade, dict) else None
        lut = grade.get("lut") if isinstance(grade, dict) else None
        tones = {"shadows": 1, "midtones": 4, "highlights": 4}
        if (not isinstance(grade, dict) or grade.keys() - {"lut", "bloom", *tones}
                or lut is not None and (not isinstance(lut, str) or not lut.endswith(".png") or len(lut) > 160
                                        or lut.startswith("/") or ".." in lut.split("/"))
                or any(not isinstance(grade[key], list) or len(grade[key]) != 3
                       or not all(finite(value, 0, high) for value in grade[key])
                       for key, high in tones.items() if key in grade)
                or bloom is not None and (not isinstance(bloom, dict) or bloom.keys() - {"intensity", "threshold", "tint"}
                                          or "intensity" in bloom and not finite(bloom["intensity"], 0, 8)
                                          or "threshold" in bloom and not finite(bloom["threshold"], 0, 16)
                                          or "tint" in bloom and not colour(bloom["tint"]))):
            raise ValueError("Invalid MILF environment grade.")
    provenance = component.get("provenance")
    if provenance is not None and (not isinstance(provenance, dict) or provenance.keys() - {"game", "rules"}
                                   or provenance.get("game") not in GAMES
                                   or not isinstance(provenance.get("rules", ""), str)
                                   or len(provenance.get("rules", "")) > 40):
        raise ValueError("Invalid MILF environment provenance.")
    return component


def read_png_rgb(data):
    """Decode an 8-bit, non-interlaced RGB or RGBA PNG to rows of (r, g, b) bytes, stdlib only.

    Engine adapters run inside editors without an imaging library; grade LUTs are tiny.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG.")
    offset, chunks, header = 8, [], None
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        elif kind == b"IEND":
            break
        offset += 12 + length
    if not header:
        raise ValueError("PNG has no header.")
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in (2, 6) or interlace or width * height > 1 << 20:
        raise ValueError("Only 8-bit, non-interlaced RGB or RGBA PNGs are supported here.")
    step = 3 if colour == 2 else 4
    raw, stride = zlib.decompress(b"".join(chunks)), width * step
    rows, previous = [], bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            left = line[x - step] if x >= step else 0
            up, corner = previous[x], previous[x - step] if x >= step else 0
            if kind == 1:
                line[x] = (line[x] + left) & 255
            elif kind == 2:
                line[x] = (line[x] + up) & 255
            elif kind == 3:
                line[x] = (line[x] + (left + up) // 2) & 255
            elif kind == 4:
                estimate = left + up - corner
                pick = min((abs(estimate - left), 0, left), (abs(estimate - up), 1, up), (abs(estimate - corner), 2, corner))
                line[x] = (line[x] + pick[2]) & 255
        rows.append([tuple(line[x:x + 3]) for x in range(0, stride, step)])
        previous = line
    return rows


def lut_fit(rows):
    """Fit a 256 x 16 colour-grading LUT as out = gain x in^exponent per channel, in display space.

    Returns [(gain, exponent)] for red, green and blue. For engines that ignore a map's LUT, the
    adapter folds this into its own colour grading.
    """
    if len(rows) != 16 or any(len(row) != 256 for row in rows):
        raise ValueError("A grade LUT must be 256 x 16.")
    fits = []
    for channel in range(3):
        points = []
        for blue in range(16):
            for green in range(16):
                for red in range(16):
                    source = (red / 15, green / 15, blue / 15)[channel]
                    result = rows[green][blue * 16 + red][channel] / 255
                    if source > 0.05 and result > 0.02:
                        points.append((math.log(source), math.log(result)))
        count = len(points)
        sum_x, sum_y = sum(x for x, _ in points), sum(y for _, y in points)
        sum_xx, sum_xy = sum(x * x for x, _ in points), sum(x * y for x, y in points)
        exponent = (count * sum_xy - sum_x * sum_y) / (count * sum_xx - sum_x * sum_x)
        fits.append((math.exp((sum_y - exponent * sum_x) / count), exponent))
    return fits
