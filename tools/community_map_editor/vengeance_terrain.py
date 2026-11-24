"""Read the height texture, cached coordinates, and quad masks of a Vengeance terrain."""

from hashlib import sha256
from contextlib import contextmanager

from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor, open_package


G16_TEXTURE = {10: (1, 2)}
RGBA8_TEXTURE = {5: (1, 4)}
HEIGHT_LAYOUT = (130, 27, "<")


def property_cursor(tags, name, kind, stream, endian):
    found = [tag for tag in tags if tag["name"].casefold() == name.casefold()]
    if len(found) != 1 or found[0]["kind"] != kind or found[0]["slot"]:
        raise ValueError(f"Missing or invalid Vengeance terrain property {name}.")
    start = found[0]["value_offset"]
    return Cursor(stream, start, start + found[0]["size"], endian)


def texture_dimension(tags, name, stream, endian):
    cursor = property_cursor(tags, name, "IntProperty", stream, endian)
    value, = cursor.fields("i")
    if cursor.offset != cursor.limit or value < 2 or value & (value - 1):
        raise ValueError("Invalid dimension in the Vengeance height texture.")
    return value


def read_texture_mip(texture, report, stream, objects, max_samples, formats):
    summary = report["summary"]
    endian = summary["endian"]
    tags, _, issues, start = read_properties(stream, summary, texture, report["names"], objects)
    pixel_format = property_cursor(tags, "Format", "ByteProperty", stream, endian)
    kind, = pixel_format.fields("B")
    if issues or kind not in formats or pixel_format.offset != pixel_format.limit:
        raise ValueError(f"Unsupported Vengeance terrain texture format for {texture['object_path']}.")
    width, height = (texture_dimension(tags, key, stream, endian) for key in ("USize", "VSize"))
    if width * height > max_samples:
        raise ValueError("The Vengeance terrain texture exceeds the editor intake size limit.")
    payload = Cursor(stream, start, texture["serial_offset"] + texture["serial_bytes"], endian)
    if not 1 <= payload.index(False) <= max(width, height).bit_length() or payload.fields("3i") != (3, 0, 1):
        raise ValueError("Unrecognized mip header in the Vengeance terrain texture.")
    empty_end, = payload.fields("i")
    if payload.index(False) != 0 or payload.offset != empty_end:
        raise ValueError("Unexpected auxiliary data in the Vengeance terrain texture.")
    mip_end, = payload.fields("i")
    size = payload.index(False)
    block, stride = formats[kind]
    cols, rows = ((length + block - 1) // block for length in (width, height))
    if size != cols * rows * stride or payload.offset + size != mip_end:
        raise ValueError("The Vengeance terrain mip length does not match its dimensions.")
    raw = payload.take(size)
    mip_width, mip_height, u_bits, v_bits = payload.fields("iiBB")
    if (mip_width, mip_height, u_bits, v_bits) != (width, height, width.bit_length() - 1, height.bit_length() - 1):
        raise ValueError("The Vengeance terrain mip dimensions do not match the texture.")
    return width, height, raw, kind


def read_bitmap(node, prop, stream, endian, samples):
    cursor = property_cursor(node["tags"], prop, "ArrayProperty", stream, endian)
    words = cursor.index(False)
    if words != (samples + 31) // 32 or cursor.limit - cursor.offset != words * 4:
        raise ValueError(f"The Vengeance {prop} does not match the height texture dimensions.")
    return cursor.take(words * 4)


def cached_coordinates(node, stream, endian, objects):
    payload = Cursor(stream, node["property_end"], node["serial_offset"] + node["serial_bytes"], endian)
    if payload.fields("3i") != (3, 4, 3):
        raise ValueError("Unrecognized Vengeance terrain header.")
    count = payload.index(False)
    if not 1 <= count <= len(objects):
        raise ValueError("Invalid Vengeance terrain sector count.")
    sectors = set()
    for _ in range(count):
        sector = payload.index(False)
        if sector <= 0 or objects.get(sector, {}).get("class_name", "").casefold() != "terrainsector" or sector in sectors:
            raise ValueError("The Vengeance terrain lists an invalid or duplicate sector.")
        sectors.add(sector)
    cols, rows = payload.fields("ii")
    if cols <= 0 or rows <= 0 or cols * rows != count:
        raise ValueError("The Vengeance terrain sector grid does not match its roster.")
    offset = payload.fields("3f")
    x_axis, y_axis, z_axis = (payload.fields("3f") for _ in range(3))
    if any((*x_axis[1:], y_axis[0], y_axis[2], *z_axis[:2])):
        raise ValueError("The Vengeance terrain's cached grid must be axis-aligned.")
    return offset, (x_axis[0], y_axis[1], z_axis[2])


@contextmanager
def external_object(asset, objects, companions):
    package = asset
    while package["outer"] < 0:
        package = objects[package["outer"]]
    namespace = package["name"]
    prefix = namespace + "."
    if (package["outer"] or package["class_name"].casefold() != "package"
            or not asset["object_path"].startswith(prefix)):
        raise ValueError("The Vengeance object import has no external package root.")
    path = companions.get(namespace.casefold()) if companions else None
    if path is None:
        raise ValueError(f"List the Vengeance package {namespace}.pkg in asset_packages.")
    local_path = asset["object_path"][len(prefix):]
    with open_package(path, "vengeance") as (report, stream):
        summary = report["summary"]
        if (summary["version"], summary["licensee_version"], summary["endian"]) != HEIGHT_LAYOUT:
            raise ValueError("The external Vengeance package has an unsupported layout.")
        matches = [asset for asset in report["exports"] if asset["object_path"].casefold() == local_path.casefold()]
        if (len(matches) != 1 or matches[0]["class_name"].casefold() != asset["class_name"].casefold()
                or not matches[0]["serial_bytes"]):
            raise ValueError(f"Expected one {asset['class_name']} export for {asset['object_path']} in {path.name}.")
        package_objects = {item["index"]: item for item in report["imports"] + report["exports"]}
        yield matches[0], report, stream, package_objects


def external_texture(texture, objects, companions, max_samples, formats):
    with external_object(texture, objects, companions) as (export, report, stream, package_objects):
        dimensions = read_texture_mip(export, report, stream, package_objects, max_samples, formats)
        receipt = {key: report["source"][key] for key in ("name", "sha256")}
    return (*dimensions, receipt)


def read_heightfield(node, report, stream, max_samples, companions=None):
    summary = report["summary"]
    endian = summary["endian"]
    if (summary["version"], summary["licensee_version"], endian) != HEIGHT_LAYOUT or node["property_issues"]:
        raise ValueError("The native terrain layout of this Vengeance package is unsupported.")
    objects = {item["index"]: item for item in report["imports"] + report["exports"]}
    cursor = property_cursor(node["tags"], "TerrainMap", "ObjectProperty", stream, endian)
    index = cursor.index(False)
    texture = objects.get(index, {})
    if cursor.offset != cursor.limit or texture.get("class_name", "").casefold() != "texture":
        raise ValueError("The Vengeance terrain requires a valid TerrainMap texture reference.")
    height_package = None
    if index < 0:
        width, height, raw, _, height_package = external_texture(texture, objects, companions, max_samples, G16_TEXTURE)
    else:
        width, height, raw, _ = read_texture_mip(texture, report, stream, objects, max_samples, G16_TEXTURE)
    visibility = read_bitmap(node, "QuadVisibilityBitmap", stream, endian, width * height)
    edge_turns = read_bitmap(node, "EdgeTurnBitmap", stream, endian, width * height)
    origin, scale = cached_coordinates(node, stream, endian, objects)
    # These flags use the full height texture row stride, even at the right edge.
    patch_flags = bytes((0 if visibility[i // 8] & (1 << (i % 8)) else 1)
                        | (2 if edge_turns[i // 8] & (1 << (i % 8)) else 0) for i in range(width * height))
    provenance = dict(height_samples_sha256=sha256(raw).hexdigest(),
                      quad_visibility_sha256=sha256(visibility).hexdigest(), edge_turns_sha256=sha256(edge_turns).hexdigest(),
                      height_texture=texture["object_path"], cached_origin=list(origin), cached_scale=list(scale))
    if height_package:
        provenance["height_package"] = height_package
    offset_x, offset_y, midpoint = origin
    world_origin = (-offset_x * scale[0], -offset_y * scale[1], 0)
    return (width, height), raw, patch_flags, world_origin, scale, midpoint, provenance
