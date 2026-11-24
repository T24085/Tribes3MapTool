"""Map T2 static object references to existing Unreal project meshes."""

import math
import re

from intake_contract import finite
from model_textures import placement_key


REFERENCE_FIELDS = {
    "interiorinstance": "interiorfile",
    "interiorshape": "filename",
    "tsstatic": "shapename",
    "staticshape": "shapename",
}


def source_reference(row):
    if row["status"] == "review" and row.get("conversion_class") == "unrealmeshactor":
        return row["model_reference"]
    kind = row["source_class"].lower()
    field = REFERENCE_FIELDS.get(kind)
    if row["status"] != "review" or not field:
        return None
    properties = {key.lower(): value for key, value in row["properties"].items()}
    name = properties.get(field)
    if not isinstance(name, str) or not name.strip():
        return None
    path = name.strip().replace("\\", "/").casefold()
    while path.startswith("./"):
        path = path[2:]
    return f"{kind}:{path}" if path and len(path) <= 512 and path.isprintable() else None


def material_reference(row, overrides):
    key = placement_key(row["id"])
    return key if key in overrides else source_reference(row)


def placements(rows, terrain, bindings):
    sources = [(row, source_reference(row)) for row in rows]
    known = {reference for _, reference in sources if reference}
    if not isinstance(bindings, dict) or bindings.keys() - known:
        raise ValueError("Mesh mappings contain an unknown source object reference.")
    if any(not isinstance(path, str) or len(path) > 512
           or not re.fullmatch(r"/Game/Community/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+", path)
           for path in bindings.values()):
        raise ValueError("Mesh mappings must target a project asset under /Game/Community.")
    result = []
    for row, reference in sources:
        if reference not in bindings:
            continue
        try:
            location, rotation, scale = placement_transform(row, terrain)
        except ValueError as exc:
            raise ValueError(f"{row['name'] or reference} has an invalid source transform. {exc}") from exc
        result.append((row, bindings[reference], location, rotation, scale))
    return result


def placement_transform(row, terrain):
    if "transform" in row:
        placement = row["transform"]
        return tuple(tuple(placement[key]) for key in ("position_cm", "rotation_xyzw", "scale"))
    return source_transform(row["properties"], terrain)


def legacy_rotation(angles, y_sign):
    # Darkstar applies Y, then X, then Z. Reflect the quaternion axes with the coordinate frame.
    x, y, z = (math.remainder(angle, math.tau) / 2 for angle in angles)
    sx, sy, sz = math.sin(x), math.sin(y), math.sin(z)
    cx, cy, cz = math.cos(x), math.cos(y), math.cos(z)
    return (y_sign * (cz * sx * cy - sz * cx * sy),
            cz * cx * sy + sz * sx * cy,
            y_sign * (cz * sx * sy + sz * cx * cy),
            cz * cx * cy - sz * sx * sy)


def source_transform(properties, terrain):
    properties = {key.lower(): value for key, value in properties.items()}

    def values(key, default, count, limit):
        value = properties.get(key, default)
        if not isinstance(value, str):
            raise ValueError(f"Invalid {key}.")
        try:
            numbers = tuple(float(part) for part in value.split())
        except ValueError as exc:
            raise ValueError(f"Invalid {key}.") from exc
        if len(numbers) != count or not all(finite(number, limit) for number in numbers):
            raise ValueError(f"Invalid {key}.")
        return numbers

    x, y, z = values("position", "", 3, 2e6)
    units, y_sign = terrain["cm_per_m"], terrain.get("y_sign", -1)
    location = (x * units, y * units * y_sign, z * units)
    if not all(finite(number, 2e6) for number in location):
        raise ValueError("Position is outside the supported draft area.")
    if "tile_grid" in terrain or len(terrain.get("height_m", ())) == 257 ** 2:
        rotation = legacy_rotation(values("rotation", "0 0 0", 3, 1e6), y_sign)
    else:
        ax, ay, az, angle = values("rotation", "0 0 1 0", 4, 1e6)
        length = math.hypot(ax, ay, az)
        angle = math.remainder(angle, 360)
        if angle and not length:
            raise ValueError("Rotation needs a nonzero axis.")
        rotation = (0, 0, 0, 1)
        if angle:
            # Torque's signed angle is reversed. Conjugating by the Y reflection
            # also flips the pseudovector, which preserves the existing marker yaw.
            half = math.radians(angle) / 2
            factor = -math.sin(half) / length
            rotation = (ax * y_sign * factor, ay * factor, az * y_sign * factor, math.cos(half))
    scale = values("scale", "1 1 1", 3, 1000)
    if any(part == 0 for part in scale):
        raise ValueError("Mesh scale cannot be zero.")
    return location, rotation, scale
