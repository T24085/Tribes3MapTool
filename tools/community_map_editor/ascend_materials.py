"""Resolve saved base-color texture inputs on Ascend static mesh materials."""

from contextlib import ExitStack, contextmanager
from hashlib import sha256
import zlib

from interior_geometry import material_slots
from model_textures import MAX_SIDE, placement_key
from unreal_binary_scene import property_tag, read_properties
from unreal_package_reader import Cursor, open_package, table_name
from vengeance_terrain_layers import COLOR_FORMATS, texture_png


COLOR_PARAMETERS = {"basecolor", "basetexture", "diffuse", "diffusecolor", "diffusetexture", "albedo"}
PIXEL_FORMATS = {"PF_A8R8G8B8": 5, "PF_B8G8R8A8": 5, "PF_DXT1": 3, "PF_DXT3": 7, "PF_DXT5": 8}
EXTERNAL_OR_UNUSED = 0x21
TEXTURE_SAMPLE = "materialexpressiontexturesample"
TEXTURE_PARAMETER = "materialexpressiontexturesampleparameter2d"
MASK_FIELDS = {"mask", "maskr", "maskg", "maskb", "maska", "gcc64_padding"}


class MissingArtPackage(ValueError):
    pass


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
        raise MissingArtPackage(f"List the Ascend artwork package {namespace}.upk in asset_packages.")
    with open_package(companion, "ascend") as (partner, payload):
        matches = [row for row in partner["exports"] if row["object_path"].casefold() == path.casefold()]
        if (len(matches) != 1 or not matches[0]["serial_bytes"]
                or matches[0]["class_name"].casefold() != asset["class_name"].casefold()):
            raise ValueError(f"No unique {asset['class_name']} export for {asset['object_path']} in {companion.name}.")
        entries = {row["index"]: row for row in partner["imports"] + partner["exports"]}
        yield matches[0], partner, payload, entries


def typed_tag(tags, name, kind, structure=None):
    matches = [tag for tag in tags if tag["name"].casefold() == name]
    if not matches:
        return None
    tag = matches[0]
    if (len(matches) != 1 or tag["kind"] != kind or tag["slot"]
            or structure and (tag["structure"] or "").casefold() != structure):
        raise ValueError(f"Invalid or repeated Ascend property {tag['name']}.")
    return tag


def object_reference(tags, stream, name):
    tag = typed_tag(tags, name, "ObjectProperty")
    if tag is None:
        return 0
    if tag["size"] != 4:
        raise ValueError(f"Invalid Ascend object reference in {name}.")
    return tag_data(stream, tag).index(True)


def placed_materials(placements, meshes, stream):
    """Read overrides only for the component that owns the placed mesh."""
    models, issues = {}, []
    for actor, raw, row, components in placements:
        reference = row.get("model_reference")
        if reference not in meshes:
            continue
        geometry = meshes[reference]
        source_line = actor["assets"][0]["line"]
        try:
            component_id = (object_reference(raw["tags"], stream, "staticmeshcomponent")
                            if source_line == actor["line"] else source_line)
            owners = [component for component in components if component["line"] == component_id]
            if not component_id and len(components) == 1:
                owners = components
            if len(owners) != 1:
                raise ValueError("Cannot associate Ascend component materials with a single placed mesh.")
            if any(tag["name"].casefold() == "materials" for component in components
                   if component is not owners[0] for tag in component["tags"]):
                issues.append((row, "Additional Ascend component materials require a separate mesh placement."))
            tag = typed_tag(owners[0]["tags"], "materials", "ArrayProperty")
            if not tag:
                continue
            cursor = tag_data(stream, tag)
            count = cursor.index(True)
            if not 0 <= count <= 256 or tag["size"] != (count + 1) * 4:
                raise ValueError("Invalid Ascend component Materials array length.")
            drawn = set(material_slots(geometry))
            slots = {}
            for slot in range(count):
                index = cursor.index(True)
                if index and slot in drawn:
                    slots[slot] = (index, 0)
                elif index:
                    issues.append((row, f"Ascend component material slot {slot} is absent from the imported mesh LOD."))
            if slots:
                models[placement_key(row["id"])] = (row["name"], geometry, slots)
        except ValueError as error:
            issues.append((row, str(error)))
    return models, issues


def texture_overrides(tags, report, stream):
    tag = typed_tag(tags, "textureparametervalues", "ArrayProperty")
    if tag is None:
        return {}
    cursor = tag_data(stream, tag)
    names = report["names"]
    overrides = {}
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
        name = fields.get("parametername", "").casefold()
        if not name or name == "none" or name in overrides:
            raise ValueError("Invalid or repeated Ascend texture parameter name.")
        # A null or omitted ParameterValue is an explicit empty override.
        overrides[name] = fields.get("parametervalue", 0)
    if cursor.offset != cursor.limit:
        raise ValueError("Unexpected trailing bytes in the Ascend texture parameter array.")
    return overrides


def material_input(tag, report, stream):
    data = tag_data(stream, tag)
    result = {}
    for _ in range(16):
        member = property_tag(data, report["names"], True)
        if member is None:
            if data.offset == data.limit:
                return result
            break
        key = member["name"].casefold()
        if key in result or member["slot"]:
            break
        if key == "useconstant" and member["kind"] == "BoolProperty":
            result[key] = bool(member["boolean"])
        elif ((key == "expression" and member["kind"] == "ObjectProperty"
               or key in MASK_FIELDS and member["kind"] == "IntProperty") and member["size"] == 4):
            result[key], = tag_data(stream, member).fields("i")
        elif key == "constant" and member["kind"] == "StructProperty":
            result[key] = None
        else:
            break
        data.offset += member["size"]
    raise ValueError("Ascend material uses an unsupported input structure.")


def direct_sample(tags, report, stream, objects):
    tag = typed_tag(tags, "diffusecolor", "StructProperty", "colormaterialinput")
    if tag is None:
        return None
    fields = material_input(tag, report, stream)
    expression = objects.get(fields.get("expression", 0))
    mask = tuple(fields.get(name, 0) for name in ("maskr", "maskg", "maskb", "maska"))
    if (not expression or fields.get("useconstant") or fields.get("mask", 0) not in (0, 1)
            or mask not in ((0, 0, 0, 0), (1, 1, 1, 0))
            or (fields.get("mask") == 1 and mask != (1, 1, 1, 0))
            or expression["class_name"].casefold() not in (TEXTURE_SAMPLE, TEXTURE_PARAMETER)):
        raise ValueError("Ascend diffuse input is not a direct RGB texture sample.")
    return expression


def texture_scope(index, owner, stream, objects):
    texture = objects.get(index)
    if not texture or texture["class_name"].casefold() != "texture2d":
        raise ValueError("Ascend material does not resolve to a Texture2D.")
    return texture, owner, stream, objects


def sample_texture(expression, owner, stream, objects, open_art, overrides):
    sample, owner, stream, objects = open_art(expression, owner, stream, objects)
    tags, _ = properties(sample, owner, stream, objects)
    uv = typed_tag(tags, "coordinates", "StructProperty", "expressioninput")
    if uv and material_input(uv, owner, stream).get("expression", 0):
        raise ValueError("Ascend diffuse sample uses custom coordinates.")
    if sample["class_name"].casefold() == TEXTURE_PARAMETER:
        parameter = typed_tag(tags, "parametername", "NameProperty")
        if not parameter or parameter["size"] != 8:
            raise ValueError("Ascend diffuse sample has an invalid parameter name.")
        name = table_name(tag_data(stream, parameter), owner["names"], True).casefold()
        if name in overrides:
            return texture_scope(*overrides[name])
    return texture_scope(object_reference(tags, stream, "texture"), owner, stream, objects)


def material_texture(asset, owner, stream, objects, open_art):
    overrides = {}
    seen = set()
    unavailable = None
    for _ in range(16):
        try:
            material, owner, stream, objects = open_art(asset, owner, stream, objects)
        except MissingArtPackage as error:
            unavailable = error
            break
        key = owner["source"]["name"].casefold(), material["index"]
        if key in seen:
            raise ValueError("Ascend material parent chain contains a cycle.")
        seen.add(key)
        klass = material["class_name"].casefold()
        tags, _ = properties(material, owner, stream, objects)
        if klass == "material":
            sample = direct_sample(tags, owner, stream, objects)
            if sample:
                return sample_texture(sample, owner, stream, objects, open_art, overrides)
            break
        if klass != "materialinstanceconstant":
            raise ValueError("Ascend shader graph requires a destination material.")
        for name, index in texture_overrides(tags, owner, stream).items():
            scope = index, owner, stream, objects
            overrides.setdefault(name, scope)
        parent = object_reference(tags, stream, "parent")
        if not parent:
            break
        if parent not in objects:
            raise ValueError("Ascend material has an invalid parent reference.")
        asset = objects[parent]
    else:
        raise ValueError("Ascend material parent chain exceeds the supported depth.")
    fallback = [scope for name, scope in overrides.items()
                if name.replace("_", "").replace(" ", "") in COLOR_PARAMETERS]
    if len(fallback) == 1:
        return texture_scope(*fallback[0])
    if unavailable:
        raise unavailable
    raise ValueError("Ascend material has no unambiguous base-color Texture2D override.")


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
    receipts = {}
    main_name = report["source"]["name"]
    with ExitStack() as scope:
        def open_art(asset, owner, stream, objects):
            local = scope.enter_context(local_object(asset, owner, stream, objects, companions))
            item = local[1]["source"]
            if item["name"] != main_name:
                receipt = {key: item[key] for key in ("name", "sha256")}
                previous = receipts.setdefault(item["name"], receipt)
                if previous != receipt:
                    raise ValueError("Ascend artwork package changed during import.")
            return local

        texture = material_texture(asset, report, stream, objects, open_art)
        image, source, pixels, exports = open_art(*texture)
        data, size, fallback, cache_receipts = texture_mip(image, source, pixels, exports, rtc)
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
