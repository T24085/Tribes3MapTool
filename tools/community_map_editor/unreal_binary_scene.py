"""Decode native UE2/UE3 actor properties into the map editor's neutral scene."""

from collections import Counter, defaultdict
import math
from pathlib import Path

from intake_contract import finite
from unreal_legacy_reader import (ACTOR_REFERENCES, ABSOLUTE_COMPONENT_FIELDS, GAMES, IDENTITY,
                                 MODEL_REFERENCES, PLACEMENT_FIELDS, relate_actors, scan_actor)
from unreal_package_reader import Cursor, MAX_RECORDS, open_package, table_name


MAX_PROPERTIES = 200_000
MAX_OBJECT_PROPERTIES = 4096
STATE_FRAME = 0x02000000
LEGACY_TYPES = (None, "ByteProperty", "IntProperty", "BoolProperty", "FloatProperty", "ObjectProperty",
                "NameProperty", "StringProperty", "ClassProperty", "ArrayProperty", "StructProperty",
                "VectorProperty", "RotatorProperty", "StrProperty", "MapProperty", "FixedArrayProperty")
VECTORS = {"location", "relativelocation", "translation", "drawscale3d", "relativescale3d", "scale3d"}
ROTATIONS = {"rotation", "relativerotation"}
SCALARS = {"drawscale", "scale"}
VENGEANCE_ACTOR_FLAGS = {"bhidden", "bcollideactors", "bblockactors", "bblockplayers", "bblockhavok"}
# Mesh components that cast no shadow in the source: sky domes, skyline backdrops, grass. Arx Novena's sky
# hemisphere with a floor shaded the whole map when imported as a shadow caster.
SHADOW_FLAGS = {"castshadow", "bcastdynamicshadow"}
# Meshes players pass through in the source: Arx Novena's 422 grass clumps and other small props.
COLLISION_FLAGS = {"collideactors", "bcollideactors"}
REFERENCES = set(ACTOR_REFERENCES + MODEL_REFERENCES)
PLACEMENT = PLACEMENT_FIELDS | VECTORS | ROTATIONS | SCALARS | REFERENCES | ABSOLUTE_COMPONENT_FIELDS
NON_ACTORS = {"actorgroup", "class", "level", "model", "package", "polys", "world"}


def late_vengeance_state(cursor, objects):
    # These state frames have one or two additional version records before the
    # saved state fields. Validate both ends before interpreting any properties.
    version_record = (3, 0, 1)
    if cursor.fields("3i") != version_record:
        raise ValueError("Unrecognized Vengeance actor state header.")
    after_version = cursor.offset
    if cursor.fields("3i") != version_record:
        cursor.offset = after_version
    cursor.take(24)
    parent, first_class, second_class, state = (cursor.index(False) for _ in range(4))
    if (parent or first_class != second_class or first_class not in objects or state not in objects
            or cursor.take(10) != b"\0" * 5 + b"\x81" + b"\0" * 4):
        raise ValueError("Unrecognized Vengeance actor state record.")


def read_object_prefix(cursor, summary, export, objects):
    modern = summary["game"] == "ascend"
    if modern and summary["version"] == 805 and export["class_name"].casefold() == "dominantdirectionallightcomponent":
        # UE3 saves an array of 16-bit dominant-light shadow samples before the component.
        shadow_samples = cursor.count((cursor.limit - cursor.offset) // 2)
        if shadow_samples * 2 > cursor.limit - cursor.offset:
            raise ValueError("Dominant light shadow samples exceed the component payload.")
        cursor.offset += shadow_samples * 2
    if not modern and summary["licensee_version"] >= 25:
        marker, = cursor.fields("i")
        extra = {3: 8, 4: 4}.get(marker)
        if extra is None:
            raise ValueError("Unrecognized Vengeance object header.")
        cursor.take(extra)
    if export["flags"] & STATE_FRAME:
        if not modern and (summary["version"], summary["licensee_version"]) == (130, 27):
            late_vengeance_state(cursor, objects)
            return
        node = cursor.index(modern)
        state = cursor.index(modern)
        if any(index and index not in objects for index in (node, state)):
            raise ValueError("Invalid object reference in the state frame.")
        cursor.take(6 if modern else 12)  # Probe mask and latent action.
        if modern:
            cursor.take(cursor.count(4096) * 12)
        if node:
            cursor.index(modern)
    if modern:
        if export["class_name"].casefold().endswith("component"):
            owner = cursor.index(True)
            if owner and owner not in objects:
                raise ValueError("Invalid component template owner.")
        cursor.fields("i")  # Net index for placed instances.


def property_tag(cursor, names, modern):
    """Read a tagged property's descriptor and leave the cursor at its value."""
    name = table_name(cursor, names, modern)
    if name.casefold() == "none":
        return None
    struct_name = boolean = None
    if modern:
        kind = table_name(cursor, names, True)
        if not kind.endswith("Property"):
            raise ValueError("Invalid UE3 tagged property type.")
        size, slot = cursor.fields("ii")
        if kind == "StructProperty":
            struct_name = table_name(cursor, names, True)
        elif kind == "BoolProperty":
            boolean, = cursor.fields("B")
        elif kind == "ByteProperty":
            table_name(cursor, names, True)  # Enum type, including None for a raw byte.
    else:
        info, = cursor.fields("B")
        kind = LEGACY_TYPES[info & 15]
        if kind is None:
            raise ValueError("Invalid UE2 property type.")
        if kind == "StructProperty":
            struct_name = table_name(cursor, names, False)
        size_code = (info >> 4) & 7
        size = ((1, 2, 4, 12, 16)[size_code] if size_code < 5
                else cursor.fields(("B", "H", "i")[size_code - 5])[0])
        slot = 0
        if kind == "BoolProperty":
            boolean = int(bool(info & 128))
            size = 0
        elif info & 128:
            first, = cursor.fields("B")
            if first < 128:
                slot = first
            else:
                width = 3 if first & 64 else 1
                slot = int.from_bytes(bytes([first]) + cursor.take(width), "big")
                slot &= 0x3fffff if width == 3 else 0x3fff
    if size < 0 or slot < 0 or cursor.offset + size > cursor.limit or boolean not in (None, 0, 1):
        raise ValueError("Invalid tagged property size, index or boolean.")
    return dict(name=name, kind=kind, structure=struct_name, size=size, slot=slot,
                value_offset=cursor.offset, boolean=boolean)


def placement_value(value, tag, modern, objects):
    """Represent supported typed values in the text reader's neutral field syntax."""
    key, kind = tag["name"].casefold(), tag["kind"]
    structure = (tag["structure"] or "").casefold()
    if key in VECTORS and (kind == "VectorProperty" or kind == "StructProperty" and structure == "vector"):
        values = value.fields("3f")
        labels = ("X", "Y", "Z")
    elif key in ROTATIONS and (kind == "RotatorProperty" or kind == "StructProperty" and structure == "rotator"):
        values = value.fields("3i")
        labels = ("Pitch", "Yaw", "Roll")
    elif key in SCALARS and kind == "FloatProperty":
        coordinate, = value.fields("f")
        if math.isfinite(coordinate):
            return str(coordinate)
        raise ValueError(f"Non-finite {tag['name']}.")
    elif key in REFERENCES and kind in {"ObjectProperty", "ClassProperty"}:
        index = value.index(modern)
        if not index:
            return "None"
        if index not in objects or not IDENTITY.fullmatch(path := objects[index]["object_path"]):
            raise ValueError(f"Unresolved object reference in {tag['name']}.")
        return path
    elif key in ABSOLUTE_COMPONENT_FIELDS | VENGEANCE_ACTOR_FLAGS | SHADOW_FLAGS | COLLISION_FLAGS and kind == "BoolProperty":
        return "True" if tag["boolean"] else "False"
    else:
        raise ValueError(f"Unsupported placement property {tag['name']} ({kind}).")
    if not all(math.isfinite(part) for part in values):
        raise ValueError(f"Non-finite {tag['name']}.")
    return "(" + ",".join(f"{label}={part}" for label, part in zip(labels, values)) + ")"


def read_properties(stream, summary, export, names, objects):
    start = export["serial_offset"]
    cursor = Cursor(stream, start, start + export["serial_bytes"], summary["endian"])
    modern = summary["game"] == "ascend"
    read_object_prefix(cursor, summary, export, objects)
    tags, entries, issues = [], [], []
    for _ in range(MAX_OBJECT_PROPERTIES):
        tag = property_tag(cursor, names, modern)
        if tag is None:
            return tags, entries, issues, cursor.offset
        end = cursor.offset + tag["size"]
        tags.append(tag)
        key = tag["name"].casefold()
        if (key in PLACEMENT or key in SHADOW_FLAGS or key in COLLISION_FLAGS
                or not modern and key in VENGEANCE_ACTOR_FLAGS):
            payload = Cursor(stream, cursor.offset, end, cursor.endian)
            try:
                data = placement_value(payload, tag, modern, objects)
                if payload.offset != end or tag["slot"]:
                    raise ValueError(f"Unexpected payload length or array slot in {tag['name']}.")
                entries.append(dict(line=export["index"], key=tag["name"], value=data))
            except ValueError as error:
                issues.append(str(error))
        cursor.offset = end
    raise ValueError("Too many tagged properties on one object.")


def decode_object(stream, summary, export, names, objects, block):
    index = export["index"]
    row = dict(block=block, line=index, end_line=index,
               header=f'Class={export["class_path"]} Name={export["name"]}', entries=[],
               object_path=export["object_path"], export_index=index,
               serial_offset=export["serial_offset"], serial_bytes=export["serial_bytes"],
               archetype=export["archetype"], tags=[], property_issues=[])
    try:
        tags, fields, issues, next_offset = read_properties(stream, summary, export, names, objects)
        row.update(entries=fields, tags=tags, property_issues=issues, property_end=next_offset)
    except ValueError as error:
        row["property_issues"].append(str(error))
    return row


MAX_ARCHETYPE_DEPTH = 8
NOT_INHERITED = {"location", *ACTOR_REFERENCES}  # Prefab-relative or owner-specific values.


def inherit_archetype(row, stream, summary, names, objects):
    """Fill placement values an instance left at its in-package archetype's value.

    UE3 saves only properties that differ from the object's archetype. Prefab pieces
    (Katabatic's generator atrium) take their mesh, rotation and scale from archetype
    exports cooked into the map, so the instance alone has no StaticMesh. Location is
    never inherited: a prefab archetype's location is relative to the prefab.
    """
    have = {field["key"].casefold() for field in row["entries"]}
    archetype, seen, inherited = row["archetype"], set(), 0
    while archetype > 0 and archetype in objects and archetype not in seen and len(seen) < MAX_ARCHETYPE_DEPTH:
        seen.add(archetype)
        source = objects[archetype]
        try:
            _, fields, issues, _ = read_properties(stream, summary, source, names, objects)
        except ValueError as error:
            fields, issues = [], [str(error)]
        if issues:
            row["property_issues"].extend(f'Archetype {source["object_path"]}. {issue}' for issue in issues)
            break
        for field in fields:
            key = field["key"].casefold()
            if key not in have and key not in NOT_INHERITED:
                have.add(key)
                row["entries"].append(dict(line=row["line"], key=field["key"], value=field["value"]))
                inherited += 1
        archetype = source["archetype"]
    return inherited


def level_actors(level, report, stream, objects):
    summary = report["summary"]
    modern = summary["game"] == "ascend"
    _, _, _, start = read_properties(stream, summary, level, report["names"], objects)
    end = level["serial_offset"] + level["serial_bytes"]
    cursor = Cursor(stream, start, end, summary["endian"])
    first, second = cursor.fields("2i")
    if modern:
        if first and objects.get(first, {}).get("class_name", "").casefold() not in {"level", "world"}:
            raise ValueError("Invalid Ascend level reference before the actor array.")
        count, capacity, min_size = second, second, 4
    else:
        count, capacity, min_size = first, second, 1
    if not 0 <= count <= capacity <= MAX_RECORDS or count * min_size > end - cursor.offset:
        raise ValueError("Invalid native level actor array dimensions.")
    actors = []
    seen = set()
    for _ in range(count):
        index = cursor.index(modern)
        if not index:
            continue
        if index < 0 or index not in objects or index in seen:
            raise ValueError("The level lists an invalid or duplicate actor export.")
        actors.append(objects[index])
        seen.add(index)
    return actors


def actor_scene(report, stream, units_per_m):
    if not finite(units_per_m, 1e6) or units_per_m <= 0:
        raise ValueError("Specify a positive source units-per-metre value.")
    summary = report["summary"]
    game = summary["game"]
    exports = report["exports"]
    objects = {row["index"]: row for row in report["imports"] + exports}
    children = defaultdict(list)
    for asset in exports:
        children[asset["outer"]].append(asset)
    levels = [asset for asset in exports if asset["class_name"].casefold() == "level"]
    root = dict(block="Map", line=0, end_line=0, header="", entries=[])
    actors, tag_count = [], 0
    assigned = set()
    for level in levels:
        level_index = level["index"]
        scope = dict(block="Level", line=level_index, end_line=level_index,
                     header=f'Name={level["name"]}', entries=[])
        root["entries"].append(scope)
        for export in level_actors(level, report, stream, objects):
            if export["class_name"].casefold() in NON_ACTORS:
                continue
            if export["index"] in assigned:
                raise ValueError("One actor export is listed in multiple level rosters.")
            assigned.add(export["index"])
            node = decode_object(stream, summary, export, report["names"], objects, "Actor")
            scope["entries"].append(node)
            components = [decode_object(stream, summary, child, report["names"], objects, "Object")
                          for child in children[export["index"]] if child["class_name"].casefold().endswith("component")]
            inherited = sum(inherit_archetype(item, stream, summary, report["names"], objects)
                            for item in (node, *components))
            node["entries"].extend(components)
            tag_count += len(node["tags"]) + sum(len(item["tags"]) for item in components)
            if tag_count > MAX_PROPERTIES:
                raise ValueError("Native actor properties exceed the supported report size.")
            actor = scan_actor(node, units_per_m, game)
            actor.update(level_name=level["name"], level_lines=[level_index], level_path=level["object_path"],
                         object_path=export["object_path"])
            for item in (node, *components):
                if item["property_issues"]:
                    actor["review"].extend(f'{item["object_path"]}. {issue}' for issue in item["property_issues"])
            unsupported = sum(tag["name"].casefold() not in PLACEMENT for item in (node, *components)
                              for tag in item["tags"])
            if unsupported:
                actor["review"].append(f"Source scene retains {unsupported} properties for manual review.")
            if inherited:
                actor["review"].append(f"{inherited} placement values are inherited from prefab archetypes.")
            if (not any(field.get("key", "").casefold() == "location" for field in node["entries"])
                    or any(item["property_issues"] for item in (node, *components))):
                actor["review"].append("No independent serialized placement was established for this actor.")
                actor["transform"] = None
            actors.append(actor)
    if not actors:
        raise ValueError("No actor exports were found for a native Level object.")
    relate_actors(actors)
    return dict(format="legacy-unreal-scene", version=1, reader="legacy-unreal-package-v1", source_game=GAMES[game],
                profile=game, source={key: report["source"][key] for key in ("name", "sha256")},
                coordinate_frame=dict(up="positive-z", handedness="left", source_units_per_m=units_per_m,
                                      source_turn_units=65536),
                coverage=dict(roles=dict(Counter(actor["role"] or "unmapped" for actor in actors)),
                              actors_with_review=sum(bool(actor["review"]) for actor in actors)),
                source_index="package-export", package_summary=summary, actors=actors, scene=[root])


LEVEL_LINE_STRIDE = 10_000_000  # Each merged level package gets its own disjoint line range.
MAX_MERGED_LEVELS = 8
LINE_KEYS = ("line", "end_line", "export_index")


def _shift_lines(value, offset):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in LINE_KEYS and type(item) is int:
                value[key] = item + offset
            else:
                _shift_lines(item, offset)
    elif isinstance(value, list):
        for item in value:
            _shift_lines(item, offset)


def merge_level_scene(scene, extra, slot, label):
    """Add another level package's actors to ``scene`` (Ascend split maps: gameplay, _Ter, _Sound).

    Lines are export indices, so each merged package moves to its own range. Its actors keep
    their own links and are marked ``merged_level`` so readers that use the primary package's
    byte stream (meshes, component materials, environment, terrain) never read them.
    """
    if not 1 <= slot <= MAX_MERGED_LEVELS:
        raise ValueError(f"Merge at most {MAX_MERGED_LEVELS} additional level packages.")
    offset = slot * LEVEL_LINE_STRIDE
    for actor in extra["actors"]:
        if any(line >= LEVEL_LINE_STRIDE for line in (actor["line"], *actor["level_lines"])):
            raise ValueError("Level package has too many exports to merge.")
        _shift_lines(actor, offset)
        actor["level_lines"] = [line + offset for line in actor["level_lines"]]
        actor["links"] = {key: line + offset for key, line in actor["links"].items()}
        actor["level_name"] = label
        actor["object_path"] = f'{label}.{actor["object_path"]}'
        actor["merged_level"] = label
    for root in extra["scene"]:
        for level in root["entries"]:
            _shift_lines(level, offset)
            level["header"] = f"Name={label}"
            scene["scene"][0]["entries"].append(level)
    scene["actors"].extend(extra["actors"])
    scene.setdefault("levels", []).append(dict(extra["source"], label=label))
    roles = Counter(actor["role"] or "unmapped" for actor in scene["actors"])
    scene["coverage"] = dict(roles=dict(roles), actors_with_review=sum(bool(actor["review"]) for actor in scene["actors"]))
    return scene


def read_level_scenes(scene, paths, game, units_per_m):
    """Merge each additional level package into ``scene``; return their source receipts."""
    receipts = []
    for slot, path in enumerate(paths, 1):
        with open_package(path, game) as (report, stream):
            extra = actor_scene(report, stream, units_per_m)
        merge_level_scene(scene, extra, slot, Path(path).stem)
        receipts.append(dict(extra["source"], origin=Path(path).name))
    return receipts


def read_binary_scene(path, game, units_per_m, levels=()):
    with open_package(path, game) as (report, stream):
        scene = actor_scene(report, stream, units_per_m)
    if levels:
        read_level_scenes(scene, levels, game, units_per_m)
    return scene
