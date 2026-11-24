"""Recover serialized Vengeance event senders and proximity trigger settings."""

from collections import defaultdict

from intake_contract import finite
from source_profiles import PROXIMITY_CHAIN_MODES
from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor, table_name


ACTIVATION_LABELS = ("Player proximity", "Pawn proximity", "Class proximity", "Any proximity", "Damage",
                     "Human player proximity")
TRIGGER_DEFAULTS = {
    "collisionradius": ("FloatProperty", 40.0),
    "collisionheight": ("FloatProperty", 40.0),
    "repeattriggertime": ("FloatProperty", 0.0),
    "retriggerdelay": ("FloatProperty", 0.0),
    "triggertype": ("ByteProperty", 0),
    "binitiallyactive": ("BoolProperty", True),
    "btriggeronceonly": ("BoolProperty", False),
    "bcollideactors": ("BoolProperty", True),
    "bblockactors": ("BoolProperty", False),
    "bblockplayers": ("BoolProperty", False),
    "bdisabletouch": ("BoolProperty", False),
    "initialstate": ("NameProperty", "NormalTrigger"),
    "tag": ("NameProperty", "Trigger"),
}
EXTRA_TRIGGER_BEHAVIOR = {
    "message", "classproximitytype", "trigger_cutscene", "cutscene_name", "triggeractor", "triggeractor2",
    "gameinfoinclusions", "gameinfoexclusions", "exclusivetogameinfo",
}


def read_value(tag, stream, names):
    start = tag["value_offset"]
    cursor = Cursor(stream, start, start + tag["size"])
    kind = tag["kind"]
    if kind == "NameProperty":
        result = table_name(cursor, names, False)
    elif kind == "BoolProperty":
        result = bool(tag["boolean"])
    else:
        result, = cursor.fields("f" if kind == "FloatProperty" else "B")
    if cursor.offset != cursor.limit or tag["slot"]:
        raise ValueError(f"Invalid Vengeance trigger property {tag['name']}.")
    return result


def proximity_trigger(actor, tags, stream, names, units):
    fields = {}
    for tag in tags:
        name = tag["name"].casefold()
        if name not in TRIGGER_DEFAULTS:
            continue
        kind, _ = TRIGGER_DEFAULTS[name]
        if tag["kind"] != kind or name in fields:
            raise ValueError(f"Invalid Vengeance trigger property {tag['name']}.")
        fields[name] = read_value(tag, stream, names)
    settings = {name: fields.get(name, default) for name, (_, default) in TRIGGER_DEFAULTS.items()}
    radius = settings["collisionradius"] * 100 / units
    half_height = settings["collisionheight"] * 100 / units
    repeat, delay = settings["repeattriggertime"], settings["retriggerdelay"]
    if (not all(finite(size, 20_000) and size > 0 for size in (radius, half_height))
            or not all(finite(time, 3600) and time >= 0 for time in (repeat, delay))):
        raise ValueError("Vengeance trigger reach or timing exceeds the supported source dimensions.")
    transform = actor["transform"]
    position = [p * 100 for p in transform["position_m"]] if transform else []
    if (not position or actor["references"].keys() & {"base", "attachparent", "owner"}
            or not all(finite(point, 2e6) for point in position)):
        raise ValueError("The Vengeance trigger needs a valid independent position.")
    return dict(position_cm=position, radius_cm=radius, half_height_cm=half_height, activation=settings["triggertype"],
                active=settings["binitiallyactive"], once=settings["btriggeronceonly"], state=settings["initialstate"],
                repeat_seconds=repeat, delay_seconds=delay, tag=settings["tag"],
                collision=settings["bcollideactors"], blocking=settings["bblockactors"] or settings["bblockplayers"],
                touch=settings["bdisabletouch"] is False)


def event_senders(report, stream, actors, objects, units, event_fields):
    """Index saved senders and actors that receive an event under each tag."""
    names = report["names"]
    destinations = defaultdict(list)
    receivers = defaultdict(set)
    for actor in actors:
        export = objects[actor["line"]]
        try:
            tags, _, errors, _ = read_properties(stream, report["summary"], export, names, objects)
        except ValueError:
            continue
        receiver_tag = export["class_name"]
        for tag in tags:
            field = tag["name"].casefold()
            if (field != "tag" and field not in event_fields) or tag["kind"] != "NameProperty":
                continue
            try:
                value = read_value(tag, stream, names).casefold()
            except ValueError:
                continue
            if field == "tag":
                receiver_tag = value
                continue
            if value in {"", "none"}:
                continue
            proximity = None
            if export["class_path"].casefold() == "engine.trigger" and field == "event":
                try:
                    if errors:
                        raise ValueError("The Vengeance trigger has an invalid source transform.")
                    proximity = proximity_trigger(actor, tags, stream, names, units)
                    extras = sorted({item["name"] for item in tags
                                     if item["name"].casefold() in EXTRA_TRIGGER_BEHAVIOR})
                    review = "Additional trigger properties need review: " + ", ".join(extras) if extras else ""
                except ValueError as error:
                    review = str(error)
            else:
                review = "The source actor or event does not use the standard proximity trigger."
            destinations[value].append(dict(source_export=export["index"], source_name=actor["name"],
                                            source_class=export["class_path"], event=value, property=field,
                                            proximity=proximity, review=review))
        receivers[receiver_tag.casefold()].add(export["index"])
    for senders in destinations.values():
        for sender in senders:
            if proximity := sender["proximity"]:
                proximity["receives_events"] = proximity["tag"].casefold() in destinations
    return destinations, receivers


def source_chain_records(brushes, targets, receivers):
    """Follow authored event senders back from imported gameplay brushes."""
    if targets is None:
        return {}
    chains, known, door_tags, pending = {}, defaultdict(set), set(), []
    for index, brush in brushes.items():
        for sender in brush["behavior"].get("trigger_senders", ()):
            pending.append(sender)
            known[sender["event"]].add(int(index))
            if brush["type"] == "mover":
                door_tags.add(sender["event"])
    chain_tags = set()
    while pending:
        sender = pending.pop()
        index = sender["source_export"]
        trigger = sender["proximity"]
        if (not trigger or trigger["state"].casefold() not in PROXIMITY_CHAIN_MODES or not trigger["receives_events"]
                or str(index) in chains):
            continue
        tag = trigger["tag"].casefold()
        incoming = targets[tag]
        chains[str(index)] = incoming
        known[tag].add(index)
        chain_tags.update((tag, sender["event"]))
        pending.extend(incoming)
    for tag in door_tags | chain_tags:
        if receivers[tag] - known[tag]:
            for sender in targets[tag]:
                if not sender["review"]:
                    sender["review"] = "Additional actors share this event tag and need conversion review."
    return chains
