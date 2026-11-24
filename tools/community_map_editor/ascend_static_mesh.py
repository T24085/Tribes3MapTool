"""Read placed UE3 static meshes from Ascend's version 805 packages."""

from hashlib import sha256
from pathlib import Path
import struct

from interior_geometry import ASCEND_STATIC_MESH, validate_geometry
from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor


MAX_VERTICES = 65_536
MAX_TRIANGLES = 250_000


def bulk_array(cursor, stride, maximum, expected=None):
    stored_stride, count = cursor.fields("2i")
    if (stored_stride != stride or not 0 <= count <= maximum
            or expected is not None and count != expected):
        raise ValueError("Unexpected Ascend static mesh buffer dimensions.")
    return cursor.take(count * stride)


def skip_source_triangles(cursor):
    # The uncooked source triangles are redundant with the indexed LOD.
    flags, count, size, offset = cursor.fields("4i")
    if count < 0 or size < 0:
        raise ValueError("Invalid Ascend source triangle bulk data.")
    if not size:
        return
    if flags & 0x21:  # Separate-file or unused bulk payload.
        return
    if offset != cursor.offset:
        raise ValueError("Unsupported Ascend source triangle bulk offset.")
    cursor.take(size)


def mesh_sections(cursor, objects):
    sections = []
    for _ in range(cursor.count(256)):
        material, _, _, _, first, count, low, high, _ = cursor.fields("9i")
        if material and (material not in objects or "material" not in objects[material]["class_name"].casefold()):
            raise ValueError("Invalid material reference in Ascend static mesh section.")
        if first < 0 or first % 3 or count < 0 or not 0 <= low <= high < MAX_VERTICES:
            raise ValueError("Invalid Ascend static mesh section range.")
        cursor.take(cursor.count(MAX_TRIANGLES) * 8)
        if cursor.take(1) != b"\0":
            raise ValueError("Console mesh section data is unsupported.")
        path = objects[material]["object_path"] if material else "DefaultMaterial"
        sections.append((material, path, first, count, low, high))
    return sections


def decode_lod(cursor, objects, units_per_m):
    skip_source_triangles(cursor)
    sections = mesh_sections(cursor, objects)
    stride, count = cursor.fields("2i")
    if stride != 12 or not 3 <= count <= MAX_VERTICES:
        raise ValueError("Invalid Ascend static mesh position stream.")
    positions = bulk_array(cursor, stride, MAX_VERTICES, count)
    vertices = [[value / units_per_m for value in position]
                for position in struct.iter_unpack(cursor.endian + "3f", positions)]
    uv_sets, stride, uv_count, full = cursor.fields("4i")
    if (not 1 <= uv_sets <= 8 or full not in (0, 1) or uv_count != count
            or stride != 8 + uv_sets * (8 if full else 4)):
        raise ValueError("Invalid Ascend static mesh UV stream.")
    texture_stream = bulk_array(cursor, stride, MAX_VERTICES, count)
    uv_format = cursor.endian + ("2f" if full else "2e")
    uv = [struct.unpack_from(uv_format, texture_stream, idx * stride + 8) for idx in range(count)]
    color_stride, colors = cursor.fields("2i")
    if colors:
        if color_stride != 4 or colors != count:
            raise ValueError("Invalid Ascend static mesh color stream.")
        bulk_array(cursor, 4, MAX_VERTICES, count)
    if cursor.count(MAX_VERTICES) != count:
        raise ValueError("Ascend static mesh LOD vertex counts disagree.")
    index_bytes = bulk_array(cursor, 2, MAX_TRIANGLES * 3)
    indices = [index for index, in struct.iter_unpack(cursor.endian + "H", index_bytes)]
    triangles = []
    end = 0
    for slot, (_, _, first, count, low, high) in enumerate(sections):
        stop = first + 3 * count
        if first != end or stop > len(indices) or high >= len(vertices):
            raise ValueError("Ascend static mesh sections do not match the index buffer.")
        for i in range(first, stop, 3):
            face = indices[i:i + 3]
            if any(not low <= idx <= high for idx in face):
                raise ValueError("Ascend static mesh index exceeds its section's vertex range.")
            if len(set(face)) == 3:
                triangles.append([slot, *face, *(coordinate for idx in face for coordinate in uv[idx])])
        end = stop
    if end != len(indices):
        raise ValueError("Ascend static mesh sections leave unused render indices.")
    return [row[1] for row in sections], vertices, triangles, [(row[0], 0) for row in sections]


def read_mesh(report, stream, export, objects, units_per_m, decoder_sha):
    summary = report["summary"]
    _, _, _, start = read_properties(stream, summary, export, report["names"], objects)
    cursor = Cursor(stream, start, export["serial_offset"] + export["serial_bytes"], summary["endian"])
    cursor.take(56)  # Mesh bounds, BodySetup reference and kDOP root bounds.
    bulk_array(cursor, 6, MAX_TRIANGLES * 2)
    kdop = bulk_array(cursor, 8, MAX_TRIANGLES)
    version, lods = cursor.fields("2i")
    if version != 18 or not 1 <= lods <= 32:
        raise ValueError("Unsupported Ascend static mesh revision or LOD count.")
    materials, vertices, triangles, slots = decode_lod(cursor, objects, units_per_m)
    collision = [[a, b, c] for a, b, c, _ in struct.iter_unpack(cursor.endian + "4H", kdop)
                 if len({a, b, c}) == 3]
    source = dict(name=report["source"]["name"], sha256=report["source"]["sha256"],
                  decoder_sha256=decoder_sha, export=export["index"], object_path=export["object_path"])
    geometry = validate_geometry(dict(format=ASCEND_STATIC_MESH, source=source, materials=materials,
                                      vertices_m=vertices, triangles=triangles, render=True,
                                      collision=dict(detail=0, vertices_m=vertices, triangles=collision)))
    return geometry, slots


def read_placed_meshes(report, stream, units_per_m, references, *, companion=False, artwork=None):
    """Decode only exact StaticMesh references that the draft can actually place."""
    objects = {row["index"]: row for row in report["imports"] + report["exports"]}
    model_refs = {}
    models = {}
    issues = {}
    decoder_sha = sha256(Path(__file__).read_bytes()).hexdigest()
    package = Path(report["source"]["name"]).stem.casefold()
    for export in report["exports"]:
        if export["class_name"].casefold() != "staticmesh" or export["serial_bytes"] <= 0:
            continue
        path = export["object_path"].casefold()
        ref = "unrealmesh:" + (f"{package}.{path}" if companion else path)
        if ref not in references:
            continue
        try:
            if ref in model_refs or ref in issues:
                raise ValueError("Multiple exports resolve to the same Ascend mesh path.")
            geometry, slots = read_mesh(report, stream, export, objects, units_per_m, decoder_sha)
            model_refs[ref] = geometry
            models[ref] = (ref.removeprefix("unrealmesh:"), geometry, slots)
        except ValueError as error:
            model_refs.pop(ref, None)
            models.pop(ref, None)
            issues[ref] = str(error)
    if artwork and models:
        artwork.read_package(models, report, stream)
    return model_refs, issues
