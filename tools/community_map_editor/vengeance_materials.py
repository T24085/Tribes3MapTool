"""Resolve Vengeance BSP and mesh color textures and supported blend modes."""

from contextlib import nullcontext
from hashlib import sha256
from math import cos, isfinite, sin, tau

from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor
from vengeance_bsp import MASKED, MODULATED, TRANSLUCENT
from vengeance_normal_maps import NORMAL_FORMATS, normal_png
from vengeance_terrain import external_object
from vengeance_terrain_layers import COLOR_FORMATS, texture_image, texture_png


MATERIAL_REFS = {
    "diffuse", "opacity", "material", "detail", "normalmap", "specular", "specularitymask", "selfillumination",
    "selfilluminationmask", "glowmapoverride", "glowmaskoverride", "perpixelreflection", "perpixelreflectionmask",
}
MATERIAL_FLAGS = {"bmasked", "btwosided", "twosided", "wireframe", "texcoordprojected"}
MATERIAL_BYTES = {"outputblending", "glowbrightness", "texcoordcount", "texcoordsource"}


def material_properties(asset, report, stream, objects):
    tags, _, issues, _ = read_properties(stream, report["summary"], asset, report["names"], objects)
    if issues:
        raise ValueError("Source material has invalid tagged properties.")
    values, effects = {}, []
    for tag in tags:
        name = tag["name"].casefold()
        if name not in MATERIAL_REFS | MATERIAL_FLAGS | MATERIAL_BYTES | {"panrate", "pandirection"}:
            continue
        if name in values or tag["slot"]:
            raise ValueError("Source material repeats a property or array slot.")
        start = tag["value_offset"]
        value = Cursor(stream, start, start + tag["size"])
        if name in MATERIAL_REFS and tag["kind"] == "ObjectProperty":
            index = value.index(False)
            if index and index not in objects:
                raise ValueError("Source material references an unknown object.")
            values[name] = index
            if index and name not in ("diffuse", "opacity", "material"):
                effects.append(tag["name"])
        elif name in MATERIAL_FLAGS and tag["kind"] == "BoolProperty":
            values[name] = bool(tag["boolean"])
        elif name in MATERIAL_BYTES and tag["kind"] == "ByteProperty":
            values[name], = value.fields("B")
        elif name == "panrate" and tag["kind"] == "FloatProperty":
            values[name], = value.fields("f")
        elif name == "pandirection" and (tag["kind"] == "RotatorProperty"
                or tag["kind"] == "StructProperty" and str(tag["structure"]).casefold() == "rotator"):
            values[name] = value.fields("3i")
        else:
            raise ValueError("Source material has an unsupported property type.")
        if value.offset != value.limit:
            raise ValueError("Source material has an invalid property size.")
    return values, effects


def texture_record(asset, report, stream, objects, companions, *, normal=False):
    formats, encode = (NORMAL_FORMATS, normal_png) if normal else (COLOR_FORMATS, texture_png)
    width, height, pixels, pixel_format, companion = texture_image(asset, report, stream, objects, companions, formats)
    source = companion or report["source"]
    data = encode(pixel_format, width, height, pixels)
    record = dict(file=f"texture_{sha256(data).hexdigest()[:32]}.png", size=[width, height],
                  source=source["name"], source_sha256=source["sha256"])
    return record, data, companion


def material_owner(asset, report, stream, objects, companions):
    if asset["index"] < 0:
        return external_object(asset, objects, companions)
    return nullcontext((asset, report, stream, objects))


def package_receipt(report):
    return {key: report["source"][key] for key in ("name", "sha256")}


def opacity_texture(asset, report, stream, objects, companions):
    receipts = []
    with material_owner(asset, report, stream, objects, companions) as (local, source, payload, entries):
        if asset["index"] < 0:
            receipts.append(package_receipt(source))
        pan = None
        if local["class_name"].casefold() == "texpanner":
            values, _ = material_properties(local, source, payload, entries)
            pitch, yaw, roll = values.get("pandirection", (0, 0, 0))
            rate = values.get("panrate", 0.1)
            if (not values.get("material") or values.get("texcoordsource", 13) not in (0, 13)
                    or values.get("texcoordcount", 0) or values.get("texcoordprojected")
                    or pitch % 65536 or roll % 65536 or not isfinite(rate) or abs(rate) > 1024):
                raise ValueError("TexPanner needs a direct texture and a bounded two-dimensional UV0 motion.")
            angle = (yaw % 65536) * tau / 65536
            pan = [round(rate * cos(angle), 12), round(rate * sin(angle), 12)]
            local = entries[values["material"]]
        if local["class_name"].casefold() != "texture":
            raise ValueError(f"Opacity input {local['class_name']} needs a destination material assignment.")
        record, data, companion = texture_record(local, source, payload, entries, companions)
        if companion:
            receipts.append(companion)
        if pan:
            record["pan"] = pan
    return record, data, receipts


def resolve_material(asset, report, stream, objects, companions, flags):
    if flags & (TRANSLUCENT | MODULATED):
        raise ValueError("Translucent or modulated BSP surfaces need a destination material assignment.")
    with material_owner(asset, report, stream, objects, companions) as (local, source_report, payload, source_objects):
        kind = local["class_name"].casefold()
        if kind not in ("texture", "shader"):
            raise ValueError(f"{local['class_name']} needs a destination material assignment.")
        values, effects = material_properties(local, source_report, payload, source_objects)
        masked = bool(flags & MASKED or values.get("bmasked"))
        receipts = [package_receipt(source_report)] if asset["index"] < 0 else []
        diffuse = opacity = output = 0
        if kind == "shader":
            output = values.get("outputblending", 0)
            diffuse = values.get("diffuse")
            opacity = values.get("opacity", 0)
            if output not in (0, 1, 3) or values.get("wireframe"):
                raise ValueError("Source Shader uses an unsupported blend or wireframe mode.")
            if not diffuse:
                raise ValueError("Source Shader has no diffuse texture.")
            local = source_objects[diffuse]
            masked |= output == 1
        binding, data, companion = texture_record(local, source_report, payload, source_objects, companions)
        if companion:
            receipts.append(companion)
        mode = "masked" if masked else "translucent" if opacity or output == 3 else "opaque"
        binding.update(mode=mode, two_sided=bool(values.get("twosided") or values.get("btwosided")))
        images = {binding["file"]: data}
        if opacity and opacity != diffuse:
            alpha, data, companions_used = opacity_texture(source_objects[opacity], source_report, payload,
                                                            source_objects, companions)
            binding["opacity"] = alpha
            images[alpha["file"]] = data
            receipts.extend(companions_used)
        if kind == "shader" and values.get("glowmapoverride") and values.get("glowmaskoverride"):
            try:
                glow = {}
                glow_images, glow_receipts = {}, []
                for channel, field in (("color", "glowmapoverride"), ("mask", "glowmaskoverride")):
                    ref = source_objects[values[field]]
                    if field == "glowmaskoverride" and values[field] == values["glowmapoverride"]:
                        glow[channel] = glow["color"]
                        continue
                    record, data, companion = texture_record(ref, source_report, payload, source_objects, companions)
                    glow[channel] = record
                    glow_images[record["file"]] = data
                    if companion:
                        glow_receipts.append(companion)
            except (ValueError, OSError, ImportError) as error:
                effects.append(f"Glow override ({error})")
            else:
                binding["glow"] = dict(glow, brightness=values.get("glowbrightness", 127))
                images.update(glow_images)
                receipts.extend(glow_receipts)
                effects = [name for name in effects if name.casefold() not in {"glowmapoverride", "glowmaskoverride"}]
        if kind == "shader":
            for field, channel in (("normalmap", "normal"), ("specularitymask", "specular_mask")):
                if not values.get(field):
                    continue
                try:
                    record, data, companion = texture_record(source_objects[values[field]], source_report, payload,
                                                              source_objects, companions, normal=channel == "normal")
                except (ValueError, OSError, ImportError) as error:
                    effects.append(f"{channel} ({error})")
                else:
                    binding[channel] = record
                    images[record["file"]] = data
                    if companion:
                        receipts.append(companion)
                    effects = [name for name in effects if name.casefold() != field]
        if effects:
            binding["warning"] = ("Source material inputs needing review: " + ", ".join(effects))[:240]
    return binding, images, receipts
