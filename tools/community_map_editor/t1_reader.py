"""Convert a legacy mission and its terrain to a neutral intake."""

import argparse
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re

from mission_boundaries import soft_boundary_recipe
from intake_contract import PLAYABLE_BOUNDS, bounded, finite, validate_legacy_height_header, validate_minimap
from minimap import framing, terrain_png
from source_profiles import NAMES, T1
from t1_terrain import ARCHIVE_LIMIT, SCALE, Volume, load_binary, resource_name
from t2_reader import LIMIT, parse_mission, publish_intake, vector


READER = "t1-mission-v8"
GROUPS = {"simgroup", "missiongroup", "teamgroup"}
TEAM = re.compile(r"team([01])", re.I)
LEGACY_DECLARATION = re.compile(rb'^\s*instant\s+[A-Za-z_]\w*\s+"', re.I | re.M)


def legacy_mission(path):
    return LEGACY_DECLARATION.search(bounded(path, LIMIT)) is not None


def classify_legacy(rows):
    ancestry = {}
    spawns = {1: [], 2: []}
    for row in rows:
        cls = row["source_class"].casefold()
        name = row["name"].casefold()
        props = {key.casefold(): value for key, value in row["properties"].items()}
        team, drop_points, start_group = ancestry.get(row["parent"], (None, False, False))
        if cls == "teamgroup":
            match = TEAM.fullmatch(name)
            team = int(match[1]) + 1 if match else None
        if cls in GROUPS and name == "droppoints" and team:
            drop_points = True
        if cls in GROUPS and name == "start" and drop_points:
            start_group = True
        ancestry[row["id"]] = (team, drop_points, start_group)
        row.update(status="review", reason="Source object retained for review")
        if cls in GROUPS:
            row.update(status="group", reason="Source hierarchy retained")
        elif cls == "missionstatement":
            row.update(status="excluded", reason="Mission script statement retained without execution")
        elif cls == "simterrain":
            row.update(status="terrain", reason="SimTerrain source and coordinate frame")
        elif cls == "item" and props.get("datablock", "").casefold() == "flag":
            objective(row, "flag", team, props)
        elif (cls == "marker" and drop_points and props.get("datablock", "").casefold() == "droppointmarker"):
            objective(row, "spawn", team, props)
            if row["status"] == "marker":
                spawns[team].append((not start_group, row["id"], row))
    for candidates in spawns.values():
        for _, _, row in sorted(candidates)[24:]:
            row.update(status="excluded", reason="Beyond the 24 supported spawn markers for this team")
    return rows


def objective(row, kind, team, props):
    try:
        if team is None:
            raise ValueError("Missing a source team group.")
        x, y, z = vector(props.get("position", ""), 3)
        _, _, yaw = vector(props.get("rotation", "0 0 0"), 3)
        location = [SCALE * x, -SCALE * y, SCALE * z]
        if not all(finite(value, 2e6) for value in location):
            raise ValueError("The marker is outside the draft coordinate limits.")
        yaw_deg = -math.degrees(math.remainder(yaw, math.tau))
    except ValueError as error:
        row["reason"] = f"Invalid {kind} marker. {error}"
        return
    row.update(status="marker", reason="CTF " + kind, kind=kind, team=team, position_cm=location, yaw=yaw_deg)


def load_heightmap(raw, metrics):
    if (not isinstance(metrics, dict) or metrics.keys() != {"origin_m", "step_m", "height_range_m"}
            or not isinstance(metrics["origin_m"], list) or len(metrics["origin_m"]) != 3
            or not all(finite(value, 20000) for value in metrics["origin_m"])
            or not finite(metrics["step_m"], 200) or not 0 < metrics["step_m"]
            or not isinstance(metrics["height_range_m"], list) or len(metrics["height_range_m"]) != 2
            or not all(finite(value, 2048) for value in metrics["height_range_m"])
            or metrics["height_range_m"][0] >= metrics["height_range_m"][1]):
        raise ValueError("The heightmap needs a metric origin, grid spacing and increasing height range.")
    validate_legacy_height_header(raw)
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("Install Pillow in the Python environment used by the map launcher.") from exc
    low, high = metrics["height_range_m"]
    x, y, z = metrics["origin_m"]
    with Image.open(BytesIO(raw), formats=("PNG",)) as image:
        image.load()
        pixels = getattr(image, "get_flattened_data", image.getdata)()
        heights = [low + (high - low) * value / 65535 for value in pixels]
    return dict(height_m=heights, origin_cm=[SCALE * x, -SCALE * y, SCALE * z], step_cm=metrics["step_m"] * SCALE,
                cm_per_m=SCALE, y_sign=-1, holes=[], material_layers=[])


def terrain_properties(rows):
    terrains = [r for r in rows if r["status"] == "terrain"]
    if len(terrains) != 1:
        raise ValueError("A legacy mission needs one SimTerrain source object.")
    return {key.casefold(): value for key, value in terrains[0]["properties"].items()}


def source_environment(props):
    try:
        start, end = (float(props[key]) * SCALE for key in ("hazedistance", "visibledistance"))
        if 0 <= start < end <= 2e6:
            return {"fog": {"start_cm": start, "visible_cm": end}}
    except (KeyError, ValueError):
        pass
    return {}


def minimap_for_rows(rows):
    boundary = [r for r in rows if r["source_class"].casefold() == "missioncenterpos"]
    if len(boundary) != 1:
        return None
    row = boundary[0]
    props = {key.casefold(): value for key, value in row["properties"].items()}
    try:
        x, y, width, height = (float(props[key]) for key in ("x", "y", "w", "h"))
        if width <= 0 or height <= 0:
            raise ValueError("The mission rectangle must have positive dimensions.")
        result = framing([[x * SCALE, -(y + height) * SCALE], [(x + width) * SCALE, -y * SCALE]])
        validate_minimap(result)
    except (KeyError, ValueError, OverflowError):
        row["reason"] = "Invalid mission rectangle. Review before setting minimap framing."
        return None
    row.update(status="boundary", reason="Playable mission rectangle")
    row["properties"][PLAYABLE_BOUNDS] = result["image_bounds"]
    return result


def _binary_bounds(rows, minimap):
    points = [(r["position_cm"][0] / SCALE, -r["position_cm"][1] / SCALE)
              for r in rows if r["status"] == "marker"]
    if minimap:
        points.extend((x / SCALE, -y / SCALE) for x, y in minimap["image_bounds"])
    if not points:
        return None
    xs, ys = zip(*points)
    return (min(xs), min(ys)), (max(xs), max(ys))


def _auto_terrain(mission, name, reference, volume):
    default = mission.with_name(name + ".height.png")
    if default.is_file():
        return default
    reference = resource_name(reference)
    if volume is not None and volume.find(reference):
        return mission
    index = mission.with_name(Path(reference).name)
    for candidate in (index.with_suffix(".ted"), index, index.with_suffix(".vol")):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Select the terrain archive, index, or authored heightmap for {name}.")


def convert_legacy(mission, output, heightmap=None, *, mission_member=None, artwork=None, interiors=None):
    mission = Path(mission)
    name = mission.stem
    volume = None
    if mission.suffix.casefold() == ".vol":
        archive = bounded(mission, ARCHIVE_LIMIT)
        volume = Volume(archive)
        members = [entry[0] for entry in volume.entries.values() if entry[0].casefold().endswith(".mis")]
        if mission_member is None and len(members) == 1:
            mission_member = members[0]
        if mission_member not in members:
            raise ValueError("Select a mission from the Tribes volume.")
        raw = volume.read(mission_member)
        if len(raw) > LIMIT or not LEGACY_DECLARATION.search(raw):
            raise ValueError("The Tribes volume member is not a supported legacy mission.")
        name = Path(mission_member).stem
        sources = [(mission.name, mission.name, archive), (mission_member, f"{mission.name}:{mission_member}", raw)]
    else:
        raw = bounded(mission, LIMIT)
        sources = [(mission.name, mission.name, raw)]
    rows = classify_legacy(parse_mission(raw, legacy=True))
    props = terrain_properties(rows)
    environment = source_environment(props)
    minimap = minimap_for_rows(rows)
    reference = props.get("tedfilename", "") or name + ".dtf"
    selected = Path(heightmap) if heightmap is not None else _auto_terrain(mission, name, reference, volume)
    if selected.suffix.casefold() == ".png":
        metadata = selected.with_suffix(".json")
        if not metadata.is_file():
            raise ValueError(f"The heightmap requires a companion metric file named {metadata.name}.")
        pix, frame = bounded(selected, LIMIT), bounded(metadata, 4096)
        terrain = load_heightmap(pix, json.loads(frame))
        for path, data in ((selected, pix), (metadata, frame)):
            sources.append((path.name, path.name, data))
    elif selected.suffix.casefold() in (".dtf", ".dtb", ".ted", ".vol"):
        origin = vector(props.get("position", ""), 3)
        if (not all(finite(v, 20000) for v in origin) or any(vector(props.get("rotation", "0 0 0"), 3))):
            raise ValueError("Binary Tribes terrain requires a finite position and unrotated source terrain.")
        terrain, pix, receipts = load_binary(selected, reference, origin, _binary_bounds(rows, minimap),
                                             volume=volume if selected == mission else None,
                                             mission_volume=(mission.name, volume) if volume and selected != mission else None)
        sources.extend(receipts)
    else:
        raise ValueError("Choose a Tribes .ted, .vol, .dtf, .dtb or metric PNG terrain source.")
    files = {"terrain.json": json.dumps(terrain, allow_nan=False).encode(), "height.png": pix}
    reader_files = [Path(__file__).with_name(file) for file in
                    ("t1_reader.py", "t1_terrain.py", "t1_lzh.py", "t1_materials.py", "t1_artwork.py",
                     "t1_bitmap.py", "t1_interior.py", "t1_mission_interiors.py", "t1_lighting.py",
                     "t1_dts.py", "t1_dts_motion.py", "t1_shape_bindings.py", "terrain_grid.py")]
    header = dict(format=13 if "tile_grid" in terrain else 11, profile=T1, source_game=NAMES[T1], reader=READER, name=name,
                  profile_sha256=sha256(b"".join(path.read_bytes() for path in reader_files)).hexdigest(),
                  sources=[dict(name=name, origin=origin, sha256=sha256(data).hexdigest())
                           for name, origin, data in sources],
                  coverage=dict(Counter(row["status"] for row in rows)))
    if environment:
        header["environment"] = environment
    if minimap is not None:
        header["minimap"] = minimap
        files["minimap.png"] = terrain_png(terrain, minimap["image_bounds"])
    if artwork is not None:
        from t1_artwork import collect_artwork

        records, images, receipts = collect_artwork(artwork, terrain["material_layers"])
        header.update(format=14, terrain_art=records)
        header["sources"].extend(receipts)
        files.update(images)
    if interiors is not None or volume is not None and any(name.endswith((".dis", ".dts", ".cs")) for name in volume.entries):
        from interior_lighting import LIGHTING_INDEX
        from model_textures import TEXTURE_INDEX
        from t1_mission_interiors import collect_models

        found, review, shapes, missing_shapes, shape_version, geometry, receipts = collect_models(
            rows, interiors, (mission.name, volume) if volume else None)
        if found or review:
            version = 17 if LIGHTING_INDEX in geometry else 16 if TEXTURE_INDEX in geometry else 15
            header.update(format=version, interiors=found, interior_review=review)
        if shapes or missing_shapes:
            header.update(format=shape_version, shapes=shapes, shape_review=missing_shapes)
        header["sources"].extend(receipts)
        files.update(geometry)
    if minimap is not None:
        soft_boundary_recipe(rows, terrain, game=NAMES[T1])  # Boundaries preset from the mission rectangle (#15)
    files["objects.json"] = json.dumps(rows, ensure_ascii=False).encode()
    header["files"] = {name: sha256(data).hexdigest() for name, data in files.items()}
    return publish_intake(header, files, Path(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mission", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--terrain", "--heightmap", dest="terrain", type=Path)
    parser.add_argument("--mission-member")
    parser.add_argument("--artwork", type=Path)
    parser.add_argument("--interiors", type=Path)
    args = parser.parse_args()
    print(convert_legacy(args.mission, args.output, args.terrain, mission_member=args.mission_member,
                         artwork=args.artwork, interiors=args.interiors))
