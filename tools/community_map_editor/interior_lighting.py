"""Portable DIF lightmap atlases and per-surface mappings."""

from hashlib import sha256
import json
import re

from model_textures import DIGEST, validate_png


LIGHTING_INDEX = "interior-lighting.json"
LIGHTMAP_FILE = re.compile(r"lightmap_[a-f0-9]{32}\.png")
MAX_LIGHTMAPS = 512
MAX_LIGHTMAP_BYTES = 96 * 1024 * 1024
MAX_LIGHTING_INDEX = 16 * 1024 * 1024
MAX_ANIMATION_BUFFER = 4 * 1024 * 1024
UV_AXES = ((0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1))
ANIMATION_TABLES = {
    "tracks": ("animatedLights", ("nameIndex", "stateIndex", "stateCount", "flags", "duration")),
    "states": ("lightStates", ("red", "green", "blue", "activeTime", "dataIndex", "dataCount")),
    "surface_data": ("stateDatas", ("surfaceIndex", "mapIndex", "lightStateIndex")),
}
ANIMATION_BUFFERS = {"names_hex": "nameBuffer", "state_masks_hex": "stateDataBuffers"}
ANIMATION_RECT_FIELDS = ("mapOffsetX", "mapOffsetY", "mapSizeX", "mapSizeY", "lightStateInfoStart", "lightCount")


def lightmap_uv(surface):
    word = surface.lightMapFinalWord
    axes = (word >> 13) & 7
    u_power, v_power = (word >> 6) & 63, word & 63
    offsets = surface.lightMapTexGenXD, surface.lightMapTexGenYD
    if axes >= len(UV_AXES) or not all(number(value) for value in offsets):
        raise ValueError("Invalid lightmap texture projection.")
    return [*UV_AXES[axes], 2.0 ** -u_power, 2.0 ** -v_power, *offsets]


def number(value):
    return type(value) in (float, int) and abs(value) <= 1e6


def dif_lighting_key(filename, level):
    return f"{filename}#{level}" if level else filename


def archive_lighting(interior, source_hash, ranges):
    from mission_textures import rgba_png

    atlases, images, review = [], {}, []
    atlas_bytes = 0
    source_atlases = getattr(interior, "lightMaps", ()) or ()
    if len(source_atlases) > MAX_LIGHTMAPS:
        review.append("This detail level exceeds the number of supported lightmap atlases.")
    for index, atlas in enumerate(source_atlases[:MAX_LIGHTMAPS]):
        record = {}
        for field, attr in (("color", "lightmap"), ("direction", "lightdirmap")):
            raw = getattr(atlas, attr, None)
            if not raw:
                continue
            try:
                raw = bytes(raw)
                if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError("Expected an embedded PNG.")
                png = rgba_png(raw)
                digest = sha256(png).hexdigest()
                name = f"lightmap_{digest[:32]}.png"
                if name not in images:
                    if atlas_bytes + len(png) > MAX_LIGHTMAP_BYTES:
                        raise ValueError("This detail level reached the lightmap size limit.")
                    atlas_bytes += len(png)
                record[field] = {"file": name, "source_sha256": sha256(raw).hexdigest()}
                images[name] = png
            except (ImportError, OSError, TypeError, ValueError) as error:
                review.append(f"Atlas {index} {field}: {error}"[:240])
        atlases.append(record)

    normal = getattr(interior, "normalLMapIndices", ()) or ()
    alarm = getattr(interior, "alarmLMapIndices", ()) or ()
    has_alarm = bool(getattr(interior, "hasAlarmState", False))
    animations = len(getattr(interior, "animatedLights", ()) or ())
    if not (atlases or normal or alarm or has_alarm or animations):
        return None, {}

    def atlas_at(indices, surface, state):
        if not indices:
            return None
        index = indices[surface] if surface < len(indices) else None
        if index in (-1, 4294967295) or index == 255 and len(atlases) <= 255:
            return None
        if type(index) is int and 0 <= index < len(atlases) and "color" in atlases[index]:
            return index
        review.append(f"Surface {surface} refers to a missing {state} atlas.")
        return None

    surfaces = []
    for source, first, count in ranges:
        normal_id = atlas_at(normal, source, "normal")
        alarm_id = atlas_at(alarm, source, "alarm") if has_alarm else None
        if normal_id is None and alarm_id is None:
            continue
        try:
            uv = lightmap_uv(interior.surfaces[source])
        except (AttributeError, ValueError):
            review.append(f"Surface {source} has invalid lightmap UV data.")
            continue
        surfaces.append([source, first, count, normal_id, alarm_id, uv])
    result = {"source_sha256": source_hash, "has_alarm": has_alarm, "animated_lights": animations,
              "atlases": atlases, "surfaces": surfaces, "review": list(dict.fromkeys(review))}
    if animations:
        try:
            result["animation"] = archive_animation(interior, animations)
        except (AttributeError, TypeError, ValueError):
            result["review"].append("The animated light tables could not be preserved by this decoder.")
        else:
            try:
                rectangles = [[surface, *(getattr(interior.surfaces[surface], field)
                                          for field in ANIMATION_RECT_FIELDS)]
                              for surface in sorted({row[0] for row in result["animation"]["surface_data"]})
                              if 0 <= surface < len(interior.surfaces)]
                if rectangles:
                    validate_rectangles(result["animation"], rectangles)
                    result["animation"]["surface_rects"] = rectangles
            except (AttributeError, TypeError, ValueError):
                result["review"].append("The animated light mask rectangles could not be preserved by this decoder.")
    return result, images


def archive_animation(interior, animations):
    data = {}
    for key, (attr, fields) in ANIMATION_TABLES.items():
        rows = getattr(interior, attr, ()) or ()
        if len(rows) > 250_000:
            raise ValueError("Oversized DIF light animation table.")
        data[key] = [[getattr(row, field) for field in fields] for row in rows]
    for key, attr in ANIMATION_BUFFERS.items():
        raw = getattr(interior, attr, ()) or ()
        if len(raw) > MAX_ANIMATION_BUFFER:
            raise ValueError("Oversized DIF light animation buffer.")
        data[key] = bytes(raw).hex()
    validate_animation(data, animations)
    return data


def validate_animation(data, animations, *, rectangles_allowed=False):
    optional = {"surface_rects"} if rectangles_allowed else set()
    if not isinstance(data, dict) or data.keys() - optional != ANIMATION_TABLES.keys() | ANIMATION_BUFFERS.keys():
        raise ValueError("Invalid interior light animation tables.")
    for key, (_, fields) in ANIMATION_TABLES.items():
        rows = data[key]
        if (not isinstance(rows, list) or len(rows) > 250_000
                or any(not isinstance(row, list) or len(row) != len(fields)
                       or any(type(value) is not int or not -(2 ** 31) <= value < 2 ** 31 for value in row)
                       for row in rows)):
            raise ValueError("Invalid interior light animation record.")
    if len(data["tracks"]) != animations or not all(
        isinstance(data[key], str) and len(data[key]) <= 2 * MAX_ANIMATION_BUFFER and len(data[key]) % 2 == 0
        and re.fullmatch(r"[0-9a-f]*", data[key]) for key in ANIMATION_BUFFERS
    ):
        raise ValueError("Invalid interior light animation inventory.")
    if "surface_rects" in data:
        validate_rectangles(data, data["surface_rects"])


def validate_rectangles(animation, rectangles):
    if not isinstance(rectangles, list) or not 0 < len(rectangles) <= len(animation["surface_data"]):
        raise ValueError("Invalid DIF animated light mask rectangles.")
    layout = {}
    previous = -1
    for row in rectangles:
        if not isinstance(row, list) or len(row) != 7 or any(type(value) is not int for value in row):
            raise ValueError("Invalid DIF animated light mask rectangle.")
        surface, x, y, width, height, first, count = row
        if (not previous < surface <= 250_000 or not 0 <= x < x + width <= 4096
                or not 0 <= y < y + height <= 4096 or not 0 <= first < first + count <= 65_536):
            raise ValueError("Invalid DIF animated light mask rectangle bounds.")
        previous = surface
        layout[surface] = (width * height, first, count)
    if layout.keys() != {row[0] for row in animation["surface_data"]}:
        raise ValueError("The DIF animated light masks and surface rectangles do not match.")
    mask_size = len(animation["state_masks_hex"]) // 2
    for surface, offset, slot in animation["surface_data"]:
        size, first, count = layout[surface]
        if not (first <= slot < first + count and (offset == -1 or 0 <= offset <= mask_size - size)):
            raise ValueError("Invalid DIF animated light mask offset or slot.")


def legacy_state_sources(record, receipts):
    states = record["legacy_states"]
    if not isinstance(states, list) or not 1 <= len(states) <= 256:
        raise ValueError("Invalid Tribes 1 light states.")
    roles = {}
    source_fields = {"source", "sha256", "version", "shift", "light_tracks"}
    overlay_fields = {"base_source", "base_sha256"}
    tracks = 0
    for index, state in enumerate(states):
        if (not isinstance(state, dict)
                or state.keys() - source_fields - overlay_fields != {"label", "role", "reference"}
                or not isinstance(state["label"], str) or not 0 < len(state["label"]) <= 240
                or not state["label"].isprintable()
                or state["role"] not in (("normal",) if index == 0 else ("alarm", "review"))):
            raise ValueError("Invalid Tribes 1 light state descriptor.")
        role, reference = state["role"], state["reference"]
        if reference is not None and (not isinstance(reference, str)
                                      or not re.fullmatch(r"[^/\\:\x00-\x1f]{1,240}\.dil", reference, re.I)):
            raise ValueError("Invalid Tribes 1 DIL name.")
        digest = None
        if state.keys() & source_fields:
            if (reference is None or role == "review" or source_fields - state.keys()
                    or not isinstance(state["source"], str) or state["source"].casefold() != reference.casefold()
                    or not isinstance(state["sha256"], str) or (state["source"], state["sha256"]) not in receipts
                    or type(state["version"]) is not int or state["version"] not in (6, 7)
                    or type(state["shift"]) is not int or not 0 <= state["shift"] <= 16
                    or type(state["light_tracks"]) is not int or not 0 <= state["light_tracks"] <= 16_384):
                raise ValueError("Tribes 1 light state is missing valid DIL provenance.")
            digest = state["sha256"]
            tracks += state["light_tracks"]
        if state.keys() & overlay_fields:
            if (digest is None or overlay_fields - state.keys()
                    or not all(isinstance(state[key], str) for key in overlay_fields)
                    or (state["base_source"], state["base_sha256"]) not in receipts):
                raise ValueError("Tribes 1 mission lighting is missing its base DIL receipt.")
        if role != "review":
            if role in roles:
                raise ValueError("Duplicate Tribes 1 normal or alarm light state.")
            roles[role] = digest
    if record["has_alarm"] != ("alarm" in roles) or record["animated_lights"] != tracks:
        raise ValueError("Tribes 1 light states do not match their coverage.")
    return roles


def valid_baked_uv(uv, normal, alarm, legacy):
    if legacy:
        return (isinstance(uv, list) and len(uv) == 2
                and all(projection is None if atlas is None else
                        isinstance(projection, list) and len(projection) == 4
                        and all(number(value) and 0 < value <= 1 for value in projection[:2])
                        and all(number(value) for value in projection[2:])
                        for projection, atlas in zip(uv, (normal, alarm))))
    return (isinstance(uv, list) and len(uv) == 6
            and all(type(value) is int for value in uv[:2]) and tuple(uv[:2]) in UV_AXES
            and all(number(value) and 0 < value <= 1 for value in uv[2:4])
            and all(number(value) for value in uv[4:]))


def validate_lighting(index, models, inventory, *, legacy=False, rectangles_allowed=False, receipts=()):
    files = {name for name in inventory if LIGHTMAP_FILE.fullmatch(name)}
    if not isinstance(index, dict) or not index or index.keys() - models.keys() or len(files) > MAX_LIGHTMAPS:
        raise ValueError("Invalid interior lighting inventory.")
    used = set()
    for name, record in index.items():
        source_hash, triangle_count = models[name]
        required = {"source_sha256", "has_alarm", "animated_lights", "atlases", "surfaces", "review"}
        if legacy:
            required.add("legacy_states")
        if (not isinstance(record, dict) or record.keys() - (set() if legacy else {"animation"}) != required
                or record["source_sha256"] != source_hash or type(record["has_alarm"]) is not bool
                or type(record["animated_lights"]) is not int or not 0 <= record["animated_lights"] <= 250_000):
            raise ValueError("Invalid interior lighting provenance.")
        if "animation" in record:
            validate_animation(record["animation"], record["animated_lights"], rectangles_allowed=rectangles_allowed)
        roles = legacy_state_sources(record, receipts) if legacy else {}
        atlases, surfaces, review = (record[key] for key in ("atlases", "surfaces", "review"))
        if (not isinstance(atlases, list) or len(atlases) > MAX_LIGHTMAPS
                or not isinstance(surfaces, list) or len(surfaces) > triangle_count
                or not isinstance(review, list) or len(review) > 500_000
                or not all(isinstance(item, str) and 0 < len(item) <= 240 for item in review)):
            raise ValueError("Invalid interior lighting records.")
        for atlas in atlases:
            if (not isinstance(atlas, dict) or atlas.keys() - {"color", "direction"}
                    or legacy and atlas.keys() != {"color"}):
                raise ValueError("Invalid interior lightmap atlas.")
            for image in atlas.values():
                if not isinstance(image, dict) or image.keys() != {"file", "source_sha256"}:
                    raise ValueError("Invalid interior lightmap file record.")
                file, digest = image["file"], image["source_sha256"]
                if (not isinstance(file, str) or file not in files or file[9:41] != inventory[file][:32]
                        or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                        or legacy and digest not in roles.values()):
                    raise ValueError("Interior lightmap file does not match its inventory.")
                used.add(file)
        previous_surface, previous_end = -1, 0
        for row in surfaces:
            if not isinstance(row, list) or len(row) != 6:
                raise ValueError("Invalid interior lightmap surface.")
            source, first, count, normal, alarm, uv = row
            if (any(type(value) is not int for value in (source, first, count))
                    or source <= previous_surface or first < previous_end or not 0 < count <= triangle_count - first
                    or (normal is None and alarm is None) or (alarm is not None and not record["has_alarm"])
                    or any(value is not None and (type(value) is not int or not 0 <= value < len(atlases)
                                                 or "color" not in atlases[value]) for value in (normal, alarm))
                    or not valid_baked_uv(uv, normal, alarm, legacy)
                    or legacy and any(atlas is not None and atlases[atlas]["color"]["source_sha256"] != roles.get(role)
                                      for role, atlas in (("normal", normal), ("alarm", alarm)))):
                raise ValueError("Invalid interior lightmap surface binding.")
            previous_surface, previous_end = source, first + count
    if files != used:
        raise ValueError("Unreferenced interior lightmap file.")


def load_lighting_index(folder, inventory):
    if LIGHTING_INDEX not in inventory:
        return {}
    from intake_contract import bounded

    data = bounded(folder / LIGHTING_INDEX, MAX_LIGHTING_INDEX)
    if sha256(data).hexdigest() != inventory[LIGHTING_INDEX]:
        raise ValueError("Intake checksum mismatch: " + LIGHTING_INDEX)
    return json.loads(data)


def model_lighting(index, filename, geometry):
    primary = prepared_level(index.get(filename))
    levels = geometry.get("lods", {}).get("secondary", ())
    secondary = {position: prepared_level(index[key]) for position in range(1, len(levels) + 1)
                 if (key := dif_lighting_key(filename, position)) in index}
    if not secondary:
        return primary
    atlases = list(primary["atlases"]) if primary else []
    known = {json.dumps(atlas, sort_keys=True): slot for slot, atlas in enumerate(atlases)}
    lod_surfaces = {}
    for position, record in secondary.items():
        slots = {}
        for slot, atlas in enumerate(record["atlases"]):
            key = json.dumps(atlas, sort_keys=True)
            if key not in known:
                known[key] = len(atlases)
                atlases.append(atlas)
            slots[slot] = known[key]
        lod_surfaces[position] = [
            [surface, first, count, slots.get(normal), slots.get(alarm), uv]
            for surface, first, count, normal, alarm, uv in record["surfaces"]
        ]
    warnings = [warning for record in (primary or {}, *secondary.values())
                for warning in record.get("animation_warnings", ())]
    return {**(primary or {}), "atlases": atlases, "surfaces": primary["surfaces"] if primary else [],
            "lod_surfaces": lod_surfaces, **({"animation_warnings": warnings} if warnings else {})}


def prepared_level(record):
    if not record or "surface_rects" not in record.get("animation", {}):
        return record
    from dif_light_animation import looping_atlas_tracks

    animated, warnings = looping_atlas_tracks(record)
    atlases = list(record["atlases"])
    variants = {}
    for key, tracks in animated.items():
        variants[key] = len(atlases)
        atlases.append({**atlases[key[1]], "animated_loops": tracks})
    surfaces = [[source, first, count, variants.get((0, normal), normal), variants.get((1, alarm), alarm), uv]
                for source, first, count, normal, alarm, uv in record["surfaces"]]
    return {**record, "atlases": atlases, "surfaces": surfaces, "animation_warnings": list(warnings)}


def alarm_switches_material(lighting):
    return bool(lighting and any(alarm is not None and alarm != normal
                                for surfaces in (lighting["surfaces"], *lighting.get("lod_surfaces", {}).values())
                                for _, _, _, normal, alarm, _ in surfaces))


def lighting_coverage(index):
    from dif_light_animation import TRIGGERED_LIGHT_FLAGS, looping_atlas_tracks, supported_triggers

    models, triggers, loop_triggers = {}, {}, {}
    for key, record in index.items():
        filename, _, _ = key.partition("#")
        values = models.setdefault(filename, {"normal_surfaces": 0, "alarm_surfaces": 0, "levels": 0})
        values["normal_surfaces"] += sum(row[3] is not None for row in record["surfaces"])
        values["alarm_surfaces"] += sum(row[4] is not None for row in record["surfaces"])
        values["levels"] += bool(record["surfaces"])
        if any(track[3] in TRIGGERED_LIGHT_FLAGS for track in record.get("animation", {}).get("tracks", ())):
            animated, _ = looping_atlas_tracks(record)
            triggers.setdefault(filename, set()).update(supported_triggers(animated))
            loop_triggers.setdefault(filename, set()).update(supported_triggers(animated, loops_only=True))
    for filename, names in triggers.items():
        if names:
            models[filename]["triggerable_lights"] = sorted(names)
        if loop_triggers[filename]:
            models[filename]["triggerable_light_loops"] = sorted(loop_triggers[filename])
    return models


def lighting_summary(record):
    from dif_light_animation import looping_atlas_tracks, supported_triggers

    animated, warnings = looping_atlas_tracks(record)
    review = [*record["review"], *warnings]
    trigger_names = sorted(supported_triggers(animated))
    loop_names = sorted(supported_triggers(animated, loops_only=True))
    return {
        "normal_surfaces": sum(row[3] is not None for row in record["surfaces"]),
        "alarm_surfaces": sum(row[4] is not None for row in record["surfaces"]),
        "color_atlases": sum("color" in atlas for atlas in record["atlases"]),
        "direction_atlases": sum("direction" in atlas for atlas in record["atlases"]),
        "authored_alarm_state": record["has_alarm"],
        "source_animated_lights": record["animated_lights"],
        "saved_animation_states": len(record.get("animation", {}).get("states", ())),
        "animated_atlases": len(animated),
        **({"triggerable_lights": trigger_names} if trigger_names else {}),
        **({"triggerable_light_loops": loop_names} if loop_names else {}),
        "review_count": len(review),
        "first_review_messages": review[:12],
        "source_sha256": record["source_sha256"],
        **({"source_light_states": record["legacy_states"]} if "legacy_states" in record else {}),
    }
