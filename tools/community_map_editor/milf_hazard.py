"""The MILF hazard component: a map's kill zones (lava, bottomless pits, deadly liquids).

Spec: T3BackEndServer docs/MILF_HAZARD.md (Tribes3-Modded/T3BackEndServer#150). It is engine
neutral. Each source game has a converter (hazards_ascend.py first) and each engine one adapter.
Units and axes follow MILF: metres, degrees, linear colour, right-handed with +X east, +Y north
and Z up.
"""

import math
import re

FORMAT = 1
GAMES = ("tribes1", "tribes2", "vengeance", "ascend", "hand_made")
KINDS = ("lava", "void", "liquid")
CAUSES = ("lava", "fall", "liquid")
# A look preset names a registered look; each engine adapter maps ids to its assets and falls back
# to its own default for an id it doesn't have. "none" draws nothing.
PRESET = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MAX_ZONES = 64
MAX_OUTLINE = 64
MAX_GLOW_SPHERES = 8
LIMIT_M = 100000
ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def vector(value, size=3, low=-LIMIT_M, high=LIMIT_M):
    return isinstance(value, list) and len(value) == size and all(finite(item, low, high) for item in value)


def convex_ccw(outline):
    """True when the XY outline is a convex polygon wound counter-clockwise (positive area)."""
    turns = []
    for index, (ax, ay) in enumerate(outline):
        bx, by = outline[(index + 1) % len(outline)]
        cx, cy = outline[(index + 2) % len(outline)]
        turns.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    return all(turn >= 0 for turn in turns) and any(turn > 0 for turn in turns)


def validate_shape(shape):
    kind = shape.get("type") if isinstance(shape, dict) else None
    if kind == "box":
        return (shape.keys() <= {"type", "center_m", "extent_m", "rotation_deg"} and vector(shape.get("center_m"))
                and vector(shape.get("extent_m"), 3, 0.1, 10000)
                and finite(shape.get("rotation_deg", 0), -360, 360))
    if kind == "sphere":
        return (shape.keys() == {"type", "center_m", "radius_m"} and vector(shape["center_m"])
                and finite(shape["radius_m"], 0.1, 10000))
    if kind == "prism":
        outline = shape.get("outline_m")
        return (shape.keys() == {"type", "outline_m", "floor_m", "top_m"}
                and isinstance(outline, list) and 3 <= len(outline) <= MAX_OUTLINE
                and all(vector(point, 2) for point in outline) and convex_ccw(outline)
                and finite(shape["floor_m"], -LIMIT_M, LIMIT_M) and finite(shape["top_m"], -LIMIT_M, LIMIT_M)
                and shape["top_m"] > shape["floor_m"])
    return False


def height_span(shape):
    if shape["type"] == "prism":
        return shape["floor_m"], shape["top_m"]
    center = shape["center_m"][2]
    half = shape["radius_m"] if shape["type"] == "sphere" else shape["extent_m"][2]
    return center - half, center + half


def validate_zone(zone, seen):
    if (not isinstance(zone, dict) or not {"id", "kind", "shape", "damage"} <= zone.keys()
            or zone.keys() - {"id", "kind", "shape", "surface_m", "damage", "look"}
            or not isinstance(zone["id"], str) or not ID.fullmatch(zone["id"]) or zone["id"] in seen
            or zone["kind"] not in KINDS or not validate_shape(zone["shape"])):
        return False
    seen.add(zone["id"])
    if "surface_m" in zone:
        low, high = height_span(zone["shape"])
        if not finite(zone["surface_m"], low, high):
            return False
    damage = zone["damage"]
    if (not isinstance(damage, dict) or damage.get("cause") not in CAUSES
            or damage.get("mode") == "kill" and damage.keys() != {"mode", "cause"}
            or damage.get("mode") == "per_second" and (damage.keys() != {"mode", "per_second", "cause"}
                                                       or not finite(damage["per_second"], 1, 100000))
            or damage.get("mode") not in ("kill", "per_second")):
        return False
    look = zone.get("look")
    if look is None:
        return True
    glow = look.get("glow") if isinstance(look, dict) else None
    if (not isinstance(look, dict) or look.keys() - {"preset", "tint", "glow"} or not PRESET.fullmatch(str(look.get("preset", "none")))
            or "tint" in look and not vector(look["tint"], 3, 0, 4)):
        return False
    if glow is None:
        return True
    spheres = glow.get("spheres", []) if isinstance(glow, dict) else None
    return (isinstance(glow, dict) and not glow.keys() - {"color", "extinction_per_m", "spheres"}
            and ("color" not in glow or vector(glow["color"], 3, 0, 1))
            and ("extinction_per_m" not in glow or finite(glow["extinction_per_m"], 0, 10))
            and isinstance(spheres, list) and len(spheres) <= MAX_GLOW_SPHERES
            and all(isinstance(sphere, dict) and sphere.keys() == {"center_m", "radius_m"}
                    and vector(sphere["center_m"]) and finite(sphere["radius_m"], 0.1, 10000)
                    for sphere in spheres))


def validate(component):
    """Raise ValueError unless the component matches format 1 of the spec."""
    zones = component.get("zones") if isinstance(component, dict) else None
    provenance = component.get("provenance") if isinstance(component, dict) else None
    seen = set()
    if (not isinstance(component, dict) or component.get("format") != FORMAT
            or component.keys() - {"format", "zones", "provenance"}
            or not isinstance(zones, list) or len(zones) > MAX_ZONES
            or not all(validate_zone(zone, seen) for zone in zones)):
        raise ValueError("Invalid MILF hazard component.")
    if provenance is not None and (not isinstance(provenance, dict) or provenance.keys() - {"game", "rules"}
                                   or provenance.get("game") not in GAMES
                                   or not isinstance(provenance.get("rules", ""), str)
                                   or len(provenance.get("rules", "")) > 40):
        raise ValueError("Invalid MILF hazard provenance.")
    return component
