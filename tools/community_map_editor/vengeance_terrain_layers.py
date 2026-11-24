"""Read Vengeance terrain paint and its source layer assignments."""

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import math

from minimap import grayscale_png
from native_terrain_paint import MAX_MASK_SAMPLES, MAX_PAINT_BYTES, MAX_PAINT_LAYERS
from terrain_grid import CELLS
from terrain_art import MAX_ART_BYTES
from unreal_binary_scene import property_tag
from unreal_package_reader import Cursor
from vengeance_terrain import RGBA8_TEXTURE, external_texture, read_texture_mip


CACHED_PLANES = ("xplane", "yplane", "zplane", "wplane")
COLOR_FORMATS = RGBA8_TEXTURE | {3: (4, 8), 7: (4, 16), 8: (4, 16)}
DXT_DECODERS = {3: (1, "DXT1"), 7: (2, "DXT3"), 8: (3, "DXT5")}
SCALAR_FIELDS = {"uscale", "vscale", "upan", "vpan", "texturerotation", "kfriction", "krestitution"}


@dataclass
class TerrainPaint:
    layers: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    masks: dict = field(default_factory=dict)
    review: list = field(default_factory=list)
    art_review: list = field(default_factory=list)
    packages: dict = field(default_factory=dict)
    artwork: dict = field(default_factory=dict)
    projection: dict = field(default_factory=dict)


def matrix_planes(value, names):
    planes = {}
    while tag := property_tag(value, names, False):
        field = tag["name"].casefold()
        end = value.offset + tag["size"]
        if field not in CACHED_PLANES or (tag["structure"] or "").casefold() != "plane" or field in planes:
            raise ValueError("Unsupported Vengeance terrain matrix.")
        cursor = Cursor(value.stream, value.offset, end, value.endian)
        entries = {}
        while member := property_tag(cursor, names, False):
            axis = member["name"].casefold()
            if (axis not in ("x", "y", "z", "w") or axis in entries
                    or member["kind"] != "FloatProperty" or member["size"] != 4):
                raise ValueError("Unsupported Vengeance matrix plane.")
            entries[axis], = cursor.fields("f")
        if cursor.offset != end or set(entries) != set("xyzw") or not all(math.isfinite(n) for n in entries.values()):
            raise ValueError("Invalid Vengeance terrain matrix plane.")
        planes[field] = entries
        value.offset = end
    if value.offset != value.limit or set(planes) != set(CACHED_PLANES):
        raise ValueError("Incomplete Vengeance terrain matrix.")
    return [[planes[row][axis] for row in CACHED_PLANES] for axis in "xy"]


def read_layer(layer, report, stream, objects):
    if layer["kind"] != "StructProperty" or (layer["structure"] or "").casefold() != "terrainlayer":
        raise ValueError("Unsupported Vengeance terrain layer record.")
    start = layer["value_offset"]
    cursor = Cursor(stream, start, start + layer["size"], report["summary"]["endian"])
    values = {}
    while tag := property_tag(cursor, report["names"], False):
        name = tag["name"].casefold()
        if name in values:
            raise ValueError("Duplicate Vengeance terrain layer property.")
        end = cursor.offset + tag["size"]
        value = Cursor(stream, cursor.offset, end, cursor.endian)
        if name in {"texture", "alphamap"} and tag["kind"] == "ObjectProperty":
            values[name] = objects.get(value.index(False))
        elif name in SCALAR_FIELDS and tag["kind"] == "FloatProperty":
            scalar, = value.fields("f")
            if not math.isfinite(scalar):
                raise ValueError("Vengeance terrain layer has a non-finite parameter.")
            values[name] = scalar
        elif name == "texturemapaxis" and tag["kind"] == "ByteProperty":
            values[name], = value.fields("B")
        elif name == "layerrotation" and (tag["structure"] or "").casefold() == "rotator":
            values[name] = value.fields("3i")
        elif name == "terrainmatrix" and (tag["structure"] or "").casefold() == "matrix":
            try:
                values[name] = matrix_planes(value, report["names"])
            except ValueError:
                values[name] = None
                value.offset = end
        else:
            cursor.offset = end
            continue
        if tag["slot"] or value.offset != end:
            raise ValueError("Invalid Vengeance terrain layer property size or slot.")
        cursor.offset = end
    if cursor.offset != cursor.limit:
        raise ValueError("Unexpected data after the Vengeance terrain layer.")
    return values


def texture_image(texture, report, stream, objects, companions, formats):
    if texture["class_name"].casefold() != "texture":
        raise ValueError(f"{texture['object_path']} needs a destination material assignment.")
    if texture["index"] < 0:
        return external_texture(texture, objects, companions, MAX_MASK_SAMPLES, formats)
    return (*read_texture_mip(texture, report, stream, objects, MAX_MASK_SAMPLES, formats), None)


def texture_png(kind, width, height, pixels):
    from PIL import Image

    args = ("bcn", DXT_DECODERS[kind]) if kind in DXT_DECODERS else ("raw", "BGRA")
    try:
        image = Image.frombytes("RGBA", (width, height), pixels, *args)
    except (ValueError, OSError) as error:
        raise ValueError("The source surface texture could not be decoded.") from error
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def read_terrain_paint(node, report, stream, companions, units_per_m):
    objects = {item["index"]: item for item in report["imports"] + report["exports"]}
    by_slot = defaultdict(list)
    for tag in node["tags"]:
        if tag["name"].casefold() == "layers":
            by_slot[tag["slot"]].append(tag)
    result = TerrainPaint()
    samples = art_bytes = 0
    images = {}
    for slot, layers in sorted(by_slot.items()):
        try:
            if not 0 <= slot < MAX_PAINT_LAYERS or len(layers) != 1:
                raise ValueError("Invalid or duplicate Vengeance terrain layer slot.")
            values = read_layer(layers[0], report, stream, objects)
            material, texture = values.pop("texture", None), values.pop("alphamap", None)
            if not material:
                raise ValueError("Terrain layer has no serialized surface material.")
            surface = material["object_path"]
            if not surface.isprintable() or not 1 <= len(surface) <= 255:
                raise ValueError("Terrain layer has an invalid material path.")
            if not texture or texture["class_name"].casefold() != "texture":
                raise ValueError("Terrain layer has no serialized alpha texture.")
            width, height, pixels, _, package = texture_image(texture, report, stream, objects, companions, RGBA8_TEXTURE)
            if max(width, height) > 64 * CELLS:
                raise ValueError("Source terrain paint dimensions exceed the intake limit.")
            alpha = pixels[3::4]
            if samples + len(alpha) > MAX_PAINT_BYTES:
                raise ValueError("Source terrain paint exceeds the intake limit.")
            samples += len(alpha)
            filename = f"terrain-alpha-{slot:02d}.png"
            result.files[filename] = grayscale_png(alpha, 8, width, height)
            slot_name = str(slot)
            result.masks[slot_name] = filename
            entry = dict(slot=slot, surface=surface, texture=texture["object_path"], dimensions=[width, height],
                         alpha_sha256=sha256(alpha).hexdigest(), filename=filename, parameters=values)
            if package:
                entry["package"] = package
                result.packages[package["name"]] = package
            result.layers.append(entry)
        except ValueError as error:
            result.review.append(dict(slot=slot, reason=str(error)))
            continue
        try:
            if material["index"] not in images:
                width, height, pixels, kind, package = texture_image(
                    material, report, stream, objects, companions, COLOR_FORMATS)
                data = texture_png(kind, width, height, pixels)
                filename = f"terrain-art-{sha256(data).hexdigest()[:32]}.png"
                if filename not in result.files:
                    if len(data) > 16 * 1024 * 1024 or art_bytes + len(data) > MAX_ART_BYTES:
                        raise ValueError("Source terrain artwork exceeds the intake limit.")
                    result.files[filename] = data
                    art_bytes += len(data)
                receipt = package or report["source"]
                if package:
                    result.packages[package["name"]] = package
                images[material["index"]] = dict(file=filename, source=receipt["name"], source_sha256=receipt["sha256"])
            result.artwork[slot_name] = images[material["index"]]
            matrix = values.get("terrainmatrix")
            projection = [[*(v * units_per_m / 100 for v in row[:3]), row[3]] for row in matrix] if matrix else []
            if projection and all(abs(value) <= 1e6 for plane in projection for value in plane):
                result.projection[slot_name] = projection
                entry["projection"] = projection
            else:
                result.art_review.append(dict(
                    slot=slot, reason="Source artwork uses the project texture repeat because the cached projection is unavailable."))
        except (ValueError, OSError, ImportError) as error:
            reason = str(error)[:240]
            result.artwork[slot_name] = {"reason": reason}
            result.art_review.append(dict(slot=slot, reason=reason))
    return result
