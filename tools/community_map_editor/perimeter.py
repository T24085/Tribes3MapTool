"""Author and validate the optional collision perimeter of an imported draft."""

import math
from typing import NamedTuple, Optional

from intake_contract import BOUNDARY_RECIPE, finite, playable_bounds
from perimeter_collision import TAG, blocks_pawns, configure_wall, filter_tags, has_wall_collision, probe_profiles, read_channels, validate_channels
from terrain_grid import height_range


THICKNESS = 1000
CLEARANCE = 100
SIDES = (("West", "East"), ("South", "North"))
WALL_SIDES = (*SIDES[0], *SIDES[1])
OPEN_CEILING = TAG + "OpenCeiling"
SEALED_FLOOR = TAG + "SealedFloor"
DEFAULT_CEILING_M = 2000
DEFAULT_DEPTH_M = 500
FLAG_CORNERS = ("T3MissionFlagMin", "T3MissionFlagMax")
FLAG_POLYGON = "T3MissionFlagPolygon"
FLAG_VERTEX = "T3MissionFlagVertex"
FLAG_COUNT = "T3MissionFlagVertexCount"
FLAG_FLOOR = "T3MissionFlagFloor"
FLAG_CEILING = "T3MissionFlagCeiling"
FLAG_CAPS = {FLAG_FLOOR: "T3MissionFlagNeedsFloor", FLAG_CEILING: "T3MissionFlagNeedsCeiling"}
FLAG_RETURN_TIMER = "T3MissionFlagReturnTimer"
FLAG_RETURN_SECONDS = "T3MissionFlagReturnSeconds"
FLAG_REQUIREMENTS = {**FLAG_CAPS, FLAG_RETURN_TIMER: "T3MissionFlagNeedsReturnTimer"}
MAX_FLAG_RETURN_SECONDS = 60
MAX_VERTICES = 32
SOLID_POLYGON = TAG + "Polygon"
POLYGON_REQUIRED = TAG + "PolygonRequired"
VISUAL_TAG = TAG + "Visual"
VISUAL_REQUIRED = TAG + "VisualRequired"
WALL_STYLES = {"energy": (0.08, 1.05, 1.35), "warning": (1.45, 0.44, 0.06)}
WARNING_AUDIENCES = {None: "On foot and in vehicles", "foot": "On foot only", "vehicle": "Vehicles only"}
DEFAULT_BOUNDARY_OPTIONS = {"perimeter": False, "flag_inset_m": None}
BOUNDARY_OPTIONS = DEFAULT_BOUNDARY_OPTIONS.keys() | {
    "wall_limits", "flag_polygon_m", "wall_polygon_m", "wall_block_channels", "wall_visual",
    "warn_players", "warning_polygon_m", "warning_limits", "warning_audience", "flag_limits", "flag_return_delay_s",
    "kill_floor_m", "flight_ceiling",
}
# Hosts kill players below a TargetPoint with this tag (T3BackEndServer#112, Tribes3MapTool#20).
KILL_FLOOR = "T3MissionKillFloor"
# Hosts stop climbing at this height, fading over the band from the optional lower marker (#22).
FLIGHT_CEILING = "T3MissionFlightCeiling"
FLIGHT_FADE = "T3MissionFlightCeilingFade"


class PolygonWalls(NamedTuple):
    vertices: tuple
    z_min: float
    z_max: float
    ceiling: bool
    floor: bool


class FlagBoundary(NamedTuple):
    points: tuple
    floor: Optional[float] = None
    ceiling: Optional[float] = None
    return_delay_s: Optional[int] = None


def terrain_elevations(terrain):
    z = terrain["origin_cm"][2]
    low, high = height_range(terrain)
    scale = terrain["cm_per_m"]
    return [z + low * scale, z + high * scale]


def read_boundary_recipe(rows):
    for row in rows:
        properties = row.get("properties", {})
        if row["status"] != "boundary" or BOUNDARY_RECIPE not in properties:
            continue
        recipe = properties[BOUNDARY_RECIPE]
        if (not isinstance(recipe, dict) or recipe.keys() != {"format", "elevation_cm", "options"}
                or type(recipe["format"]) is not int or recipe["format"] != 1):
            raise ValueError("Invalid shared boundary recipe.")
        elevation, options = recipe["elevation_cm"], recipe["options"]
        if (not isinstance(elevation, list) or len(elevation) != 2
                or not all(finite(value, 10_000_000) for value in elevation) or elevation[0] > elevation[1]
                or not isinstance(options, dict) or not DEFAULT_BOUNDARY_OPTIONS.keys() <= options.keys() <= BOUNDARY_OPTIONS):
            raise ValueError("Invalid shared boundary recipe.")
        return recipe


def mission_extents(bounds, terrain, *, depth_m=DEFAULT_DEPTH_M, height_m=DEFAULT_CEILING_M, elevation_cm=None):
    lower, upper = bounds
    low, high = terrain_elevations(terrain) if elevation_cm is None else elevation_cm
    return ([*lower, low - depth_m * 100], [*upper, high + height_m * 100])


def wall_boxes(lower, upper, *, ceiling=True, floor=False):
    middle = [(a + b) / 2 for a, b in zip(lower, upper)]
    span = [b - a for a, b in zip(lower, upper)]
    if not all(math.isfinite(distance) and distance > 2 * CLEARANCE for distance in span):
        raise ValueError("The playable perimeter is too narrow for CTF markers.")
    ceiling_overlap = THICKNESS if ceiling else 0
    boxes = {}
    for axis, labels in enumerate(SIDES):
        for sign, label in zip((-1, 1), labels):
            center = [middle[0], middle[1], middle[2] + ceiling_overlap / 2]
            center[axis] = (lower if sign < 0 else upper)[axis] + sign * THICKNESS / 2
            size = [span[0] + 2 * THICKNESS, span[1] + 2 * THICKNESS, span[2] + ceiling_overlap]
            size[axis] = THICKNESS
            boxes[label] = (center, size)
    cap_size = [span[0] + 2 * THICKNESS, span[1] + 2 * THICKNESS, THICKNESS]
    if ceiling:
        boxes["Ceiling"] = ([middle[0], middle[1], upper[2] + THICKNESS / 2], cap_size)
    if floor:
        boxes["Floor"] = ([middle[0], middle[1], lower[2] - THICKNESS / 2], cap_size.copy())
    return boxes


def check_marker_clearance(lower, upper, markers):
    for label, point in markers:
        if not all(a + CLEARANCE <= n <= b - CLEARANCE for a, b, n in zip(lower, upper, point)):
            raise ValueError(f"{label} must be at least one metre inside the mission perimeter.")


def height_limits(limits):
    if not isinstance(limits, dict) or limits.keys() != {"ceiling_m", "floor_m"}:
        raise ValueError("Invalid mission boundary height settings.")
    ceiling, floor = limits["ceiling_m"], limits["floor_m"]
    for label, distance in (("Ceiling", ceiling), ("Floor", floor)):
        if distance is not None and (type(distance) not in (int, float) or not 0 <= distance <= 10_000):
            raise ValueError(f"{label} distance must be between 0 and 10,000 metres.")
    return ceiling, floor


def plan_walls(bounds, terrain, markers, limits, polygon=None, *, elevation_cm=None):
    if limits is None:
        limits = {"ceiling_m": DEFAULT_CEILING_M, "floor_m": None}
    ceiling, floor = height_limits(limits)
    lower, upper = mission_extents(
        bounds, terrain, depth_m=DEFAULT_DEPTH_M if floor is None else floor,
        height_m=DEFAULT_CEILING_M if ceiling is None else ceiling,
        elevation_cm=elevation_cm,
    )
    if polygon is not None:
        vertices = parse_polygon(polygon, "Solid perimeter")
        check_polygon_outline(vertices, "Solid perimeter", bounds)
        geometry = PolygonWalls(vertices, lower[2], upper[2], ceiling is not None, floor is not None)
        check_polygon_walls(geometry, markers)
        return geometry
    check_marker_clearance(lower, upper, markers)
    return wall_boxes(lower, upper, ceiling=ceiling is not None, floor=floor is not None)


def wall_appearance(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() != {"style", "distance_m"}:
        raise ValueError("Invalid mission boundary appearance.")
    style, distance = value["style"], value["distance_m"]
    if not isinstance(style, str) or style not in WALL_STYLES:
        raise ValueError("Choose a boundary style.")
    if type(distance) not in (int, float) or not 1 <= distance <= 10_000:
        raise ValueError("Boundary fade distance must be between 1 and 10,000 metres.")
    return {"style": style, "distance_m": float(distance)}


def wall_surface(walls):
    if isinstance(walls, PolygonWalls):
        return walls
    ceiling, floor = "Ceiling" in walls, "Floor" in walls
    lower, upper = verified_extents(walls, ceiling=ceiling, floor=floor)
    return PolygonWalls(rectangle_outline((lower[:2], upper[:2])), lower[2], upper[2], ceiling, floor)


def warning_surface(bounds, terrain, walls, polygon, limits, *, elevation_cm=None):
    if walls and polygon is None and limits is None:
        return wall_surface(walls)
    if polygon is None:
        vertices = wall_surface(walls).vertices if walls else rectangle_outline(bounds)
    else:
        vertices = parse_polygon(polygon, "Warning boundary")
        check_polygon_outline(vertices, "Warning boundary", bounds)
    if limits is None:
        return PolygonWalls(vertices, 0, 0, False, False)
    ceiling, floor = height_limits(limits)
    if ceiling is None and floor is None:
        return PolygonWalls(vertices, 0, 0, False, False)
    lower, upper = mission_extents(bounds, terrain, depth_m=floor or 0, height_m=ceiling or 0,
                                   elevation_cm=elevation_cm)
    if ceiling is not None and floor is not None and lower[2] >= upper[2]:
        raise ValueError("The warning ceiling must be above its floor.")
    return PolygonWalls(vertices, lower[2], upper[2], ceiling is not None, floor is not None)


def plan_boundaries(header, terrain, rows, *, perimeter, flag_inset_m, wall_limits=None, flag_polygon_m=None,
                    wall_polygon_m=None, wall_block_channels=None, wall_visual=None, warn_players=False,
                    warning_polygon_m=None, warning_limits=None, warning_audience=None, flag_limits=None,
                    flag_return_delay_s=None, kill_floor_m=None, flight_ceiling=None):
    if kill_floor_m is not None:
        kill_floor_height(header, terrain, rows, kill_floor_m)
    if flight_ceiling is not None:
        flight_ceiling_heights(header, terrain, rows, flight_ceiling)
    if type(perimeter) is not bool:
        raise ValueError("Choose whether to add solid side walls.")
    if type(warn_players) is not bool:
        raise ValueError("Choose whether to warn players outside the mission area.")
    if warning_audience is not None and (not isinstance(warning_audience, str) or warning_audience not in WARNING_AUDIENCES):
        raise ValueError("Choose who gets the warning.")
    if not warn_players and any(value is not None for value in (warning_polygon_m, warning_limits, warning_audience)):
        raise ValueError("Enable player warnings before setting their shape, height limits or audience.")
    channels = validate_channels(wall_block_channels)
    appearance = wall_appearance(wall_visual)
    if any(value is not None for value in (wall_limits, wall_polygon_m, channels, appearance)) and not perimeter:
        raise ValueError("Enable solid side walls before setting their shape, limits, collision channels or appearance.")
    if flag_inset_m is not None and flag_polygon_m is not None:
        raise ValueError("Choose either the flag-only inset or a flag-only polygon.")
    if (flag_limits is not None or flag_return_delay_s is not None) and flag_inset_m is None and flag_polygon_m is None:
        raise ValueError("Set a flag-only inset or draw a polygon before setting its height limits or return timer.")
    if flag_return_delay_s is not None and (type(flag_return_delay_s) is not int
                                           or not 1 <= flag_return_delay_s <= MAX_FLAG_RETURN_SECONDS):
        raise ValueError(f"Enter a flag return delay from 1 to {MAX_FLAG_RETURN_SECONDS} whole seconds.")
    if not perimeter and flag_inset_m is None and flag_polygon_m is None and not warn_players:
        return None, None, None
    bounds = playable_bounds(header, rows)
    recipe = read_boundary_recipe(rows)
    elevation_cm = recipe["elevation_cm"] if recipe else None
    markers = [(row["name"] or row["kind"], row["position_cm"])
               for row in rows if row["status"] == "marker"] if blocks_pawns(channels) else []
    flags = [(row["name"] or "Flag", row["position_cm"])
             for row in rows if row["status"] == "marker" and row["kind"] == "flag"]
    walls = plan_walls(bounds, terrain, markers, wall_limits, wall_polygon_m,
                       elevation_cm=elevation_cm) if perimeter else None
    limit = flag_bounds(bounds, flag_inset_m, flags) if flag_inset_m is not None else None
    if flag_polygon_m is not None:
        limit = parse_polygon(flag_polygon_m, "Flag boundary")
        check_flag_polygon(limit, flags, bounds)
    if isinstance(walls, PolygonWalls) and limit is not None and blocks_pawns(channels):
        check_inside_perimeter(walls.vertices, rectangle_outline(limit) if len(limit) == 2 else limit)
    if limit is not None:
        floor = ceiling = None
        if flag_limits is not None:
            above, below = height_limits(flag_limits)
            if above is not None or below is not None:
                low, high = mission_extents(bounds, terrain, depth_m=below or 0, height_m=above or 0,
                                            elevation_cm=elevation_cm)
                floor, ceiling = (low[2] if below is not None else None, high[2] if above is not None else None)
                check_flag_altitude(floor, ceiling, flags)
        limit = FlagBoundary(limit, floor, ceiling, flag_return_delay_s)
    warning = warning_surface(bounds, terrain, walls, warning_polygon_m, warning_limits,
                              elevation_cm=elevation_cm) if warn_players else None
    return walls, limit, warning


def kill_floor_height(header, terrain, rows, kill_floor_m):
    """The kill floor's height in draft centimetres: ``kill_floor_m`` below the lowest terrain.

    Players below it die (the source game's KillZ). Every CTF marker must be at least a metre above it.
    """
    if type(kill_floor_m) not in (int, float) or not 0 <= kill_floor_m <= 10_000 or kill_floor_m != kill_floor_m:
        raise ValueError("Enter a kill floor from 0 to 10,000 metres below the lowest terrain.")
    recipe = read_boundary_recipe(rows)
    low = (recipe["elevation_cm"] if recipe else terrain_elevations(terrain))[0]
    height = low - kill_floor_m * 100
    for row in rows:
        if row["status"] == "marker" and row["position_cm"][2] < height + CLEARANCE:
            raise ValueError(f"{row['name'] or row['kind']} must be at least one metre above the kill floor.")
    return height


def flight_ceiling_heights(header, terrain, rows, ceiling):
    """(ceiling, fade or None) in draft centimetres from {"height_m": above lowest terrain, "fade_m": band}.

    Climbing fades through the band below the ceiling and stops at it (Tribes 2 flightCeiling over
    flightCeilingRange, Ascend StallZ). It fences nothing off, so markers may sit above it, as some
    Tribes 2 spawns do; players there simply cannot climb higher.
    """
    if not isinstance(ceiling, dict) or not {"height_m"} <= ceiling.keys() <= {"height_m", "fade_m"}:
        raise ValueError("Enter the flight ceiling as a height and an optional fade band.")
    height, fade = ceiling["height_m"], ceiling.get("fade_m")
    for value, label, limit in ((height, "flight ceiling height", 20_000), (fade, "flight ceiling fade band", 10_000)):
        if value is not None and (type(value) not in (int, float) or value != value or not 0 < value <= limit):
            raise ValueError(f"Enter the {label} as a positive distance up to {limit:,} metres.")
    if fade is not None and fade >= height:
        raise ValueError("The flight ceiling fade band must be smaller than its height.")
    recipe = read_boundary_recipe(rows)
    low = (recipe["elevation_cm"] if recipe else terrain_elevations(terrain))[0]
    top = low + height * 100
    return top, (top - fade * 100 if fade is not None else None)


def place_flight_ceiling(actors, bounds, heights):
    """Place the hidden TargetPoints that tell hosts where climbing fades and stops."""
    import unreal

    (x0, y0), (x1, y1) = bounds
    top, fade = heights
    placed = []
    for label, tag, z in (("Flight ceiling", FLIGHT_CEILING, top), ("Flight ceiling fade", FLIGHT_FADE, fade)):
        if z is None:
            continue
        point = actors.spawn_actor_from_class(unreal.TargetPoint, unreal.Vector((x0 + x1) / 2, (y0 + y1) / 2, z))
        if not point:
            raise RuntimeError("Could not create the flight ceiling marker.")
        point.set_actor_label(label)
        point.set_folder_path("TRIBES CTF/Mission perimeter")
        point.set_editor_property("tags", [unreal.Name(tag)])
        point.set_actor_enable_collision(False)
        point.set_actor_hidden_in_game(True)
        placed.append(point)
    return placed


def place_kill_floor(actors, bounds, height):
    """Place the hidden, non-colliding TargetPoint that tells hosts where the kill floor is."""
    import unreal

    (x0, y0), (x1, y1) = bounds
    point = actors.spawn_actor_from_class(unreal.TargetPoint, unreal.Vector((x0 + x1) / 2, (y0 + y1) / 2, height))
    if not point:
        raise RuntimeError("Could not create the kill floor marker.")
    point.set_actor_label("Kill floor")
    point.set_folder_path("TRIBES CTF/Mission perimeter")
    point.set_editor_property("tags", [unreal.Name(KILL_FLOOR)])
    point.set_actor_enable_collision(False)
    point.set_actor_hidden_in_game(True)
    return point


def flag_bounds(bounds, inset_m, flags):
    if (type(inset_m) not in (int, float) or not 0 < inset_m <= 100_000):
        raise ValueError("Enter a positive flag limit inset in metres.")
    inset = inset_m * 100
    lower, upper = bounds
    limit = ([x + inset for x in lower], [x - inset for x in upper])
    check_flag_bounds(limit, flags)
    return limit


def check_flag_altitude(floor, ceiling, flags):
    for at in (floor, ceiling):
        if at is not None and (not math.isfinite(at) or abs(at) > 10_000_000):
            raise ValueError("The flag-only floor or ceiling is outside the supported world coordinates.")
    if floor is not None and ceiling is not None and floor >= ceiling:
        raise ValueError("The flag-only ceiling must be above its floor.")
    for label, point in flags:
        if (floor is not None and point[2] < floor + CLEARANCE
                or ceiling is not None and point[2] > ceiling - CLEARANCE):
            raise ValueError(f"{label} must be at least one metre inside the flag-only height limits.")


def check_flag_bounds(bounds, flags):
    lower, upper = bounds
    if any(not -10_000_000 <= at <= 10_000_000 for at in (*lower, *upper)):
        raise ValueError("The flag-only limit is outside the supported world coordinates.")
    if any(b - a <= 2 * CLEARANCE for a, b in zip(lower, upper)):
        raise ValueError("The flag-only limit leaves too little room. Reduce its inset.")
    for label, point in flags:
        if not all(a + CLEARANCE <= n <= b - CLEARANCE for a, b, n in zip(lower, upper, point)):
            raise ValueError(f"{label} must be at least one metre inside the flag-only limit.")


def turn(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def inside_segment(a, b, p):
    return all(min(left, right) <= value <= max(left, right) for left, right, value in zip(a, b, p))


def edges_of(polygon):
    return list(zip(polygon, (*polygon[1:], polygon[0])))


def rectangle_outline(bounds):
    (left, top), (right, bottom) = bounds
    return ((left, top), (right, top), (right, bottom), (left, bottom))


def intersects(a, b, c, d):
    sides = (turn(a, b, c), turn(a, b, d), turn(c, d, a), turn(c, d, b))
    if sides[0] * sides[1] < 0 and sides[2] * sides[3] < 0:
        return True
    return any(side == 0 and inside_segment(i, j, p)
               for side, (i, j, p) in zip(sides, ((a, b, c), (a, b, d), (c, d, a), (c, d, b))))


def polygon_contains(edges, p):
    x, y = p
    inside = False
    for a, b in edges:
        side = turn(a, b, p)
        if side == 0 and inside_segment(a, b, p):
            return True
        if (a[1] > y) != (b[1] > y) and (side > 0) == (b[1] > a[1]):
            inside = not inside
    return inside


def segment_distance(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    px, py = p[0] - a[0], p[1] - a[1]
    ratio = min(1, max(0, (dx * px + dy * py) / (dx * dx + dy * dy)))
    return math.hypot(px - ratio * dx, py - ratio * dy)


def parse_polygon(vertices, label):
    if not isinstance(vertices, (tuple, list)) or not 3 <= len(vertices) <= MAX_VERTICES:
        raise ValueError(f"{label} needs between 3 and {MAX_VERTICES} corners.")
    if any(not isinstance(point, (list, tuple)) or len(point) != 2
           or any(type(coordinate) not in (int, float) or not -100_000 <= coordinate <= 100_000
                  for coordinate in point) for point in vertices):
        raise ValueError(f"{label} vertices must be valid world coordinates in metres.")
    return tuple((x * 100, y * 100) for x, y in vertices)


def check_flag_polygon(polygon, flags, bounds=None):
    check_polygon_outline(polygon, "Flag polygon", bounds)
    check_polygon_clearance(polygon, flags, "flag-only limit")


def check_polygon_outline(polygon, label, bounds=None):
    if not 3 <= len(polygon) <= MAX_VERTICES or any(
        not -10_000_000 <= coordinate <= 10_000_000 for vertex in polygon for coordinate in vertex
    ):
        raise ValueError(f"{label} has invalid world coordinates or a missing corner.")
    if bounds is not None:
        lower, upper = bounds
        if any(not all(a < value < b for a, value, b in zip(lower, vertex, upper)) for vertex in polygon):
            raise ValueError(f"{label} corners must be inside the playable boundary.")
    edges = edges_of(polygon)
    if len(set(polygon)) != len(polygon) or any(turn(a, b, polygon[(index + 2) % len(polygon)]) == 0
                                               for index, (a, b) in enumerate(edges)):
        raise ValueError(f"{label} corners must be distinct and turn onto a new edge.")
    for index, (a, b) in enumerate(edges):
        for c, d in edges[index + 1:]:
            if a != d and b != c and intersects(a, b, c, d):
                raise ValueError(f"{label} edges cannot cross.")


def check_polygon_clearance(polygon, markers, boundary):
    edges = edges_of(polygon)
    for label, point in markers:
        xy = point[:2]
        if not polygon_contains(edges, xy) or any(segment_distance(a, b, xy) < CLEARANCE for a, b in edges):
            raise ValueError(f"{label} must be at least one metre inside the {boundary}.")


def check_polygon_walls(walls, markers):
    if (not all(math.isfinite(v) for v in (walls.z_min, walls.z_max))
            or walls.z_max - walls.z_min <= 2 * CLEARANCE):
        raise ValueError("The playable perimeter needs more than two metres of height.")
    check_polygon_clearance(walls.vertices, markers, "mission perimeter")
    for label, (_, _, z) in markers:
        if not walls.z_min + CLEARANCE <= z <= walls.z_max - CLEARANCE:
            raise ValueError(f"{label} must be at least one metre inside the mission perimeter.")


def check_inside_perimeter(outer, inner):
    edges = edges_of(outer)
    if (not all(polygon_contains(edges, point) for point in inner)
            or any(intersects(a, b, c, d) for a, b in edges for c, d in edges_of(inner))):
        raise ValueError("The whole flag-only limit must be inside the solid perimeter.")


def triangulate_polygon(vertices):
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in edges_of(vertices))
    remaining = list(range(len(vertices)))
    if area < 0:
        remaining.reverse()
    triangles = []
    while len(remaining) > 3:
        for at, middle in enumerate(remaining):
            triangle = (remaining[at - 1], middle, remaining[(at + 1) % len(remaining)])
            ia, ib, ic = triangle
            a, b, c = vertices[ia], vertices[ib], vertices[ic]
            if turn(a, b, c) <= 0:
                continue
            others = (vertices[index] for index in remaining if index not in triangle)
            if any(min(turn(a, b, p), turn(b, c, p), turn(c, a, p)) >= 0 for p in others):
                continue
            triangles.append(triangle)
            del remaining[at]
            break
        else:
            raise ValueError("The solid perimeter floor or ceiling could not be triangulated.")
    triangles.append(tuple(remaining))
    return triangles


def place_flag_bounds(actors, boundary):
    import unreal

    def add_marker(label, location, tags):
        point = actors.spawn_actor_from_class(unreal.TargetPoint, unreal.Vector(*location))
        if not point:
            raise RuntimeError("Could not create a flag-only limit marker.")
        point.set_actor_label("Flag-only limit · " + label)
        point.set_folder_path("TRIBES CTF/Mission perimeter")
        point.set_editor_property("tags", [unreal.Name(tag) for tag in tags])
        point.set_actor_enable_collision(False)
        point.set_actor_hidden_in_game(True)

    altitudes = {FLAG_FLOOR: boundary.floor, FLAG_CEILING: boundary.ceiling}
    required = [FLAG_REQUIREMENTS[tag] for tag, height in altitudes.items() if height is not None]
    if boundary.return_delay_s is not None:
        required.append(FLAG_REQUIREMENTS[FLAG_RETURN_TIMER])
    points = boundary.points
    for index, (x, y) in enumerate(points, 1):
        if len(points) == 2:
            label = "minimum" if index == 1 else "maximum"
            tags = [FLAG_CORNERS[index - 1]]
        else:
            label = f"vertex {index:02d}"
            tags = [FLAG_POLYGON, FLAG_VERTEX + str(index), FLAG_COUNT + str(len(points))]
        add_marker(label, (x, y, 0), [*tags, *required])
    for tag, height in altitudes.items():
        if height is not None:
            add_marker("floor" if tag == FLAG_FLOOR else "ceiling", (*points[0], height), [tag])
    if boundary.return_delay_s is not None:
        add_marker("return timer", (*points[0], 0),
                   [FLAG_RETURN_TIMER, f"{FLAG_RETURN_SECONDS}{boundary.return_delay_s}"])


def verify_flag_bounds(actors, flags, hard_perimeter=None):
    import unreal

    if hard_perimeter and not blocks_pawns(read_channels(
        actor for actor in actors if actor.actor_has_tag(unreal.Name(TAG))
    )):
        hard_perimeter = None
    corners, vertices, counts, caps, requirements = {}, {}, set(), {}, set()
    timer = None
    roles = {*FLAG_CORNERS, FLAG_POLYGON, *FLAG_REQUIREMENTS}
    for actor in actors:
        tags = {str(tag) for tag in actor.tags}
        found = tags & roles
        required = frozenset(tag for tag, requirement in FLAG_REQUIREMENTS.items() if requirement in tags)
        if not found and not required:
            continue
        if len(found) != 1 or not isinstance(actor, unreal.TargetPoint) or actor.get_actor_enable_collision():
            raise ValueError("Flag-only boundary markers must be non-colliding TargetPoints with one role.")
        tag, = found
        at = actor.get_actor_location()
        if tag in FLAG_CAPS:
            if required or tag in caps:
                raise ValueError("Each flag-only height limit needs one cap marker.")
            caps[tag] = at.z
            continue
        if tag == FLAG_RETURN_TIMER:
            values = [seconds for seconds in range(1, MAX_FLAG_RETURN_SECONDS + 1) if f"{FLAG_RETURN_SECONDS}{seconds}" in tags]
            if required or timer is not None or len(values) != 1:
                raise ValueError("The flag-only return timer needs one valid duration marker.")
            timer = values[0]
            continue
        requirements.add(required)
        if tag == FLAG_POLYGON:
            indices = [i for i in range(1, MAX_VERTICES + 1) if FLAG_VERTEX + str(i) in tags]
            sizes = [n for n in range(3, MAX_VERTICES + 1) if FLAG_COUNT + str(n) in tags]
            if len(indices) != 1 or len(sizes) != 1 or indices[0] in vertices:
                raise ValueError("Flag polygon vertices must be distinct, indexed, non-colliding TargetPoints.")
            vertices[indices[0]] = (at.x, at.y)
            counts.add(sizes[0])
        else:
            if tag in corners:
                raise ValueError("The flag-only limit needs two distinct, non-colliding TargetPoints.")
            corners[tag] = (at.x, at.y)
    present = set(caps)
    if timer is not None:
        present.add(FLAG_RETURN_TIMER)
    if (requirements or present) and requirements != {frozenset(present)}:
        raise ValueError("The flag-only height markers or return timer are missing, unexpected or inconsistent.")
    check_flag_altitude(caps.get(FLAG_FLOOR), caps.get(FLAG_CEILING), flags)
    if vertices:
        if corners or len(counts) != 1 or vertices.keys() != set(range(1, next(iter(counts)) + 1)):
            raise ValueError("The flag polygon is missing vertices or mixed with an inset.")
        polygon = tuple(vertices[i] for i in range(1, len(vertices) + 1))
        if isinstance(hard_perimeter, PolygonWalls):
            check_flag_polygon(polygon, flags)
            check_inside_perimeter(hard_perimeter.vertices, polygon)
        else:
            check_flag_polygon(polygon, flags, hard_perimeter)
        return
    if not corners:
        return
    if set(corners) != set(FLAG_CORNERS):
        raise ValueError("Both flag-only limit markers are required.")
    lower, upper = [corners[tag] for tag in FLAG_CORNERS]
    check_flag_bounds((lower, upper), flags)
    if isinstance(hard_perimeter, PolygonWalls):
        check_inside_perimeter(hard_perimeter.vertices, rectangle_outline((lower, upper)))
    elif hard_perimeter:
        walls_min, walls_max = hard_perimeter
        if not all(a < lo < hi < b for a, lo, hi, b in zip(walls_min, lower, upper, walls_max)):
            raise ValueError("The flag-only limit must be inside the hard perimeter.")


def place_walls(actors, boxes, *, draft=None, block_channels=None, visual=None):
    import unreal

    if isinstance(boxes, PolygonWalls):
        from polygon_walls_unreal import place

        collision_actors = [place(actors, draft, boxes, block_channels)]
    else:
        collision_actors = place_boxes(actors, boxes, block_channels)
    if visual is not None:
        from perimeter_visual import place_visual

        place_visual(actors, draft, wall_surface(boxes), wall_appearance(visual))
        for actor in collision_actors:
            actor.tags = [*actor.tags, unreal.Name(VISUAL_REQUIRED)]


def place_boxes(actors, boxes, block_channels):
    import unreal

    placed = []
    for side, (center, size) in boxes.items():
        actor = actors.spawn_actor_from_class(unreal.TriggerBox, unreal.Vector(*center))
        if not actor:
            raise RuntimeError(f"Could not create the mission perimeter {side.lower()} wall.")
        actor.set_actor_label(f"Mission perimeter · {side}")
        actor.set_folder_path("TRIBES CTF/Mission perimeter")
        tags = [TAG, TAG + side, *filter_tags(block_channels)]
        if side in WALL_SIDES:
            if "Ceiling" not in boxes:
                tags.append(OPEN_CEILING)
            if "Floor" in boxes:
                tags.append(SEALED_FLOOR)
        actor.set_editor_property("tags", [unreal.Name(tag) for tag in tags])
        component = actor.get_component_by_class(unreal.BoxComponent)
        component.set_box_extent(unreal.Vector(*(value / 2 for value in size)), False)
        configure_wall(actor, component, block_channels)
        placed.append(actor)
    return placed


def check_wall_visual(actors, walls, surface):
    import unreal

    visuals = [actor for actor in actors if actor.actor_has_tag(unreal.Name(VISUAL_TAG))]
    required = [actor for actor in walls if actor.actor_has_tag(unreal.Name(VISUAL_REQUIRED))]
    if not visuals and not required:
        return
    if surface is None or len(required) != len(walls) or len(visuals) != 1:
        raise ValueError("The mission perimeter is missing its matching visual or collision actors.")
    from perimeter_visual import verify_visual

    verify_visual(visuals[0], surface)


def verify_walls(world, actors, markers):
    import unreal

    walls = [actor for actor in actors if actor.actor_has_tag(unreal.Name(TAG))]
    polygon = any(actor.actor_has_tag(unreal.Name(SOLID_POLYGON)) for actor in walls)
    if not polygon and any(actor.actor_has_tag(unreal.Name(POLYGON_REQUIRED)) for actor in actors):
        raise ValueError("The saved draft is missing its solid perimeter collision actor.")
    if not walls:
        check_wall_visual(actors, walls, None)
        return
    channels = read_channels(walls)
    pawn_markers = markers if blocks_pawns(channels) else []
    if polygon:
        from polygon_walls_unreal import verify

        surface = verify(world, walls, actors, pawn_markers, channels)
        check_wall_visual(actors, walls, surface)
        return surface
    actual = {}
    cap_modes = set()
    for actor in walls:
        sides = [
            side for side in (*WALL_SIDES, "Ceiling", "Floor")
            if actor.actor_has_tag(unreal.Name(TAG + side))
        ]
        if len(sides) != 1 or sides[0] in actual or not isinstance(actor, unreal.TriggerBox):
            raise ValueError("Each mission perimeter wall or cap must have its own distinct TriggerBox.")
        side, = sides
        if side in WALL_SIDES:
            cap_modes.add((not actor.actor_has_tag(unreal.Name(OPEN_CEILING)),
                           actor.actor_has_tag(unreal.Name(SEALED_FLOOR))))
        component = actor.get_component_by_class(unreal.BoxComponent)
        if not has_wall_collision(actor, component, channels):
            raise ValueError(f"The mission perimeter {side.lower()} wall lost its collision settings.")
        center, extent, _ = unreal.SystemLibrary.get_component_bounds(component)
        actual[side] = ([center.x, center.y, center.z], [2 * extent.x, 2 * extent.y, 2 * extent.z])
    if len(cap_modes) != 1:
        raise ValueError("The four perimeter walls must agree on which caps are required.")
    (ceiling, floor), = cap_modes
    lower, upper = verified_extents(actual, ceiling=ceiling, floor=floor)
    check_wall_visual(actors, walls, wall_surface(actual))
    check_marker_clearance(lower, upper, pawn_markers)

    # Trace only the generated walls. Terrain or base collisions cannot pass this check.
    ignore = [actor for actor in actors if actor not in walls]
    middle = [(a + b) / 2 for a, b in zip(lower, upper)]
    directions = [(0, -1, "West"), (0, 1, "East"), (1, -1, "South"), (1, 1, "North")]
    if ceiling:
        directions.append((2, 1, "Ceiling"))
    if floor:
        directions.append((2, -1, "Floor"))
    for axis, sign, side in directions:
        end = list(middle)
        end[axis] = (lower if sign < 0 else upper)[axis] + sign * (THICKNESS + 150)
        for profile, should_block in probe_profiles(channels):
            hits = unreal.SystemLibrary.capsule_trace_multi_by_profile(
                world, unreal.Vector(*middle), unreal.Vector(*end), 42, 96, profile,
                False, ignore, unreal.DrawDebugTrace.NONE, True,
            ) or []
            collisions = [row[9] for row in (hit.to_tuple() for hit in hits) if row[0] and row[9] in walls]
            expected = any(actor.actor_has_tag(unreal.Name(TAG + side)) for actor in collisions) if should_block else not collisions
            if not expected:
                raise ValueError(f"The mission perimeter {side.lower()} wall has an unexpected {profile} collision response.")
    return lower, upper


def verified_extents(actual, *, ceiling=True, floor=False):
    expected_sides = set(WALL_SIDES)
    if ceiling:
        expected_sides.add("Ceiling")
    if floor:
        expected_sides.add("Floor")
    if actual.keys() != expected_sides:
        raise ValueError("The mission perimeter must have four walls and the configured ceiling and floor.")
    west, east, south, north = (actual[side] for side in WALL_SIDES)
    lower = [west[0][0] + west[1][0] / 2, south[0][1] + south[1][1] / 2, west[0][2] - west[1][2] / 2]
    upper = [east[0][0] - east[1][0] / 2, north[0][1] - north[1][1] / 2,
             west[0][2] + west[1][2] / 2 - (THICKNESS if ceiling else 0)]
    for side, expected in wall_boxes(lower, upper, ceiling=ceiling, floor=floor).items():
        if any(not math.isclose(a, b, rel_tol=0, abs_tol=0.25)
               for groups in zip(actual[side], expected) for a, b in zip(*groups)):
            raise ValueError(f"The mission perimeter has a gap or a misaligned {side.lower()} wall.")
    return lower, upper
