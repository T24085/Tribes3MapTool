"""Resolve base-color texture overrides on Ascend static mesh materials."""

from contextlib import contextmanager
from hashlib import sha256
import zlib

from model_textures import MAX_SIDE
from unreal_binary_scene import property_tag, read_properties
from unreal_package_reader import Cursor, open_package, table_name
from vengeance_terrain_layers import COLOR_FORMATS, texture_png


COLOR_PARAMETERS = {"basecolor", "basetexture", "diffuse", "diffusecolor", "diffusetexture", "albedo"}
PIXEL_FORMATS = {"PF_A8R8G8B8": 5, "PF_B8G8R8A8": 5, "PF_DXT1": 3, "PF_DXT3": 7, "PF_DXT5": 8}
EXTERNAL_OR_UNUSED = 0x21


def properties(asset, report, stream, objects):
    layout = report["summary"]
    if layout["version"] != 805 or layout["endian"] != "<":
        raise ValueError("Unsupported Ascend material package layout.")
    tags, _, issues, end = read_properties(stream, layout, asset, report["names"], objects)
    if issues:
        raise ValueError("Ascend material or texture has invalid tagged properties.")
    return tags, end


def tag_data(stream, tag):
    start = tag["value_offset"]
    return Cursor(stream, start, start + tag["size"])


@contextmanager
def local_object(asset, report, stream, objects, companions):
    if asset["index"] > 0:
        yield asset, report, stream, objects
        return
    namespace, separator, path = asset["object_path"].partition(".")
    if not separator:
        raise ValueError("Ascend material or texture import has no package namespace.")
    companion = companions.get(namespace.casefold())
    if companion is None:
        raise ValueError(f"List the Ascend artwork package {namespace}.upk in asset_packages.")
    with open_package(companion, "ascend") as (partner, payload):
        matches = [row for row in partner["exports"] if row["object_path"].casefold() == path.casefold()]
        if (len(matches) != 1 or not matches[0]["serial_bytes"]
                or matches[0]["class_name"].casefold() != asset["class_name"].casefold()):
            raise ValueError(f"No unique {asset['class_name']} export for {asset['object_path']} in {companion.name}.")
        entries = {row["index"]: row for row in partner["imports"] + partner["exports"]}
        yield matches[0], partner, payload, entries


def color_override(asset, report, stream, objects):
    tags, _ = properties(asset, report, stream, objects)
    arrays = [tag for tag in tags if tag["name"].casefold() == "textureparametervalues"]
    if len(arrays) != 1 or arrays[0]["kind"] != "ArrayProperty" or arrays[0]["slot"]:
        raise ValueError("No single usable Ascend texture parameter array.")
    cursor = tag_data(stream, arrays[0])
    names = report["names"]
    color = []
    for _ in range(cursor.count(128)):
        fields = {}
        for _ in range(64):
            tag = property_tag(cursor, names, True)
            if tag is None:
                break
            key = tag["name"].casefold()
            kind = {"parametername": "NameProperty", "parametervalue": "ObjectProperty"}.get(key)
            if kind:
                if tag["slot"] or key in fields or tag["kind"] != kind:
                    raise ValueError("Invalid Ascend texture parameter entry.")
                value = tag_data(stream, tag)
                fields[key] = table_name(value, names, True) if kind == "NameProperty" else value.index(True)
                if value.offset != value.limit:
                    raise ValueError("Invalid Ascend texture parameter length.")
            cursor.offset += tag["size"]
        else:
            raise ValueError("Oversized Ascend texture parameter entry.")
        name = fields.get("parametername", "").lower().replace("_", "").replace(" ", "")
        if name in COLOR_PARAMETERS:
            color.append(fields.get("parametervalue"))
    if (cursor.offset != cursor.limit or len(color) != 1 or color[0] not in objects
            or objects[color[0]]["class_name"].casefold() != "texture2d"):
        raise ValueError("Ascend material has no unambiguous base-color Texture2D override.")
    return objects[color[0]]


def bulk_range(cursor):
    flags, count, size, offset = cursor.fields("I3i")
    if count < 0 or size < 0 or (size and offset < 0):
        raise ValueError("Invalid Ascend texture bulk data header.")
    if not size or flags & EXTERNAL_OR_UNUSED:
        return None
    if offset != cursor.offset or size > cursor.limit - offset:
        raise ValueError("Ascend texture bulk data is outside its export.")
    cursor.offset += size
    return (offset, size) if flags in (0, 0x40) and size == count else None


def texture_mip(asset, report, stream, objects, rtc):
    if asset["class_name"].casefold() != "texture2d":
        raise ValueError("Ascend base color requires a Texture2D export.")
    tags, start = properties(asset, report, stream, objects)
    formats = [tag for tag in tags if tag["name"].casefold() == "format"]
    if len(formats) != 1 or formats[0]["kind"] != "ByteProperty" or formats[0]["slot"] or formats[0]["size"] != 8:
        raise ValueError("Missing tagged Ascend texture pixel format.")
    name = table_name(tag_data(stream, formats[0]), report["names"], True)
    kind = PIXEL_FORMATS.get(name)
    if kind is None:
        raise ValueError(f"Unsupported Ascend texture pixel format {name}.")
    cursor = Cursor(stream, start, asset["serial_offset"] + asset["serial_bytes"])
    bulk_range(cursor)  # Optional authoring payload, followed by the runtime mip array.
    stride, block_size = COLOR_FORMATS[kind]
    best = None
    largest = 0
    for _ in range(cursor.count(24)):
        payload = bulk_range(cursor)
        width, height = cursor.fields("2i")
        if not 1 <= width <= 32768 or not 1 <= height <= 32768:
            raise ValueError("Invalid Ascend texture mip dimensions.")
        largest = max(largest, width * height)
        if not payload or max(width, height) > MAX_SIDE:
            continue
        offset, length = payload
        expected = ((width + stride - 1) // stride) * ((height + stride - 1) // stride) * block_size
        if length != expected:
            raise ValueError("Ascend texture mip dimensions disagree with its byte count.")
        if best is None or width * height > best[0] * best[1]:
            best = width, height, offset, length
    cache_issue = None
    if rtc.available:
        try:
            cached = rtc.read_mip(asset, report, kind, best[0] * best[1] if best else 0)
            if cached:
                width, height, pixels, receipts = cached
                return texture_png(kind, width, height, pixels), [width, height], None, receipts
        except (OSError, ValueError, zlib.error) as error:
            cache_issue = f"Ascend RTC cache could not be used. {error}"
    if best is None:
        raise ValueError(cache_issue or "No supported Ascend texture mip. Supply the matching RTC cache if available.")
    width, height, offset, length = best
    data = texture_png(kind, width, height, stream[offset:offset + length])
    fallback = cache_issue or ("Larger mips were unavailable." if width * height < largest else None)
    return data, [width, height], fallback, ()


def resolve_material(asset, report, stream, objects, companions, _flags, *, rtc):
    if asset["class_name"].casefold() != "materialinstanceconstant":
        raise ValueError("Ascend shader graph requires a destination material.")
    receipts = {}
    main_name = report["source"]["name"]
    with local_object(asset, report, stream, objects, companions) as (material, owner, payload, entries):
        texture = color_override(material, owner, payload, entries)
        with local_object(texture, owner, payload, entries, companions) as (image, source, pixels, exports):
            data, size, fallback, cache_receipts = texture_mip(image, source, pixels, exports, rtc)
            for package in (owner, source):
                item = package["source"]
                if item["name"] != main_name:
                    receipts[item["name"]] = {key: item[key] for key in ("name", "sha256")}
    warning = "Base color imported as opaque. Other Ascend shader inputs require review."
    if fallback:
        warning = f"{warning} {fallback}"[:240]
    for receipt in cache_receipts:
        receipts[receipt["name"]] = receipt
    image_source = cache_receipts[-1] if cache_receipts else source["source"]
    name = "texture_" + sha256(data).hexdigest()[:32] + ".png"
    binding = dict(file=name, source=image_source["name"], source_sha256=image_source["sha256"],
                   size=size, mode="opaque", two_sided=False, warning=warning)
    return binding, {name: data}, receipts.values()
