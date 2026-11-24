"""Convert compiled Vengeance level surfaces to static mesh geometry."""

from hashlib import sha256
import math
from pathlib import Path

from interior_geometry import VENGEANCE_BSP, validate_geometry
from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor, MAX_RECORDS


INVISIBLE = 0x00000001
MASKED = 0x00000002
TRANSLUCENT = 0x00000004
NOT_SOLID = 0x00000008
FAKE_BACKDROP = 0x00000080
TWO_SIDED = 0x00000100
MODULATED = 0x00000040
PORTAL = 0x04000000
BLEND_FLAGS = MASKED | TRANSLUCENT | MODULATED


def compact_count(cursor):
    count = cursor.index(False)
    if not 0 <= count <= MAX_RECORDS:
        raise ValueError("Invalid Vengeance BSP array length.")
    return count


def exported_object(cursor, index, expected, objects):
    row = objects.get(index)
    if index <= 0 or not row or row["class_name"].casefold() != expected:
        raise ValueError(f"Invalid Vengeance level {expected} reference at offset {cursor.offset}.")
    return row


def object_payload(report, stream, row, objects):
    summary = report["summary"]
    _, _, issues, start = read_properties(stream, summary, row, report["names"], objects)
    if issues:
        raise ValueError("The Vengeance BSP object has unsupported serialized properties.")
    return Cursor(stream, start, row["serial_offset"] + row["serial_bytes"], summary["endian"])


def level_model(report, stream, level, objects):
    cursor = object_payload(report, stream, level, objects)
    count, capacity = cursor.fields("2i")
    if not 0 <= count <= capacity <= MAX_RECORDS:
        raise ValueError("Invalid level roster before the BSP model reference.")
    for _ in range(count):
        cursor.index(False)
    if cursor.offset == cursor.limit:
        return None
    for _ in range(4):  # URL protocol, host, map and portal.
        cursor.string(False)
    for _ in range(compact_count(cursor)):
        cursor.string(False)
    cursor.take(8)  # URL port and validity.
    if cursor.fields("3i") != (3, 8, 6):
        raise ValueError("Unrecognized Vengeance level BSP reference layout.")
    cursor.take(4)  # Level serializer version.
    index = cursor.index(False)
    return exported_object(cursor, index, "model", objects) if index else None


def model_arrays(report, stream, model, objects):
    cursor = object_payload(report, stream, model, objects)
    cursor.take(41)  # UPrimitive bounds and sphere.
    if cursor.fields("3i") != (3, 0, 5):
        raise ValueError("Unrecognized Vengeance BSP model header.")
    vectors = [cursor.fields("3f") for _ in range(compact_count(cursor))]
    points = [cursor.fields("3f") for _ in range(compact_count(cursor))]
    nodes = []
    for _ in range(compact_count(cursor)):
        if cursor.fields("3i") != (3, 2, 2):
            raise ValueError("Unrecognized Vengeance BSP node.")
        cursor.take(25)  # Plane, zone mask and node flags.
        first, surface = (cursor.index(False) for _ in range(2))
        for _ in range(5):
            cursor.index(False)  # Tree links and bound references.
        cursor.take(34)  # Bounding spheres and zones.
        size, = cursor.fields("B")
        cursor.take(21)  # Leaves, render section, lightmap and Vengeance byte.
        nodes.append((first, surface, size))
    surfaces = []
    for _ in range(compact_count(cursor)):
        if cursor.fields("3i") != (3, 1, 1):
            raise ValueError("Unrecognized Vengeance BSP surface.")
        material = cursor.index(False)
        flags, = cursor.fields("I")
        base, normal, u_axis, v_axis, _, _ = (cursor.index(False) for _ in range(6))
        cursor.take(20)  # Surface plane and source lightmap scale.
        if (material and material not in objects or not 0 <= base < len(points)
                or any(not 0 <= index < len(vectors) for index in (normal, u_axis, v_axis))):
            raise ValueError("Invalid Vengeance BSP surface references.")
        surfaces.append((material, flags, points[base], vectors[normal], vectors[u_axis], vectors[v_axis]))
    vertex_pool = []
    for _ in range(compact_count(cursor)):
        vertex_pool.append(cursor.index(False))
        cursor.index(False)  # Adjacent side.
    return points, nodes, surfaces, vertex_pool


def dot(first, second):
    return sum(a * b for a, b in zip(first, second))


def triangle_winding(first, second, third, normal):
    ax, ay, az = (b - a for a, b in zip(first, second))
    bx, by, bz = (b - a for a, b in zip(first, third))
    return dot((ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx), normal)


def project_model(report, stream, model, objects, units, decoder_hash):
    points, nodes, surfaces, vertex_pool = model_arrays(report, stream, model, objects)
    triangles, collision = [], []
    materials, vertices = {}, {}
    for first, surface, size in nodes:
        if size < 3:
            continue
        if not 0 <= surface < len(surfaces) or not 0 <= first <= len(vertex_pool) - size:
            raise ValueError("A Vengeance BSP node references a missing surface or vertex range.")
        indices = vertex_pool[first:first + size]
        if any(not 0 <= index < len(points) for index in indices):
            raise ValueError("A Vengeance BSP face references a missing point.")
        material, flags, base, normal, u_axis, v_axis = surfaces[surface]
        visible = not flags & (INVISIBLE | FAKE_BACKDROP | PORTAL)
        solid = not flags & (NOT_SOLID | PORTAL)
        if not (visible or solid):
            continue
        if not all(math.isfinite(value) for vector in (base, normal, u_axis, v_axis) for value in vector):
            raise ValueError("Invalid Vengeance BSP surface coordinates.")
        for second, third in zip(indices[1:], indices[2:]):
            a, b, c = (points[index] for index in (indices[0], second, third))
            winding = triangle_winding(a, b, c, normal)
            if not math.isfinite(winding):
                raise ValueError("Invalid Vengeance BSP point coordinates.")
            if abs(winding) <= 1e-8:
                continue
            ordered = (indices[0], third, second) if winding > 0 else (indices[0], second, third)
            face = [vertices.setdefault(index, len(vertices)) for index in ordered]
            if visible:
                slot = materials.setdefault((material, flags & BLEND_FLAGS), len(materials))
                uv = []
                for index in ordered:
                    displacement = tuple(v - origin for v, origin in zip(points[index], base))
                    uv.extend((dot(displacement, u_axis), dot(displacement, v_axis)))
                triangles.append([slot, *face, *uv])
                if flags & TWO_SIDED:
                    triangles.append([slot, face[0], face[2], face[1], *uv[:2], *uv[4:], *uv[2:4]])
            if solid:
                collision.append(face)
            if len(triangles) > MAX_RECORDS or len(collision) > MAX_RECORDS:
                raise ValueError("Vengeance BSP has too many triangles for one draft mesh.")
    if not triangles and not collision:
        return None
    locations = [[value / units for value in points[index]] for index in vertices]
    render = bool(triangles)
    if not render:
        triangles = [[0, *face, 0, 0, 0, 0, 0, 0] for face in collision]
    names = [objects[index]["object_path"] if index else "Unassigned" for index, _ in materials] if render else ["Collision"]
    geometry = validate_geometry(dict(format=VENGEANCE_BSP,
                                     source=dict(name=report["source"]["name"], sha256=report["source"]["sha256"],
                                                 decoder_sha256=decoder_hash, export=model["index"]),
                                     materials=names, vertices_m=locations, triangles=triangles, render=render,
                                     collision=dict(detail=0, vertices_m=locations, triangles=collision)))
    return geometry, list(materials)


def read_bsp_models(report, stream, units):
    objects = {row["index"]: row for row in report["imports"] + report["exports"]}
    summary = report["summary"]
    decoder_hash = sha256(Path(__file__).read_bytes()).hexdigest()
    generated = []
    issues = []
    seen = set()
    for level in report["exports"]:
        if level["class_name"].casefold() != "level":
            continue
        try:
            if (summary["version"], summary["licensee_version"], summary["endian"]) != (130, 27, "<"):
                raise ValueError("The native BSP layout of this Vengeance package is unsupported.")
            model = level_model(report, stream, level, objects)
            if model is None or model["index"] in seen:
                continue
            seen.add(model["index"])
            decoded = project_model(report, stream, model, objects, units, decoder_hash)
            if decoded:
                generated.append((level["object_path"], *decoded))
        except ValueError as error:
            issues.append(dict(level=level["object_path"], reason=str(error)))
    return generated, issues
