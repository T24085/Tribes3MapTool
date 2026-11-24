"""Build and verify a static collision shell along the playable polygon."""

import json
import math

import unreal

from perimeter import (SOLID_POLYGON, TAG, PolygonWalls, check_polygon_outline, check_polygon_walls, edges_of,
                       segment_distance, triangulate_polygon)
from perimeter_collision import configure_wall, filter_tags, has_wall_collision, probe_profiles


PLAN = "T3MissionPerimeterPlan"
CAPSULE_RADIUS = 42
CAPSULE_HALF_HEIGHT = 96


def mesh_geometry(walls):
    points = walls.vertices
    count = len(points)
    x, y = (sum(coordinates) / count for coordinates in zip(*points))
    z = (walls.z_min + walls.z_max) / 2
    vertices = [(a - x, b - y, level - z) for level in (walls.z_min, walls.z_max) for a, b in points]
    indices = []
    for i in range(count):
        j = (i + 1) % count
        indices.extend(((i, j, j + count), (i, j + count, i + count)))
    caps = triangulate_polygon(points) if walls.floor or walls.ceiling else []
    if walls.floor:
        indices.extend((c, b, a) for a, b, c in caps)
    if walls.ceiling:
        indices.extend(tuple(i + count for i in triangle) for triangle in caps)
    return (x, y, z), vertices, indices, caps


def place(actors, draft, walls, channels):
    from source_profiles import imported_draft

    if not imported_draft(draft):
        raise ValueError("A solid polygon must belong to an imported draft.")
    position, vertices, indices, _ = mesh_geometry(walls)
    mesh = unreal.DynamicMesh()
    mesh.append_buffers_to_mesh(unreal.GeometryScriptSimpleMeshBuffers(
        vertices=[unreal.Vector(*point) for point in vertices],
        # UE 5.3 computes static mesh tangents even for a hidden collision asset.
        uv0=[unreal.Vector2D((x + z * 0.173) / 100, (y + z * 0.367) / 100) for x, y, z in vertices],
        triangles=[unreal.IntVector(*triangle) for triangle in indices],
    ))
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True,
        enable_collision=True,
        collision_mode=unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE,
    )
    path = f"/Game/Community/{draft}/SM_MissionPerimeter"
    assets = unreal.EditorAssetLibrary
    if assets.does_asset_exist(path):
        raise ValueError("The reserved mission perimeter collision asset already exists.")
    asset, result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(mesh, path, options)
    if not isinstance(asset, unreal.StaticMesh) or result != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise RuntimeError("Could not create the solid perimeter collision mesh.")
    body = asset.get_editor_property("body_setup")
    body.set_editor_property("double_sided_geometry", True)
    assets.set_metadata_tag(asset, PLAN, json.dumps(walls._asdict(), separators=(",", ":")))
    if not assets.save_loaded_asset(asset):
        raise RuntimeError("Could not save the solid perimeter collision mesh.")
    actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*position))
    if not actor:
        raise RuntimeError("Could not place the solid perimeter collision mesh.")
    component = actor.static_mesh_component
    component.set_static_mesh(asset)
    actor.set_actor_label("Mission perimeter · Solid polygon")
    actor.set_folder_path("TRIBES CTF/Mission perimeter")
    actor.tags = [unreal.Name(tag) for tag in (TAG, SOLID_POLYGON, *filter_tags(channels))]
    configure_wall(actor, component, channels)


def read_plan(asset):
    if not isinstance(asset, unreal.StaticMesh):
        raise ValueError("The solid perimeter lost its collision mesh.")
    try:
        values = json.loads(unreal.EditorAssetLibrary.get_metadata_tag(asset, PLAN))
        walls = PolygonWalls(**values)
        if (type(walls.floor) is not bool or type(walls.ceiling) is not bool
                or type(walls.z_min) not in (int, float) or type(walls.z_max) not in (int, float)
                or not isinstance(walls.vertices, list)
                or any(not isinstance(point, list) or len(point) != 2
                       or any(type(v) not in (int, float) for v in point) for point in walls.vertices)):
            raise ValueError("Invalid perimeter geometry.")
        walls = walls._replace(vertices=tuple(tuple(point) for point in walls.vertices))
        check_polygon_outline(walls.vertices, "Solid perimeter")
    except (TypeError, ValueError) as exc:
        raise ValueError("The solid perimeter lost its recorded geometry.") from exc
    return walls


def verify(world, actors, all_actors, markers, channels):
    if len(actors) != 1 or not isinstance(actors[0], unreal.StaticMeshActor):
        raise ValueError("A solid polygon needs exactly one collision mesh actor.")
    actor, = actors
    component = actor.static_mesh_component
    asset = component.get_editor_property("static_mesh")
    walls = read_plan(asset)
    check_polygon_walls(walls, markers)
    position, _, indices, caps = mesh_geometry(walls)
    body = asset.get_editor_property("body_setup")
    if (not body or not body.get_editor_property("double_sided_geometry")
            or body.get_editor_property("collision_trace_flag") != unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
            or not has_wall_collision(actor, component, channels)
            or len(indices) != asset.get_num_triangles(0)):
        raise ValueError("The solid perimeter collision mesh lost its collision settings or faces.")
    if (math.dist(actor.get_actor_location().to_tuple(), position) > 0.25
            or actor.get_actor_scale3d().to_tuple() != (1, 1, 1)
            or any(abs(angle) > 1e-5 for angle in actor.get_actor_rotation().to_tuple())):
        raise ValueError("The solid perimeter moved or rotated from its authored outline.")
    ignore = [other for other in all_actors if other != actor]

    def sweep(center, offset, label, *, capsule):
        ends = [unreal.Vector(*(value + sign * shift for value, shift in zip(center, offset))) for sign in (-1, 1)]
        for start, end in (ends, reversed(ends)):
            trace = unreal.SystemLibrary.capsule_trace_multi_by_profile if capsule else unreal.SystemLibrary.line_trace_multi_by_profile
            dimensions = (CAPSULE_RADIUS, CAPSULE_HALF_HEIGHT) if capsule else ()
            for profile, should_block in probe_profiles(channels):
                hits = trace(world, start, end, *dimensions, profile, False, ignore, unreal.DrawDebugTrace.NONE, True) or []
                collisions = [hit for hit in (value.to_tuple() for value in hits) if hit[0] and hit[9] == actor]
                expected = any(not hit[1] and math.dist(hit[5].to_tuple(), center) < 2
                               for hit in collisions) if should_block else not collisions
                if not expected:
                    raise ValueError(f"The solid perimeter {label} has an unexpected {profile} collision response.")

    edges = edges_of(walls.vertices)
    for index, (a, b) in enumerate(edges):
        x, y = ((start + end) / 2 for start, end in zip(a, b))
        dx, dy = b[0] - a[0], b[1] - a[1]
        room = min(segment_distance(c, d, (x, y)) for i, (c, d) in enumerate(edges) if i != index)
        reach = min(150, room / 2)
        normal_scale = reach / math.hypot(dx, dy)
        # A capsule cannot start clear beside a tiny bevel. Trace its collision face directly.
        sweep((x, y, position[2]), (dy * normal_scale, -dx * normal_scale, 0),
              f"edge {index + 1}", capsule=reach > CAPSULE_RADIUS + 1)
    for cap, active, z in (("floor", walls.floor, walls.z_min), ("ceiling", walls.ceiling, walls.z_max)):
        if active:
            for triangle in caps:
                x, y = (sum(values) / 3 for values in zip(*(walls.vertices[i] for i in triangle)))
                reach = min(150, (walls.z_max - walls.z_min) / 2)
                room = min(segment_distance(a, b, (x, y)) for a, b in edges)
                sweep((x, y, z), (0, 0, reach), cap, capsule=room > CAPSULE_RADIUS + 1)
    return walls
