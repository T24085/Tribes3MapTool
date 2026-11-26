"""Find a Tribes: Vengeance map's play area from its BoundaryVolume (Tribes3-Modded/Tribes3MapTool#17).

Each Vengeance multiplayer map has one ``Movement.BoundaryVolume``: a convex brush volume that can
be switched on or off (``Active``, on unless the map turns it off). The game keeps the active one as
``Level.Game.BoundaryVolume``. The stock script (Gameplay.u) kills any player and destroys any vehicle
outside it at once, and returns a loose flag outside it home. There is no warning or grace time.

``read_boundary`` returns the volume's footprint in metres in the draft frame: source units divided
by the import's units per metre, no axis changes. A brush point's world position is the actor's
``Location`` plus the point minus ``PrePivot``. Rotated or scaled volumes are left for review.
"""

import math

from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor, open_package
from vengeance_brushes import identity_brush_scale
from vengeance_bsp import model_arrays

MAX_CORNERS = 32  # Solid polygon walls support up to 32 corners.
MARGIN_M = 1.0  # Polygon wall corners must sit strictly inside the playable bounds.


def convex_hull(points):
    """Counter-clockwise convex hull of 2D points (monotone chain), without repeated or collinear points."""
    points = sorted(set(points))
    if len(points) < 3:
        return points

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def volume_fields(tags, stream):
    fields = {}
    for tag in tags:
        key = tag["name"].casefold()
        value = Cursor(stream, tag["value_offset"], tag["value_offset"] + tag["size"])
        if key in ("location", "prepivot") and tag["size"] == 12:
            fields[key] = value.fields("3f")
        elif key == "brush" and tag["kind"] == "ObjectProperty":
            fields[key] = value.index(False)
        elif key == "active" and tag["kind"] == "BoolProperty":
            fields[key] = bool(tag["boolean"])
        elif key == "rotation" and (tag["structure"] or "").casefold() == "rotator":
            fields[key] = value.fields("3i")
        elif key in ("mainscale", "postscale", "drawscale", "drawscale3d"):
            fields.setdefault("scaled", []).append(tag["name"])
    return fields


def read_boundary(path, units):
    """Return the active BoundaryVolume as a dict, or (None, reason) when there isn't a usable one."""
    found, reasons = [], []
    with open_package(path, "vengeance") as (report, stream):
        objects = {row["index"]: row for row in report["imports"] + report["exports"]}
        for export in report["exports"]:
            if export["class_name"] != "BoundaryVolume":
                continue
            name = export["name"]
            tags, _, errors, _ = read_properties(stream, report["summary"], export, report["names"], objects)
            if errors:
                reasons.append(f"{name} has unreadable properties")
                continue
            fields = volume_fields(tags, stream)
            if fields.get("active") is False:
                continue
            if any(fields.get("rotation", (0, 0, 0))):
                reasons.append(f"{name} is rotated")
                continue
            if any(scale_tag(tags, stream, report["names"], key) for key in ("mainscale", "postscale")):
                reasons.append(f"{name} is scaled")
                continue
            model = objects.get(fields.get("brush", 0))
            if not model or model["class_name"].casefold() != "model":
                reasons.append(f"{name} has no brush model")
                continue
            try:
                points = model_arrays(report, stream, model, objects)[0]
            except ValueError as error:
                reasons.append(f"{name} brush could not be read: {error}")
                continue
            location = fields.get("location", (0, 0, 0))
            pivot = fields.get("prepivot", (0, 0, 0))
            world = [[(point[axis] - pivot[axis] + location[axis]) / units for axis in range(3)] for point in points]
            if not world or not all(math.isfinite(value) for point in world for value in point):
                reasons.append(f"{name} has no finite brush points")
                continue
            found.append((name, world))
    if len(found) != 1:
        return None, ("; ".join(reasons) or "no BoundaryVolume") if not found else "several active BoundaryVolumes"
    name, world = found[0]
    hull = convex_hull([(round(x, 3), round(y, 3)) for x, y, _ in world])
    return dict(name=name, outline_m=hull, z_m=[min(p[2] for p in world), max(p[2] for p in world)]), None


def scale_tag(tags, stream, names, key):
    """True when a brush scale struct is not the identity."""
    tag = next((tag for tag in tags if tag["name"].casefold() == key), None)
    if tag is None:
        return False
    try:
        identity_brush_scale(Cursor(stream, tag["value_offset"], tag["value_offset"] + tag["size"]), names)
    except ValueError:
        return True
    return False


def play_area(volume, terrain_bounds_cm, markers_m):
    """Turn a volume into (bounds_cm, polygon_m or None, note). Returns None when markers fall outside."""
    outline = volume["outline_m"]
    if len(outline) < 3:
        return None
    xs, ys = [x for x, _ in outline], [y for _, y in outline]
    lower, upper = [min(xs), min(ys)], [max(xs), max(ys)]
    if any(not (lower[0] < m[0] < upper[0] and lower[1] < m[1] < upper[1]) for m in markers_m):
        return None
    rectangle = len(outline) == 4 and all(any(abs(x - edge) < 1e-3 for edge in (lower[0], upper[0])) and
                                         any(abs(y - edge) < 1e-3 for edge in (lower[1], upper[1]))
                                         for x, y in outline)
    terrain_lower = [value / 100 for value in terrain_bounds_cm[0]]
    terrain_upper = [value / 100 for value in terrain_bounds_cm[1]]
    margin = 0 if rectangle else MARGIN_M
    box_lower = [low - margin for low in lower]
    box_upper = [high + margin for high in upper]
    inside = all(t_low < low and high < t_high
                 for low, high, t_low, t_high in zip(box_lower, box_upper, terrain_lower, terrain_upper))
    if rectangle or not inside or len(outline) > MAX_CORNERS:
        clipped_lower = [max(a, b) for a, b in zip(lower, terrain_lower)]
        clipped_upper = [min(a, b) for a, b in zip(upper, terrain_upper)]
        if any(low >= high for low, high in zip(clipped_lower, clipped_upper)):
            return None
        note = None if rectangle and inside else "The volume's outline reaches past the terrain or has too many corners, so its bounding rectangle is used."
        return [[v * 100 for v in clipped_lower], [v * 100 for v in clipped_upper]], None, note
    return [[v * 100 for v in box_lower], [v * 100 for v in box_upper]], [list(point) for point in outline], None


def boundary_recipe(volume, polygon_m, terrain_elevation_cm):
    """Solid walls on the volume (its outline when usable) with the ceiling at the volume's top."""
    low_cm, high_cm = terrain_elevation_cm
    ceiling_m = round(volume["z_m"][1] - high_cm / 100, 2)
    options = {"perimeter": True, "flag_inset_m": None,
               "wall_limits": {"ceiling_m": min(ceiling_m, 10_000.0) if ceiling_m >= 0 else None, "floor_m": None}}
    if polygon_m:
        options["wall_polygon_m"] = polygon_m
    return {"format": 1, "elevation_cm": [low_cm, high_cm], "options": options}


def describe(volume, polygon_m, note, recipe):
    xs = [x for x, _ in volume["outline_m"]]
    ys = [y for _, y in volume["outline_m"]]
    text = (f"Play area from the Vengeance {volume['name']} ({max(xs) - min(xs):,.0f} × {max(ys) - min(ys):,.0f} m"
            f"{', ' + str(len(polygon_m)) + '-corner outline' if polygon_m else ''}). Boundaries preset: solid walls")
    ceiling = recipe["options"]["wall_limits"]["ceiling_m"]
    text += f" with the ceiling at the volume's top ({volume['z_m'][1]:,.0f} m)." if ceiling is not None else \
        "; the volume's top is below the highest terrain, so the default ceiling is used."
    text += (" The original kills players and destroys vehicles outside the volume at once and returns loose flags"
             " home; walls stand in for that until the runtime boundary layer (T3BackEndServer#112).")
    if note:
        text += " " + note
    return text[:600]
