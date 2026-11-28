"""Convert a Tribes: Ascend map's kill zones into the MILF hazard component (T3BackEndServer#150).

Ascend pits kill with a ``UTKillZVolume`` (a thin brush slab, 10,000 damage per second) and show
"lava" with ``FogVolumeSphericalDensityInfo`` volumes under it: a self-lit fog whose material's
``EmissiveColor`` glows and whose density peaks at the centre (Katabatic's generator pits). There
is no lava mesh. A kill volume with glow spheres in its column becomes a ``lava`` zone; one with
none is a ``void`` (fall death).

Source units divide by the import's units per metre, then Y flips into MILF axes, as for every
MILF component.
"""

from ascend_collision import array_fields, convex_elements
from ascend_materials import properties, tag_data, typed_tag, vector_overrides
from milf_hazard import FORMAT, validate
from unreal_package_reader import open_package

RULES = "ascend-hazard-1"
LAVA_LOOK = "ascend-lava"  # Ascend's default lava look; a map can override it.
KILL_VOLUMES = {"utkillzvolume", "trkillzvolume"}
FOG_SPHERE = "FogVolumeSphericalDensityComponent"
KILL_DAMAGE = 1000  # Damage per second at or above this kills on entry.
MAX_VOLUMES = 256


def hull_xy(points):
    """Counter-clockwise convex hull of 2D points (Andrew's monotone chain)."""
    points = sorted(set(points))
    if len(points) < 3:
        return points

    def side(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and side(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and side(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def inside(outline, x, y):
    return all((bx - ax) * (y - ay) - (by - ay) * (x - ax) >= -1e-6
               for (ax, ay), (bx, by) in zip(outline, outline[1:] + outline[:1]))


def scalar(tags, stream, name, kind="FloatProperty"):
    tag = typed_tag(tags, name, kind)
    return tag_data(stream, tag).fields("f")[0] if tag and tag["size"] == 4 else None


def milf(point, units):
    x, y, z = (value / units for value in point)
    return [round(x, 3), round(-y, 3), round(z, 3)]


def kill_volume(export, report, stream, objects, children, units):
    tags, _ = properties(export, report, stream, objects)
    if any(typed_tag(tags, key, "StructProperty") for key in ("rotation", "drawscale3d", "prepivot")) \
            or typed_tag(tags, "drawscale", "FloatProperty"):
        return None  # Only unrotated, unscaled volumes are read, as for the map bounds.
    location = typed_tag(tags, "location", "StructProperty", "vector")
    origin = tag_data(stream, location).fields("3f") if location and location["size"] == 12 else (0, 0, 0)
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
                points.extend(milf([o + v for o, v in zip(origin, vertex)], units) for vertex in hull["vertices_m"])
    if not points:
        return None
    rate = scalar(tags, stream, "damagepersec")
    return dict(name=export["name"], outline=hull_xy([(x, y) for x, y, _ in points]),
                floor=min(z for *_, z in points), top=max(z for *_, z in points),
                per_second=rate if rate is not None else KILL_DAMAGE * 10)


def fog_sphere(export, report, stream, objects, units):
    tags, _ = properties(export, report, stream, objects)
    center = typed_tag(tags, "spherecenter", "StructProperty", "vector")
    radius, density = scalar(tags, stream, "sphereradius"), scalar(tags, stream, "maxdensity")
    if not center or center["size"] != 12 or not radius or density is None:
        return None
    color = None
    material = typed_tag(tags, "fogmaterial", "ObjectProperty")
    if material and material["size"] == 4:
        row = objects.get(tag_data(stream, material).fields("i")[0])
        if row and row.get("serial_offset") is not None:
            color = vector_overrides(properties(row, report, stream, objects)[0], report, stream).get("emissivecolor")
    if color is None:
        light = typed_tag(tags, "approxfoglightcolor", "StructProperty", "linearcolor")
        color = list(tag_data(stream, light).fields("4f")[:3]) if light and light["size"] == 16 else None
    return dict(center=milf(tag_data(stream, center).fields("3f"), units), radius=radius / units,
                extinction=density * units, color=[min(1.0, max(0.0, value)) for value in color] if color else None)


def read_level(path, units):
    volumes, spheres = [], []
    with open_package(path, "ascend") as (report, stream):
        objects = {row["index"]: row for row in report["imports"] + report["exports"]}
        children = {}
        for export in report["exports"]:
            children.setdefault(export["outer"], []).append(export)
        for export in report["exports"]:
            try:
                if export["class_name"].casefold() in KILL_VOLUMES:
                    if len(volumes) >= MAX_VOLUMES:
                        raise ValueError("Too many Ascend kill volumes.")
                    volume = kill_volume(export, report, stream, objects, children, units)
                    if volume:
                        volumes.append(volume)
                elif export["class_name"] == FOG_SPHERE:
                    sphere = fog_sphere(export, report, stream, objects, units)
                    if sphere:
                        spheres.append(sphere)
            except (ValueError, KeyError, TypeError):
                continue  # An unreadable volume stays in review, as before.
    return volumes, spheres


def zone(volume, spheres):
    outline = [[round(x, 3), round(y, 3)] for x, y in volume["outline"]]
    glows = [sphere for sphere in spheres
             if inside(volume["outline"], *sphere["center"][:2]) and sphere["center"][2] <= volume["top"] + sphere["radius"]]
    kill = volume["per_second"] >= KILL_DAMAGE
    result = dict(id=volume["name"].casefold(), kind="lava" if glows else "void",
                  damage=dict(mode="kill", cause="lava" if glows else "fall") if kill else
                  dict(mode="per_second", per_second=round(volume["per_second"], 3), cause="lava" if glows else "fall"))
    floor, top = volume["floor"], volume["top"]
    if glows:
        floor = min(floor, *(sphere["center"][2] - sphere["radius"] for sphere in glows))
        lowest = min(glows, key=lambda sphere: sphere["center"][2])
        # The densest, lowest glow is where Ascend's "lava" reads; the surface sits at its centre.
        result["surface_m"] = round(lowest["center"][2], 3)
        glow = dict(extinction_per_m=round(min(10.0, max(sphere["extinction"] for sphere in glows)), 4),
                    spheres=[dict(center_m=sphere["center"], radius_m=round(sphere["radius"], 3))
                             for sphere in sorted(glows, key=lambda sphere: sphere["center"][2])[:8]])
        color = next((sphere["color"] for sphere in glows if sphere["color"]), None)
        if color:
            glow["color"] = [round(value, 4) for value in color]
        result["look"] = dict(preset=LAVA_LOOK, glow=glow)
    else:
        result["look"] = dict(preset="none")
    result["shape"] = dict(type="prism", outline_m=outline, floor_m=round(floor, 3), top_m=round(top, 3))
    return result


def read_ascend_hazards(paths, units):
    """Return the MILF hazard component for one Ascend map's level packages, or None without kill zones."""
    volumes, spheres = [], []
    for path in paths:
        found, glows = read_level(path, units)
        volumes.extend(found)
        spheres.extend(glows)
    zones, seen = [], set()
    for volume in sorted(volumes, key=lambda item: item["name"].casefold()):
        if len(volume["outline"]) < 3 or volume["name"].casefold() in seen:
            continue
        seen.add(volume["name"].casefold())
        zones.append(zone(volume, spheres))
    if not zones:
        return None
    return validate(dict(format=FORMAT, zones=zones[:64], provenance=dict(game="ascend", rules=RULES)))
