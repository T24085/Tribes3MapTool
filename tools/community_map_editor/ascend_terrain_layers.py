"""Recover UE3 terrain layer order, painted weights, and supported color inputs."""

from hashlib import sha256
import math

from ascend_materials import local_object, object_reference, properties, resolve_material, tag_data, typed_tag
from minimap import grayscale_png
from native_terrain_paint import MAX_MASK_SAMPLES, MAX_PAINT_BYTES, MAX_PAINT_LAYERS
from terrain_art import MAX_ART_BYTES
from unreal_binary_scene import MAX_OBJECT_PROPERTIES, property_tag
from unreal_package_reader import Cursor
from vengeance_terrain_layers import TerrainPaint


def records(value, names, maximum):
    rows = []
    for _ in range(value.count(maximum)):
        row = []
        for _ in range(MAX_OBJECT_PROPERTIES):
            tag = property_tag(value, names, True)
            if tag is None:
                break
            row.append(tag)
            value.offset += tag["size"]
        else:
            raise ValueError("Too many properties in an Ascend terrain record.")
        rows.append(row)
    if value.offset != value.limit:
        raise ValueError("Unexpected data after Ascend terrain records.")
    return rows


def scalar(tags, stream, key, kind, form, default):
    tag = typed_tag(tags, key, kind)
    if tag is None:
        return default
    value = tag_data(stream, tag)
    if kind == "BoolProperty":
        result = bool(tag["boolean"])
    elif kind == "StrProperty":
        result = value.string(True)
    else:
        result, = value.fields(form)
    if value.offset != value.limit or isinstance(result, float) and not math.isfinite(result):
        raise ValueError(f"Invalid Ascend terrain property {key}.")
    return result


def alpha_maps(node, stream, samples):
    if samples > MAX_MASK_SAMPLES:
        raise ValueError("Ascend terrain paint exceeds the intake resolution limit.")
    start = node["property_end"] + 8 + 3 * samples  # Past the validated height and patch-flag arrays.
    value = Cursor(stream, start, node["serial_offset"] + node["serial_bytes"])
    masks, total = [], 0
    for _ in range(value.count(MAX_PAINT_LAYERS)):
        size = value.count(MAX_MASK_SAMPLES)
        total += size
        if size not in (0, samples) or total > MAX_PAINT_BYTES:
            raise ValueError("Ascend alpha arrays do not match the terrain grid or paint size limit.")
        masks.append(value.take(size))
    return masks


def material_inputs(setup, report, stream, objects, companions, rtc, entry):
    if not setup or setup["class_name"].casefold() != "terrainlayersetup":
        raise ValueError("The layer has no usable Ascend TerrainLayerSetup.")
    receipts = {}
    with local_object(setup, report, stream, objects, companions) as (asset, owner, payload, entries):
        tags, _ = properties(asset, owner, payload, entries)
        material_list = typed_tag(tags, "materials", "ArrayProperty")
        if not material_list:
            raise ValueError("The Ascend terrain setup has no serialized material list.")
        rows = records(tag_data(payload, material_list), owner["names"], MAX_PAINT_LAYERS)
        entry["setup_material_count"] = len(rows)
        if len(rows) != 1:
            raise ValueError("The Ascend terrain setup needs a single material for automatic color import.")
        material_fields, = rows
        filters = [key for key in ("minheight", "maxheight", "minslope", "maxslope")
                   if (tag := typed_tag(material_fields, key, "StructProperty")) and
                   enabled_filter(tag, owner, payload)]
        noise = scalar(material_fields, payload, "usenoise", "BoolProperty", None, False)
        strength = scalar(material_fields, payload, "alpha", "FloatProperty", "f", 1.0)
        entry.update(material_filters=filters, material_noise=noise, material_alpha=strength)
        material = entries.get(object_reference(material_fields, payload, "material"))
        if not material or material["class_name"].casefold() != "terrainmaterial":
            raise ValueError("The Ascend terrain setup has no usable TerrainMaterial.")
        receipts[owner["source"]["name"]] = owner["source"]
        with local_object(material, owner, payload, entries, companions) as (asset, owner, payload, entries):
            tags, _ = properties(asset, owner, payload, entries)
            shader = entries.get(object_reference(tags, payload, "material"))
            if not shader:
                raise ValueError("The Ascend TerrainMaterial has no serialized shader.")
            entry["shader"] = shader["object_path"]
            entry["mapping"] = {key: value for key in ("mappingscale", "mappingrotation", "mappingpanv", "mappingpanu")
                                if (value := scalar(tags, payload, key, "FloatProperty", "f", None)) is not None}
            matrix = typed_tag(tags, "localtomapping", "StructProperty", "matrix")
            if matrix:
                if matrix["size"] != 64:
                    raise ValueError("Unexpected Ascend terrain mapping matrix size.")
                elements = tag_data(payload, matrix).fields("16f")
                if not all(math.isfinite(value) for value in elements):
                    raise ValueError("Non-finite Ascend terrain mapping matrix.")
                entry["mapping"]["local_to_mapping_serialized"] = elements
            if filters or noise or strength != 1:
                raise ValueError("Procedural Ascend terrain material filters need a destination material assignment.")
            if rtc is None:
                raise ValueError("Source artwork resolution requires the map intake workflow.")
            binding, images, artwork_sources = resolve_material(shader, owner, payload, entries, companions, 0, rtc=rtc)
            binding.pop("ascend_glow", None)  # Terrain layers carry base colour only.
            receipts[owner["source"]["name"]] = owner["source"]
            receipts.update((receipt["name"], receipt) for receipt in artwork_sources)
    receipts.pop(report["source"]["name"], None)
    return binding, images[binding["file"]], receipts.values()


def enabled_filter(tag, report, stream):
    value = tag_data(stream, tag)
    for _ in range(MAX_OBJECT_PROPERTIES):
        member = property_tag(value, report["names"], True)
        if member is None:
            if value.offset == value.limit:
                return False
            break
        if member["name"].casefold() == "enabled":
            return scalar([member], stream, "enabled", "BoolProperty", None, False)
        value.offset += member["size"]
    raise ValueError("Unrecognized Ascend terrain material filter.")


def read_ascend_paint(node, report, stream, companions, size, rtc):
    result = TerrainPaint()
    width, height = size
    samples = width * height
    try:
        tag = typed_tag(node["tags"], "layers", "ArrayProperty")
        if tag is None:
            return result
        if report["summary"]["version"] != 805 or report["summary"]["endian"] != "<":
            raise ValueError("Unsupported Ascend terrain paint package layout.")
        rows = records(tag_data(stream, tag), report["names"], MAX_PAINT_LAYERS)
        if not rows:
            return result
        masks = alpha_maps(node, stream, samples)
    except ValueError as error:
        result.review.append(dict(slot=0, reason=str(error)))
        return result
    objects = {item["index"]: item for item in report["imports"] + report["exports"]}
    companions = companions or {}
    art_bytes = 0
    for slot, row in enumerate(rows):
        try:
            setup = objects.get(object_reference(row, stream, "setup"))
            name = scalar(row, stream, "name", "StrProperty", None, "")
            surface = name or (setup["object_path"] if setup else f"Layer {slot + 1}")
            if not 1 <= len(surface) <= 255 or not surface.isprintable():
                raise ValueError("Invalid Ascend terrain layer name.")
            index = scalar(row, stream, "alphamapindex", "IntProperty", "i", -1)
            hidden = scalar(row, stream, "hidden", "BoolProperty", None, False)
            if not -1 <= index < len(masks):
                raise ValueError("The Ascend terrain layer points outside its alpha arrays.")
            alpha = (masks[index] if index >= 0 else b"") or bytes(samples)
            if (len(result.layers) + 1) * samples > MAX_PAINT_BYTES:
                raise ValueError("Expanded Ascend terrain paint exceeds the intake limit.")
            filename = f"terrain-alpha-{slot:02d}.png"
            entry = dict(slot=slot, surface=surface, dimensions=[width, height], filename=filename,
                         alpha_sha256=sha256(alpha).hexdigest(), alpha_map_index=index, hidden=hidden)
            if setup:
                entry["setup"] = setup["object_path"]
            result.files[filename] = grayscale_png(alpha, 8, width, height)
            result.masks[str(slot)] = filename
            result.layers.append(entry)
        except ValueError as error:
            result.review.append(dict(slot=slot, reason=str(error)))
            continue
        if hidden:
            continue
        try:
            binding, image, receipts = material_inputs(setup, report, stream, objects, companions, rtc, entry)
            sources = {receipt["name"]: {key: receipt[key] for key in ("name", "sha256")} for receipt in receipts}
            if any(result.packages.get(name, receipt) != receipt for name, receipt in sources.items()):
                raise ValueError("An Ascend terrain artwork package changed during import.")
            filename = f"terrain-art-{sha256(image).hexdigest()[:32]}.png"
            if filename not in result.files:
                if len(image) > 16 * 1024 * 1024 or art_bytes + len(image) > MAX_ART_BYTES:
                    raise ValueError("Source terrain artwork exceeds the intake limit.")
                result.files[filename] = image
                art_bytes += len(image)
            result.artwork[str(slot)] = dict(file=filename, source=binding["source"],
                                             source_sha256=binding["source_sha256"])
            result.packages.update(sources)
            reason = f"Source color uses the project's texture repeat. {binding['warning']}"
            result.art_review.append(dict(slot=slot, reason=reason[:240]))
        except (ValueError, OSError, ImportError) as error:
            reason = str(error)[:240]
            result.artwork[str(slot)] = dict(reason=reason)
            result.art_review.append(dict(slot=slot, reason=reason))
    return result
