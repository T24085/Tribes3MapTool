"""Read native Unreal height and visibility data into editable terrain tiles."""

from array import array
from hashlib import sha256
import math
import sys

from ascend_terrain_layers import read_ascend_paint
from intake_contract import finite, intake_file_limit
from minimap import grayscale_png
from source_profiles import UNREAL_INTAKE_VERSION
from terrain_grid import CELLS, VERTICES, preview_samples
from unreal_binary_scene import actor_scene
from unreal_legacy_reader import descendants
from unreal_package_reader import Cursor, open_package
from vengeance_terrain import read_heightfield
from vengeance_terrain_layers import read_terrain_paint


HEIGHT_MIDPOINT = 32768
HEIGHT_UNITS = 128
HIDDEN_PATCH = 1
TURNED_PATCH = 2
MAX_HEIGHT_SAMPLES = intake_file_limit("terrain.json", UNREAL_INTAKE_VERSION) // 16
DIMENSION_FIELDS = ("numverticesx", "numverticesy", "numpatchesx", "numpatchesy")


def dimensions(node, stream, endian):
    values = {}
    for tag in node["tags"]:
        name = tag["name"].casefold()
        if name not in DIMENSION_FIELDS:
            continue
        if name in values or tag["kind"] != "IntProperty" or tag["size"] != 4 or tag["slot"]:
            raise ValueError("Invalid or repeated native terrain dimension.")
        cursor = Cursor(stream, tag["value_offset"], tag["value_offset"] + 4, endian)
        values[name], = cursor.fields("i")
    width, height, patches_x, patches_y = (values.get(name) for name in DIMENSION_FIELDS)
    if (width is None or height is None or not 2 <= width <= 64 * CELLS + 1 or not 2 <= height <= 64 * CELLS + 1
            or patches_x is not None and patches_x != width - 1 or patches_y is not None and patches_y != height - 1):
        raise ValueError("Native terrain dimensions are missing or inconsistent with its patch count.")
    return width, height


def tile_dimensions(width, height):
    columns, rows = math.ceil((width - 1) / CELLS), math.ceil((height - 1) / CELLS)
    if not 1 <= columns <= 64 or not 1 <= rows <= 64:
        raise ValueError("Native terrain dimensions exceed the editor tile grid limits.")
    if columns * rows * VERTICES**2 > MAX_HEIGHT_SAMPLES:
        raise ValueError("Native terrain dimensions exceed the editor intake size limit.")
    return columns, rows


def terrain_arrays(node, stream, endian, samples):
    cursor = Cursor(stream, node["property_end"], node["serial_offset"] + node["serial_bytes"], endian)
    if cursor.count(MAX_HEIGHT_SAMPLES) != samples:
        raise ValueError("The native terrain height array does not match its dimensions.")
    raw_heights = cursor.take(samples * 2)
    if cursor.count(MAX_HEIGHT_SAMPLES) != samples:
        raise ValueError("The native terrain visibility array does not match its dimensions.")
    flags = cursor.take(samples)
    if set(flags) - {0, HIDDEN_PATCH}:
        raise ValueError("Native terrain has patch flags that this importer cannot interpret.")
    provenance = {"height_samples_sha256": sha256(raw_heights).hexdigest(),
                  "patch_flags_sha256": sha256(flags).hexdigest()}
    return raw_heights, flags, provenance


def terrain_tiles(heights, flags, width, height, columns, rows, factor, midpoint, *, source_edges):
    blocks, grid = [], []
    elevations = [(sample - midpoint) * factor for sample in heights]
    if not finite(min(elevations), 2048) or not finite(max(elevations), 2048):
        raise ValueError("Native terrain heights exceed the editor intake limits.")
    for row in range(rows):
        slots = []
        y0 = row * CELLS
        for col in range(columns):
            x0 = col * CELLS
            local_heights = []
            for y in range(y0, y0 + VERTICES):
                start = min(y, height - 1) * width
                end = min(x0 + VERTICES, width)
                local_heights.extend(elevations[start + x0:start + end])
                local_heights.extend([elevations[start + width - 1]] * (x0 + VERTICES - end))
            holes = []
            turned = []
            for dy in range(CELLS):
                y = y0 + dy
                if y >= height - 1:
                    holes.extend(range(dy * CELLS, (dy + 1) * CELLS))
                    continue
                valid = min(CELLS, width - 1 - x0)
                start = y * width + x0
                for dx, flag in enumerate(flags[start:start + valid]):
                    if flag & HIDDEN_PATCH:
                        holes.append(dy * CELLS + dx)
                    elif flag & TURNED_PATCH:
                        turned.append(dy * CELLS + dx)
                holes.extend(range(dy * CELLS + valid, (dy + 1) * CELLS))
            slots.append(len(blocks))
            block = dict(height_m=local_heights, holes=holes)
            if source_edges:
                block["turned_quads"] = turned
            blocks.append(block)
        grid.append(slots)
    if all(len(block["holes"]) == CELLS**2 for block in blocks):
        raise ValueError("The selected native terrain has no visible patches.")
    return grid, blocks


def ascend_heightfield(actor, node, scene, stream, endian):
    placement = actor["transform"]
    if (not placement or placement["rotation_xyzw"] != [0, 0, 0, 1]
            or not any(field.get("key", "").casefold() == "drawscale3d" for field in node["entries"])):
        raise ValueError("Native terrain requires an independent, axis-aligned actor with serialized scale.")
    units = scene["coordinate_frame"]["source_units_per_m"]
    sx, sy, sz = placement["scale"]
    width, height = dimensions(node, stream, endian)
    tile_dimensions(width, height)  # Bound the array reads before allocating the payload.
    raw, flags, provenance = terrain_arrays(node, stream, endian, width * height)
    origin = [value * units for value in placement["position_m"]]
    return (width, height), raw, flags, origin, (sx, sy, sz / HEIGHT_UNITS), HEIGHT_MIDPOINT, provenance


def terrain_candidates(scene, selected=None):
    source_class = "terraininfo" if scene["profile"] == "vengeance" else "terrain"
    return [actor for actor in scene["actors"]
            if (actor["source_class"] or "").rsplit(".", 1)[-1].casefold() == source_class
            and (selected is None or selected.casefold() in
                 ((actor["name"] or "").casefold(), actor["object_path"].casefold()))]


def decode_terrain(scene, report, stream, selected, *, companions=None, rtc=None):
    vengeance = report["summary"]["game"] == "vengeance"
    endian = report["summary"]["endian"]
    candidates = terrain_candidates(scene, selected)
    if len(candidates) != 1:
        raise ValueError("Select one native terrain actor in the import settings, or supply a heightmap.")
    actor, = candidates
    if actor["references"].keys() & {"base", "attachparent"}:
        raise ValueError("Native terrain needs an independent actor placement.")
    node = next(item for item in descendants(scene["scene"][0]) if item.get("export_index") == actor["line"])
    if vengeance:
        size, raw, flags, origin, scale, midpoint, provenance = read_heightfield(
            node, report, stream, MAX_HEIGHT_SAMPLES, companions)
    else:
        size, raw, flags, origin, scale, midpoint, provenance = ascend_heightfield(actor, node, scene, stream, endian)
    width, height = size
    cols, rows = tile_dimensions(width, height)
    units = scene["coordinate_frame"]["source_units_per_m"]
    sx, sy, sz = scale
    step_cm = sx * 100 / units
    origin_cm = [100 * value / units for value in origin]
    scale_m = sz / units
    if (not finite(step_cm, 20000) or step_cm <= 0 or sx != sy or not math.isfinite(scale_m) or scale_m <= 0
            or not all(finite(value, 2e6) for value in origin_cm) or not math.isfinite(midpoint)):
        raise ValueError("Native terrain requires equal positive XY grid spacing and positive vertical scale.")
    heights = array("H", raw)
    if (endian == "<") != (sys.byteorder == "little"):
        heights.byteswap()
    grid, blocks = terrain_tiles(heights, flags, width, height, cols, rows, scale_m, midpoint, source_edges=vengeance)
    terrain = dict(origin_cm=origin_cm, step_cm=step_cm, y_sign=1,
                   cm_per_m=100, material_layers=[], tile_grid=grid, terrain_blocks=blocks)
    scene["native_terrain"] = dict(provenance, object_path=actor["object_path"], export_index=actor["line"],
                                  vertices=[width, height], metres_per_height_unit=scale_m, height_midpoint=midpoint)
    if vengeance:
        paint = read_terrain_paint(node, report, stream, companions, units)
    else:
        paint = read_ascend_paint(node, report, stream, companions, size, rtc)
    if paint.layers:
        terrain["material_layers"] = [[layer["slot"], layer["surface"]] for layer in paint.layers]
        scene["native_terrain"]["paint_layers"] = paint.layers
    if paint.review:
        scene["native_terrain"]["paint_review"] = paint.review
    if paint.art_review:
        scene["native_terrain"]["art_review"] = paint.art_review
    raw, px_width, px_height = preview_samples(terrain)
    return terrain, grayscale_png(raw, 16, px_width, px_height), paint


def read_native_terrain_draft(path, game, units_per_m, selected=None):
    with open_package(path, game) as (report, stream):
        scene = actor_scene(report, stream, units_per_m)
        terrain, preview, _ = decode_terrain(scene, report, stream, selected)
    return scene, terrain, preview
