"""Identify Vengeance elevator settings and hulls that have a runtime conversion."""

import math
from collections import Counter

from vengeance_mover_plan import normal_proximity_senders


def linked_elevator_senders(physics, chains=()):
    if not physics.get("incoming_trigger"):
        return ()
    return normal_proximity_senders(physics.get("trigger_senders", ()), chains)


def zero_gravity_hull(brush, geometry, source_units_per_m, *, linked=False, chains=()):
    if brush["type"] != "elevator":
        return None
    physics = brush["behavior"]
    if linked:
        if not linked_elevator_senders(physics, chains):
            return None
    elif (not physics["active"] or physics["incoming_trigger"]
          or physics["trigger_tag"].casefold() not in {"", "none", "elevatorvolume"}):
        return None
    if (physics["moves_projectiles"] or physics["priority"]
            or any(physics["gravity_m_s2"]) or any(physics["zone_velocity_m_s"])
            or physics["ground_friction"] or physics["fluid_friction"]
            or physics["terminal_speed_m_s"] != 100000 / source_units_per_m
            or any(physics[key].casefold() not in {"", "none"} for key in
                   ("trigger_event", "associated_actor", "associated_actor_tag"))):
        return None

    collision = geometry["collision"]
    points, faces = collision["vertices_m"], collision["triangles"]
    if not faces:
        return None
    distinct = {}
    unique_faces = []
    for face in faces:
        vertices = tuple(tuple(points[i]) for i in face)
        if len(set(vertices)) != 3:
            return None
        unique_faces.append([distinct.setdefault(point, len(distinct)) for point in vertices])
    if not 4 <= len(distinct) <= 128:
        return None
    edges = Counter(tuple(sorted((left, right))) for face in unique_faces
                    for left, right in zip(face, face[1:] + face[:1]))
    if any(count != 2 for count in edges.values()):
        return None

    vertices = list(distinct)
    for face in unique_faces:
        a, b, c = (vertices[index] for index in face)
        u = tuple(x - y for x, y in zip(b, a))
        v = tuple(x - y for x, y in zip(c, a))
        nx, ny, nz = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.hypot(nx, ny, nz)
        if not length:
            return None
        epsilon = length * .001
        negative = positive = False
        for point in vertices:
            distance = nx * (point[0] - a[0]) + ny * (point[1] - a[1]) + nz * (point[2] - a[2])
            negative |= distance < -epsilon
            positive |= distance > epsilon
        if negative == positive:
            return None
    return [[axis * 100 for axis in point] for point in vertices]
