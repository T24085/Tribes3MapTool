"""Project authored T2 weather fields without running mission scripts."""

import math
from pathlib import PurePosixPath
import re


WEATHER_FILE = "weather.json"
CLOUD_ACTOR_TAG = "T3DraftCloudLayer"
PRECIPITATION_ACTOR_TAG = "T3DraftPrecipitation"


def _number(raw, low, high, *, integer=False):
    value = float(raw)
    if not math.isfinite(value) or not low <= value <= high or integer and not value.is_integer():
        raise ValueError("Value outside the weather field range.")
    return int(value) if integer else value


def _vector(raw, count, low, high):
    values = raw.split()
    if len(values) != count:
        raise ValueError("Wrong number of weather vector components.")
    return [_number(value, low, high) for value in values]


def _switch(raw):
    values = {"0": False, "1": True, "false": False, "true": True}
    if raw.casefold() not in values:
        raise ValueError("Invalid weather switch.")
    return values[raw.casefold()]


def _material_list(raw):
    value = raw.strip().replace("\\", "/")
    path = PurePosixPath(value)
    if (not value.isprintable() or len(value) > 255 or path.is_absolute()
            or ".." in path.parts or ":" in value or path.suffix.casefold() != ".dml"):
        raise ValueError("Invalid sky material-list reference.")
    return path.as_posix()


def _sky(fields, read, y_sign):
    values = {}
    read(values, "material_list", "materiallist", _material_list)
    read(values, "uses_sky_textures", "useskytextures", _switch)
    read(values, "wind_affects_precipitation", "windeffectprecipitation", _switch)
    read(values, "solid_rgba", "skysolidcolor", lambda raw: _vector(raw, 4, 0, 1))
    read(values, "wind_source_xyz", "windvelocity", lambda raw: _vector(raw, 3, -10000, 10000))
    if "wind_source_xyz" in values:
        x, y, z = values["wind_source_xyz"]
        values["wind_target_axes"] = [x, y * y_sign, z]
    heights = {}
    for index in range(3):
        read(heights, str(index), f"cloudheightper[{index}]", lambda raw: _number(raw, 0, 1))
    if heights:
        values["cloud_height_fractions"] = heights
    speeds = {}
    for field in ("cloudspeed0", "cloudspeed1", "cloudspeed2", "cloudspeed3"):
        read(speeds, field, field, _cloud_speed)
    if speeds:
        values["cloud_speeds_source"] = speeds
    return values


def _cloud_speed(raw):
    values = raw.split()
    if len(values) not in (1, 2):
        raise ValueError("A cloud speed needs one or two components.")
    return [_number(value, -10000, 10000) for value in values]


def _precipitation(fields, read, invalid, scale):
    values = {}
    datablock = fields.get("datablock")
    if isinstance(datablock, str) and datablock.strip().isprintable() and len(datablock) <= 128:
        values["datablock"] = datablock.strip()
        values["kind"] = {"snow": "snow", "rain": "rain"}.get(datablock.strip().casefold(), "unmapped")
    else:
        values["kind"] = "unmapped"
        if "datablock" in fields:
            invalid.append("datablock")
    read(values, "fraction", "percentage", lambda raw: _number(raw, 0, 1))
    read(values, "max_drops", "maxnumdrops", lambda raw: _number(raw, 0, 100000, integer=True))
    read(values, "radius_cm", "maxradius", lambda raw: _number(raw, 0, 10000) * scale)
    for field in ("minvelocity", "maxvelocity", "offsetspeed"):
        read(values, field + "_source", field, lambda raw: _number(raw, 0, 10000))
    if values.get("minvelocity_source", 0) > values.get("maxvelocity_source", math.inf):
        del values["minvelocity_source"], values["maxvelocity_source"]
        invalid.extend(("minvelocity", "maxvelocity"))
    colors = {}
    for number in range(1, 4):
        key = f"color{number}"
        # The original -1 red channel is an unused color slot.
        read(colors, str(number), key, _precipitation_color)
    if colors:
        values["colors_source"] = colors
    return values


def _precipitation_color(raw):
    color = _vector(raw, 4, -1, 1)
    if min(color) < 0 and color != [-1, 0, 0, 1]:
        raise ValueError("Invalid precipitation color.")
    return color


def mission_weather(rows, terrain):
    """Keep supported values and diagnostics by object, including multiple emitters."""
    objects = []
    for row in rows:
        cls = row["source_class"].casefold()
        if cls not in ("sky", "precipitation") or row["status"] == "excluded":
            continue
        fields = {key.casefold(): value for key, value in row["properties"].items()}
        invalid = []

        def read(result, field, source, convert):
            if source in fields:
                try:
                    result[field] = convert(fields[source])
                except (TypeError, ValueError, AttributeError):
                    invalid.append(source)

        values = (_sky(fields, read, terrain.get("y_sign", -1)) if cls == "sky"
                  else _precipitation(fields, read, invalid, terrain["cm_per_m"]))
        if values or invalid:
            objects.append({"source_id": row["id"], "class": cls, "values": values,
                            "invalid_source_fields": sorted(invalid)})
    return {"format": 1, "objects": objects}


def cloud_layers(weather, artwork, y_sign):
    skies = [row for row in weather["objects"] if row["class"] == "sky"]
    if len(skies) != 1:
        return []
    sky = skies[0]
    art = next((row for row in artwork if row["source_id"] == sky["source_id"]), None)
    if not art or len(art["slots"]) < 7:
        return []
    # Classic DMLs reserve six skybox faces and an environment map before clouds.
    # Some maps omit the environment map and explicitly name slot seven as a cloud.
    slot_seven = PurePosixPath(art["slots"][6]["reference"]).stem
    first = 6 if re.search(r"(?:^|[_-])cloud(?:layer|s)?(?:\d|[_-]|$)", slot_seven, re.I) else 7
    values = sky["values"]
    heights = values.get("cloud_height_fractions", {})
    speeds = values.get("cloud_speeds_source", {})
    wind_x, wind_y, _ = values.get("wind_target_axes", [1, 0, 0])
    wind_length = math.hypot(wind_x, wind_y)
    heading = (wind_x / wind_length, wind_y / wind_length) if wind_length else (1, 0)
    layers = []
    for layer, entry in enumerate(art["slots"][first:first + 3]):
        if "file" not in entry:
            continue
        drift = speeds.get(f"cloudspeed{layer + 1}", speeds.get("cloudspeed0", [0]) if layer == 0 else [0])
        x, y = (drift[0], drift[1] * y_sign) if len(drift) == 2 else tuple(drift[0] * axis for axis in heading)
        layers.append({
            "source_id": sky["source_id"], "slot": first + layer + 1, "layer": layer + 1,
            "reference": entry["reference"], "height": heights.get(str(layer), 0.3),
            # Use a repeatable draft scale for the legacy scroll values.
            "uv_per_second": (x * 60, y * 60),
        })
    return layers


def precipitation_emitters(weather, cm_per_m):
    defaults = {
        "snow": (2000, 125, 0.25, 1.5, [1, 1, 1, 0.85]),
        "rain": (2000, 125, 8, 12, [0.64, 0.78, 0.93, 0.55]),
    }
    skies = [row["values"] for row in weather["objects"] if row["class"] == "sky"]
    sky = skies[0] if len(skies) == 1 else {}
    wind = sky.get("wind_target_axes", [0, 0, 0]) if sky.get("wind_affects_precipitation") else [0, 0, 0]
    emitters = []
    for row in weather["objects"]:
        values = row["values"]
        kind = values.get("kind")
        if row["class"] != "precipitation" or kind not in defaults:
            continue
        count, radius, slow, fast, tint = defaults[kind]
        count = round(values.get("max_drops", count) * values.get("fraction", 1))
        radius = values.get("radius_cm", radius * cm_per_m)
        if count <= 0 or radius <= 0:
            continue
        speed = sorted((values.get("minvelocity_source", slow), values.get("maxvelocity_source", fast)))
        colors = [color for color in values.get("colors_source", {}).values() if color[0] >= 0]
        emitters.append({
            "source_id": row["source_id"], "kind": kind, "drops": count, "radius_cm": radius,
            "fall_cm_per_second": tuple(v * cm_per_m for v in speed),
            "drift_cm_per_second": values.get("offsetspeed_source", 0) * cm_per_m,
            "wind_cm_per_second": tuple(axis * cm_per_m for axis in wind),
            "colors": colors or [tint],
        })
    return emitters
