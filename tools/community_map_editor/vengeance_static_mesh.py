"""Read placed static meshes from Vengeance's PC version 130/27 packages."""

from hashlib import sha256
from pathlib import Path
import struct

from interior_geometry import VENGEANCE_STATIC_MESH, valid_vertex_normal, validate_geometry
from unreal_binary_scene import property_tag, read_properties
from unreal_package_reader import Cursor
from vengeance_terrain import HEIGHT_LAYOUT


MAX_VERTICES = 65_536
MAX_TRIANGLES = 250_000


def array_count(cursor, maximum):
    count = cursor.index(False)
    if not 0 <= count <= maximum:
        raise ValueError("Invalid Vengeance static mesh array length.")
    return count


def mesh_materials(cursor, tags, names, objects):
    material_tags = [tag for tag in tags if tag["name"].casefold() == "materials"]
    if not material_tags:
        return []
    tag = material_tags[0]
    if len(material_tags) != 1 or tag["slot"] or tag["kind"] != "ArrayProperty":
        raise ValueError("Invalid Vengeance static mesh material array.")
    start = tag["value_offset"]
    value = Cursor(cursor.stream, start, start + tag["size"], cursor.endian)
    materials = []
    for _ in range(array_count(value, 256)):
        material = 0
        collision = None
        while field := property_tag(value, names, False):
            end = field["value_offset"] + field["size"]
            kind = field["name"].casefold()
            if kind == "material" and field["kind"] == "ObjectProperty" and not field["slot"]:
                material = value.index(False)
                if value.offset != end or material and material not in objects:
                    raise ValueError("Invalid Vengeance static mesh material reference.")
            elif kind == "enablecollision" and field["kind"] == "BoolProperty" and not field["slot"]:
                collision = bool(field["boolean"])
            value.offset = end
        materials.append((material, collision))
    if value.offset != value.limit:
        raise ValueError("Vengeance static mesh material array has trailing data.")
    return materials


def vertex_and_uv_streams(cursor, units_per_m):
    marker, schema, tangents = cursor.fields("3i")
    if (marker, schema) != (3, 0) or tangents not in (0, 1):
        raise ValueError("Unsupported Vengeance vertex stream header.")
    count = array_count(cursor, MAX_VERTICES)
    vertices, normals = [], []
    for x, y, z, nx, ny, nz in struct.iter_unpack(cursor.endian + "6f", cursor.take(count * 24)):
        vertices.append([x / units_per_m, y / units_per_m, z / units_per_m])
        normals.append([nx, ny, nz])
    cursor.take(4)  # Vertex stream revision.
    if tangents:
        tangent_count = array_count(cursor, MAX_VERTICES)
        if tangent_count not in (0, count):
            raise ValueError("Vengeance static mesh tangent and vertex counts disagree.")
        cursor.take(tangent_count * 24)
    for _ in range(2):  # Vertex colors and alpha.
        color_count = array_count(cursor, MAX_VERTICES)
        if color_count not in (0, count):
            raise ValueError("Vengeance static mesh color and vertex counts disagree.")
        cursor.take(color_count * 4 + 4)
    uv_layers = array_count(cursor, 8)
    if not uv_layers:
        raise ValueError("Vengeance static mesh has no texture UV stream.")
    uv = []
    for _ in range(uv_layers):
        points = array_count(cursor, MAX_VERTICES)
        if points != count:
            raise ValueError("Vengeance static mesh UV and vertex counts disagree.")
        coordinates = cursor.take(points * 8)
        uv.append([list(point) for point in struct.iter_unpack(cursor.endian + "2f", coordinates)])
        cursor.take(8)  # UV channel and stream revision.
    return vertices, normals, uv


def render_triangles(sections, indices, vertices, uv):
    faces = []
    end = 0
    for slot, (_, first, low, high, _, count) in enumerate(sections):
        stop = first + count * 3
        if first != end or not 0 <= low <= high < len(vertices) or stop > len(indices):
            raise ValueError("Vengeance static mesh section does not match the render buffers.")
        for offset in range(first, stop, 3):
            face = indices[offset:offset + 3]
            if any(not low <= index <= high for index in face):
                raise ValueError("Vengeance static mesh index exceeds its section's vertex range.")
            if len(set(face)) == 3:
                faces.append([slot, *face, *(v for index in face for v in uv[index])])
        end = stop
    if end != len(indices):
        raise ValueError("Vengeance static mesh sections leave unused render indices.")
    return faces


def read_mesh(report, stream, export, objects, units_per_m, decoder_sha):
    summary = report["summary"]
    tags, _, issues, start = read_properties(stream, summary, export, report["names"], objects)
    if issues:
        raise ValueError("The Vengeance static mesh has invalid tagged properties.")
    cursor = Cursor(stream, start, export["serial_offset"] + export["serial_bytes"], summary["endian"])
    materials = mesh_materials(cursor, tags, report["names"], objects)
    cursor.take(41)  # Base primitive bounds and sphere.
    if cursor.fields("3i") != (3, 1, 3):
        raise ValueError("Unsupported Vengeance static mesh payload revision.")
    sections = [cursor.fields("I5H") for _ in range(array_count(cursor, 256))]
    if len(materials) != len(sections):
        raise ValueError("Vengeance static mesh section and material counts disagree.")
    cursor.take(25)  # Serialized mesh bounds.
    vertices, normals, uv = vertex_and_uv_streams(cursor, units_per_m)
    indices = [value for value, in struct.iter_unpack(
        cursor.endian + "H", cursor.take(array_count(cursor, MAX_TRIANGLES * 3) * 2))]
    cursor.take(4)
    cursor.take(array_count(cursor, MAX_TRIANGLES * 6) * 2 + 4)  # Wireframe indices and revision.
    body = cursor.index(False)
    if body and body not in objects:
        raise ValueError("Invalid Vengeance static mesh body reference.")
    cursor.take(array_count(cursor, MAX_TRIANGLES * 2 + 1) * 32)  # Compiled collision tree.
    collision = []
    for _ in range(array_count(cursor, MAX_TRIANGLES)):
        a, b, c, slot = cursor.fields("4H")
        if max(a, b, c) >= len(vertices) or slot >= len(sections) or materials[slot][1] is False:
            raise ValueError("Invalid Vengeance collision triangle reference.")
        if len({a, b, c}) == 3:
            collision.append([a, b, c])
    source = dict(name=report["source"]["name"], sha256=report["source"]["sha256"],
                  decoder_sha256=decoder_sha, export=export["index"], object_path=export["object_path"])
    names = [objects[index]["object_path"] if index else "DefaultMaterial" for index, _ in materials]
    triangles = render_triangles(sections, indices, vertices, uv[0])
    geometry = dict(format=VENGEANCE_STATIC_MESH, source=source, materials=names,
                    vertices_m=vertices, triangles=triangles, render=True,
                    collision=dict(detail=0, vertices_m=vertices, triangles=collision))
    if all(map(valid_vertex_normal, normals)):
        geometry["vertex_normals"] = normals
    if len(uv) > 1:
        geometry["extra_uvs"] = uv[1:]
    return validate_geometry(geometry), [(index, 0) for index, _ in materials]


def read_placed_meshes(report, stream, units_per_m, references, *, companion=False, artwork=None):
    summary = report["summary"]
    objects = {row["index"]: row for row in report["imports"] + report["exports"]}
    namespace = Path(report["source"]["name"]).stem.casefold() + "." if companion else ""
    decoder_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    resolved, issues, models = {}, {}, {}
    for export in report["exports"]:
        reference = "unrealmesh:" + namespace + export["object_path"].casefold()
        if reference not in references or export["class_name"].casefold() != "staticmesh":
            continue
        try:
            if reference in resolved or reference in issues:
                raise ValueError("Multiple Vengeance meshes resolve to the same object path.")
            if (summary["version"], summary["licensee_version"], summary["endian"]) != HEIGHT_LAYOUT:
                raise ValueError("Unsupported Vengeance static mesh package layout.")
            geometry, slots = read_mesh(report, stream, export, objects, units_per_m, decoder_sha)
            resolved[reference] = geometry
            models[reference] = (namespace + export["object_path"], geometry, slots)
        except ValueError as error:
            resolved.pop(reference, None)
            models.pop(reference, None)
            issues[reference] = str(error)
    if artwork and models:
        artwork.read_package(models, report, stream)
    return resolved, issues
