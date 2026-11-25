"""Export a verified, asset-free layout that the intake picker can reopen."""

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import zipfile

from intake_contract import BOUNDARY_RECIPE, EQUIPMENT_FILE, PLAYABLE_BOUNDS, PUBLIC_LAYOUT_READER, load_intake
from mesh_mappings import REFERENCE_FIELDS, source_reference
from minimap import grayscale_png, terrain_png
from mission_equipment import EQUIPMENT_ROLES, mission_equipment, power_context
from perimeter import BOUNDARY_OPTIONS, DEFAULT_BOUNDARY_OPTIONS, plan_boundaries, read_boundary_recipe, terrain_elevations
from t2_reader import PROFILE, unpack_intake
from source_profiles import (NAMES, T1, T2, T2_GENERATOR_SCOPE_VERSION, T2_GENERATOR_SCOPE_VERSIONS,
                             UNREAL_INTAKE_VERSIONS, UNREAL_PROFILES)
from terrain_grid import preview_size


READER = PUBLIC_LAYOUT_READER
CLASSES = {
    "forcefieldbare", "interiorinstance", "item", "missionarea", "missiongroup",
    "simgroup", "sky", "spawnsphere", "staticshape", "sun", "terrainblock", "tsstatic", "turret",
}
LEGACY_CLASSES = {"interiorshape", "marker", "missioncenterpos", "planet", "simterrain", "teamgroup"}
NUMERIC_FIELDS = {
    "position": (3,), "rotation": (3, 4), "scale": (3,), "area": (4,), "squaresize": (1,),
    "direction": (3,), "color": (3, 4), "ambient": (3, 4),
    "fogdistance": (1,), "visibledistance": (1,), "fogcolor": (3, 4),
    "x": (1,), "y": (1,), "w": (1,), "h": (1,),
}
LABELS = {
    "boundary": "Playable area", "excluded": "Excluded object", "group": "Group",
    "marker": "Objective", "review": "Placement reference", "terrain": "Placeholder terrain",
}


def encode(data):
    return json.dumps(data, allow_nan=False, sort_keys=True).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def numeric_text(value, sizes):
    try:
        numbers = [float(part) for part in value.split()]
    except ValueError:
        return ""
    if len(numbers) not in sizes or not all(math.isfinite(number) for number in numbers):
        return ""
    return " ".join(format(number, ".17g") for number in numbers)


def public_objects(rows, *, legacy=False):
    _, _, power_groups = power_context(rows)
    aliases, identifiers, result = {}, {}, []
    for row in rows:
        ident = len(result) + 1
        source_id, status = row["id"], row["status"]
        kind = row["source_class"].casefold()
        source = {key.casefold(): value for key, value in row["properties"].items()
                  if isinstance(value, str)}
        properties = {key: numeric_text(source[key], sizes) for key, sizes in NUMERIC_FIELDS.items()
                      if key in source}
        if status == "boundary" and PLAYABLE_BOUNDS in row["properties"]:
            properties[PLAYABLE_BOUNDS] = row["properties"][PLAYABLE_BOUNDS]
        if (team := source.get("team", "").strip()) in {"0", "1", "2"}:
            properties["team"] = team
        elif "team" in source:
            properties["team"] = "0"
        datablock = source.get("datablock", "").casefold()
        role = EQUIPMENT_ROLES.get((kind, datablock))
        if role or (kind == "item" and datablock == "flag") or (legacy and kind == "marker" and datablock == "droppointmarker"):
            properties["datablock"] = datablock

        label = f"{LABELS[status]} {ident}"
        if status == "group":
            group = power_groups.get(source_id)
            team_group = row["name"].casefold()
            if legacy and kind == "teamgroup" and team_group in {"team0", "team1"}:
                label = team_group
            elif team_group in {"team1", "team2"}:
                label = f"Team{team_group[-1]}"
            elif group:
                label = f"Power group {ident} powered"
            if group and group["declared_power"] is not None:
                properties["providespower"] = "1" if group["declared_power"] else "0"
        elif role:
            label = f"{role.replace('_', ' ').capitalize()} {ident}"

        reference = source_reference(row)
        if reference and not reference.startswith("unrealmesh:"):
            suffix = {"interiorinstance": "dif", "interiorshape": "dis"}.get(kind, "dts")
            alias = aliases.setdefault(reference, f"model-{len(aliases) + 1:05d}.{suffix}")
            properties[REFERENCE_FIELDS[kind]] = alias

        entry = dict(id=ident, parent=identifiers.get(row["parent"]), status=status, name=label,
                     source_class=kind if kind in CLASSES or legacy and kind in LEGACY_CLASSES else "unclassified",
                     properties=properties,
                     reason=LABELS[status])
        if status == "marker":
            entry.update({key: row[key] for key in ("kind", "position_cm", "yaw")}, team=int(row["team"]))
            entry["name"] = f"Team {entry['team']} {entry['kind']} {ident}"
        conversion = row.get("conversion_class")
        if conversion and conversion != "unrealtriggerevent":
            entry["conversion_class"] = conversion
        if reference and reference.startswith("unrealmesh:"):
            entry["model_reference"] = aliases.setdefault(reference, f"unrealmesh:model-{len(aliases) + 1:05d}")
            entry["transform"] = row["transform"]
        identifiers[source_id] = ident
        result.append(entry)
    return result


def public_files(header, terrain, rows, *, boundary_options=None):
    profile = header["profile"]
    legacy = profile == T1
    unreal = profile in UNREAL_PROFILES.values()
    recipe = read_boundary_recipe(rows)
    if boundary_options is not None:
        if not isinstance(boundary_options, dict) or boundary_options.keys() - BOUNDARY_OPTIONS:
            raise ValueError("Invalid boundary options for public layout export.")
        options = {**DEFAULT_BOUNDARY_OPTIONS, **boundary_options}
        active = any(value is not None for value in plan_boundaries(header, terrain, rows, **options))
        recipe = ({"format": 1, "options": options,
                   "elevation_cm": recipe["elevation_cm"] if recipe else terrain_elevations(terrain)} if active else None)
    rows = public_objects(rows, legacy=legacy)
    if recipe:
        next(row for row in rows if row["status"] == "boundary")["properties"][BOUNDARY_RECIPE] = recipe
    # Keep the layout's units and terrain footprint. Generate all image bytes anew.
    ground = {key: terrain[key] for key in ("step_cm", "cm_per_m", "origin_cm")}
    grid = 256 if profile == T2 else 257
    ground.update(y_sign=terrain.get("y_sign", -1), material_layers=[])
    block = dict(height_m=[0] * grid**2, holes=[])
    if "tile_grid" in terrain:
        ground.update(tile_grid=[[0] * len(row) for row in terrain["tile_grid"]], terrain_blocks=[block])
        px_width, px_height = preview_size(ground)
    else:
        ground.update(block)
        px_width = px_height = grid
    files = {"objects.json": encode(rows), "terrain.json": encode(ground),
             "height.png": grayscale_png(bytes(2 * px_width * px_height), 16, px_width, px_height)}
    game = NAMES[profile]
    version = min(header["format"], 13) if "tile_grid" in terrain else 11
    public = dict(format=UNREAL_INTAKE_VERSIONS[0] if unreal else version if legacy else 2,
                  profile=profile, reader=READER, source_game=game,
                  name=f"Shared {game} Layout", profile_sha256=(digest(b"t1-public-layout-v1") if legacy else digest(PROFILE.read_bytes())),
                  sources=[dict(name="objects.json", origin="generated layout", sha256=digest(files["objects.json"]))],
                  coverage=dict(Counter(row["status"] for row in rows)))
    if "minimap" in header:
        public["minimap"] = header["minimap"]
        files["minimap.png"] = terrain_png(ground, public["minimap"]["image_bounds"])
    if "environment" in header:
        public.update(format=3 if profile == T2 else public["format"], environment=header["environment"])
    generator_scopes = header["format"] in T2_GENERATOR_SCOPE_VERSIONS
    equipment = mission_equipment(rows, ground, generator_scopes=generator_scopes) if profile == T2 else None
    if equipment and equipment["equipment"]:
        public["format"] = T2_GENERATOR_SCOPE_VERSION if generator_scopes else 7
        files[EQUIPMENT_FILE] = encode(equipment)
    public["files"] = {name: digest(data) for name, data in files.items()}
    files["manifest.json"] = encode(public)
    return files


def export_public_layout(manifest, destination, *, boundary_options=None):
    destination = Path(destination)
    if destination.suffix.casefold() != ".zip":
        raise ValueError("Save the public layout as a ZIP file.")
    files = public_files(*load_intake(manifest), boundary_options=boundary_options)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        temp = Path(directory)
        archive = temp / "layout.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, data in files.items():
                bundle.writestr(name, data)
        unpack_intake(archive, temp / "verified")
        os.replace(archive, destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_zip", type=Path)
    arguments = parser.parse_args()
    if arguments.output_zip.exists():
        parser.error("Choose a new output ZIP file.")
    print(export_public_layout(arguments.manifest, arguments.output_zip))
