"""Read a Tribes: Ascend map's post-process colour grade (Tribes3MapTool#38, T3BackEndServer#146).

UE3 maps set their look in post-process settings: a colour-grading lookup table, scene shadows,
mid-tones and highlights, and bloom. A PostProcessVolume replaces the WorldInfo defaults where it
applies; Ascend maps usually have one volume covering the map, so the highest-priority volume wins
and WorldInfo is the fallback. Fields a source leaves unset take UE3's defaults.

The result is the intake environment's ``grade`` block, in source terms. environment_ascend.py
turns it into the MILF environment grade.
"""

import struct

from unreal_binary_scene import property_tag, read_properties
from unreal_native_environment import rgb
from unreal_package_reader import Cursor, table_name

from intake_contract import GRADE_LUT_FILE  # noqa: E402  (one name for reader and contract)
LUT_SIZE = (256, 16)
UE3_DEFAULTS = {"shadows": [0.0, 0.0, 0.0], "midtones": [1.0, 1.0, 1.0], "highlights": [1.0, 1.0, 1.0],
                "bloom": {"scale": 1.0, "threshold": 1.0, "tint": [1.0, 1.0, 1.0]}}
VECTORS = {"scene_shadows": "shadows", "scene_midtones": "midtones", "scene_highlights": "highlights"}


def settings_fields(cursor, report, stream, objects):
    """Read the members of a saved PostProcessSettings struct that make up the grade."""
    found = {}
    for _ in range(256):
        member = property_tag(cursor, report["names"], True)
        if member is None:
            break
        key, kind, start = member["name"].casefold(), member["kind"], member["value_offset"]
        data = Cursor(stream, start, start + member["size"], report["summary"]["endian"])
        if key in VECTORS and kind == "StructProperty" and member["size"] == 12:
            found[VECTORS[key]] = [round(value, 4) for value in struct.unpack("<3f", data.take(12))]
        elif key in ("bloom_scale", "bloom_threshold") and kind == "FloatProperty":
            found[key] = struct.unpack("<f", data.take(4))[0]
        elif key == "bloom_tint" and kind == "StructProperty" and member["size"] == 4:
            found["bloom_tint"] = [round(value, 4) for value in rgb(list(data.take(4)))]
        elif key == "colorgrading_lookuptable" and kind == "ObjectProperty" and member["size"] == 4:
            found["lut"] = objects.get(struct.unpack("<i", data.take(4))[0])
        cursor.offset += member["size"]
    return found


def struct_tag(tags, name):
    matches = [tag for tag in tags if tag["name"].casefold() == name and tag["kind"] == "StructProperty"]
    return matches[0] if len(matches) == 1 else None


def read_grade(report, stream):
    """Return (grade settings in source terms, LUT Texture2D export or None) for an Ascend level."""
    objects = {row["index"]: row for row in report["imports"] + report["exports"]}
    candidates = []
    for row in report["exports"]:
        kind = row["class_name"].casefold()
        if kind not in ("postprocessvolume", "worldinfo"):
            continue
        tags, _, issues, _ = read_properties(stream, report["summary"], row, report["names"], objects)
        if issues:
            continue
        tag = struct_tag(tags, "settings" if kind == "postprocessvolume" else "defaultpostprocesssettings")
        if tag is None:
            continue
        priority = 0.0
        for item in tags:
            if item["name"].casefold() == "priority" and item["kind"] == "FloatProperty":
                start = item["value_offset"]
                priority = struct.unpack("<f", Cursor(stream, start, start + 4, report["summary"]["endian"]).take(4))[0]
        start = tag["value_offset"]
        fields = settings_fields(Cursor(stream, start, start + tag["size"], report["summary"]["endian"]),
                                 report, stream, objects)
        # A volume outranks the world defaults; among volumes, the higher priority wins.
        candidates.append(((kind == "postprocessvolume", priority), fields))
    if not candidates:
        return None, None
    _, fields = max(candidates, key=lambda item: item[0])
    grade = {key: list(fields.get(key, UE3_DEFAULTS[key])) for key in ("shadows", "midtones", "highlights")}
    grade["bloom"] = {"scale": round(fields.get("bloom_scale", 1.0), 4),
                      "threshold": round(fields.get("bloom_threshold", 1.0), 4),
                      "tint": fields.get("bloom_tint", UE3_DEFAULTS["bloom"]["tint"])}
    lut = fields.get("lut")
    if lut is not None and lut["class_name"].casefold() != "texture2d":
        lut = None
    return grade, lut


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("The colour-grading table is not a PNG.")
    return struct.unpack(">II", data[16:24])
