"""Resolve literal Tribes 2 interior-light startup calls as map data."""

import re
from collections import defaultdict

COMMAND_CLASS = "MissionLightCommand"
BINDING_TAG = "T3StartupLight_"
CALL_PREFIX = re.compile(r"\b(?:activateLight|deactivateLight)\s*\(", re.IGNORECASE)
CALL = re.compile(
    r'([A-Za-z_][A-Za-z0-9_]{0,63})\s*\.\s*(activateLight|deactivateLight)'
    r'\s*\(\s*"([ -!#-\[\]-~]{1,128})"\s*\)\s*;', re.IGNORECASE | re.ASCII,
)


def parse_call(source):
    match = CALL.fullmatch(source) if isinstance(source, str) else None
    if match:
        receiver, method, light = match.groups()
        if light.strip() == light:
            return receiver.lower(), light.lower(), method.lower() == "activatelight"
    return None


def startup_bindings(rows, available):
    """Return per-interior parameter defaults and reviewed command IDs."""
    if not available:
        return {}, {}
    names = defaultdict(list)
    initial = defaultdict(dict)
    applied = defaultdict(list)
    for row in rows:
        if row["name"] and row["source_class"] not in (COMMAND_CLASS, "MissionStatement"):
            names[row["name"].lower()].append(row["id"])
    for row in rows:
        if row["source_class"] != COMMAND_CLASS or row["parent"] is not None or row["status"] == "excluded":
            continue
        command = parse_call(row["properties"].get("statement"))
        if command is None:
            continue
        receiver, light, enable = command
        candidates = names[receiver]
        if len(candidates) != 1:
            continue
        target = candidates[0]
        if target >= row["id"] or light not in available.get(target, ()):
            continue
        if enable:
            initial[target][light] = 0.0
        elif initial[target].get(light) == 0.0:
            # A light that was active keeps its first key on deactivation.
            initial[target][light] = -2.0
        applied[target].append(row["id"])
    return dict(initial), dict(applied)


def placed_command_ids(actors):
    return {
        int(tag[len(BINDING_TAG):])
        for actor in actors for tag in map(str, actor.tags)
        if re.fullmatch(BINDING_TAG + r"[0-9]{1,9}", tag)
    }
