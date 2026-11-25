"""Project saved global light and fog inputs from native Unreal packages."""

import math

from intake_contract import finite
from unreal_legacy_reader import descendants
from unreal_package_reader import Cursor


FLOAT = ("FloatProperty", None, "f")
BYTE = ("ByteProperty", None, "B")
BOOL = ("BoolProperty", None, None)
ROTATOR = ("StructProperty", "Rotator", "3i")
COLOR = ("StructProperty", "Color", "4B")


def fields(node, stream, endian, spec):
    if node["property_issues"]:
        raise ValueError("The saved environment object could not be decoded.")
    result = {}
    saved = {}
    for tag in node["tags"]:
        name = tag["name"].casefold()
        if name not in spec:
            continue
        kind, structure, form = spec[name]
        if name in result or tag["slot"] or (tag["kind"], tag["structure"]) != (kind, structure):
            raise ValueError(f"Invalid or duplicate environment property {tag['name']}.")
        start = tag["value_offset"]
        cursor = Cursor(stream, start, start + tag["size"], endian)
        if kind == "BoolProperty":
            value = bool(tag["boolean"])
        else:
            parts = cursor.fields(form)
            value = parts[0] if len(parts) == 1 else list(parts)
        if cursor.offset != cursor.limit or isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Invalid environment value in {tag['name']}.")
        result[name] = value
        saved[tag["name"]] = value
    if saved:
        node.setdefault("environment_fields", {}).update(saved)
    return result


def rgb(bgra):
    def linear(channel):
        unit = channel / 255
        return unit / 12.92 if unit <= 0.04045 else ((unit + .055) / 1.055) ** 2.4

    return [linear(channel) for channel in reversed(bgra[:3])]


def direction(rotation):
    pitch, yaw, _ = (value * math.tau / 65536 for value in rotation)
    return [math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)]


def component(node, suffix):
    matches = [child for child in descendants(node) if child["header"].split(" ", 1)[0].casefold().endswith(suffix)]
    if len(matches) != 1:
        raise ValueError("One saved environment component is required.")
    return matches[0]


def bounded_scalar(values, key, low, high):
    value = values.get(key)
    if value is not None and not low <= value <= high:
        raise ValueError(f"Saved {key} exceeds the editor range.")
    return value


def ascend_fog(actor, node, stream, endian, cm_per_unit):
    fog = component(node, "exponentialheightfogcomponent")
    values = fields(fog, stream, endian, {
        "fogenabled": BOOL, "benabled": BOOL, "fogdensity": FLOAT, "startdistance": FLOAT,
        "fogheight": FLOAT, "fogheightfalloff": FLOAT, "fogmaxopacity": FLOAT,
        "oppositelightcolor": COLOR, "lightinscatteringcolor": COLOR,
    })
    if not values.get("benabled", True) or not values.get("fogenabled", True):
        return {}, "Height fog is disabled in the source."
    density = bounded_scalar(values, "fogdensity", 0, 5)
    if density is None:
        raise ValueError("Height fog needs a serialized density or its source archetype.")
    height = values.get("fogheight")
    if height is None and actor["transform"]:
        height = actor["transform"]["position_m"][2] * 100 / cm_per_unit
    start = values.get("startdistance", 0) * cm_per_unit
    if not 0 <= start <= 2e6 or height is not None and not finite(height * cm_per_unit, 2e6):
        raise ValueError("Saved height fog placement exceeds the editor range.")
    result = dict(start_cm=start, density=density)
    if height is not None:
        result["height_cm"] = height * cm_per_unit
    for source, key, limit in (("fogheightfalloff", "height_falloff", 2), ("fogmaxopacity", "max_opacity", 1)):
        value = bounded_scalar(values, source, 0, limit)
        if value is not None:
            result[key] = value
    opposite = values.get("oppositelightcolor")
    primary = values.get("lightinscatteringcolor")
    if primary or opposite:
        result["color"] = rgb(opposite or primary)
    note = "Imported global height fog. Source directional scattering remains in review."
    return result, note


def vengeance_fog(nodes, stream, endian, cm_per_unit):
    zones = [fields(node, stream, endian, {
        "bdistancefog": BOOL, "distancefogstart": FLOAT, "distancefogend": FLOAT, "distancefogcolor": COLOR,
    }) for node in nodes]
    if not any(zone.get("bdistancefog") for zone in zones):
        return {}, "Distance fog activation was not saved on these zones."
    if any(zone != zones[0] for zone in zones):
        raise ValueError("Distinct source zone fog settings need individual volume conversion.")
    first = zones[0]
    start = first.get("distancefogstart")
    end = first.get("distancefogend")
    if start is None or end is None or not 0 <= start < end <= 2e6 / cm_per_unit:
        raise ValueError("Distance fog needs saved start and end distances in range.")
    result = dict(start_cm=start * cm_per_unit, visible_cm=end * cm_per_unit)
    if "distancefogcolor" in first:
        result["color"] = rgb(first["distancefogcolor"])
    return result, "Matching zone distance fog mapped to a global draft fog approximation."


def read_environment(scene, stream, endian):
    nodes = {node["line"]: node for node in descendants(scene["scene"][0])}
    by_class = {}
    for actor in scene["actors"]:
        kind = (actor["source_class"] or "").rsplit(".", 1)[-1].casefold()
        by_class.setdefault(kind, []).append(actor)
    modern = scene["profile"] == "ascend"
    cm_per_unit = 100 / scene["coordinate_frame"]["source_units_per_m"]
    result, notes = {}, {}

    def record(rows, callback):
        try:
            values, reason = callback()
        except ValueError as error:
            values, reason = {}, str(error)
        for actor in rows:
            notes.setdefault(str(actor["line"]), []).append(reason)
        return values

    # Prefer an explicitly dominant UE3 light. A light needs a rotation, even when it has no position.
    lights = (by_class.get("dominantdirectionallight") or by_class.get("directionallight", [])) if modern else by_class.get("sunlight", [])

    def sunlight():
        if len(lights) != 1:
            raise ValueError("Multiple source sun lights need manual review.")
        values = fields(nodes[lights[0]["line"]], stream, endian, {"rotation": ROTATOR, "lightbrightness": FLOAT})
        if "rotation" not in values:
            raise ValueError("Sun direction needs an explicitly saved rotation.")
        sun = dict(direction=direction(values["rotation"]))
        reason = "Imported global sun. Source light baking and shader effects remain in review."
        if modern:
            try:
                light = component(nodes[lights[0]["line"]], "directionallightcomponent")
                settings = fields(light, stream, endian, {"brightness": FLOAT, "lightcolor": COLOR})
                value = bounded_scalar(settings, "brightness", 0, 8)
                if value is not None:
                    sun["intensity_scale"] = value
                if "lightcolor" in settings:
                    sun["color"] = rgb(settings["lightcolor"])
            except ValueError as error:
                reason = f"Imported sun direction. {error}"
        else:
            value = bounded_scalar(values, "lightbrightness", 0, 2040)
            if value is not None:
                sun["intensity_scale"] = value / 255
        return sun, reason

    if lights:
        sun = record(lights, sunlight)
        if sun:
            result["sun"] = sun
    skies = by_class.get("skylight", []) if modern else by_class.get("zoneinfo", [])

    def ambient():
        if modern:
            if len(skies) != 1:
                raise ValueError("Multiple source skylights need manual review.")
            sky = component(nodes[skies[0]["line"]], "skylightcomponent")
            values = fields(sky, stream, endian, {"brightness": FLOAT, "lightcolor": COLOR})
            brightness = bounded_scalar(values, "brightness", 0, 8)
            ambient = {} if brightness is None else {"ambient_scale": brightness}
            if "lightcolor" in values:
                ambient["ambient"] = rgb(values["lightcolor"])
            return ambient, "Saved skylight inputs mapped to the draft sky light."
        values = [fields(nodes[actor["line"]], stream, endian, {"ambientbrightness": BYTE})
                  .get("ambientbrightness") for actor in skies]
        if values[0] is not None and all(value == values[0] for value in values):
            return {"ambient_scale": values[0] / 255}, "Common zone ambient brightness mapped to the draft sky light."
        return {}, "Zone ambient lighting requires individual review."

    if skies:
        sky = record(skies, ambient)
        if sky:
            result.setdefault("sun", {}).update(sky)
    fogs = by_class.get("exponentialheightfog", []) if modern else skies

    def fog_settings():
        if not modern:
            return vengeance_fog([nodes[actor["line"]] for actor in fogs], stream, endian, cm_per_unit)
        if len(fogs) != 1:
            raise ValueError("Multiple source height fog actors need individual review.")
        actor, = fogs
        return ascend_fog(actor, nodes[actor["line"]], stream, endian, cm_per_unit)

    if fogs:
        fog = record(fogs, fog_settings)
        if fog:
            result["fog"] = fog
    if notes:
        scene["environment_review"] = notes
    return result
