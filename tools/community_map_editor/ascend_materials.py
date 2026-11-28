"""Resolve saved base-color texture inputs on Ascend static mesh materials."""

from contextlib import ExitStack, contextmanager
from hashlib import sha256
from math import isfinite
import re
import zlib

from interior_geometry import material_slots
from model_textures import MAX_SIDE, placement_key
from unreal_binary_scene import property_tag, read_properties
from unreal_package_reader import Cursor, decode_chunk, open_package, table_name
from vengeance_terrain_layers import COLOR_FORMATS, texture_png


COLOR_PARAMETERS = {"basecolor", "basetexture", "diffuse", "diffusecolor", "diffusetexture", "albedo"}
# Base-color parameter names Tribes: Ascend materials use: Dif, DIF1, DIF01, Diffuse2 and the names above.
COLOR_PARAMETER = re.compile(r"(?:dif|diffuse|albedo|basecolor|basetexture|diffusecolor|diffusetexture)\d{0,2}")
PIXEL_FORMATS = {"PF_A8R8G8B8": 5, "PF_B8G8R8A8": 5, "PF_DXT1": 3, "PF_DXT3": 7, "PF_DXT5": 8}
# UE3 bulk data flags.
BULK_SEPARATE_FILE = 0x01  # Stored in a texture file cache (.tfc) instead of the package.
BULK_ZLIB = 0x02
BULK_LZO = 0x10
BULK_UNUSED = 0x20
BULK_PAYLOAD_ONLY = 0x40
TEXTURE_SAMPLE = "materialexpressiontexturesample"
TEXTURE_PARAMETER = "materialexpressiontexturesampleparameter2d"
MASK_FIELDS = {"mask", "maskr", "maskg", "maskb", "maska", "gcc64_padding"}
# Shader graph nodes that carry the base color through to DiffuseColor, with the inputs that hold it.
# LinearInterpolate's B input is a blended layer, so it ranks below A.
COLOR_PATH = {
    "materialexpressionmultiply": ("a", "b"),
    "materialexpressionadd": ("a", "b"),
    "materialexpressionlinearinterpolate": ("a", "b"),
    "materialexpressiondesaturation": ("input",),
    "materialexpressionconstantclamp": ("input",),
    "materialexpressionclamp": ("input",),
    "materialexpressionpower": ("base",),
}
SECONDARY_INPUTS = {("materialexpressionlinearinterpolate", "b")}
# Layer and mask names that are not the surface's own base color.
DETAIL_WORDS = ("detail", "overlay", "moss", "dirt", "grime", "mask", "msk", "noise", "emi", "nrm", "spec")
BASE_WORDS = ("dif", "diffuse", "albedo", "basecolor", "_d")
MAX_GRAPH_NODES = 64
APPROXIMATE = "Tints, blended layers and custom tiling from the Ascend shader were not applied."


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


def rgb_expression(tag, report, stream, objects):
    fields = material_input(tag, report, stream)
    expression = objects.get(fields.get("expression", 0))
    mask = tuple(fields.get(name, 0) for name in ("maskr", "maskg", "maskb", "maska"))
    if (not expression or fields.get("useconstant") or fields.get("mask", 0) not in (0, 1)
            or mask not in ((0, 0, 0, 0), (1, 1, 1, 0))
            or (fields.get("mask") == 1 and mask != (1, 1, 1, 0))):
        raise ValueError("Ascend diffuse input is not a direct RGB texture sample.")
    return expression


def direct_sample(tags, report, stream, objects, open_art):
    tag = typed_tag(tags, "diffusecolor", "StructProperty", "colormaterialinput")
    if tag is None:
        return None
    expression = rgb_expression(tag, report, stream, objects)
    klass = expression["class_name"].casefold()
    if klass in (TEXTURE_SAMPLE, TEXTURE_PARAMETER):
        return (expression, report, stream, objects), False
    if klass == "materialexpressionmultiply":
        node, report, stream, objects = open_art(expression, report, stream, objects)
        tags, _ = properties(node, report, stream, objects)
        inputs = [typed_tag(tags, key, "StructProperty", "expressioninput") for key in ("a", "b")]
        if all(inputs):
            children = [rgb_expression(member, report, stream, objects) for member in inputs]
            if children[0]["class_name"].casefold() == "materialexpressionvertexcolor":
                children.reverse()
            sample, tint = children
            if (sample["class_name"].casefold() in (TEXTURE_SAMPLE, TEXTURE_PARAMETER)
                    and tint["class_name"].casefold() == "materialexpressionvertexcolor"):
                return (sample, report, stream, objects), True
    raise ValueError("Ascend diffuse input is not a direct RGB texture sample or texture multiplied by vertex RGB.")


def color_parameter(name):
    return bool(COLOR_PARAMETER.fullmatch(name.casefold().replace("_", "").replace(" ", "")))


def graph_samples(expression, owner, stream, objects, open_art):
    """Find the texture samples on DiffuseColor's color path, for shaders with tints or blended layers.

    Walks color-carrying nodes (multiply, add, lerp, desaturate, clamp, power) and returns
    (sample scope, depth, secondary) for each RGB texture sample. Single-channel samples are masks,
    and cube or reflection-vector samples are reflections, so neither is a base color.
    """
    found, pending, visited = [], [(expression, owner, stream, objects, 0, False)], 0
    while pending:
        node, owner, stream, objects, depth, secondary = pending.pop(0)
        visited += 1
        if visited > MAX_GRAPH_NODES:
            raise ValueError("Ascend diffuse graph is too large to resolve.")
        node, owner, stream, objects = open_art(node, owner, stream, objects)
        klass = node["class_name"].casefold()
        if klass in (TEXTURE_SAMPLE, TEXTURE_PARAMETER):
            found.append(((node, owner, stream, objects), depth, secondary))
            continue
        inputs = COLOR_PATH.get(klass)
        if not inputs:
            continue  # Constants, parameters, vertex color and unknown nodes add no texture.
        tags, _ = properties(node, owner, stream, objects)
        for name in inputs:
            tag = typed_tag(tags, name, "StructProperty", "expressioninput")
            if tag is None:
                continue
            try:
                fields = material_input(tag, owner, stream)
            except ValueError:
                continue
            child = objects.get(fields.get("expression", 0))
            channels = tuple(fields.get(key, 0) for key in ("maskr", "maskg", "maskb", "maska"))
            if not child or fields.get("mask") and channels != (1, 1, 1, 0):
                continue  # Masked to a single channel: a blend weight, not a color.
            pending.append((child, owner, stream, objects, depth + 1,
                            secondary or (klass, name) in SECONDARY_INPUTS))
    return found


def sample_parameter(sample):
    node, owner, stream, objects = sample
    if node["class_name"].casefold() != TEXTURE_PARAMETER:
        return None
    tags, _ = properties(node, owner, stream, objects)
    parameter = typed_tag(tags, "parametername", "NameProperty")
    if not parameter or parameter["size"] != 8:
        return None
    return table_name(tag_data(stream, parameter), owner["names"], True)


def graph_texture(expression, owner, stream, objects, open_art, overrides):
    """Pick the one base-color Texture2D on a shader graph's color path, or explain why not."""
    ranked = {}
    for sample, depth, secondary in graph_samples(expression, owner, stream, objects, open_art):
        try:
            texture, uv, custom = sample_texture(*sample, open_art, overrides, allow_custom_uv=True)
        except ValueError:
            continue  # Reflection, cube or otherwise unusable samples cannot be the base color.
        label = texture[0]["object_path"].rsplit(".", 1)[-1]
        words = (label + " " + (sample_parameter(sample) or "")).casefold()
        rank = (any(word in words for word in DETAIL_WORDS), not any(word in words for word in BASE_WORDS),
                secondary, custom, depth)
        key = texture[0]["object_path"].casefold()
        if key not in ranked or rank < ranked[key][0]:
            ranked[key] = rank, texture, uv, label
    if not ranked:
        raise ValueError("Ascend diffuse graph has no RGB Texture2D sample on its color path.")
    best = sorted(ranked.values(), key=lambda item: item[0])
    if len(best) > 1 and best[0][0] == best[1][0]:
        raise ValueError(f"Ascend base color is ambiguous between {best[0][3]} and {best[1][3]}.")
    return best[0][1], best[0][2]


def sample_coordinates(tags, owner, stream, objects, open_art):
    uv = typed_tag(tags, "coordinates", "StructProperty", "expressioninput")
    if uv is None:
        return None
    fields = material_input(uv, owner, stream)
    if not fields.get("expression"):
        if not fields.get("useconstant"):
            return None
        raise ValueError("Ascend diffuse sample uses custom coordinates.")
    coordinate = objects.get(fields["expression"])
    if (not coordinate or coordinate["class_name"].casefold() != "materialexpressiontexturecoordinate"
            or any(fields.get(key) for key in MASK_FIELDS - {"gcc64_padding"}) or fields.get("useconstant")):
        raise ValueError("Ascend diffuse sample uses custom coordinates.")
    coordinate, owner, stream, objects = open_art(coordinate, owner, stream, objects)
    tags, _ = properties(coordinate, owner, stream, objects)
    for axis in ("u", "v"):
        mirror = typed_tag(tags, "unmirror" + axis, "BoolProperty")
        if mirror and mirror["boolean"]:
            raise ValueError("Ascend unmirrored texture coordinates require review.")
    uv = []
    for name, kind, form, default in (("coordinateindex", "IntProperty", "i", 0),
                                       ("utiling", "FloatProperty", "f", 1.0),
                                       ("vtiling", "FloatProperty", "f", 1.0)):
        tag = typed_tag(tags, name, kind)
        if tag and tag["size"] != 4:
            raise ValueError("Invalid Ascend texture coordinate value.")
        uv.append(tag_data(stream, tag).fields(form)[0] if tag else default)
    if not 0 <= uv[0] <= 7 or any(not isfinite(value) or abs(value) > 1024 for value in uv[1:]):
        raise ValueError("Invalid Ascend texture tiling or channel.")
    return uv


def texture_scope(index, owner, stream, objects):
    texture = objects.get(index)
    if not texture or texture["class_name"].casefold() != "texture2d":
        raise ValueError("Ascend material does not resolve to a Texture2D.")
    return texture, owner, stream, objects


PANNER = "materialexpressionpanner"
TEXTURE_COORDINATE = "materialexpressiontexturecoordinate"
MAX_PAN_SPEED = 64.0


def sample_motion(expression, owner, stream, objects, open_art):
    """{"tiling": [u, v], "pan": [x, y]} for a sample scrolled by a Panner over a TextureCoordinate, else None.

    Effect textures (Tartarus's waterfall) scroll and repeat: a Panner's SpeedX and SpeedY move the
    coordinate, and the TextureCoordinate under it sets the repeat. Anything else returns None and the
    sample keeps the mesh's own coordinates.
    """
    try:
        sample, owner, stream, objects = open_art(expression, owner, stream, objects)
        tags, _ = properties(sample, owner, stream, objects)
        tag = typed_tag(tags, "coordinates", "StructProperty", "expressioninput")
        fields = material_input(tag, owner, stream) if tag else {}
        node = objects.get(fields.get("expression", 0))
        if not node or node["class_name"].casefold() != PANNER:
            return None
        node, owner, stream, objects = open_art(node, owner, stream, objects)
        panner, _ = properties(node, owner, stream, objects)
        pan = []
        for name in ("speedx", "speedy"):
            speed = typed_tag(panner, name, "FloatProperty")
            pan.append(tag_data(stream, speed).fields("f")[0] if speed and speed["size"] == 4 else 0.0)
        tiling = [1.0, 1.0]
        coordinate = typed_tag(panner, "coordinate", "StructProperty", "expressioninput")
        under = objects.get(material_input(coordinate, owner, stream).get("expression", 0)) if coordinate else None
        if under and under["class_name"].casefold() == TEXTURE_COORDINATE:
            under, owner, stream, objects = open_art(under, owner, stream, objects)
            coordinate_tags, _ = properties(under, owner, stream, objects)
            for axis, name in enumerate(("utiling", "vtiling")):
                value = typed_tag(coordinate_tags, name, "FloatProperty")
                if value and value["size"] == 4:
                    tiling[axis] = tag_data(stream, value).fields("f")[0]
        elif under:
            return None  # A Panner over something other than plain coordinates: leave it out.
    except (ValueError, KeyError, TypeError):
        return None
    if (any(not isfinite(value) or abs(value) > MAX_PAN_SPEED for value in pan)
            or any(not isfinite(value) or not 0 < abs(value) <= 1024 for value in tiling)):
        return None
    return {"tiling": [round(value, 4) for value in tiling], "pan": [round(value, 4) for value in pan]}


def sample_texture(expression, owner, stream, objects, open_art, overrides, *, allow_custom_uv=False):
    """Return (texture scope, uv); with allow_custom_uv, (texture scope, uv, custom) and default tiling."""
    sample, owner, stream, objects = open_art(expression, owner, stream, objects)
    tags, _ = properties(sample, owner, stream, objects)
    custom = False
    try:
        uv = sample_coordinates(tags, owner, stream, objects, open_art)
    except ValueError as error:
        if not allow_custom_uv or "custom coordinates" not in str(error):
            raise
        uv, custom = None, True
    texture = None
    if sample["class_name"].casefold() == TEXTURE_PARAMETER:
        parameter = typed_tag(tags, "parametername", "NameProperty")
        if not parameter or parameter["size"] != 8:
            raise ValueError("Ascend diffuse sample has an invalid parameter name.")
        name = table_name(tag_data(stream, parameter), owner["names"], True).casefold()
        if name in overrides:
            texture = texture_scope(*overrides[name])
    if texture is None:
        texture = texture_scope(object_reference(tags, stream, "texture"), owner, stream, objects)
    return (texture, uv, custom) if allow_custom_uv else (texture, uv)


def material_texture(asset, owner, stream, objects, open_art):
    overrides = {}
    seen = set()
    unavailable = None
    missing_diffuse = False
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
            try:
                diffuse = direct_sample(tags, owner, stream, objects, open_art)
            except ValueError as error:
                if "not a direct RGB texture sample" not in str(error):
                    raise
                # Tinted, blended or layered shader: take its single base-color sample (#9).
                tag = typed_tag(tags, "diffusecolor", "StructProperty", "colormaterialinput")
                fields = material_input(tag, owner, stream)
                expression = objects.get(fields.get("expression", 0))
                channels = tuple(fields.get(key, 0) for key in ("maskr", "maskg", "maskb", "maska"))
                if (not expression or fields.get("useconstant")
                        or fields.get("mask") and channels != (1, 1, 1, 0)):
                    raise
                texture, uv = graph_texture(expression, owner, stream, objects, open_art, overrides)
                return texture, ({"uv": uv} if uv is not None else {}), APPROXIMATE
            if diffuse:
                sample, vertex_rgb = diffuse
                texture, uv = sample_texture(*sample, open_art, overrides)
                settings = {"uv": uv} if uv is not None else {}
                if vertex_rgb:
                    settings["vertex_rgb"] = True
                return texture, settings, None
            missing_diffuse = True
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
    fallback = [scope for name, scope in overrides.items() if color_parameter(name)]
    if len(fallback) == 1:
        return texture_scope(*fallback[0]), {}, None
    if unavailable:
        raise unavailable
    if missing_diffuse:
        raise ValueError("Ascend material has no diffuse input (an emissive, effect or force-field shader).")
    raise ValueError("Ascend material has no unambiguous base-color Texture2D override.")


def bulk_range(cursor):
    """Return a mip payload as (where, offset, size, codec, count), or None when unusable.

    ``where`` is "inline" for bytes inside the export or "cache" for bytes stored in the
    texture file cache (.tfc) named by the texture's TextureFileCacheName property.
    """
    flags, count, size, offset = cursor.fields("I3i")
    if flags & BULK_UNUSED:
        return None  # Unused mip slots carry size and offset -1.
    if count < 0 or size < 0 or (size and offset < 0):
        raise ValueError("Invalid Ascend texture bulk data header.")
    if not size:
        return None
    codec = 2 if flags & BULK_LZO else 1 if flags & BULK_ZLIB else 0
    if flags & ~(BULK_SEPARATE_FILE | BULK_ZLIB | BULK_LZO | BULK_PAYLOAD_ONLY) or flags & BULK_ZLIB and flags & BULK_LZO:
        return None
    if not codec and size != count:
        return None
    if flags & BULK_SEPARATE_FILE:
        return "cache", offset, size, codec, count
    if offset != cursor.offset or size > cursor.limit - offset:
        raise ValueError("Ascend texture bulk data is outside its export.")
    cursor.offset += size
    return "inline", offset, size, codec, count


def cache_name(tags, report, stream):
    tag = next((tag for tag in tags if tag["name"].casefold() == "texturefilecachename"), None)
    if tag is None or tag["kind"] != "NameProperty" or tag["size"] != 8:
        return None
    return table_name(tag_data(stream, tag), report["names"], True)


def mip_bytes(payload, stream, rtc, cache):
    where, offset, size, codec, count = payload
    if where == "cache":
        if not cache:
            raise ValueError("The Ascend texture stores its mips in a texture file cache but names none.")
        return rtc.read_external(cache, offset, size, codec, count)
    if not codec:
        return stream[offset:offset + size]
    return decode_chunk(stream, offset, size, count, codec)


MASK_CHANNELS = ("maskr", "maskg", "maskb", "maska")


def material_blend(asset, owner, stream, objects, open_art):
    """Return the base Material's (blend mode, two-sided, mask) for an Ascend material (#35).

    The mask is (texture scope, channel 0-3) when the material is BLEND_Masked: Ascend foliage
    reads its cut-out from the diffuse alpha, a separate mask texture's channel, or a normal
    map's alpha. Instances inherit blending from the base Material and may override textures.
    """
    overrides = {}
    for _ in range(16):
        material, owner, stream, objects = open_art(asset, owner, stream, objects)
        tags, _ = properties(material, owner, stream, objects)
        if material["class_name"].casefold() == "material":
            break
        for name, index in texture_overrides(tags, owner, stream).items():
            overrides.setdefault(name, (index, owner, stream, objects))
        parent = object_reference(tags, stream, "parent")
        if not parent or parent not in objects:
            return "BLEND_Opaque", False, None
        asset = objects[parent]
    else:
        raise ValueError("Ascend material parent chain exceeds the supported depth.")
    blend = typed_tag(tags, "blendmode", "ByteProperty")
    mode = table_name(tag_data(stream, blend), owner["names"], True) if blend and blend["size"] == 8 else "BLEND_Opaque"
    sided = typed_tag(tags, "twosided", "BoolProperty")
    two_sided = bool(sided and sided["boolean"])
    if mode != "BLEND_Masked":
        return mode, two_sided, None
    tag = typed_tag(tags, "opacitymask", "StructProperty", "scalarmaterialinput")
    fields = material_input(tag, owner, stream) if tag else {}
    expression = objects.get(fields.get("expression", 0))
    if not expression or fields.get("useconstant"):
        raise ValueError("Ascend masked material has no texture opacity mask.")
    if fields.get("mask"):
        channels = [index for index, key in enumerate(MASK_CHANNELS) if fields.get(key)]
        if len(channels) != 1:
            raise ValueError("Ascend opacity mask must read a single texture channel.")
        channel, = channels
    else:
        channel = 0  # An unmasked colour output used as a scalar reads its red channel.
    if expression["class_name"].casefold() not in (TEXTURE_SAMPLE, TEXTURE_PARAMETER):
        raise ValueError("Ascend opacity mask is not a direct texture sample.")
    texture, _ = sample_texture(expression, owner, stream, objects, open_art, overrides)
    return mode, two_sided, (texture, channel)


MAX_GLOW_TINT = 64.0
GLOW_NODES = 48
VECTOR_PARAMETER = "materialexpressionvectorparameter"
CONSTANT_COLOR = "materialexpressionconstant3vector"
LERP = "materialexpressionlinearinterpolate"


def vector_overrides(tags, report, stream):
    """Return {parameter name: [r, g, b]} from a material instance's VectorParameterValues."""
    tag = typed_tag(tags, "vectorparametervalues", "ArrayProperty")
    if tag is None:
        return {}
    cursor = tag_data(stream, tag)
    names = report["names"]
    overrides = {}
    for _ in range(cursor.count(64)):
        name, value = None, None
        for _ in range(64):
            member = property_tag(cursor, names, True)
            if member is None:
                break
            key = member["name"].casefold()
            data = tag_data(stream, member)
            if key == "parametername" and member["kind"] == "NameProperty":
                name = table_name(data, names, True).casefold()
            elif key == "parametervalue" and member["kind"] == "StructProperty" and member["size"] == 16:
                value = list(data.fields("4f")[:3])
            cursor.offset += member["size"]
        else:
            raise ValueError("Oversized Ascend vector parameter entry.")
        if name and value is not None:
            overrides.setdefault(name, value)
    return overrides


def input_channel(fields):
    """The single texture channel (0-3) an input reads, or None when it reads the colour."""
    if not fields.get("mask"):
        return None
    channels = [index for index, key in enumerate(MASK_CHANNELS) if fields.get(key)]
    return channels[0] if len(channels) == 1 else None


def expression_inputs(tags, report, stream):
    """(expression, channel) pairs feeding a node, primary inputs first (a lerp's A before B; never
    its alpha). The channel is set when the input reads one channel of its source."""
    inputs = []
    for tag in tags:
        if tag["kind"] == "StructProperty" and (tag["structure"] or "").casefold() == "expressioninput":
            if tag["name"].casefold() in ("alpha", "coordinates", "height", "exponent"):
                continue
            fields = material_input(tag, report, stream)
            if fields.get("expression", 0):
                inputs.append((fields["expression"], input_channel(fields)))
    return inputs


def colour_value(expression, tags, report, stream, vectors):
    """The RGB a vector parameter or constant colour node gives, after instance overrides."""
    if expression["class_name"].casefold() == VECTOR_PARAMETER:
        name_tag = typed_tag(tags, "parametername", "NameProperty")
        name = table_name(tag_data(stream, name_tag), report["names"], True).casefold() if name_tag else ""
        if name in vectors:
            return vectors[name]
        default = typed_tag(tags, "defaultvalue", "StructProperty")
        # UE3 saves only non-default values; an unsaved DefaultValue is black (0, 0, 0).
        return list(tag_data(stream, default).fields("4f")[:3]) if default and default["size"] == 16 else [0.0, 0.0, 0.0]
    values = {}
    for channel in ("r", "g", "b"):
        tag = typed_tag(tags, channel, "FloatProperty")
        values[channel] = tag_data(stream, tag).fields("f")[0] if tag else 0.0
    return [values["r"], values["g"], values["b"]]


WATER_EXCLUDED = ("rushing", "fall", "plant", "lily", "splash", "droplet", "drip")
WATER_PARAMETERS = {"slime color": "tint", "fill noise a": "fill_a", "fill noise b": "fill_b", "tiling": "tiling"}


def water_surface(asset, owner, stream, objects, open_art):
    """TA standing-water parameters for a material named as water, or None.

    Tartarus's swamp (SPL_Water_Unlit_A and its instances) is an unlit, see-through surface coloured by a
    "slime color", two "fill noise" colours mixed through a silt texture, and a "Tiling" repeat. Rushing
    water, waterfalls and water plants are other effects and are left to the self-lit path.
    """
    vectors, names = {}, []
    for _ in range(16):
        material, owner, stream, objects = open_art(asset, owner, stream, objects)
        names.append(material.get("object_path", "").casefold())
        tags, _ = properties(material, owner, stream, objects)
        if material["class_name"].casefold() == "material":
            break
        for name, value in vector_overrides(tags, owner, stream).items():
            vectors.setdefault(name, value)
        parent = object_reference(tags, stream, "parent")
        if not parent or parent not in objects:
            return None
        asset = objects[parent]
    else:
        return None
    if not any("water" in name.rsplit(".", 1)[-1] or ".water." in name for name in names) or any(
            word in name.rsplit(".", 1)[-1] for name in names for word in WATER_EXCLUDED):
        return None
    found = {}
    for row in owner["exports"]:
        if row.get("outer") != material["index"] or row["class_name"].casefold() != VECTOR_PARAMETER:
            continue
        try:
            node_tags, _ = properties(row, owner, stream, objects)
            name_tag = typed_tag(node_tags, "parametername", "NameProperty")
            name = table_name(tag_data(stream, name_tag), owner["names"], True).casefold() if name_tag else ""
            if name in WATER_PARAMETERS:
                found[WATER_PARAMETERS[name]] = colour_value(row, node_tags, owner, stream, vectors)
        except ValueError:
            continue
    if "tint" not in found:
        return None
    clean = lambda values: [round(min(64.0, max(0.0, value)), 4) for value in values]  # noqa: E731
    water = {"tint": clean(found["tint"]), "fill": [clean(found.get("fill_a", [0.2, 0.2, 0.2])),
                                                     clean(found.get("fill_b", [0.2, 0.2, 0.2]))]}
    tiling = found.get("tiling", [1.0, 1.0])[:2]
    water["tiling"] = [round(min(1024.0, max(0.01, value)), 4) for value in tiling]
    return water


def material_emissive(asset, owner, stream, objects, open_art):
    """Return (texture scope, linear tint, channel or None, motion or None) for an Ascend glow, or None (#40).

    Ascend glows are usually an emissive texture multiplied by a colour parameter (above 1 for
    brightness), with masks and edge falloff around them. This takes the first texture and the
    first colour on the emissive path, honouring instance overrides, and leaves the rest out.
    """
    textures, vectors = {}, {}
    for _ in range(16):
        material, owner, stream, objects = open_art(asset, owner, stream, objects)
        tags, _ = properties(material, owner, stream, objects)
        if material["class_name"].casefold() == "material":
            break
        for name, index in texture_overrides(tags, owner, stream).items():
            textures.setdefault(name, (index, owner, stream, objects))
        for name, value in vector_overrides(tags, owner, stream).items():
            vectors.setdefault(name, value)
        parent = object_reference(tags, stream, "parent")
        if not parent or parent not in objects:
            return None
        asset = objects[parent]
    else:
        raise ValueError("Ascend material parent chain exceeds the supported depth.")
    tag = typed_tag(tags, "emissivecolor", "StructProperty", "colormaterialinput")
    fields = material_input(tag, owner, stream) if tag else {}
    queue, seen = [(fields.get("expression", 0), input_channel(fields))], set()
    texture, tint, channel, mask_fallback, motion = None, None, None, None, None
    while queue and len(seen) < GLOW_NODES and (texture is None or tint is None):
        index, reads = queue.pop(0)
        node = objects.get(index)
        if not node or index in seen:
            continue
        seen.add(index)
        kind = node["class_name"].casefold()
        try:
            node_tags, _ = properties(node, owner, stream, objects)
        except ValueError:
            continue  # An unreadable helper node (for example a power or fresnel) is left out.
        if kind in (TEXTURE_SAMPLE, TEXTURE_PARAMETER):
            if texture is None:
                # A glow that reads one channel (often a mask in the diffuse alpha) glows only there.
                # A glow or sky sampled through custom coordinates (a panning sky) falls back to the
                # mesh's own UVs: the art shows, without the motion.
                found, *_ = sample_texture(node, owner, stream, objects, open_art, textures, allow_custom_uv=True)
                found_motion = sample_motion(node, owner, stream, objects, open_art)
                name = found[0].get("object_path", "").rsplit(".", 1)[-1].casefold()
                if name.endswith("_msk") or "mask" in name:
                    # A cloud or opacity mask multiplied into a painted sky: keep looking for the art
                    # (Arx Novena's sky is two _EMI halves x a cloud mask), using the mask only if alone.
                    mask_fallback = mask_fallback or (found, reads, found_motion)
                else:
                    texture, channel, motion = found, reads, found_motion
            continue
        if kind in (VECTOR_PARAMETER, CONSTANT_COLOR):
            if tint is None:
                tint = colour_value(node, node_tags, owner, stream, vectors)
            continue
        inputs = expression_inputs(node_tags, owner, stream)
        if kind == LERP and tint is None and len(inputs) == 2:
            # A blend between two colours (for example core to edge over a fresnel falloff) spans
            # the whole range across a rounded surface, so its average is the fair single colour.
            ends = [objects.get(index) for index, _ in inputs]
            if all(end and end["class_name"].casefold() in (VECTOR_PARAMETER, CONSTANT_COLOR) for end in ends):
                colours = [colour_value(end, properties(end, owner, stream, objects)[0], owner, stream, vectors)
                           for end in ends]
                if all(colours):
                    tint = [(a + b) / 2 for a, b in zip(*colours)]
                    continue
        queue.extend(inputs)
    if texture is None and mask_fallback:
        texture, channel, motion = mask_fallback
    if texture is None:
        return None
    tint = [min(MAX_GLOW_TINT, max(0.0, value)) for value in (tint or [1.0, 1.0, 1.0])]
    if max(tint) < 0.01:
        return None  # A black glow colour: the surface does not glow in the source.
    return texture, tint, channel, motion


def channel_png(png, channel):
    """One channel of a PNG as a grey RGB image: the glow mask an Ascend material reads."""
    from io import BytesIO
    from PIL import Image

    image = Image.open(BytesIO(png)).convert("RGBA").getchannel(channel)
    buffer = BytesIO()
    # Model textures are 8-bit RGBA (model_textures.validate_png), so the mask keeps a full alpha.
    opaque = Image.new("L", image.size, 255)
    Image.merge("RGBA", (image, image, image, opaque)).save(buffer, "PNG")
    return buffer.getvalue()


def with_alpha(color_png, mask_png, channel):
    """Put one channel of the mask texture into the colour texture's alpha, at the colour's size."""
    from io import BytesIO
    from PIL import Image

    color = Image.open(BytesIO(color_png)).convert("RGBA")
    mask = Image.open(BytesIO(mask_png)).convert("RGBA").getchannel(channel)
    if mask.size != color.size:
        mask = mask.resize(color.size, Image.BILINEAR)
    color.putalpha(mask)
    buffer = BytesIO()
    color.save(buffer, "PNG")
    return buffer.getvalue()


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
    cache = cache_name(tags, report, stream)
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
        if payload[0] == "cache" and not cache:
            continue
        expected = ((width + stride - 1) // stride) * ((height + stride - 1) // stride) * block_size
        if payload[4] != expected:
            raise ValueError("Ascend texture mip dimensions disagree with its byte count.")
        if best is None or width * height > best[0] * best[1]:
            best = width, height, payload
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
    width, height, payload = best
    receipts = ()
    try:
        pixels = mip_bytes(payload, stream, rtc, cache)
    except (OSError, ValueError, zlib.error) as error:
        raise ValueError(f"Ascend texture mip could not be read. {error}") from error
    if payload[0] == "cache":
        receipts = (rtc.cache_receipt(cache),)
    data = texture_png(kind, width, height, pixels)
    fallback = cache_issue or ("Larger mips were unavailable." if width * height < largest else None)
    return data, [width, height], fallback, receipts


def resolve_material(asset, report, stream, objects, companions, _flags, *, rtc, remaster=None):
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

        unlit, unlit_channel, unlit_motion, water = None, None, None, None
        try:
            texture, settings, approximate = material_texture(asset, report, stream, objects, open_art)
        except ValueError as error:
            # Self-lit Ascend surfaces (sky domes, skyline backdrops) have no base colour, only an
            # emissive texture. That texture becomes the surface, drawn unlit at its own brightness.
            if not any(text in str(error) for text in ("no diffuse input", "not a direct RGB texture sample",
                                                       "custom coordinates")):
                raise
            try:
                emissive = material_emissive(asset, report, stream, objects, open_art)
            except ValueError:
                emissive = None
            if not emissive:
                raise error
            texture, settings, approximate = emissive[0], {}, None
            unlit = emissive[1]
            # An effect drawn from one channel of a mask (Tartarus's waterfall: T_RainDance_Waterfalls_MSK
            # x a green-grey tint) becomes that channel as grey art; drawn additive it glows where the
            # mask is bright and is see-through elsewhere. Its scrolling motion is left out for now.
            unlit_channel = emissive[2]
            unlit_motion = emissive[3]
            try:
                water = water_surface(asset, report, stream, objects, open_art)
            except ValueError:
                water = None
        image, source, pixels, exports = open_art(*texture)
        data, size, fallback, cache_receipts = texture_mip(image, source, pixels, exports, rtc)
        if unlit and unlit_channel is not None:
            data = channel_png(data, unlit_channel)
        mask_png, blend_issue = None, None
        try:
            blend, two_sided, mask = material_blend(asset, report, stream, objects, open_art)
        except ValueError as error:
            # Keep the base colour; an unreadable mask leaves the material opaque for review.
            blend, two_sided, mask, blend_issue = "BLEND_Opaque", False, None, str(error)
        # Painted sky domes go through the AI; additive star fields and nebulae get a plain Lanczos 2x,
        # because the AI smears point stars into soft blobs (Katabatic's star cards).
        role = ("glow" if blend in ("BLEND_Additive", "BLEND_Translucent") else "sky") if unlit else "base"
        remastered, note = remaster.load(image["object_path"], size, data, role) if remaster else (None, None)
        if mask:
            mask_image, mask_source, mask_pixels, mask_exports = open_art(*mask[0])
            mask_png, _, _, mask_receipts = texture_mip(mask_image, mask_source, mask_pixels, mask_exports, rtc)
            for receipt in mask_receipts:
                receipts[receipt["name"]] = receipt
        glow, glow_issue = None, None
        try:
            emissive = None if unlit else material_emissive(asset, report, stream, objects, open_art)
            if emissive:
                glow_image, glow_owner, glow_pixels, glow_exports = open_art(*emissive[0])
                glow_png, glow_size, _, glow_receipts = texture_mip(glow_image, glow_owner, glow_pixels,
                                                                    glow_exports, rtc)
                for receipt in glow_receipts:
                    receipts[receipt["name"]] = receipt
                glow_source = glow_receipts[-1] if glow_receipts else glow_owner["source"]
                if emissive[2] is not None:
                    glow_png = channel_png(glow_png, emissive[2])
                if remaster:
                    better, glow_note = remaster.load(glow_image["object_path"], glow_size, glow_png, "glow")
                    if better:
                        glow_png, glow_size, glow_source = better
                        receipts[glow_source["name"]] = glow_source
                glow = (glow_png, glow_size, glow_source, emissive[1])
        except ValueError as error:
            glow_issue = f"Glow left out: {error}"
    masked = mask_png is not None
    if unlit and not masked and blend in ("BLEND_Translucent", "BLEND_Additive", "BLEND_Masked"):
        # A see-through self-lit backdrop (Arx Novena's painted city skyline) is cut out by its own alpha.
        from io import BytesIO
        from PIL import Image

        alpha = Image.open(BytesIO(data)).convert("RGBA").getchannel("A").getextrema()
        masked = alpha[0] < 128 <= alpha[1]
    if masked:
        warning = "Base color and source opacity mask imported (masked material)."
    else:
        warning = "Base color imported as opaque. Other Ascend shader inputs require review."
        if blend in ("BLEND_Additive", "BLEND_Translucent", "BLEND_Modulate"):
            warning += f" Source blend {blend.removeprefix('BLEND_').lower()} is not converted yet."
        if blend_issue:
            warning += f" {blend_issue}"
    if approximate:
        warning = f"{warning} {approximate}"
    if remastered:
        data, size, image_source = remastered
        receipts[image_source["name"]] = image_source
        warning = f"{warning} Remastered texture."
    elif note:
        warning = f"{warning} {note}"
    if fallback and not remastered:
        warning = f"{warning} {fallback}"
    if glow:
        warning = f"Glow imported as texture x colour. {warning}"
    if unlit:
        warning = f"Self-lit surface (sky or backdrop) imported unlit from its emissive texture. {warning}"
    elif glow_issue:
        warning = f"{warning} {glow_issue}"
    warning = warning[:240]
    for receipt in cache_receipts:
        receipts[receipt["name"]] = receipt
    if not remastered:
        image_source = cache_receipts[-1] if cache_receipts else source["source"]
    if mask_png is not None:
        data = with_alpha(data, mask_png, mask[1])
    name = "texture_" + sha256(data).hexdigest()[:32] + ".png"
    binding = dict(file=name, source=image_source["name"], source_sha256=image_source["sha256"],
                   size=size, mode="masked" if masked else "opaque", two_sided=two_sided,
                   warning=warning)
    if settings:
        binding["ascend_base"] = settings
    files = {name: data}
    if unlit:
        binding["ascend_glow"] = dict(color=dict(file=name, source=image_source["name"],
                                                 source_sha256=image_source["sha256"], size=size),
                                      tint=[round(min(MAX_GLOW_TINT, max(0.0, value)), 4) for value in unlit])
        if unlit_motion:
            # The waterfall's streaks repeat and scroll as in the source.
            binding["ascend_glow"].update(unlit_motion)
        if water:
            # Standing water (Tartarus's swamp): a see-through surface in TA's colours over a water volume.
            binding["ascend_water"] = water
            binding.pop("ascend_additive", None)
        binding["ascend_unlit"] = True
        if not masked and blend == "BLEND_Translucent" and unlit_motion:
            # A scrolling translucent effect (Tartarus's waterfall): solid where its mask is bright and
            # see-through elsewhere. Drawn additive with the star cards' dimming it all but vanished.
            binding["ascend_effect"] = True
        elif not masked and blend in ("BLEND_Additive", "BLEND_Translucent"):
            # Star fields, nebulae and light shafts: black adds nothing, bright parts glow. Drawn opaque
            # they became dark panels in Katabatic's sky.
            binding["ascend_additive"] = True
    if glow:
        glow_png, glow_size, glow_source, tint = glow
        glow_name = "texture_" + sha256(glow_png).hexdigest()[:32] + ".png"
        files[glow_name] = glow_png
        binding["ascend_glow"] = dict(color=dict(file=glow_name, source=glow_source["name"],
                                                 source_sha256=glow_source["sha256"], size=glow_size),
                                      tint=[round(value, 4) for value in tint])
    return binding, files, receipts.values()
