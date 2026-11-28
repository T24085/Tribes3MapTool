"""Find a Tribes: Ascend map's own out-of-bounds box (Tribes3-Modded/Tribes3MapTool#12).

Ascend maps fence the play area with long, thin ``BlockingVolume`` walls (usually behind a visible
force field) and cap it with a flat volume high above. Their brush shapes are saved as convex hulls
on each volume's ``BrushComponent``. The gameplay level's ``WorldInfo`` also sets ``KillZ`` (fall
death height) and ``StallZ`` (flight ceiling).

``read_source_bounds`` reads those from the map's level packages, and ``derive_play_box`` picks the
walls that enclose every CTF marker. Everything is in metres in the draft frame: source units
divided by the import's units per metre, with no axis changes.
"""

from ascend_collision import array_fields, convex_elements
from ascend_materials import properties, tag_data, typed_tag
from unreal_package_reader import open_package

MAX_VOLUMES = 4096  # ArxNovena, a city map, has 781 blocking volumes.
WALL_RATIO = 0.05  # A wall is at most this thin across, relative to its length.
CEILING_COVER = 0.9  # A ceiling volume covers at least this share of the play box.
# Ascend's visible out-of-bounds force fields: flat panels that never closed the box (Tartarus leaves
# 413 m gaps at two corners) and only showed the edge; the blocking volumes did the blocking. The
# import leaves them out and the Boundaries walls draw the edge instead (Tribes3MapTool#30).
BOUNDARY_PANEL_MESHES = ("common_ground.sm.sm_creativitywall",)
BOUNDARY_PANEL_REASON = ("Ascend out-of-bounds force-field panel, left out. Set Wall appearance in "
                         "Boundaries… to show the edge.")


def boundary_panel(mesh_source):
    """True for a source mesh that is one of Ascend's visible out-of-bounds force-field panels."""
    return (mesh_source or "").casefold().startswith(BOUNDARY_PANEL_MESHES)


def volume_boxes(path, units):
    """Return ([(name, lower_m, upper_m)], {"killz_m": .., "stallz_m": ..}) from one level package."""
    boxes, limits = [], {}
    with open_package(path, "ascend") as (report, stream):
        objects = {row["index"]: row for row in report["imports"] + report["exports"]}
        children = {}
        for export in report["exports"]:
            children.setdefault(export["outer"], []).append(export)
        for export in report["exports"]:
            klass = export["class_name"]
            if klass == "WorldInfo":
                tags, _ = properties(export, report, stream, objects)
                for key in ("killz", "stallz"):
                    tag = typed_tag(tags, key, "FloatProperty")
                    if tag and tag["size"] == 4:
                        limits[key + "_m"] = tag_data(stream, tag).fields("f")[0] / units
                continue
            if klass != "BlockingVolume":
                continue
            if len(boxes) >= MAX_VOLUMES:
                raise ValueError("Too many Ascend blocking volumes to read the map bounds.")
            try:
                tags, _ = properties(export, report, stream, objects)
                location = typed_tag(tags, "location", "StructProperty", "vector")
                origin = tag_data(stream, location).fields("3f") if location and location["size"] == 12 else (0, 0, 0)
                if any(typed_tag(tags, key, "StructProperty") for key in ("rotation", "drawscale3d", "prepivot")) \
                        or typed_tag(tags, "drawscale", "FloatProperty"):
                    continue  # Only unrotated, unscaled volumes are read; others stay in review.
                points = []
                for child in children.get(export["index"], ()):
                    if child["class_name"] != "BrushComponent":
                        continue
                    component, _ = properties(child, report, stream, objects)
                    aggregate = typed_tag(component, "brushagggeom", "StructProperty", "kaggregategeom")
                    if not aggregate:
                        continue
                    arrays = array_fields(tag_data(stream, aggregate), report["names"])
                    convex = typed_tag(arrays.values(), "convexelems", "ArrayProperty")
                    if convex:
                        for hull in convex_elements(convex, stream, report["names"], 1, 64):
                            points.extend(hull["vertices_m"])
            except ValueError:
                continue
            if points:
                lower = [(origin[axis] + min(point[axis] for point in points)) / units for axis in range(3)]
                upper = [(origin[axis] + max(point[axis] for point in points)) / units for axis in range(3)]
                boxes.append((export["name"], lower, upper))
    return boxes, limits


def read_source_bounds(paths, units):
    """Read blocking volumes and WorldInfo limits from every level package of one map."""
    boxes, limits = [], {}
    for path in paths:
        found, values = volume_boxes(path, units)
        boxes.extend(found)
        for key, value in values.items():
            limits.setdefault(key, value)
    return boxes, limits


def derive_play_box(boxes, markers_m):
    """Pick the walls that enclose every marker. Return a play box dict, or None when the map has none.

    The play box is the space between the walls' inner faces: ``lower_m``/``upper_m`` in XY, the
    walls' vertical span ``wall_z_m``, the ceiling's underside ``ceiling_m`` (or None) and the
    ``volumes`` used.
    """
    if not markers_m:
        return None
    low = [min(point[axis] for point in markers_m) for axis in range(3)]
    high = [max(point[axis] for point in markers_m) for axis in range(3)]
    sides = {}  # (axis, sign) -> (inner face, name, box)
    for name, lower, upper in boxes:
        size = [b - a for a, b in zip(lower, upper)]
        for axis in (0, 1):
            other = 1 - axis
            if size[axis] > WALL_RATIO * size[other]:
                continue  # Not thin across this axis.
            # A boundary wall spans the markers along its length and their height range.
            if lower[other] > low[other] or upper[other] < high[other] or lower[2] > low[2] or upper[2] < high[2]:
                continue
            if upper[axis] <= low[axis]:
                key, face = (axis, -1), upper[axis]
                if key not in sides or face > sides[key][0]:
                    sides[key] = face, name, (lower, upper)
            elif lower[axis] >= high[axis]:
                key, face = (axis, 1), lower[axis]
                if key not in sides or face < sides[key][0]:
                    sides[key] = face, name, (lower, upper)
    if len(sides) != 4:
        return None
    lower_xy = [sides[0, -1][0], sides[1, -1][0]]
    upper_xy = [sides[0, 1][0], sides[1, 1][0]]
    walls = [entry[2] for entry in sides.values()]
    wall_z = [max(box[0][2] for box in walls), min(box[1][2] for box in walls)]
    area = (upper_xy[0] - lower_xy[0]) * (upper_xy[1] - lower_xy[1])
    ceiling, ceiling_name = None, None
    for name, lower, upper in boxes:
        if lower[2] < high[2]:
            continue
        cover = [max(0, min(upper[axis], upper_xy[axis]) - max(lower[axis], lower_xy[axis])) for axis in (0, 1)]
        if cover[0] * cover[1] >= CEILING_COVER * area and (ceiling is None or lower[2] < ceiling):
            ceiling, ceiling_name = lower[2], name
    names = sorted({entry[1] for entry in sides.values()} | ({ceiling_name} if ceiling_name else set()))
    return dict(lower_m=lower_xy, upper_m=upper_xy, wall_z_m=wall_z, ceiling_m=ceiling, volumes=names)


def boundary_recipe(play, terrain_elevation_cm, limits=None):
    """Boundaries settings that rebuild the source walls: solid perimeter, the source ceiling and,
    when KillZ is below the terrain, a kill floor at KillZ (#20)."""
    low_cm, high_cm = terrain_elevation_cm
    top_m = play["ceiling_m"] if play["ceiling_m"] is not None else play["wall_z_m"][1]
    ceiling_m = max(0.0, round(top_m - high_cm / 100, 2))
    options = {"perimeter": True, "flag_inset_m": None,
               "wall_limits": {"ceiling_m": min(ceiling_m, 10_000.0), "floor_m": None}}
    killz = (limits or {}).get("killz_m")
    if killz is not None and killz * 100 < low_cm:
        options["kill_floor_m"] = min(round(low_cm / 100 - killz, 2), 10_000.0)
    stallz = (limits or {}).get("stallz_m")
    if stallz is not None and 0 < stallz - low_cm / 100 <= 20_000:
        # Jetpacks stall above StallZ: a hard flight ceiling with no fade band (#22).
        options["flight_ceiling"] = {"height_m": round(stallz - low_cm / 100, 2)}
    return {"format": 1, "elevation_cm": [low_cm, high_cm], "options": options}


def describe(play, limits):
    """One review line naming what the bounds came from."""
    width, depth = (b - a for a, b in zip(play["lower_m"], play["upper_m"]))
    text = (f"Source out-of-bounds box {width:,.0f} × {depth:,.0f} m from {len(play['volumes'])} blocking volumes; "
            "Boundaries walls block players there")
    if play["ceiling_m"] is not None:
        text += f" with a ceiling at {play['ceiling_m']:,.0f} m"
    notes = []
    if "killz_m" in limits:
        text += f", and a kill floor at KillZ ({limits['killz_m']:,.0f} m)"
    if "stallz_m" in limits:
        text += f", and a flight ceiling at StallZ ({limits['stallz_m']:,.0f} m)"
    if notes:
        text += ". Not applied: " + ", ".join(notes)
    return (text + ".")[:240]
