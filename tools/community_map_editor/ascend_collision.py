"""Read tagged UE3 collision primitives on an Ascend static mesh body."""

import math

from ascend_materials import properties, tag_data, typed_tag
from unreal_binary_scene import property_tag


SHAPES = {
    "sphereelems": ("sphere", ("radius",)),
    "boxelems": ("box", ("x", "y", "z")),
    "sphylelems": ("capsule", ("radius", "length")),
}
IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def fields(cursor, names):
    members = {}
    while tag := property_tag(cursor, names, True):
        key = tag["name"].casefold()
        if key in members or len(members) == 32:
            raise ValueError("Repeated or oversized Ascend collision properties.")
        members[key] = tag
        cursor.offset += tag["size"]
    return members


def array_fields(cursor, names):
    members = fields(cursor, names)
    if cursor.offset != cursor.limit:
        raise ValueError("Unexpected trailing Ascend collision data.")
    return members


def rigid_transform(tag, stream, names, units):
    matrix = IDENTITY
    if tag:
        cursor = tag_data(stream, tag)
        if tag["size"] == 64:
            matrix = cursor.fields("16f")
        else:
            planes = array_fields(cursor, names)
            if planes.keys() != {"xplane", "yplane", "zplane", "wplane"}:
                raise ValueError("Incomplete Ascend collision matrix.")
            matrix = []
            for axis in "xyzw":
                plane = typed_tag(planes.values(), f"{axis}plane", "StructProperty", "plane")
                if plane["size"] != 16:
                    raise ValueError("Unsupported Ascend collision matrix plane.")
                matrix.extend(tag_data(stream, plane).fields("4f"))
    axes = [matrix[offset:offset + 3] for offset in (0, 4, 8)]
    if (not all(math.isfinite(value) for value in matrix)
            or any(abs(matrix[index] - (index == 15)) > 1e-4 for index in (3, 7, 11, 15))):
        raise ValueError("Invalid Ascend collision transform.")
    return [value / units for value in matrix[12:15]], [list(axis) for axis in axes]


def enabled(tags, name, default=False):
    tag = typed_tag(tags, name, "BoolProperty")
    if tag and (tag["size"] or tag["boolean"] is None):
        raise ValueError(f"Invalid Ascend collision boolean {name}.")
    return bool(tag["boolean"]) if tag else default


def primitive(kind, dimensions, record, stream, names, units):
    if record.keys() - {*dimensions, "tm", "bnorbcollision", "bperpolyshape"}:
        raise ValueError("Unsupported Ascend collision primitive fields.")
    tags = record.values()
    if enabled(tags, "bnorbcollision") or enabled(tags, "bperpolyshape"):
        raise ValueError("Ascend collision primitive uses mixed query or physics settings.")
    center, axes = rigid_transform(typed_tag(tags, "tm", "StructProperty", "matrix"), stream, names, units)
    sizes = []
    for field in dimensions:
        tag = typed_tag(tags, field, "FloatProperty")
        if tag and tag["size"] != 4:
            raise ValueError("Invalid Ascend collision dimension.")
        sizes.append((tag_data(stream, tag).fields("f")[0] if tag else 1) / units)
    return dict(kind=kind, center_m=center, axes=axes, size_m=sizes)


def read_simple_collision(index, mesh_tags, has_triangles, report, stream, objects, units):
    if not index:
        return None
    body = objects.get(index)
    if not body or index < 0 or body["class_name"].casefold() != "rb_bodysetup" or not body["serial_bytes"]:
        raise ValueError("Ascend mesh references an unresolved or unsupported BodySetup.")
    tags, _ = properties(body, report, stream, objects)
    aggregate = typed_tag(tags, "agggeom", "StructProperty", "kaggregategeom")
    if not aggregate:
        raise ValueError("Ascend BodySetup has no readable tagged aggregate collision.")
    names = report["names"]
    arrays = array_fields(tag_data(stream, aggregate), names)
    if arrays.keys() - {*SHAPES, "convexelems", "bskipcloseandparallelchecks"}:
        raise ValueError("Unsupported Ascend aggregate collision fields.")
    if enabled(arrays.values(), "bskipcloseandparallelchecks"):
        raise ValueError("Ascend aggregate collision uses custom contact handling.")
    convex = typed_tag(arrays.values(), "convexelems", "ArrayProperty")
    if convex and (convex["size"] != 4 or tag_data(stream, convex).count(128)):
        raise ValueError("Ascend BodySetup convex collision requires author review.")
    shapes = []
    for name, (kind, dimensions) in SHAPES.items():
        array = typed_tag(arrays.values(), name, "ArrayProperty")
        if not array:
            continue
        cursor = tag_data(stream, array)
        for _ in range(cursor.count(128 - len(shapes))):
            shapes.append(primitive(kind, dimensions, fields(cursor, names), stream, names, units))
        if cursor.offset != cursor.limit:
            raise ValueError("Unexpected Ascend collision shape array length.")
    if not shapes:
        return None
    if (enabled(tags, "bnocollision") or not enabled(tags, "bblockzeroextent", True)
            or not enabled(tags, "bblocknonzeroextent", True)
            or not enabled(mesh_tags, "usesimpleboxcollision", True)
            or not enabled(mesh_tags, "usesimplerigidbodycollision", True)):
        raise ValueError("Ascend BodySetup uses collision flags that need author review.")
    if not enabled(mesh_tags, "usesimplelinecollision", True):
        if not has_triangles:
            raise ValueError("Ascend requests per-polygon line collision without compiled triangles.")
        return dict(mode="both", shapes=shapes)
    return dict(mode="simple", shapes=shapes)
