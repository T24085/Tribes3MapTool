"""Portable bindings for explicitly supplied terrain artwork."""

import re


ART_FILE = re.compile(r"terrain-art-[a-f0-9]{32}\.png")
MAX_ART_BYTES = 96 * 1024 * 1024
MAX_ART_TEXTURES = 256
SOURCE_ART = "Source artwork"


def available_art(header):
    return {int(slot): record["file"] for slot, record in header.get("terrain_art", {}).items() if "file" in record}


def validate_art(header, terrain, files):
    records = header.get("terrain_art")
    material_slots = {str(slot) for slot, _ in terrain["material_layers"]}
    sources = {(source["name"], source["sha256"]) for source in header["sources"]}
    if not isinstance(records, dict) or not records or not records.keys() <= material_slots:
        raise ValueError("Invalid terrain artwork slot inventory.")
    used = set()
    for record in records.values():
        if not isinstance(record, dict):
            raise ValueError("Invalid terrain artwork binding.")
        if record.keys() == {"reason"}:
            reason = record["reason"]
            if not isinstance(reason, str) or not 0 < len(reason) <= 240:
                raise ValueError("Invalid terrain artwork review reason.")
            continue
        keys = {"file", "source", "source_sha256"}
        if (record.keys() not in (keys, keys | {"palette", "palette_sha256"})
                or any(not isinstance(value, str) for value in record.values())
                or record["file"] not in files or (record["source"], record["source_sha256"]) not in sources
                or "palette" in record and (record["palette"], record["palette_sha256"]) not in sources):
            raise ValueError("Terrain artwork is missing its source or PNG.")
        used.add(record["file"])
    if used != files:
        raise ValueError("Unreferenced terrain artwork PNG.")
