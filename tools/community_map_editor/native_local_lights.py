"""Normalized draft records for independently placed Ascend lights."""

ASCEND_LIGHT_CLASSES = {"engine.pointlight": "point", "engine.spotlight": "spot"}


def validate_local_light(row):
    from intake_contract import finite

    light = row.get("local_light")
    kind = ASCEND_LIGHT_CLASSES.get(row["source_class"].casefold())
    required = {"kind", "radius_cm", "brightness"}
    if kind == "spot":
        required |= {"inner_angle", "outer_angle"}
    if (not kind or not isinstance(light, dict) or light.keys() - required - {"color", "falloff", "cast_shadows"}
            or required - light.keys() or light["kind"] != kind or row["status"] != "review"
            or "transform" not in row or not finite(light["radius_cm"], 2e6) or light["radius_cm"] <= 0
            or not finite(light["brightness"], 8) or light["brightness"] < 0
            or "cast_shadows" in light and type(light["cast_shadows"]) is not bool
            or "falloff" in light and (not finite(light["falloff"], 64) or light["falloff"] <= 0)):
        raise ValueError("Invalid native local light.")
    if kind == "spot" and (not all(finite(light[angle], 89) for angle in ("inner_angle", "outer_angle"))
                           or not 0 <= light["inner_angle"] <= light["outer_angle"] or light["outer_angle"] < 1):
        raise ValueError("Invalid native spotlight cone.")
    if "color" in light and (not isinstance(light["color"], list) or len(light["color"]) != 3
                             or not all(finite(channel, 1) and channel >= 0 for channel in light["color"])):
        raise ValueError("Invalid native local light color.")
