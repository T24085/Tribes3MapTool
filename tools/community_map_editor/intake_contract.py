"""Neutral intake validation shared between the reader and the UE editor."""

import hashlib
import json
from collections import Counter
from pathlib import Path
import re

from model_textures import (MAX_TEXTURE_BYTES, MAX_TEXTURE_INDEX, MAX_TEXTURES, TEXTURE_FILE, TEXTURE_INDEX,
                            placement_key, validate_bindings, validate_png)
from interior_lighting import LIGHTING_INDEX, LIGHTMAP_FILE, MAX_LIGHTMAP_BYTES, MAX_LIGHTMAPS, dif_lighting_key, validate_lighting
from mission_audio import (AUDIO_INDEX, AUDIO_MEMBER, MAX_AUDIO_BYTES, MAX_AUDIO_INDEX, MAX_CLIPS, MAX_WAVE_BYTES,
                           validate_audio_index, wave_metadata)
from mission_weather import WEATHER_FILE, mission_weather
from native_terrain_paint import MAX_PAINT_BYTES, MAX_PAINT_LAYERS, MAX_PAINT_PNG_BYTES, PAINT_FILE, decode_mask
from sky_intake import MAX_SKIES, MAX_SKY_BYTES, MAX_SKY_SLOTS, SKY_INDEX, SKY_PNG, validate_sky_artwork
from source_profiles import (ASCEND, ASCEND_ART_VERSIONS, ASCEND_BSP_INTAKE_VERSION,
                             ASCEND_COLLISION_VERSIONS, ASCEND_CONVEX_COLLISION_VERSION,
                             ASCEND_LEVEL_VERSIONS, ASCEND_PLACEMENT_ART_VERSION, ASCEND_PRIVATE_MESH_VERSIONS,
                             ASCEND_TERRAIN_PAINT_VERSION,
                             BRUSH_PREVIEW_REFERENCE, NAMES, T2, T2_ACOUSTIC_COLLISION_VERSION, T2_ACOUSTIC_VERSIONS,
                             T2_AUDIO_PROFILES_VERSION, T2_AUDIO_VERSIONS,
                             T2_DIF_ANIMATION_VERSION, T2_DIF_LOD_LIGHT_VERSION,
                             T2_DIF_LOD_VERSIONS, T2_DIF_VERSIONS, T2_GENERATOR_SCOPE_VERSION, T2_GENERATOR_SCOPE_VERSIONS,
                             UNREAL_INTAKE_VERSION,
                             UNREAL_INTAKE_VERSIONS, VENGEANCE, VENGEANCE_ART_INTAKE_VERSION,
                             VENGEANCE_BSP_MATERIALS_VERSION, VENGEANCE_MESH_MATERIALS_VERSION,
                             VENGEANCE_EXTENDED_SHADER_VERSIONS, VENGEANCE_MESH_VERSIONS, VENGEANCE_MODEL_VERSIONS,
                             VENGEANCE_BRUSH_VERSIONS, VENGEANCE_DOOR_VERSION, VENGEANCE_ELEVATOR_LINK_VERSION,
                             VENGEANCE_ELEVATOR_VERSION,
                             VENGEANCE_MOVER_PLAYBACK_VERSION,
                             PROXIMITY_CHAIN_MODES, VENGEANCE_PERMANENT_DOOR_VERSION, VENGEANCE_SURFACE_DETAIL_VERSION,
                             VENGEANCE_TRIGGER_CHAIN_VERSION, VENGEANCE_TRIGGER_DOOR_VERSION,
                             VENGEANCE_TERRAIN_VERSIONS, profiles_for_version)
from terrain_art import ART_FILE, MAX_ART_BYTES, MAX_ART_TEXTURES, validate_art
from terrain_grid import CELLS, VERTICES, legacy_holes, preview_size

BASE_FILES = r"objects\.json|terrain\.json|height\.png|material-(?:index|[0-7])\.png"
INTERIOR_FILE = re.compile(r"interior-[0-9]{4}\.json")
SHAPE_FILE = re.compile(r"shape-[0-9]{4}\.json")
MAX_INTAKE_INTERIORS = 128
MAX_INTAKE_INTERIOR_BYTES = 96 * 1024 * 1024
PLAYABLE_BOUNDS = "playable_bounds_cm"
BOUNDARY_RECIPE = "draft_boundary_recipe"
NATIVE_ENVIRONMENT_VERSION = 53
NATIVE_LOCAL_LIGHTS_VERSION = 54
VENGEANCE_VERTEX_ATTRIBUTES_VERSION = 55
ASCEND_VERTEX_ATTRIBUTES_VERSION = 56
VERTEX_ATTRIBUTES_VERSIONS = {VENGEANCE_VERTEX_ATTRIBUTES_VERSION, ASCEND_VERTEX_ATTRIBUTES_VERSION}
NATIVE_SCENE_ENVELOPES = {NATIVE_ENVIRONMENT_VERSION, NATIVE_LOCAL_LIGHTS_VERSION, *VERTEX_ATTRIBUTES_VERSIONS}
LATEST_INTAKE_VERSION = max(*NATIVE_SCENE_ENVELOPES, UNREAL_INTAKE_VERSION, *T2_DIF_VERSIONS,
                            *T2_GENERATOR_SCOPE_VERSIONS)
T1_SHAPE_INTAKE_VERSION = {4: 18, 5: 19, 6: 20, 7: 21}
EQUIPMENT_FILE = "equipment.json"
UNREAL_SCENE = "source-scene.json"
UNREAL_CLASSES = {"unrealterrain", "unrealbounds", "unrealctfmarker", "unrealmeshactor", "unrealtriggerevent", "unreallight"}
UNREAL_READER = "legacy-unreal-intake-v1"
PUBLIC_LAYOUT_READER = "t2-public-layout-v1"
MAX_MANIFEST_BYTES = 192 * 1024


def intake_filename(name, version):
    if version in UNREAL_INTAKE_VERSIONS:
        return (name in {"objects.json", "terrain.json", "height.png", "minimap.png", UNREAL_SCENE}
                or INTERIOR_FILE.fullmatch(name)
                or version in (*VENGEANCE_TERRAIN_VERSIONS, *VENGEANCE_MODEL_VERSIONS, ASCEND_TERRAIN_PAINT_VERSION)
                and PAINT_FILE.fullmatch(name)
                or version in (VENGEANCE_ART_INTAKE_VERSION, *VENGEANCE_MODEL_VERSIONS, ASCEND_TERRAIN_PAINT_VERSION)
                and ART_FILE.fullmatch(name)
                or version in (*VENGEANCE_MODEL_VERSIONS, *ASCEND_ART_VERSIONS, *ASCEND_COLLISION_VERSIONS,
                               *ASCEND_LEVEL_VERSIONS)
                and (name == TEXTURE_INDEX or TEXTURE_FILE.fullmatch(name)))
    if 11 <= version <= 21:
        return name in {"objects.json", "terrain.json", "height.png", "minimap.png"} or (
            version >= 14 and ART_FILE.fullmatch(name) is not None) or (
            version >= 15 and INTERIOR_FILE.fullmatch(name) is not None) or (
            version >= 16 and (name == TEXTURE_INDEX or TEXTURE_FILE.fullmatch(name) is not None)) or (
            version >= 17 and (name == LIGHTING_INDEX or LIGHTMAP_FILE.fullmatch(name) is not None)) or (
            version >= 18 and SHAPE_FILE.fullmatch(name) is not None)
    return (re.fullmatch(BASE_FILES, name) or version >= 2 and name == "minimap.png"
            or version >= 4 and INTERIOR_FILE.fullmatch(name)
            or version >= 5 and SHAPE_FILE.fullmatch(name)
            or version >= 6 and (name == TEXTURE_INDEX or TEXTURE_FILE.fullmatch(name))
            or version >= 7 and name == EQUIPMENT_FILE
            or version >= 8 and name == WEATHER_FILE
            or version >= 9 and (name == SKY_INDEX or SKY_PNG.fullmatch(name))
            or version >= 10 and (name == LIGHTING_INDEX or LIGHTMAP_FILE.fullmatch(name))
            or version in T2_AUDIO_VERSIONS and (name == AUDIO_INDEX or AUDIO_MEMBER.fullmatch(name)))


def max_intake_files(version):
    if version in UNREAL_INTAKE_VERSIONS:
        if version in (*VENGEANCE_MODEL_VERSIONS, ASCEND_TERRAIN_PAINT_VERSION):
            return 6 + MAX_INTAKE_INTERIORS + 2 * MAX_PAINT_LAYERS + MAX_TEXTURES
        if version in (*ASCEND_ART_VERSIONS, *ASCEND_COLLISION_VERSIONS, ASCEND_BSP_INTAKE_VERSION):
            return 6 + MAX_INTAKE_INTERIORS + MAX_TEXTURES
        return (5 + MAX_INTAKE_INTERIORS + (MAX_PAINT_LAYERS if version in VENGEANCE_TERRAIN_VERSIONS else 0)
                + (MAX_PAINT_LAYERS if version == VENGEANCE_ART_INTAKE_VERSION else 0))
    if 15 <= version <= 21:
        return (4 + MAX_ART_TEXTURES + MAX_INTAKE_INTERIORS
                + (1 + MAX_TEXTURES if version >= 16 else 0)
                + (1 + MAX_LIGHTMAPS if version >= 17 else 0)
                + (MAX_INTAKE_INTERIORS if version >= 18 else 0))
    if version == 14:
        return 4 + MAX_ART_TEXTURES
    if version in (11, 12, 13):
        return 4
    if version >= 6:
        sky_files = 1 + MAX_SKIES * MAX_SKY_SLOTS if version >= 9 else 0
        lighting_files = 1 + MAX_LIGHTMAPS if version >= 10 else 0
        audio_files = 1 + MAX_CLIPS if version in T2_AUDIO_VERSIONS else 0
        return (16 + int(version >= 7) + int(version >= 8) + MAX_INTAKE_INTERIORS + MAX_TEXTURES
                + sky_files + lighting_files + audio_files)
    return 15 + MAX_INTAKE_INTERIORS if version in (4, 5) else 12 + version


MAX_INTAKE_FILES = max(map(max_intake_files, range(1, LATEST_INTAKE_VERSION + 1)))
LEGACY_HEIGHT_HEADER = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000101000001011000")


def validate_legacy_height_header(raw):
    if len(raw) < 33 or not raw.startswith(LEGACY_HEIGHT_HEADER):
        raise ValueError("Choose a 257 × 257, 16-bit grayscale PNG heightmap.")


def intake_file_limit(name, version):
    if version in T2_AUDIO_VERSIONS:
        if name == AUDIO_INDEX:
            return MAX_AUDIO_INDEX
        if AUDIO_MEMBER.fullmatch(name):
            return MAX_WAVE_BYTES
    if name == UNREAL_SCENE and version in UNREAL_INTAKE_VERSIONS:
        return 64 * 1024 * 1024
    if name == "terrain.json" and 12 <= version <= LATEST_INTAKE_VERSION:
        return 64 * 1024 * 1024
    return MAX_TEXTURE_INDEX if name == TEXTURE_INDEX else 16 * 1024 * 1024


def bounded(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{path.name} exceeds the supported size.")
    return data


def validate_legacy_grid(grid, heights):
    if (not isinstance(grid, dict)
            or set(grid) != {"block_version", "tile", "grid_size", "block_id", "material_list", "height_range_m",
                             "material_flags", "material_indices"}
            or type(grid["block_version"]) is not int or grid["block_version"] not in (0, 4, 5)
            or type(grid["block_id"]) is not int or not 0 <= grid["block_id"] < 2**31
            or not all(isinstance(grid[key], list) and len(grid[key]) == 2
                       and all(type(value) is int for value in grid[key]) for key in ("tile", "grid_size"))
            or not all(0 <= tile < size <= 64 for tile, size in zip(grid["tile"], grid["grid_size"]))
            or grid["material_list"] is not None and (not isinstance(grid["material_list"], str)
                                                    or not 1 <= len(grid["material_list"]) <= 255
                                                    or not grid["material_list"].isprintable())
            or not isinstance(grid["height_range_m"], list) or len(grid["height_range_m"]) != 2
            or not all(finite(value, 2048) for value in grid["height_range_m"])
            or grid["height_range_m"] != [min(heights), max(heights)]
            or not all(isinstance(grid[key], str) and re.fullmatch(r"[0-9a-f]{131072}", grid[key])
                       for key in ("material_flags", "material_indices"))):
        raise ValueError("Invalid binary Tribes terrain provenance.")


def finite(value, bound):
    return type(value) in (float, int) and abs(value) <= bound


def validate_height_block(block, samples, *, legacy):
    heights = block.get("height_m")
    if not isinstance(heights, list) or len(heights) != samples or not all(finite(z, 2048) for z in heights):
        raise ValueError("Invalid heightfield.")
    holes = block.get("holes")
    if not isinstance(holes, list) or any(type(v) is not int or not 0 <= v < CELLS**2 for v in holes):
        raise ValueError("Invalid terrain hole.")
    if "legacy_grid" in block:
        if not legacy:
            raise ValueError("Binary Tribes terrain provenance requires the Tribes 1 profile.")
        validate_legacy_grid(block["legacy_grid"], heights)


def validate_tiled_heightfield(terrain, height_header, *, painted, legacy=True):
    grid, blocks = terrain.get("tile_grid"), terrain.get("terrain_blocks")
    if (not isinstance(grid, list) or not 1 <= len(grid) <= 64
            or not isinstance(grid[0], list) or not 1 <= len(grid[0]) <= 64
            or legacy and not painted and len(grid) * len(grid[0]) < 2
            or not isinstance(blocks, list) or not 1 <= len(blocks) <= len(grid) * len(grid[0])
            or any(not isinstance(row, list) or len(row) != len(grid[0]) or any(
                type(slot) is not int or not 0 <= slot < len(blocks) for slot in row) for row in grid)
            or {slot for row in grid for slot in row} != set(range(len(blocks)))):
        raise ValueError("Invalid tiled terrain grid or block references.")
    material_slots = set()
    fields = {"height_m", "holes", "legacy_grid"} if legacy else {"height_m", "holes", "turned_quads"}
    for block in blocks:
        if not isinstance(block, dict) or block.keys() - fields:
            raise ValueError("Invalid tiled terrain block.")
        validate_height_block(block, VERTICES**2, legacy=legacy)
        if "turned_quads" in block:
            turned = block["turned_quads"]
            if (not isinstance(turned, list) or not all(type(cell) is int and 0 <= cell < CELLS**2 for cell in turned)
                    or any(a >= b for a, b in zip(turned, turned[1:])) or set(turned).intersection(block["holes"])):
                raise ValueError("Invalid terrain triangle diagonals.")
        if painted and "legacy_grid" in block:
            source = block["legacy_grid"]
            if block["holes"] != legacy_holes(bytes.fromhex(source["material_flags"])):
                raise ValueError("Tribes terrain holes do not match the source flags.")
            material_slots.update(bytes.fromhex(source["material_indices"]))
    if painted:
        from terrain_layers import layer_choices

        if (material_slots and any("legacy_grid" not in block for block in blocks)
                or {slot for slot, _, _ in layer_choices(terrain)} != material_slots):
            raise ValueError("Tribes terrain layer names do not match the source material slots.")
    width, height = preview_size(terrain)
    prefix = LEGACY_HEIGHT_HEADER[:16] + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x10\x00"
    if len(height_header) != 33 or not height_header.startswith(prefix):
        raise ValueError("Invalid tiled terrain height preview.")


def validate_native_paint(header, terrain, scene, mask_metadata):
    field = header["terrain_paint"]
    ascend = header["format"] == ASCEND_TERRAIN_PAINT_VERSION
    keys = {"vertices", "masks", "base_slot", "hidden_slots"} if ascend else {"vertices", "masks"}
    if (not isinstance(field, dict) or set(field) != keys
            or not isinstance(field["vertices"], list) or len(field["vertices"]) != 2
            or not isinstance(field["masks"], dict) or not field["masks"]):
        raise ValueError("Invalid native terrain paint inventory.")
    grid = terrain["tile_grid"]
    if any(type(axis) is not int or not (tiles - 1) * CELLS + 2 <= axis <= tiles * CELLS + 1
           for axis, tiles in zip(field["vertices"], (len(grid[0]), len(grid)))):
        raise ValueError("Native terrain paint dimensions do not match its tile grid.")
    ground = scene.get("native_terrain") if isinstance(scene, dict) else None
    if (not isinstance(ground, dict) or ground.get("vertices") != field["vertices"]
            or not isinstance(ground.get("paint_layers"), list) or not ground["paint_layers"]):
        raise ValueError("Native terrain paint does not match its source terrain.")
    names, layers, hidden_slots = {}, [], []
    for item in ground["paint_layers"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid source terrain layer.")
        slot, surface = item.get("slot"), item.get("surface")
        if (type(slot) is not int or not 0 <= slot < MAX_PAINT_LAYERS or str(slot) in names
                or not isinstance(surface, str) or not 1 <= len(surface) <= 255 or not surface.isprintable()):
            raise ValueError("Invalid source terrain layer name or slot.")
        filename = f"terrain-alpha-{slot:02d}.png"
        receipt = item.get("package")
        if receipt is not None and (
            not isinstance(receipt, dict) or set(receipt) != {"name", "sha256"}
            or not any(all(receipt[key] == source[key] for key in receipt) for source in header["sources"][1:])
        ):
            raise ValueError("Native terrain alpha package does not match the intake provenance.")
        dimensions = item.get("dimensions")
        if (not isinstance(dimensions, list) or len(dimensions) != 2 or item.get("filename") != filename
                or mask_metadata.get(filename) != (*dimensions, item.get("alpha_sha256"))):
            raise ValueError("Native terrain alpha pixels do not match the source layer.")
        if ascend:
            if (dimensions != field["vertices"] or type(item.get("hidden")) is not bool
                    or type(item.get("alpha_map_index")) is not int
                    or not -1 <= item["alpha_map_index"] < MAX_PAINT_LAYERS):
                raise ValueError("Invalid Ascend terrain layer metadata.")
            if item["hidden"]:
                hidden_slots.append(slot)
        names[str(slot)] = filename
        layers.append([slot, surface])
    if field["masks"] != names or set(names.values()) != set(mask_metadata) or terrain["material_layers"] != sorted(layers):
        raise ValueError("Native terrain paint does not match its material slots.")
    if ascend:
        base = 0 if "0" in names and 0 not in hidden_slots else None
        if ((field["base_slot"] is not None and type(field["base_slot"]) is not int)
                or field["base_slot"] != base or not isinstance(field["hidden_slots"], list)
                or any(type(slot) is not int for slot in field["hidden_slots"])
                or field["hidden_slots"] != sorted(hidden_slots)):
            raise ValueError("Ascend paint visibility does not match its layers.")
    if "terrain_uv" in header:
        projection = header["terrain_uv"]
        slots = {slot for slot, bind in header["terrain_art"].items() if "file" in bind}
        if (not isinstance(projection, dict) or not slots or projection.keys() - slots
                or any(not isinstance(axes, list) or len(axes) != 2 or any(
                    not isinstance(axis, list) or len(axis) != 4 or not all(finite(value, 1e6) for value in axis)
                    for axis in axes) for axes in projection.values())
                or projection != {str(row["slot"]): row["projection"] for row in ground["paint_layers"] if "projection" in row}):
            raise ValueError("Invalid native terrain source artwork projection.")


def validate_environment(settings, *, unreal=False):
    def vector(value):
        return isinstance(value, list) and len(value) == 3 and all(finite(item, 1) for item in value)

    if not isinstance(settings, dict) or not settings or settings.keys() - {"sun", "fog"}:
        raise ValueError("Invalid intake environment.")
    if "sun" in settings:
        sun = settings["sun"]
        scales = {"intensity_scale", "ambient_scale"} if unreal else set()
        directions = {"direction", "color", "ambient"}
        if (not isinstance(sun, dict) or not sun or sun.keys() - (directions | scales)
                or not all(vector(sun[key]) for key in sun.keys() & directions)
                or any(not finite(sun[key], 8) or sun[key] < 0 for key in sun.keys() & scales)
                or any(min(sun[key]) < 0 for key in sun.keys() & {"color", "ambient"})
                or ("direction" in sun and abs(sum(v*v for v in sun["direction"]) - 1) > 1e-6)):
            raise ValueError("Invalid intake sun.")
    if "fog" in settings:
        fog = settings["fog"]
        distances = {"start_cm", "visible_cm"}
        native_ranges = {"density": (0, 5), "height_falloff": (0, 2), "max_opacity": (0, 1),
                         "height_cm": (-2e6, 2e6)} if unreal else {}
        if (not isinstance(fog, dict) or "start_cm" not in fog
                or fog.keys() - distances - {"color"} - native_ranges.keys()
                or any(not finite(fog[key], 2e6) or fog[key] < 0 for key in fog.keys() & distances)
                or any(not finite(fog[key], max(abs(low), high)) or not low <= fog[key] <= high
                       for key, (low, high) in native_ranges.items() if key in fog)
                or ("color" in fog and (not vector(fog["color"]) or min(fog["color"]) < 0))):
            raise ValueError("Invalid intake fog.")


def playable_bounds(header, rows):
    """Get the gameplay rectangle in Unreal centimetres, including legacy intakes."""
    settings = header.get("minimap")
    boundaries = [row for row in rows if row["status"] == "boundary"]
    if settings is None or len(boundaries) != 1:
        raise ValueError("A playable MissionArea is required for a boundary.")
    bounds = boundaries[0].get("properties", {}).get(PLAYABLE_BOUNDS, settings["image_bounds"])
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
            or any(not isinstance(point, (list, tuple)) or len(point) != 2
                   or not all(finite(coordinate, 10_000_000) for coordinate in point) for point in bounds)
            or not all(low < high for low, high in zip(*bounds))):
        raise ValueError("Invalid playable bounds in centimetres.")
    return bounds


def validate_minimap(settings):
    def values(value, length, low, high):
        return (isinstance(value, list) and len(value) == length
                and all(finite(v, max(abs(low), abs(high))) and low <= v <= high for v in value))

    if not isinstance(settings, dict) or settings.keys() != {
        "center", "extent", "scale", "rotation_deg", "zoom", "image", "image_bounds",
    }:
        raise ValueError("Invalid intake minimap settings.")
    bounds, zoom = settings["image_bounds"], settings["zoom"]
    if (
        settings["image"] != "minimap.png"
        or not values(settings["center"], 2, -10_000_000, 10_000_000)
        or not values(settings["extent"], 2, 100, 10_000_000)
        or not values([settings["scale"]], 1, 0.1, 10)
        or not finite(settings["rotation_deg"], 360)
        or not isinstance(bounds, list) or len(bounds) != 2
        or not all(values(corner, 2, -10_000_000, 10_000_000) for corner in bounds)
        or not all(a < b for a, b in zip(*bounds))
        or not isinstance(zoom, dict) or zoom.keys() != {"min", "default", "max"}
        or not values([zoom["min"], zoom["default"], zoom["max"]], 3, 0.05, 32)
        or not zoom["min"] <= zoom["default"] <= zoom["max"]
    ):
        raise ValueError("Invalid intake minimap settings.")


def load_intake_geometry(folder, filename, digest):
    from interior_geometry import MAX_GEOMETRY_BYTES, UNREAL_GEOMETRY, validate_geometry

    shape = SHAPE_FILE.fullmatch(filename)
    if not shape and not INTERIOR_FILE.fullmatch(filename):
        raise ValueError("Invalid model geometry filename.")
    path = (folder / filename).resolve(strict=True)
    if path.parent != folder:
        raise ValueError("Model geometry file outside the intake.")
    data = bounded(path, MAX_GEOMETRY_BYTES)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Intake checksum mismatch: " + filename)
    geometry = validate_geometry(json.loads(data))
    if geometry["format"] not in ((2, 4, 5, 6, 7) if shape else {1, 3, *UNREAL_GEOMETRY}):
        raise ValueError("Geometry format does not match its intake filename.")
    return geometry, len(data)


def validate_model_index(header, inventory, *, field, review_field, pattern, prefix, supported, required):
    found, review = header.get(field), header.get(review_field)
    files = {filename for filename in inventory if pattern.fullmatch(filename)}
    present = field in header or review_field in header or files
    version = header["format"]
    if not present and version not in required:
        return {}
    if (version not in supported or not isinstance(found, dict) or not isinstance(review, dict)
            or not (found or review) or found.keys() & review.keys()
            or len(found) + len(review) > MAX_INTAKE_INTERIORS
            or not all(isinstance(name, str) and pattern.fullmatch(name) for name in found.values())
            or len(found) != len(files) or set(found.values()) != files
            or not all(isinstance(message, str) and 0 < len(message) <= 240 for message in review.values())
            or not all(ref.startswith(prefix) for ref in found.keys() | review.keys())):
        raise ValueError(f"Invalid intake {field} inventory.")
    return {name: ref for ref, name in found.items()}


def intake_scene_format(header):
    version = header.get("format") if isinstance(header, dict) else None
    if type(version) is not int or not 1 <= version <= LATEST_INTAKE_VERSION:
        raise ValueError("Unsupported intake format.")
    if version in NATIVE_SCENE_ENVELOPES:
        scene = header.get("scene_format")
        if (type(scene) is not int or scene not in UNREAL_INTAKE_VERSIONS
                or version == NATIVE_ENVIRONMENT_VERSION and "environment" not in header
                or version == NATIVE_LOCAL_LIGHTS_VERSION and
                (header.get("profile") != ASCEND or header.get("reader") != UNREAL_READER)
                or version == VENGEANCE_VERTEX_ATTRIBUTES_VERSION and
                (header.get("profile") != VENGEANCE or header.get("reader") != UNREAL_READER
                 or scene not in (*VENGEANCE_MESH_VERSIONS, *VENGEANCE_EXTENDED_SHADER_VERSIONS,
                                  *VENGEANCE_BRUSH_VERSIONS))
                or version == ASCEND_VERTEX_ATTRIBUTES_VERSION and
                (header.get("profile") != ASCEND or header.get("reader") != UNREAL_READER
                 or scene not in ASCEND_PRIVATE_MESH_VERSIONS)):
            raise ValueError("Invalid versioned native scene intake.")
        return scene
    if "scene_format" in header:
        raise ValueError("Unexpected scene format for this intake.")
    return version


def intake_header(manifest):
    header = json.loads(bounded(manifest, MAX_MANIFEST_BYTES))
    scene_format = intake_scene_format(header)
    if (
        not isinstance(header.get("profile"), str)
        or header["profile"] not in profiles_for_version(scene_format)
        or header["profile"] != T2 and header.get("source_game") != NAMES[header["profile"]]
    ):
        raise ValueError("Unsupported intake format or profile.")
    return header


def validate_unreal_row(row, *, allow_local_lights=False):
    conversion_class = row.get("conversion_class")
    if "conversion_class" in row and (not isinstance(conversion_class, str) or conversion_class not in UNREAL_CLASSES):
        raise ValueError("Invalid Unreal conversion class.")
    required = {"marker": "unrealctfmarker", "terrain": "unrealterrain", "boundary": "unrealbounds"}
    if row["status"] in required and conversion_class != required[row["status"]]:
        raise ValueError("Unreal object status does not match its conversion class.")
    if row["status"] == "marker" and type(row.get("team")) is not int:
        raise ValueError("Unreal CTF teams must be integer team IDs.")
    placement = row.get("transform")
    if "transform" in row:
        if not isinstance(placement, dict) or set(placement) != {"position_cm", "rotation_xyzw", "scale"}:
            raise ValueError("Invalid Unreal actor placement.")
        for field, count, limit in (("position_cm", 3, 2e6), ("rotation_xyzw", 4, 1), ("scale", 3, 1000)):
            values = placement[field]
            if not isinstance(values, list) or len(values) != count or not all(finite(v, limit) for v in values):
                raise ValueError("Invalid Unreal actor placement values.")
        if abs(sum(v*v for v in placement["rotation_xyzw"]) - 1) > 1e-6 or 0 in placement["scale"]:
            raise ValueError("Invalid Unreal actor rotation or scale.")
    if conversion_class == "unrealmeshactor":
        model = row.get("model_reference")
        if row["status"] != "review" or placement is None or not isinstance(model, str) or not re.fullmatch(
                r"unrealmesh:[a-z0-9_./:$-]{1,512}", model):
            raise ValueError("Invalid Unreal source mesh mapping row.")
    elif "model_reference" in row:
        raise ValueError("Unreal source mesh references require a mapped mesh actor.")
    if conversion_class == "unrealtriggerevent" and (row["status"] != "review" or placement is not None
            or row["source_class"].casefold() != "engine.trigger"):
        raise ValueError("An Unreal trigger record must be a review-only Engine.Trigger actor.")
    if conversion_class == "unreallight" or "local_light" in row:
        from native_local_lights import validate_local_light

        if not allow_local_lights or conversion_class != "unreallight":
            raise ValueError("Local light records require a compatible native intake.")
        validate_local_light(row)


def validate_brush_records(scene, rows, references, *, version):
    brushes = scene.get("native_brushes")
    if not isinstance(brushes, dict) or not brushes or len(brushes) > len(rows):
        raise ValueError("Missing or invalid Vengeance brush records.")
    playback = version >= VENGEANCE_MOVER_PLAYBACK_VERSION
    sender_records = version >= VENGEANCE_ELEVATOR_LINK_VERSION
    actor_exports = {}
    if sender_records:
        actors = scene.get("actors")
        if not isinstance(actors, list):
            raise ValueError("Vengeance trigger senders need the source actor inventory.")
        actor_exports = {actor["line"]: actor for actor in actors
                         if isinstance(actor, dict) and type(actor.get("line")) is int}
    has_lift_sender = has_door_sender = has_permanent_door = False

    def placement_key(reference, source_class, transform, source_index):
        return reference, source_class, json.dumps(transform, sort_keys=True, separators=(",", ":")), source_index

    matched_rows = [row for row in rows if row.get("model_reference") in references]
    if {row["model_reference"] for row in matched_rows} != references:
        raise ValueError("Vengeance brush geometry is missing its draft actor.")
    placements = Counter(placement_key(row["model_reference"], row["source_class"].casefold(), row["transform"],
                                       row["properties"].get("Brush export") if playback else None)
                         for row in matched_rows)
    fields = {"type", "reference", "transform", "motion"}
    if playback:
        fields.add("behavior")
    for index, record in brushes.items():
        if (not re.fullmatch(r"[1-9][0-9]{0,5}", index) or not isinstance(record, dict)
                or record.keys() != fields
                or not isinstance(record["reference"], str) or record["reference"] not in references
                or not isinstance(record["type"], str)):
            raise ValueError("Invalid Vengeance brush source placement.")
        source_class = {"mover": "engine.mover", "elevator": "movement.elevatorvolume"}.get(record["type"])
        match = placement_key(record["reference"], source_class, record["transform"], index if playback else None)
        if not placements[match]:
            raise ValueError("Vengeance brush source record does not match its draft actor.")
        placements[match] -= 1
        if source_class == "engine.mover":
            validate_mover_motion(record["motion"])
        elif record["motion"] is not None:
            raise ValueError("A Vengeance elevator-volume preview cannot have mover keys.")
        if playback:
            state = record["motion"]["initial_state"].casefold() if record["motion"] else ""
            behavior = record["behavior"]
            timed_door = version >= VENGEANCE_DOOR_VERSION and state == "bumpopentimed"
            linked_door = (version >= VENGEANCE_TRIGGER_DOOR_VERSION and state == "triggeropentimed"
                           and isinstance(behavior, dict) and "trigger_senders" in behavior)
            permanent_door = (version >= VENGEANCE_PERMANENT_DOOR_VERSION and state in ("bumpopentimed", "triggeropentimed")
                              and isinstance(behavior, dict) and "opens_once" in behavior)
            validate_brush_behavior(record["type"], behavior, timed_door=timed_door, permanent_door=permanent_door,
                                    lift_links=version >= VENGEANCE_ELEVATOR_VERSION, sender_records=sender_records,
                                    linked_door=linked_door)
            has_permanent_door |= permanent_door
            if sender_records and record["type"] == "elevator":
                has_lift_sender |= validate_trigger_senders(behavior["trigger_senders"], actor_exports,
                                                            behavior["trigger_tag"], behavior["incoming_trigger"])
            if linked_door:
                has_door_sender |= validate_trigger_senders(record["behavior"]["trigger_senders"], actor_exports,
                                                            record["motion"]["events"].get("tag", "Mover"), True)
    if any(placements.values()):
        raise ValueError("Vengeance brush preview is missing its source record.")
    if ((version == VENGEANCE_ELEVATOR_LINK_VERSION and not has_lift_sender)
            or (version == VENGEANCE_TRIGGER_DOOR_VERSION and not has_door_sender)):
        raise ValueError("A linked Vengeance intake needs a saved event sender for its receiver type.")
    if version == VENGEANCE_PERMANENT_DOOR_VERSION and not has_permanent_door:
        raise ValueError("A version 37 Vengeance intake needs a door that stays open.")
    if version == VENGEANCE_TRIGGER_CHAIN_VERSION:
        validate_trigger_chains(scene.get("trigger_chains"), brushes, actor_exports)


def validate_trigger_chains(chains, brushes, actors):
    if (not isinstance(chains, dict) or not 1 <= len(chains) <= 1024
            or not all(re.fullmatch(r"[1-9][0-9]{0,5}", index) for index in chains)):
        raise ValueError("Invalid Vengeance trigger chain inventory.")
    used = {}
    rows = [brush["behavior"].get("trigger_senders", ()) for brush in brushes.values()]
    for senders in (*rows, *chains.values()):
        if not isinstance(senders, (list, tuple)) or len(senders) > 1024:
            raise ValueError("Invalid Vengeance trigger chain link list.")
        for sender in senders:
            if not isinstance(sender, dict) or type(sender.get("source_export")) is not int:
                raise ValueError("Invalid Vengeance chained sender.")
            trigger = sender.get("proximity")
            if trigger is None:
                continue
            known = used.setdefault(sender["source_export"], sender)
            if known != sender:
                raise ValueError("A chained trigger has inconsistent saved settings.")
    for index, senders in chains.items():
        trigger = used.get(int(index), {}).get("proximity")
        if (not isinstance(trigger, dict) or not isinstance(trigger.get("tag"), str)
                or str(trigger.get("state", "")).casefold() not in PROXIMITY_CHAIN_MODES
                or trigger.get("receives_events") is not True):
            raise ValueError("A chain receiver must be a saved stateful proximity trigger.")
        validate_trigger_senders(senders, actors, trigger["tag"], True)
    pending = [str(sender["source_export"]) for senders in rows for sender in senders]
    connected = set()
    while pending:
        index = pending.pop()
        if index in chains and index not in connected:
            connected.add(index)
            pending.extend(str(sender["source_export"]) for sender in chains[index])
    if connected != chains.keys():
        raise ValueError("Vengeance trigger chains must lead to a saved brush receiver.")


def brush_vector(value, limit):
    return isinstance(value, list) and len(value) == 3 and all(finite(point, limit) for point in value)


def brush_angles(value):
    return isinstance(value, list) and len(value) == 3 and all(type(angle) is int and -(2**31) <= angle < 2**31
                                                              for angle in value)


def validate_brush_behavior(kind, behavior, *, timed_door=False, lift_links=False, sender_records=False,
                            linked_door=False, permanent_door=False):
    if not isinstance(behavior, dict):
        raise ValueError("Invalid Vengeance brush behavior.")
    if kind == "mover":
        flags = {"oscillating", "shortest_rotation", "hidden", "linked"}
        fields = flags | {"saved_rotation_units", "pivot_m", "encroach_type"}
        if timed_door:
            fields.add("bump_type")
        if linked_door:
            fields.add("trigger_senders")
        if permanent_door:
            fields.add("opens_once")
        if (behavior.keys() != fields
                or not all(type(behavior[name]) is bool for name in flags)
                or not brush_angles(behavior["saved_rotation_units"]) or not brush_vector(behavior["pivot_m"], 20_000)
                or type(behavior["encroach_type"]) is not int or not 0 <= behavior["encroach_type"] < 256
                or permanent_door and behavior["opens_once"] is not True
                or timed_door and (type(behavior["bump_type"]) is not int or not 0 <= behavior["bump_type"] < 256)):
            raise ValueError("Invalid Vengeance mover behavior.")
    else:
        flags = {"active", "hidden", "moves_projectiles"}
        if lift_links:
            flags.add("incoming_trigger")
        physics = {"ground_friction", "fluid_friction", "terminal_speed_m_s"}
        links = {"render_material", "trigger_tag", "trigger_event", "associated_actor", "associated_actor_tag"}
        fields = flags | physics | links | {"gravity_m_s2", "zone_velocity_m_s", "priority"}
        if sender_records:
            fields.add("trigger_senders")
        if (behavior.keys() != fields
                or not all(type(behavior[name]) is bool for name in flags)
                or not all(finite(behavior[name], 1e6) and behavior[name] >= 0 for name in physics)
                or not brush_vector(behavior["gravity_m_s2"], 20_000)
                or not brush_vector(behavior["zone_velocity_m_s"], 20_000)
                or type(behavior["priority"]) is not int or not -(2**31) <= behavior["priority"] < 2**31
                or not all(isinstance(behavior[name], str) and len(behavior[name]) <= 512 for name in links)):
            raise ValueError("Invalid Vengeance elevator physics.")


def validate_trigger_senders(senders, actors, receiver_tag, has_incoming):
    if not isinstance(senders, list) or bool(senders) != has_incoming or len(senders) > 1024:
        raise ValueError("Invalid Vengeance trigger sender list.")
    fields = {"source_export", "source_name", "source_class", "event", "property", "proximity", "review"}
    for sender in senders:
        if (not isinstance(sender, dict) or sender.keys() != fields or type(sender["source_export"]) is not int
                or not all(isinstance(sender[name], str) and len(sender[name]) <= 512
                           for name in fields - {"source_export", "proximity"})):
            raise ValueError("Invalid Vengeance trigger sender record.")
        actor = actors.get(sender["source_export"], {})
        if (actor.get("source_class") != sender["source_class"] or actor.get("name") != sender["source_name"]
                or sender["event"] != receiver_tag.casefold()
                or not re.fullmatch(r"[a-z0-9_]{0,50}event", sender["property"])):
            raise ValueError("Vengeance trigger sender does not match its source actor or event.")
        proximity = sender["proximity"]
        if proximity is None:
            if not sender["review"]:
                raise ValueError("A Vengeance sender without decoded proximity needs a review reason.")
            continue
        flags = {"active", "once", "collision", "blocking", "touch", "receives_events"}
        sizes = {"radius_cm", "half_height_cm"}
        timers = {"repeat_seconds", "delay_seconds"}
        if (sender["source_class"].casefold() != "engine.trigger" or sender["property"] != "event"
                or not isinstance(proximity, dict)
                or proximity.keys() != flags | sizes | timers | {"position_cm", "activation", "state", "tag"}
                or not all(type(proximity[key]) is bool for key in flags)
                or not all(finite(proximity[key], 20_000) and proximity[key] > 0 for key in sizes)
                or not all(finite(proximity[key], 3600) and proximity[key] >= 0 for key in timers)
                or not brush_vector(proximity["position_cm"], 2e6)
                or type(proximity["activation"]) is not int or not 0 <= proximity["activation"] < 256
                or not all(isinstance(proximity[key], str) and len(proximity[key]) <= 512 for key in ("state", "tag"))):
            raise ValueError("Invalid Vengeance proximity trigger.")
    return bool(senders)


def validate_mover_motion(motion):
    fields = {"keys", "current_key", "base_position_m", "base_rotation_units", "timing_seconds", "initial_state",
              "glide_type", "events"}
    if not isinstance(motion, dict) or motion.keys() != fields:
        raise ValueError("Invalid Vengeance mover record.")

    keys = motion["keys"]
    timing = motion["timing_seconds"]
    events = motion["events"]
    if (not isinstance(keys, list) or not 1 <= len(keys) <= 24 or type(motion["current_key"]) is not int
            or not 0 <= motion["current_key"] < len(keys) or type(motion["glide_type"]) is not int
            or not 0 <= motion["glide_type"] <= 255 or not isinstance(motion["initial_state"], str)
            or not isinstance(events, dict) or not all(isinstance(value, str) for value in events.values())
            or not isinstance(timing, dict) or timing.keys() != {"movetime", "stayopentime", "delaytime"}
            or not all(finite(value, 3600) and value >= 0 for value in timing.values())
            or motion["base_position_m"] is not None and not brush_vector(motion["base_position_m"], 20_000)
            or motion["base_rotation_units"] is not None and not brush_angles(motion["base_rotation_units"])):
        raise ValueError("Invalid Vengeance mover timing or base pose.")
    if any(not isinstance(key, dict) or key.keys() != {"position_offset_m", "rotation_offset_units"}
           or not brush_vector(key["position_offset_m"], 20_000)
           or not brush_angles(key["rotation_offset_units"]) for key in keys):
        raise ValueError("Invalid Vengeance mover keyframe.")


def load_mission_audio(manifest, inventory):
    """Read the previously validated private index with its inventory checksum."""
    if AUDIO_INDEX not in inventory:
        return None
    folder = Path(manifest).resolve().parent
    path = (folder / AUDIO_INDEX).resolve(strict=True)
    if path.parent != folder:
        raise ValueError("Intake file outside the package.")
    data = bounded(path, MAX_AUDIO_INDEX)
    if hashlib.sha256(data).hexdigest() != inventory[AUDIO_INDEX]:
        raise ValueError("Intake checksum mismatch: " + AUDIO_INDEX)
    return json.loads(data)


def load_intake(manifest):
    manifest = Path(manifest).resolve(strict=True)
    header = intake_header(manifest)
    native_envelope = header["format"] in NATIVE_SCENE_ENVELOPES
    native_lights = header["format"] == NATIVE_LOCAL_LIGHTS_VERSION
    vertex_attributes = header["format"] in VERTEX_ATTRIBUTES_VERSIONS
    allow_local_lights = native_lights or header["format"] == ASCEND_VERTEX_ATTRIBUTES_VERSION
    if native_envelope:
        # Geometry consumers use the existing scene and inventory contract.
        header["format"] = header.pop("scene_format")

    def valid_digest(value):
        return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)

    if (
        not isinstance(header.get("name"), str)
        or not header["name"].strip()
        or not all(
            isinstance(header.get(key), str) and header[key].strip()
            for key in ("reader", "source_game")
        )
        or not valid_digest(header.get("profile_sha256"))
        or not isinstance(header.get("sources"), list)
        or not header["sources"]
        or not all(
            isinstance(s, dict)
            and valid_digest(s.get("sha256"))
            and all(isinstance(s.get(key), str) for key in ("name", "origin"))
            for s in header["sources"]
        )
    ):
        raise ValueError("Incomplete map name or source provenance.")
    inventory = header["files"]
    has_audio = header['format'] in T2_AUDIO_VERSIONS
    unreal = header["format"] in UNREAL_INTAKE_VERSIONS
    native_models = header["format"] in VENGEANCE_MODEL_VERSIONS
    ascend_art = header["format"] in ASCEND_ART_VERSIONS
    ascend_level = header["format"] in ASCEND_LEVEL_VERSIONS
    ascend_paint = header["format"] == ASCEND_TERRAIN_PAINT_VERSION
    ascend_simple_collision = header["format"] in ASCEND_COLLISION_VERSIONS
    ascend_convex = header["format"] == ASCEND_CONVEX_COLLISION_VERSION
    ascend_placement_art = header["format"] == ASCEND_PLACEMENT_ART_VERSION
    brush_intake = header["format"] in VENGEANCE_BRUSH_VERSIONS
    shader_inputs = header["format"] in VENGEANCE_EXTENDED_SHADER_VERSIONS
    surface_detail = header["format"] == VENGEANCE_SURFACE_DETAIL_VERSION
    vengeance_static = header["format"] in VENGEANCE_MESH_VERSIONS or shader_inputs or brush_intake
    vengeance_mesh_art = header["format"] == VENGEANCE_MESH_MATERIALS_VERSION
    native_paint = ascend_paint or header["format"] in VENGEANCE_TERRAIN_VERSIONS or native_models and "terrain_paint" in header
    native_art = (header["format"] == VENGEANCE_ART_INTAKE_VERSION
                  or (native_models or ascend_paint) and "terrain_art" in header and native_paint)
    if (
        not isinstance(inventory, dict)
        or not {"objects.json", "terrain.json", "height.png"} <= inventory.keys()
        or len(inventory) > max_intake_files(header["format"])
    ):
        raise ValueError("Incomplete intake inventory.")
    if ("minimap" in header) != ("minimap.png" in inventory):
        raise ValueError("Intake minimap settings and artwork must appear together.")
    if has_audio and (AUDIO_INDEX not in inventory or header['reader'] != f't2-mission-v{header["format"]}.0'):
        raise ValueError('Mission audio requires its versioned index and reader.')
    if unreal and (header["reader"] not in (UNREAL_READER, PUBLIC_LAYOUT_READER)
                   or (UNREAL_SCENE in inventory) != (header["reader"] == UNREAL_READER)):
        raise ValueError("Unexpected source scene inventory for this Unreal intake.")
    if unreal and "minimap" not in header:
        raise ValueError("Unreal scene intakes require metric minimap framing.")
    if (("terrain_paint" in header) != native_paint
            or native_paint and header["reader"] != UNREAL_READER):
        raise ValueError("Invalid intake format for native terrain paint.")
    if unreal and (("terrain_art" in header) != native_art or ("terrain_uv" in header) != native_art):
        raise ValueError("Invalid intake format for native terrain artwork.")
    if unreal and header.get("interior_review"):
        raise ValueError("Unreal model review belongs in the source scene.")
    if unreal and header.keys() - {"format", "profile", "source_game", "name", "reader", "profile_sha256",
                                  "sources", "files", "coverage", "minimap", "environment", "interiors", "interior_review",
                                  "terrain_paint", "terrain_art", "terrain_uv"}:
        raise ValueError("Unexpected Unreal intake manifest fields.")
    if header["format"] == 14 and "terrain_art" not in header or header["format"] < 14 and "terrain_art" in header:
        raise ValueError("Terrain artwork requires a compatible intake and its artwork bindings.")
    if header["format"] == 7 and EQUIPMENT_FILE not in inventory:
        raise ValueError("Intake equipment requires its versioned projection.")
    if header["format"] in (8, 9) and WEATHER_FILE not in inventory:
        raise ValueError("Intake weather requires its versioned projection.")
    if ((header["format"] == 9 and SKY_INDEX not in inventory)
            or (SKY_INDEX in inventory and WEATHER_FILE not in inventory)):
        raise ValueError("Intake sky artwork requires its versioned index.")
    if header["format"] in (10, 17) and LIGHTING_INDEX not in inventory:
        raise ValueError("Intake lighting requires its versioned index.")
    if "minimap" in header:
        validate_minimap(header["minimap"])
    if (header["format"] == 3 and "environment" not in header
            or header["format"] < 3 and "environment" in header):
        raise ValueError("Invalid intake environment format.")
    if "environment" in header:
        validate_environment(header["environment"], unreal=native_envelope)
    legacy_int = 15 <= header["format"] <= 21
    legacy_textures = 16 <= header["format"] <= 21
    vengeance_bsp = unreal and header["profile"] == VENGEANCE and header["reader"] == UNREAL_READER
    ascend_mesh = (header["format"] in ASCEND_PRIVATE_MESH_VERSIONS
                   and header["profile"] == ASCEND and header["reader"] == UNREAL_READER)
    if vengeance_bsp or ascend_mesh:
        interior_prefix = "unrealmesh:bsp." if vengeance_bsp and not vengeance_static else "unrealmesh:"
    else:
        interior_prefix = "interiorshape:" if legacy_int else "interiorinstance:"
    dif_animation = header["format"] == T2_DIF_ANIMATION_VERSION
    generator_scopes = header["format"] in T2_GENERATOR_SCOPE_VERSIONS
    flexible_dif = dif_animation or generator_scopes
    interior_versions = (*range(4, 11), *range(15, 22), *T2_DIF_VERSIONS, *T2_GENERATOR_SCOPE_VERSIONS)
    if vengeance_bsp:
        interior_versions += UNREAL_INTAKE_VERSIONS
    if ascend_mesh:
        interior_versions += ASCEND_PRIVATE_MESH_VERSIONS
    model_files = validate_model_index(header, inventory, field="interiors", review_field="interior_review",
                                       pattern=INTERIOR_FILE, prefix=interior_prefix, supported=interior_versions,
                                       required=(4, 15, 16, 17, ASCEND_PLACEMENT_ART_VERSION,
                                                 *ASCEND_COLLISION_VERSIONS, ASCEND_BSP_INTAKE_VERSION))
    model_files.update(validate_model_index(header, inventory, field="shapes", review_field="shape_review",
                                           pattern=SHAPE_FILE, prefix=("tsstatic:", "staticshape:"),
                                           supported=(*range(5, 11), *range(18, 22), *T2_DIF_VERSIONS,
                                                      *T2_GENERATOR_SCOPE_VERSIONS),
                                           required=(5, *range(18, 22))))
    texture_files = {name for name in inventory if TEXTURE_FILE.fullmatch(name)}
    has_textures = TEXTURE_INDEX in inventory
    needs_texture_index = (header["format"] in (6, 16, 17, VENGEANCE_BSP_MATERIALS_VERSION)
                           or legacy_textures and bool(model_files) or vengeance_mesh_art or shader_inputs or ascend_art)
    if ((needs_texture_index or texture_files) and not has_textures
            or has_textures and not legacy_textures and not texture_files
            or len(texture_files) > MAX_TEXTURES):
        raise ValueError("Incomplete intake model textures.")
    sky_files = {name for name in inventory if SKY_PNG.fullmatch(name)}
    lightmaps = {name for name in inventory if LIGHTMAP_FILE.fullmatch(name)}
    if lightmaps and LIGHTING_INDEX not in inventory:
        raise ValueError("Intake interior lightmaps require their lighting index.")
    optional_files = sum(name in inventory for name in (TEXTURE_INDEX, EQUIPMENT_FILE, WEATHER_FILE, SKY_INDEX,
                                                         LIGHTING_INDEX, AUDIO_INDEX))
    audio_files = {name for name in inventory if AUDIO_MEMBER.fullmatch(name)}
    if len(audio_files) > MAX_CLIPS:
        raise ValueError('Too many mission WAV files in one intake.')
    art_files = {name for name in inventory if ART_FILE.fullmatch(name)}
    mask_files = {name for name in inventory if PAINT_FILE.fullmatch(name)}
    if mask_files and not native_paint:
        raise ValueError("Native terrain mask files are missing their slot bindings.")
    if art_files and "terrain_art" not in header:
        raise ValueError("Terrain artwork files are missing slot bindings.")
    if (len(inventory) - len(model_files) - len(texture_files) - len(sky_files) - len(lightmaps)
            - len(art_files) - len(mask_files) - len(audio_files) - optional_files > 15):
        raise ValueError("Too many base intake files.")
    docs, slots, lit_models, texture_sizes = {}, {}, {}, {}
    audio_bytes, wave_files = 0, {}
    receipts = {(row["name"], row["sha256"]) for row in header["sources"]} if legacy_int or ascend_mesh or native_models else set()
    height_header = b""
    shape_version = 0
    model_bytes = texture_bytes = sky_bytes = lightmap_bytes = art_bytes = 0
    mask_metadata, paint_bytes, paint_samples = {}, 0, 0
    vengeance_mesh_files = set()
    brush_files = set()
    ascend_bsp_files = set()
    has_simple_collision = False
    has_convex = False
    has_dif_lods = False
    acoustic_intake = header["format"] in T2_ACOUSTIC_VERSIONS
    collision_intake = header["format"] == T2_ACOUSTIC_COLLISION_VERSION
    has_acoustics = False
    has_acoustic_collision = False
    has_vertex_attributes = False
    for name, digest in inventory.items():
        if not intake_filename(name, header["format"]):
            raise ValueError("Unexpected intake filename.")
        if name in model_files:
            from interior_geometry import (ASCEND_BSP, ASCEND_STATIC_MESH, NATIVE_VERTEX_FIELDS, VENGEANCE_BSP,
                                           VENGEANCE_STATIC_MESH, material_slots)

            geometry, size = load_intake_geometry(manifest.parent, name, digest)
            has_dif_lods |= 'lods' in geometry
            if 'acoustics' in geometry:
                if not acoustic_intake or geometry['format'] != 1 or not INTERIOR_FILE.fullmatch(name):
                    raise ValueError('DIF acoustic geometry requires intake version 49.')
                has_acoustics = True
                if 'collision' in geometry['acoustics']:
                    if not collision_intake:
                        raise ValueError('DIF acoustic collision requires intake version 50.')
                    has_acoustic_collision = True
            model_bytes += size
            if model_bytes > MAX_INTAKE_INTERIOR_BYTES:
                raise ValueError("Oversized intake model geometry.")
            model_format = geometry["format"]
            if model_format == ASCEND_BSP:
                source = geometry["source"]
                receipt = header["sources"][0]
                if (not ascend_level or model_files[name] != f"unrealmesh:bsp.{source['export']}"
                        or any(source[key] != receipt[key] for key in ("name", "sha256"))):
                    raise ValueError("BSP model does not match the Ascend package source.")
                ascend_bsp_files.add(name)
                if geometry["render"]:
                    slots[name] = {str(slot) for slot in material_slots(geometry)}
                continue
            if model_format == VENGEANCE_BSP:
                source = geometry["source"]
                receipt = header["sources"][0]
                brush = brush_intake and model_files[name] == f"{BRUSH_PREVIEW_REFERENCE}{source['export']}"
                if (not vengeance_bsp or (not brush and model_files[name] != f"unrealmesh:bsp.{source['export']}")
                        or any(source[key] != receipt[key] for key in ("name", "sha256"))):
                    raise ValueError("BSP model does not match the Vengeance package source.")
                if brush:
                    brush_files.add(name)
                if native_models and geometry["render"]:
                    slots[name] = {str(slot) for slot in material_slots(geometry)}
                continue
            if model_format in (ASCEND_STATIC_MESH, VENGEANCE_STATIC_MESH):
                source = geometry["source"]
                path = source["object_path"]
                from_main = all(source[key] == header["sources"][0][key] for key in ("name", "sha256"))
                companion_suffix = ".upk" if model_format == ASCEND_STATIC_MESH else ".pkg"
                if not from_main and Path(source["name"]).suffix.casefold() == companion_suffix:
                    path = f'{Path(source["name"]).stem}.{path}'
                    receipt_matches = (source["name"], source["sha256"]) in receipts
                else:
                    receipt_matches = from_main
                compatible = ascend_mesh if model_format == ASCEND_STATIC_MESH else vengeance_bsp and vengeance_static
                if not compatible or not receipt_matches or model_files[name] != "unrealmesh:" + path.casefold():
                    raise ValueError("Static mesh does not match its Unreal package source.")
                if geometry.keys() & NATIVE_VERTEX_FIELDS:
                    if not vertex_attributes:
                        game, version = (('Ascend', ASCEND_VERTEX_ATTRIBUTES_VERSION) if model_format == ASCEND_STATIC_MESH
                                         else ('Vengeance', VENGEANCE_VERTEX_ATTRIBUTES_VERSION))
                        raise ValueError(f"{game} vertex attributes require intake version {version}.")
                    has_vertex_attributes = True
                if model_format == ASCEND_STATIC_MESH:
                    simple = geometry["collision"].get("simple")
                    if simple:
                        if not (ascend_simple_collision or ascend_level):
                            raise ValueError("Ascend simple collision requires intake version 41 or later.")
                        has_simple_collision = True
                        if any(shape["kind"] == "convex" for shape in simple["shapes"]):
                            if not (ascend_convex or ascend_level):
                                raise ValueError("Ascend convex collision requires intake version 42.")
                            has_convex = True
                    if ascend_art or (ascend_simple_collision or ascend_level) and has_textures:
                        slots[name] = {str(slot) for slot in material_slots(geometry)}
                if model_format == VENGEANCE_STATIC_MESH:
                    vengeance_mesh_files.add(name)
                    if vengeance_mesh_art or shader_inputs or brush_intake:
                        slots[name] = {str(slot) for slot in material_slots(geometry)}
                continue
            source_name = model_files[name].rsplit("/", 1)[-1].split(":", 1)[-1]
            if geometry["source"]["name"].casefold() != source_name:
                raise ValueError("Intake model source does not match its mission reference.")
            required_version = T1_SHAPE_INTAKE_VERSION.get(model_format, 0)
            if unreal or (model_format >= 3) != legacy_int or required_version > header["format"]:
                raise ValueError("Intake geometry does not match its source profile.")
            if model_format == 3:
                selector = geometry["source"]
                if not {(selector["name"], selector["sha256"]),
                        (selector["geometry_name"], selector["geometry_sha256"]),
                        (selector["material_name"], selector["material_sha256"])} <= receipts:
                    raise ValueError("Tribes interior is missing its DIS, DIG or DML source receipt.")
            if model_format >= 4 and (geometry["source"]["name"], geometry["source"]["sha256"]) not in receipts:
                raise ValueError("The shape is missing its Tribes DTS source receipt.")
            shape_version = max(shape_version, required_version)
            if LIGHTING_INDEX in inventory and model_format in (1, 3):
                source_hash = geometry["source"]["sha256"]
                lit_models[name] = (source_hash, len(geometry["triangles"]))
                if flexible_dif or header["format"] == T2_DIF_LOD_LIGHT_VERSION:
                    for position, level in enumerate(geometry.get("lods", {}).get("secondary", ()), 1):
                        lit_models[dif_lighting_key(name, position)] = (source_hash, len(level["triangles"]))
            if has_textures:
                from interior_geometry import material_slots

                slots[name] = {str(slot) for slot in material_slots(geometry)}
            continue
        path = (manifest.parent / name).resolve(strict=True)
        if path.parent != manifest.parent:
            raise ValueError("Intake file outside the package.")
        data = bounded(path, intake_file_limit(name, header["format"]))
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Intake checksum mismatch: " + name)
        if name in audio_files:
            audio_bytes += len(data)
            if audio_bytes > MAX_AUDIO_BYTES:
                raise ValueError('Oversized intake mission audio.')
            wave_files[name] = wave_metadata(data)
        if name == "height.png" and header["profile"] != T2:
            height_header = data[:33]
        if name in texture_files:
            texture_bytes += len(data)
            if texture_bytes > MAX_TEXTURE_BYTES:
                raise ValueError("Oversized intake textures.")
            texture_sizes[name] = validate_png(data)
        if name in art_files:
            art_bytes += len(data)
            if art_bytes > MAX_ART_BYTES or name != f"terrain-art-{digest[:32]}.png":
                raise ValueError("Invalid terrain artwork size or filename.")
            validate_png(data)
        if name in mask_files:
            paint_bytes += len(data)
            if paint_bytes > MAX_PAINT_PNG_BYTES:
                raise ValueError("Oversized native terrain paint files.")
            width, height, pixels = decode_mask(data)
            paint_samples += len(pixels)
            if paint_samples > MAX_PAINT_BYTES:
                raise ValueError("Oversized native terrain paint samples.")
            mask_metadata[name] = (width, height, hashlib.sha256(pixels).hexdigest())
        if name in sky_files:
            sky_bytes += len(data)
            if sky_bytes > MAX_SKY_BYTES:
                raise ValueError("Oversized intake sky artwork.")
            validate_png(data)
        if name in lightmaps:
            lightmap_bytes += len(data)
            if lightmap_bytes > MAX_LIGHTMAP_BYTES:
                raise ValueError("Oversized intake interior lightmaps.")
            validate_png(data)
        if name == "minimap.png" and (
            len(data) < 33 or data[:16] != bytes.fromhex("89504e470d0a1a0a0000000d49484452")
            or any(not 1 <= int.from_bytes(data[start:start + 4], "big") <= 4096 for start in (16, 20))
        ):
            raise ValueError("Invalid intake minimap PNG header or dimensions.")
        if name.endswith(".json"):
            docs[name] = json.loads(data)
    if not flexible_dif and has_dif_lods != (header["format"] in T2_DIF_LOD_VERSIONS):
        raise ValueError("Intake version does not match its DIF detail levels.")
    if 19 <= header["format"] <= 21 and header["format"] != shape_version:
        raise ValueError("The newest DTS shape does not match the intake version.")
    if vertex_attributes and not has_vertex_attributes:
        raise ValueError("This intake version requires authored vertex attributes.")
    if brush_intake and not brush_files:
        raise ValueError("Vengeance brush intake requires authored brush geometry.")
    if vengeance_static and not (shader_inputs or brush_intake or vengeance_mesh_files):
        raise ValueError("Vengeance static mesh intake requires placed mesh geometry.")
    if ascend_simple_collision and not has_simple_collision:
        raise ValueError("Ascend collision intake requires authored simple shapes.")
    if ascend_convex and not has_convex:
        raise ValueError("Ascend convex collision intake requires an authored hull.")
    if header["format"] == ASCEND_BSP_INTAKE_VERSION and not ascend_bsp_files:
        raise ValueError("Ascend BSP intake requires compiled level geometry.")
    if (ascend_simple_collision or ascend_level) and has_textures and isinstance(docs[TEXTURE_INDEX], dict):
        ascend_placement_art = any(key.startswith("placement-") for key in docs[TEXTURE_INDEX])
    if has_textures and not ascend_placement_art:
        legacy_art = legacy_textures or native_models or ascend_art or ascend_simple_collision or ascend_level
        validate_bindings(docs[TEXTURE_INDEX], slots, inventory,
                          legacy_sizes=texture_sizes if legacy_art else None,
                          receipts=receipts, native_bsp=native_models or ascend_art or ascend_simple_collision or ascend_level,
                          shader_inputs=shader_inputs or brush_intake,
                          surface_detail=surface_detail or brush_intake, require_extended=not brush_intake)
        if vengeance_mesh_art and not any("file" in binding for filename in vengeance_mesh_files
                                          for binding in docs[TEXTURE_INDEX][filename].values()):
            raise ValueError("Vengeance mesh artwork intake requires at least one authored mesh texture.")
    lighting = docs.get(LIGHTING_INDEX, {})
    if LIGHTING_INDEX in docs:
        validate_lighting(lighting, lit_models, inventory, legacy=legacy_int,
                          rectangles_allowed=flexible_dif, receipts=receipts)
    has_secondary_lighting = any("#" in key for key in lighting)
    if not flexible_dif and has_secondary_lighting != (header["format"] == T2_DIF_LOD_LIGHT_VERSION):
        raise ValueError("Intake version does not match its secondary DIF lighting.")
    if dif_animation and not any("surface_rects" in record.get("animation", {}) for record in lighting.values()):
        raise ValueError("Animated DIF lighting intake requires source mask rectangles.")
    terrain = docs["terrain.json"]
    if not isinstance(terrain, dict):
        raise ValueError("Invalid terrain record.")
    if "authoring" in terrain:
        authoring = terrain["authoring"]
        if (not isinstance(authoring, dict)
                or set(authoring) != {"source_version", "texture_rules", "terraformer_rules"}
                or type(authoring["source_version"]) is not int or authoring["source_version"] != 3
                or not all(isinstance(authoring[name], str) and all(ord(ch) <= 255 for ch in authoring[name])
                           for name in ("texture_rules", "terraformer_rules"))):
            raise ValueError("Invalid T2 terrain authoring record.")
    if type(terrain.get("y_sign", -1)) is not int or terrain.get("y_sign", -1) not in (
        -1,
        1,
    ):
        raise ValueError("Invalid terrain orientation.")
    if native_paint or header["format"] in (12, 13, 14) or (legacy_int or unreal) and "tile_grid" in terrain:
        empty_layers = header["format"] == 12 or unreal and not native_paint
        if terrain.keys() != {"step_cm", "cm_per_m", "origin_cm", "y_sign", "material_layers",
                              "tile_grid", "terrain_blocks"} or empty_layers and terrain["material_layers"] != []:
            raise ValueError("Invalid tiled terrain fields.")
        validate_tiled_heightfield(terrain, height_header, painted=not unreal and header["format"] >= 13,
                                   legacy=not unreal)
        if "terrain_art" in header:
            validate_art(header, terrain, art_files)
        if native_paint:
            validate_native_paint(header, terrain, docs.get(UNREAL_SCENE), mask_metadata)
    else:
        if "tile_grid" in terrain or "terrain_blocks" in terrain:
            raise ValueError("Tiled terrain requires intake format 12.")
        if header["format"] == 11 or legacy_int or unreal:
            validate_legacy_height_header(height_header)
            if "terrain_art" in header:
                raise ValueError("Terrain artwork requires tiled Tribes terrain.")
        validate_height_block(terrain, (VERTICES if header["profile"] != T2 else CELLS)**2,
                              legacy=header["profile"] != T2 and not unreal)
    if (
        not finite(terrain["step_cm"], 20000)
        or terrain["step_cm"] <= 0
        or not finite(terrain["cm_per_m"], 1000)
        or terrain["cm_per_m"] <= 0
    ):
        raise ValueError("Invalid terrain scale.")
    if not isinstance(terrain.get("origin_cm"), list) or len(terrain["origin_cm"]) != 3 or not all(
        finite(v, 2e6) for v in terrain["origin_cm"]
    ):
        raise ValueError("Invalid terrain origin.")
    rows = docs["objects.json"]
    if not isinstance(rows, list) or len(rows) > 50000:
        raise ValueError("Invalid object inventory.")
    ids = set()
    statuses = {"marker", "terrain", "excluded", "group", "review"}
    if header["format"] >= 2:
        statuses.add("boundary")
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or row["id"] in ids
            or row.get("parent") is not None
            and row["parent"] not in ids
            or not isinstance(row.get("status"), str) or row["status"] not in statuses
            or not all(
                isinstance(row.get(k), str) for k in ("name", "reason", "source_class")
            )
            or not isinstance(row.get("properties"), dict)
        ):
            raise ValueError("Invalid object record or parent relationship.")
        ids.add(row["id"])
        if unreal:
            validate_unreal_row(row, allow_local_lights=allow_local_lights)
        elif row.keys() & {"conversion_class", "transform", "model_reference", "local_light"}:
            raise ValueError("Unreal scene placement requires intake version 22.")
        if row["status"] == "marker":
            if (
                row["kind"] not in ("spawn", "flag")
                or row["team"] not in (1, 2)
                or len(row["position_cm"]) != 3
                or not all(finite(v, 2e6) for v in row["position_cm"])
                or not finite(row["yaw"], 360)
            ):
                raise ValueError("Invalid objective marker.")
    if native_lights and not any("local_light" in row for row in rows):
        raise ValueError("The native local light intake needs at least one light.")
    if ascend_placement_art:
        placements = {}
        for row in rows:
            if row["status"] == "review" and row.get("conversion_class") == "unrealmeshactor":
                filename = header["interiors"].get(row["model_reference"])
                if filename in slots:
                    placements[placement_key(row["id"])] = slots[filename]
        validate_bindings(docs[TEXTURE_INDEX], slots, inventory, legacy_sizes=texture_sizes,
                          receipts=receipts, native_bsp=True, placements=placements)
    if dict(Counter(r["status"] for r in rows)) != header.get("coverage"):
        raise ValueError("Object counts do not match the manifest.")
    if header["coverage"].get("boundary", 0) != int("minimap" in header):
        raise ValueError("Intake minimap requires one playable boundary.")
    bounds = playable_bounds(header, rows) if "minimap" in header else None
    if any(row["status"] == "boundary" and BOUNDARY_RECIPE in row["properties"] for row in rows):
        from perimeter import plan_boundaries, read_boundary_recipe

        recipe = read_boundary_recipe(rows)
        plan_boundaries(header, terrain, rows, **recipe["options"])
    if unreal:
        ctf = [row for row in rows if row["status"] == "marker"]
        totals = Counter((row["team"], row["kind"]) for row in ctf)
        if (header["coverage"].get("terrain") != 1
                or any(totals[team, "flag"] != 1 or not 1 <= totals[team, "spawn"] <= 24 for team in (1, 2))):
            raise ValueError("An Unreal draft requires one terrain and a complete CTF layout.")
        low, high = bounds
        if any(not all(lo <= point <= hi for lo, point, hi in zip(low, row["position_cm"], high)) for row in ctf):
            raise ValueError("An Unreal CTF marker lies outside the declared playable bounds.")
    if UNREAL_SCENE in docs:
        scene = docs[UNREAL_SCENE]
        if (not isinstance(scene, dict) or scene.get("format") != "legacy-unreal-scene"
                or scene.get("source_game") != header["source_game"] or scene.get("source") != {
                    key: header["sources"][0][key] for key in ("name", "sha256")}):
            raise ValueError("Unreal source scene does not match the intake provenance.")
        if ("trigger_chains" in scene) != (header["format"] == VENGEANCE_TRIGGER_CHAIN_VERSION):
            raise ValueError("Vengeance trigger chain records require intake version 38.")
        if brush_intake:
            validate_brush_records(scene, rows, {model_files[name] for name in brush_files}, version=header["format"])
        ground = scene.get("native_terrain")
        if isinstance(ground, dict) and "height_package" in ground:
            receipt = ground["height_package"]
            if (header["profile"] != VENGEANCE or not isinstance(receipt, dict)
                    or set(receipt) != {"name", "sha256"}
                    or not isinstance(receipt["name"], str) or not receipt["name"].casefold().endswith(".pkg")
                    or not any(all(receipt[key] == source[key] for key in receipt) for source in header["sources"][1:])):
                raise ValueError("External Vengeance height texture does not match the intake provenance.")
    if (WEATHER_FILE in docs or header["format"] in (10, *T2_DIF_VERSIONS)
            or generator_scopes and header["reader"] != PUBLIC_LAYOUT_READER):
        weather = mission_weather(rows, terrain)
        expected = weather if weather["objects"] else None
        if json.dumps(docs.get(WEATHER_FILE), sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError("Intake weather records do not match the mission.")
        if SKY_INDEX in docs:
            validate_sky_artwork(docs[SKY_INDEX], weather, inventory)
    if header["format"] >= 4:
        from mesh_mappings import source_reference

        known = {source_reference(row) for row in rows}
        references = set().union(*(header.get(field, {}).keys() for field in
                                  ("interiors", "interior_review", "shapes", "shape_review")))
        if references - known:
            raise ValueError("Intake model does not belong to this mission.")
    if EQUIPMENT_FILE in docs or header["format"] in (8, 9, 10, *T2_DIF_VERSIONS) or generator_scopes:
        from mission_equipment import mission_equipment

        expected = mission_equipment(rows, terrain, generator_scopes=generator_scopes)
        # Compare canonical JSON so booleans or integers cannot substitute for other JSON types.
        has_equipment = EQUIPMENT_FILE in docs
        if (header["format"] == T2_GENERATOR_SCOPE_VERSION and not has_equipment
                or has_equipment != bool(expected["equipment"])
                or has_equipment and json.dumps(docs[EQUIPMENT_FILE], sort_keys=True)
                != json.dumps(expected, sort_keys=True)):
            raise ValueError("Intake equipment does not match the source objects and power groups.")
    if has_audio:
        audio_receipts = {(item['name'], item['origin'], item['sha256']) for item in header['sources']}
        audio = docs[AUDIO_INDEX]
        inherited = header['format'] == T2_AUDIO_PROFILES_VERSION or (
            acoustic_intake and isinstance(audio, dict) and audio.get('format') == 2)
        validate_audio_index(audio, rows, terrain, wave_files, inventory, audio_receipts, inherited=inherited)
        if acoustic_intake and (not has_acoustics
                                or not any(row['settings'].get('outside_only') for row in audio['emitters'])):
            raise ValueError('This intake requires an outside emitter and verified acoustic geometry.')
        if collision_intake and not has_acoustic_collision:
            raise ValueError('This intake requires verified DIF acoustic collision.')
    return header, terrain, rows
