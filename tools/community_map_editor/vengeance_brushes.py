"""Recover authored mover and elevator-volume brushes for draft review."""

import math
from hashlib import sha256
from pathlib import Path

from intake_contract import finite
from source_profiles import BRUSH_PREVIEW_REFERENCE
from unreal_binary_scene import property_tag, read_properties
from unreal_legacy_reader import ANGLES, direct_fields, transform, tuple_field
from unreal_package_reader import Cursor, table_name
from vengeance_bsp import project_model
from vengeance_mover_plan import rotate
from vengeance_terrain import HEIGHT_LAYOUT
from vengeance_triggers import event_senders, source_chain_records


BRUSH_CLASSES = {"engine.mover": "mover", "movement.elevatorvolume": "elevator"}
TIMES = {"movetime": 1.0, "stayopentime": 4.0, "delaytime": 0.0}
EVENT_FIELDS = {"tag", "event", "initialstate", "openedevent", "closedevent", "openingevent", "closingevent",
                "loopevent", "bumpevent", "playerbumpevent", "returngroup", "antiportaltag", "associatedactortag"}
OUTGOING_EVENTS = {name for name in EVENT_FIELDS if name.endswith("event")}
VECTORS = {"prepivot", "basepos", "keypos", "gravity", "zonevelocity"}
ROTATIONS = {"baserot", "keyrot"}
SCALES = {"mainscale", "postscale"}
FLAGS = {"boscillatingloop", "buseshortestrotation", "bslave", "bisleader", "bhidden", "active", "bmoveprojectiles"}
DOOR_FLAGS = {"btriggeronceonly", "busetriggered", "bdamagetriggered"}
FLOATS = TIMES.keys() | {"terminalvelocity", "groundfriction", "fluidfriction"}
BYTES = {"keynum", "numkeys", "moverglidetype", "moverencroachtype", "bumptype"}
MOTION_SOUNDS = {"openingsound", "openedsound", "closingsound", "closedsound", "loopsound", "moveambientsound"}
OBJECTS = {"brush", "rendermaterial", "associatedactor"} | MOTION_SOUNDS
FLAGS |= DOOR_FLAGS
BRUSH_FIELDS = VECTORS | ROTATIONS | SCALES | EVENT_FIELDS | FLAGS | FLOATS | BYTES | OBJECTS | {"priority"}


def identity_brush_scale(value, names):
    scale, shear = (1.0, 1.0, 1.0), 0.0
    seen = set()
    while tag := property_tag(value, names, False):
        key, kind = tag["name"].casefold(), tag["kind"]
        if key in seen or tag["slot"]:
            raise ValueError("Duplicate Vengeance brush scale field.")
        seen.add(key)
        end = tag["value_offset"] + tag["size"]
        if key == "scale" and (kind == "VectorProperty" or kind == "StructProperty" and tag["structure"].casefold() == "vector"):
            scale = value.fields("3f")
        elif key == "sheerrate" and kind == "FloatProperty":
            shear, = value.fields("f")
        elif key == "sheeraxis" and kind == "ByteProperty":
            value.take(1)
        else:
            raise ValueError("Unsupported Vengeance brush scale field.")
        if value.offset != end:
            raise ValueError("Invalid Vengeance brush scale field size.")
    if scale != (1.0, 1.0, 1.0) or shear != 0:
        raise ValueError("Nonidentity brush scale or shear requires an authoring transform.")


def brush_properties(tags, stream, names):
    result, key_pos, key_rot = {}, {}, {}
    for tag in tags:
        key, kind, slot = tag["name"].casefold(), tag["kind"], tag["slot"]
        if key not in BRUSH_FIELDS:
            continue
        fields = {"keypos": key_pos, "keyrot": key_rot}.get(key, result)
        index = key if fields is result else slot
        if index in fields or (slot and fields is result) or slot >= 24:
            raise ValueError("Duplicate or invalid Vengeance mover key or brush property.")
        start = tag["value_offset"]
        value = Cursor(stream, start, start + tag["size"])
        struct_name = (tag["structure"] or "").casefold()
        if key in OBJECTS and kind == "ObjectProperty":
            fields[index] = value.index(False)
        elif key in VECTORS and (kind == "VectorProperty" or kind == "StructProperty" and struct_name == "vector"):
            vector = value.fields("3f")
            if not all(math.isfinite(point) for point in vector):
                raise ValueError("Nonfinite Vengeance brush coordinates.")
            fields[index] = vector
        elif key in ROTATIONS and (kind == "RotatorProperty" or kind == "StructProperty" and struct_name == "rotator"):
            fields[index] = value.fields("3i")
        elif key in SCALES and kind == "StructProperty" and struct_name == "scale":
            identity_brush_scale(value, names)
            fields[index] = True
        elif key in FLOATS and kind == "FloatProperty":
            fields[index], = value.fields("f")
            if not finite(fields[index], 1e6) or fields[index] < 0 or key in TIMES and fields[index] > 3600:
                raise ValueError("Invalid Vengeance brush motion or physics value.")
        elif key in EVENT_FIELDS and kind == "NameProperty":
            fields[index] = table_name(value, names, False)
        elif key in BYTES and kind == "ByteProperty":
            fields[index], = value.fields("B")
        elif key in FLAGS and kind == "BoolProperty":
            fields[index] = bool(tag["boolean"])
        elif key == "priority" and kind == "IntProperty":
            fields[index], = value.fields("i")
        else:
            raise ValueError(f"Unsupported Vengeance brush property {tag['name']}.")
        if value.offset != value.limit:
            raise ValueError(f"Invalid Vengeance brush property size for {tag['name']}.")
    return result, key_pos, key_rot


def brush_transform(fields, units, block):
    pose = transform(block, units)
    scale = pose["scale"]
    pivot = fields.get("prepivot", (0, 0, 0))
    delta = rotate([p / units * s for p, s in zip(pivot, scale)], pose["rotation_xyzw"])
    location = [(p - offset) * 100 for p, offset in zip(pose["position_m"], delta)]
    if not all(finite(p, 2e6) for p in location) or not all(finite(s, 1000) for s in scale):
        raise ValueError("Vengeance brush pose exceeds the supported draft coordinates.")
    return dict(position_cm=location, rotation_xyzw=pose["rotation_xyzw"], scale=scale)


def mover_motion(fields, key_pos, key_rot, units):
    keys, current = fields.get("numkeys", 2), fields.get("keynum", 0)
    if not 1 <= keys <= 24 or not 0 <= current < keys or any(key >= keys for key in key_pos.keys() | key_rot.keys()):
        raise ValueError("Vengeance mover key count does not match its saved keys.")
    ordered = [{"position_offset_m": [v / units for v in key_pos.get(key, (0, 0, 0))],
                "rotation_offset_units": list(key_rot.get(key, (0, 0, 0)))} for key in range(keys)]
    origin = [v / units for v in fields["basepos"]] if "basepos" in fields else None
    angles = list(fields["baserot"]) if "baserot" in fields else None
    if not all(finite(v, 20_000) for key in ordered for v in key["position_offset_m"] + (origin or [])):
        raise ValueError("Vengeance mover key position exceeds the supported map dimensions.")
    return dict(keys=ordered, current_key=current, base_position_m=origin, base_rotation_units=angles,
                timing_seconds={name: fields.get(name, default) for name, default in TIMES.items()},
                initial_state=fields.get("initialstate", "BumpOpenTimed"), glide_type=fields.get("moverglidetype", 1),
                events={name: fields[name] for name in sorted(EVENT_FIELDS - {"initialstate"}) if name in fields})


def mover_playback(fields, units, block, senders=None):
    pose, _ = direct_fields(block)
    angles = tuple_field(pose.get("rotation", "()"), ANGLES, 0)
    if any(angle != int(angle) or not -(2**31) <= angle < 2**31 for angle in angles):
        raise ValueError("Invalid Vengeance mover rotation units.")
    state = fields.get("initialstate", "BumpOpenTimed").casefold()
    links = {"bslave", "bisleader", "loopsound"}
    is_timed_door = state in {"bumpopentimed", "triggeropentimed"}
    if is_timed_door:
        links |= (DOOR_FLAGS - {"btriggeronceonly"}) | MOTION_SOUNDS
    behavior = dict(saved_rotation_units=[int(angle) for angle in angles],
                pivot_m=[part / units for part in fields.get("prepivot", (0, 0, 0))],
                oscillating=fields.get("boscillatingloop", False),
                shortest_rotation=fields.get("buseshortestrotation", False),
                linked=any(fields.get(name) for name in links),
                hidden=fields.get("bhidden", False), encroach_type=fields.get("moverencroachtype", 1))
    if state == "bumpopentimed":
        behavior["bump_type"] = fields.get("bumptype", 0)
    if is_timed_door and fields.get("btriggeronceonly"):
        behavior["opens_once"] = True
    if senders:
        behavior["trigger_senders"] = senders
    return behavior


def elevator_physics(fields, units, objects, targets):
    material = fields.get("rendermaterial")
    if material and material not in objects:
        raise ValueError("Unresolved Vengeance elevator material.")
    associated_actor = fields.get("associatedactor")
    if associated_actor and associated_actor not in objects:
        raise ValueError("Unresolved Vengeance elevator actor reference.")
    tag = fields.get("tag", "ElevatorVolume")
    senders = targets.get(tag.casefold(), [])
    return dict(active=fields.get("active", True), hidden=fields.get("bhidden", False),
                gravity_m_s2=[part / units for part in fields.get("gravity", (0, 0, 0))],
                zone_velocity_m_s=[part / units for part in fields.get("zonevelocity", (0, 0, 0))],
                terminal_speed_m_s=fields.get("terminalvelocity", 100000) / units,
                ground_friction=fields.get("groundfriction", 0), fluid_friction=fields.get("fluidfriction", 0),
                priority=fields.get("priority", 0), moves_projectiles=fields.get("bmoveprojectiles", False),
                render_material=objects[material]["object_path"] if material else "FX.ElevatorShader",
                trigger_tag=tag, trigger_event=fields.get("event", "None"), incoming_trigger=bool(senders),
                trigger_senders=senders,
                associated_actor=objects[associated_actor]["object_path"] if associated_actor else "None",
                associated_actor_tag=fields.get("associatedactortag", "None"))


def read_native_brushes(report, stream, units, actors, blocks, excluded):
    objects = {row["index"]: row for row in report["imports"] + report["exports"]}
    summary = report["summary"]
    decoder_hash = sha256(Path(__file__).read_bytes() + Path(__file__).with_name("vengeance_bsp.py").read_bytes()).hexdigest()
    brushes, models, issues = {}, {}, {}
    targets = receiver_index = None
    for actor in actors:
        index = actor["line"]
        export = objects[index]
        kind = BRUSH_CLASSES.get(export["class_path"].casefold())
        if not kind or index in excluded:
            continue
        try:
            if (summary["version"], summary["licensee_version"], summary["endian"]) != HEIGHT_LAYOUT:
                raise ValueError("The Vengeance brush package layout is unsupported.")
            if actor["references"].keys() & {"base", "attachparent"}:
                raise ValueError("The attached Vengeance brush needs its parent transform.")
            tags, _, errors, _ = read_properties(stream, summary, export, report["names"], objects)
            if errors:
                raise ValueError("The Vengeance brush actor has an invalid source transform.")
            fields, positions, rotations = brush_properties(tags, stream, report["names"])
            model = objects.get(fields.get("brush"))
            if not model or model["index"] <= 0 or model["class_name"].casefold() != "model":
                raise ValueError("The Vengeance brush actor needs its embedded Model geometry.")
            pose = brush_transform(fields, units, blocks[index])
            motion = mover_motion(fields, positions, rotations, units) if kind == "mover" else None
            triggered_door = motion and motion["initial_state"].casefold() == "triggeropentimed"
            if (kind == "elevator" or triggered_door) and targets is None:
                targets, receiver_index = event_senders(report, stream, actors, objects, units, OUTGOING_EVENTS)
            door_senders = targets.get(fields.get("tag", "Mover").casefold()) if triggered_door else None
            behavior = (mover_playback(fields, units, blocks[index], door_senders) if kind == "mover"
                        else elevator_physics(fields, units, objects, targets))
            reference = f"{BRUSH_PREVIEW_REFERENCE}{model['index']}"
            if reference not in models:
                decoded = project_model(report, stream, model, objects, units, decoder_hash)
                if not decoded or not decoded[0]["render"]:
                    raise ValueError("The Vengeance brush model has no preview surface.")
                models[reference] = (model["object_path"], *decoded)
            brushes[str(index)] = dict(type=kind, reference=reference, transform=pose, motion=motion, behavior=behavior)
        except ValueError as error:
            issues[str(index)] = str(error)
    chains = source_chain_records(brushes, targets, receiver_index)
    return brushes, models, issues, chains
