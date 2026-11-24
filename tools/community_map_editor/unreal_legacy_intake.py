"""Project a legacy Unreal scene and authored terrain into a CTF draft intake."""

import argparse
from collections import Counter, defaultdict
from functools import partial
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from ascend_materials import placed_materials as ascend_placed_materials, resolve_material as resolve_ascend_material
from ascend_static_mesh import read_placed_meshes as read_ascend_meshes
from ascend_texture_cache import AscendTextureCache
from intake_contract import (MAX_INTAKE_INTERIORS, MAX_INTAKE_INTERIOR_BYTES, PLAYABLE_BOUNDS, UNREAL_READER, UNREAL_SCENE,
                             bounded, finite, validate_minimap)
from interior_geometry import MAX_GEOMETRY_BYTES
from model_textures import PackageMaterials, SURFACE_DETAIL, TEXTURE_INDEX, has_shader_inputs, texture_records
from minimap import framing, terrain_png
from source_profiles import (ASCEND_MESH_INTAKE_VERSION, ASCEND_MODEL_ART_VERSION, ASCEND_PLACEMENT_ART_VERSION,
                             UNREAL_INTAKE_VERSIONS, UNREAL_PROFILES,
                             VENGEANCE_ART_INTAKE_VERSION, VENGEANCE_BSP_MATERIALS_VERSION,
                             VENGEANCE_DOOR_VERSION, VENGEANCE_ELEVATOR_LINK_VERSION, VENGEANCE_ELEVATOR_VERSION,
                             VENGEANCE_MESH_MATERIALS_VERSION,
                             VENGEANCE_MOVER_PLAYBACK_VERSION,
                             VENGEANCE_PAINT_INTAKE_VERSION,
                             VENGEANCE_SHADER_INPUTS_VERSION, VENGEANCE_STATIC_MESH_VERSION, VENGEANCE_SURFACE_DETAIL_VERSION,
                             VENGEANCE_PERMANENT_DOOR_VERSION, VENGEANCE_TRIGGER_CHAIN_VERSION,
                             VENGEANCE_TRIGGER_DOOR_VERSION)
from t1_reader import load_heightmap
from t2_reader import publish_intake
from terrain_grid import CELLS
from unreal_binary_scene import actor_scene
from unreal_legacy_reader import attributes, descendants, read_unreal_export
from unreal_native_terrain import decode_terrain
from unreal_package_reader import GAMES as NATIVE_PACKAGE_TYPES, open_package
from vengeance_bsp import read_bsp_models
from vengeance_brushes import read_native_brushes
from vengeance_materials import resolve_material as resolve_vengeance_material
from vengeance_static_mesh import read_placed_meshes as read_vengeance_meshes
from vengeance_terrain_layers import TerrainPaint
from vengeance_triggers import ACTIVATION_LABELS


METRICS = ("origin_m", "step_m", "height_range_m")
REQUIRED = {"format", "game", "units_per_metre", "teams"}
SUPPLIED_HEIGHTMAP = {*METRICS, "heightmap", "raster_y"}


def encoded(record):
    return json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def project_markers(scene, teams):
    by_name = defaultdict(list)
    for actor in scene["actors"]:
        if actor["name"]:
            name = actor["name"]
            names = {name.casefold()}
            if actor["level_name"]:
                names.add(f'{actor["level_name"]}.{name}'.casefold())
            if actor.get("object_path"):
                names.add(actor["object_path"].casefold())
            for key in names:
                by_name[key].append(actor)
    if not isinstance(teams, list) or len(teams) != 2:
        raise ValueError("Provide the flag and spawns for each of the two CTF teams.")
    markers = {}

    def marker(name, team, kind):
        if not isinstance(name, str) or len(matches := by_name.get(name.casefold(), ())) != 1:
            raise ValueError(f"Select one unambiguous source actor for each {kind}.")
        actor, = matches
        transform = actor["transform"]
        if (transform is None or not transform["authored"] or actor["components"] and any(
                item["transform"] is not None for item in actor["components"])
                and any("root component" in reason for reason in actor["review"])
                or actor["references"].keys() & {"base", "attachparent"}):
            raise ValueError(f"Choose a {kind} with an independent actor transform.")
        if actor["line"] in markers:
            raise ValueError("One source actor cannot be assigned twice.")
        x, y, z, w = transform["rotation_xyzw"]
        yaw = math.degrees(math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z)))
        markers[actor["line"]] = dict(team=team, kind=kind, yaw=yaw,
                                       position_cm=[value * 100 for value in transform["position_m"]])

    for team, entry in enumerate(teams, 1):
        if (not isinstance(entry, dict) or set(entry) != {"flag", "spawns"}
                or not isinstance(entry["spawns"], list) or not 1 <= len(entry["spawns"]) <= 24):
            raise ValueError("Each team needs one flag and 1 to 24 spawn actors.")
        marker(entry["flag"], team, "flag")
        for name in entry["spawns"]:
            marker(name, team, "spawn")
    return markers


def project_rows(scene, markers, heightmap, *, placement_components=None):
    root, = [item for item in scene["scene"] if item.get("block", "").casefold() == "map"]
    nodes = {item["line"]: item for item in descendants(root)}
    native = scene.get("native_terrain")
    event_receivers = defaultdict(list)
    chains = scene.get("trigger_chains", {})
    brushes = scene.get("native_brushes", {})
    from vengeance_mover_plan import plan_proximity_chains

    _, rejected = plan_proximity_chains(chains, brushes, set(map(int, brushes)))
    chain_ids = set(map(int, chains))
    for index, brush in brushes.items():
        for sender in brush["behavior"].get("trigger_senders", ()):
            event_receivers[sender["source_export"]].append((index, brush["type"], sender))
    for index, senders in chains.items():
        for sender in senders:
            event_receivers[sender["source_export"]].append((index, "trigger", sender))
    rows = [
        dict(id=1, parent=None, source_class="Terrain" if native else "SourceHeightmap",
             name=native["object_path"] if native else heightmap, conversion_class="unrealterrain",
             status="terrain", reason="Native terrain heightfield" if native else "Author-supplied terrain heightfield",
             properties={}),
        dict(id=2, parent=None, source_class="PlayArea", name="Playable bounds", conversion_class="unrealbounds",
             status="boundary", reason="Imported terrain framing", properties={}),
    ]
    if native:
        for issue in (*native.get("paint_review", ()), *native.get("art_review", ())):
            rows.append(dict(id=len(rows) + 1, parent=1, source_class="TerrainLayer",
                             name=f"Terrain layer {issue['slot']}", status="review",
                             reason=issue["reason"], properties={}))
    for actor in scene["actors"]:
        line = actor["line"]
        if native and line == native["export_index"]:
            continue
        raw = nodes[line]
        row = dict(id=len(rows) + 1, parent=None, source_class=actor["source_class"] or "Unclassified",
                   name=actor["name"] or f'Actor at line {line}',
                   status="review", reason=" · ".join(actor["review"]) or "Source actor retained for review",
                   properties={item["key"]: item["value"] for item in raw["entries"] if "key" in item})
        if line in markers:
            row.update(markers[line], status="marker", reason="Author-assigned CTF marker",
                       conversion_class="unrealctfmarker")
        elif brush := brushes.get(str(line)):
            row.update(conversion_class="unrealmeshactor", model_reference=brush["reference"], transform=brush["transform"],
                       reason="Imported brush for editor review. Gameplay behavior needs conversion.")
            row["properties"]["Brush export"] = str(line)
            if motion := brush["motion"]:
                from vengeance_mover_plan import bump_track, loop_track, trigger_track

                row["properties"].update({"Mover state": motion["initial_state"], "Authored keys": str(len(motion["keys"])),
                                          "Move seconds": str(motion["timing_seconds"]["movetime"])})
                if brush["behavior"].get("opens_once"):
                    row["properties"]["Stays open after activation"] = "True"
                if loop_track(brush):
                    row["reason"] = "Eligible for a self-running transform sequence."
                elif bump_track(brush):
                    row["reason"] = "Eligible for a timed sequence triggered by a blocking hit."
                    row["properties"]["Bump type"] = str(brush["behavior"]["bump_type"])
                elif brush["behavior"].get("trigger_senders"):
                    senders = brush["behavior"]["trigger_senders"]
                    row["properties"]["Saved event senders"] = ", ".join(sender["source_name"] for sender in senders)[:512]
                    row["reason"] = ("Eligible for a timed sequence triggered by proximity." if trigger_track(brush, chain_ids)[0]
                                     else "Saved event links identified. The door behavior still needs review.")
            else:
                physics = brush["behavior"]
                row["properties"].update({"Initially active": str(physics["active"]),
                                          "Gravity (m/s²)": str(physics["gravity_m_s2"]),
                                          "Zone velocity (m/s)": str(physics["zone_velocity_m_s"]),
                                          "Incoming saved trigger": str(physics["incoming_trigger"])})
                if physics["trigger_senders"]:
                    row["properties"]["Trigger tag"] = physics["trigger_tag"]
                    row["properties"]["Saved event senders"] = ", ".join(
                        sender["source_name"] for sender in physics["trigger_senders"])[:512]
                    row["reason"] = "Saved event links identified. Inspect these senders when testing elevator gameplay."
            if line in rejected:
                row["reason"] = "Saved trigger chain is cyclic or contains unsupported links."
        elif issue := scene.get("native_brush_review", {}).get(str(line)):
            row["reason"] += " · " + issue
        elif actor["role"] == "static-mesh":
            transform = actor["transform"]
            model = actor["assets"]
            if (transform and len(model) == 1 and model[0]["kind"] == "staticmesh"
                    and not actor["references"].keys() & {"base", "attachparent"}
                    and not any(item["transform"] for item in actor["components"])
                    and not any("Subobject at line" in reason for reason in actor["review"])
                    and all(finite(value, 1000) for value in transform["scale"])):
                row.update(conversion_class="unrealmeshactor", model_reference="unrealmesh:" + model[0]["source"].casefold(),
                           transform=dict(position_cm=[v * 100 for v in transform["position_m"]],
                                          rotation_xyzw=transform["rotation_xyzw"], scale=transform["scale"]))
                if placement_components is not None:
                    components = [nodes[part["line"]] for part in actor["components"]
                                  if attributes(part["declaration"]).get("class", "").rsplit(".", 1)[-1].casefold()
                                  == "staticmeshcomponent"]
                    if any(tag["name"].casefold() == "materials"
                           for part in components for tag in part["tags"]):
                        placement_components.append((actor, raw, row, components))
            else:
                row["reason"] += " · Resolve model and component transforms before placement."
        if receivers := event_receivers.get(line):
            for kind, label in (("elevator", "Elevator brush exports"), ("mover", "Door brush exports"),
                                ("trigger", "Controlled trigger exports")):
                matches = {index for index, brush_type, _ in receivers if brush_type == kind}
                if matches:
                    row["properties"][label] = ", ".join(sorted(matches))[:512]
            row["properties"]["Event targets"] = ", ".join(sorted({sender["event"] for _, _, sender in receivers}))[:512]
            sender = receivers[0][2]
            if row["source_class"].casefold() == "engine.trigger" and row["status"] == "review":
                row.setdefault("conversion_class", "unrealtriggerevent")
            if trigger := sender["proximity"]:
                activation = trigger["activation"]
                activation_label = (ACTIVATION_LABELS[activation] if activation < len(ACTIVATION_LABELS)
                                    else f"Source mode {activation}")
                row["properties"].update({"Trigger reach (cm)": f'{trigger["radius_cm"]:g} radius, '
                                                                f'{trigger["half_height_cm"]:g} half-height',
                                          "Trigger activation": activation_label,
                                          "Initially active": str(trigger["active"]),
                                          "Repeat seconds": str(trigger["repeat_seconds"]),
                                          "Retrigger delay": str(trigger["delay_seconds"]),
                                          "Once only": str(trigger["once"])})
            if row.get("conversion_class") == "unrealtriggerevent":
                row["reason"] = sender["review"] or "Saved trigger event targets linked brushes."
                if incoming := chains.get(str(line)):
                    row["properties"]["Trigger state"] = sender["proximity"]["state"]
                    row["properties"]["Saved event senders"] = ", ".join(item["source_name"] for item in incoming)[:512]
                    row["reason"] = sender["review"] or "Saved events control this trigger's activity."
        rows.append(row)
    return rows


def read_unreal_settings(metadata):
    description = bounded(metadata, 64 * 1024)
    settings = json.loads(description)
    if (not isinstance(settings, dict) or not REQUIRED <= settings.keys()
            or type(settings["format"]) is not int or settings["format"] != 1
            or not isinstance(settings["game"], str) or settings["game"] not in UNREAL_PROFILES):
        raise ValueError("Invalid Unreal scene import settings.")
    optional = settings.keys() - REQUIRED - {"bounds_m", "asset_packages"}
    packages = settings.get("asset_packages", [])
    extension = "upk" if settings["game"] == "ascend" else "pkg"
    if (not isinstance(packages, list) or len(packages) > 32
            or not all(isinstance(name, str) and re.fullmatch(rf"[\w.-]{{1,180}}\.{extension}", name, re.I)
                       for name in packages)
            or len({Path(name).stem.casefold() for name in packages}) != len(packages)):
        raise ValueError(f"List up to 32 unique .{extension} companions beside the source map.")
    if "heightmap" in optional:
        if (optional != SUPPLIED_HEIGHTMAP or type(settings["raster_y"]) is not int or settings["raster_y"] not in (-1, 1)
                or not isinstance(settings["heightmap"], str)
                or not re.fullmatch(r"[\w.-]{1,180}\.png", settings["heightmap"], re.I)):
            raise ValueError("Supply the heightmap PNG, metric frame and raster direction together.")
    elif (optional - {"terrain_actor"} or "terrain_actor" in optional
          and (not isinstance(settings["terrain_actor"], str) or not 1 <= len(settings["terrain_actor"]) <= 512)):
        raise ValueError("Choose native terrain or supply a complete heightmap frame.")
    return settings, description


def companion_paths(source, names):
    source = source.resolve(strict=True)
    paths = {}
    for name in names:
        path = source.with_name(name).resolve(strict=True)
        if path == source or path.parent != source.parent or path.name.casefold() != name.casefold():
            raise ValueError("Companion packages must be separate files beside the source map.")
        paths[path.stem.casefold()] = path
    return paths


def convert_unreal(source, output, metadata=None):
    source = Path(source)
    metadata = Path(metadata) if metadata is not None else source.with_suffix(".import.json")
    settings, description = read_unreal_settings(metadata)
    game = settings["game"]
    binary = source.suffix.casefold() in NATIVE_PACKAGE_TYPES
    native = "heightmap" not in settings
    extra_sources = [(metadata, description)]
    if native and NATIVE_PACKAGE_TYPES.get(source.suffix.casefold()) != game:
        raise ValueError("Native terrain requires a binary map package matching the selected game.")
    units = settings["units_per_metre"]
    models = []
    brush_models = {}
    mesh_docs, mesh_issues, companion_receipts = {}, {}, []
    paint = TerrainPaint()
    names = settings.get("asset_packages", [])
    if names and not binary:
        raise ValueError("Companion packages require a binary map source.")
    companions = companion_paths(source, names) if names else {}
    artwork = None
    if binary:
        if game == "vengeance":
            artwork = PackageMaterials(companions, resolve_vengeance_material, "Vengeance")
            read_meshes = partial(read_vengeance_meshes, artwork=artwork)
        else:
            rtc = AscendTextureCache(source.resolve(strict=True).parent)
            artwork = PackageMaterials(companions, partial(resolve_ascend_material, rtc=rtc), "Ascend")
            read_meshes = partial(read_ascend_meshes, artwork=artwork)
        with open_package(source, game) as (report, stream):
            scene = actor_scene(report, stream, units)
            assignments = project_markers(scene, settings["teams"])
            references = {row["model_reference"] for row in project_rows(scene, assignments, settings.get("heightmap"))
                          if "model_reference" in row}
            mesh_docs, mesh_issues = read_meshes(report, stream, units, references)
            imported_meshes = {"unrealmesh:" + row["object_path"].casefold(): row["object_path"].split(".", 1)[0]
                               for row in report["imports"] if row["class_name"].casefold() == "staticmesh"}
            if native:
                terrain, raw, paint = decode_terrain(
                    scene, report, stream, settings.get("terrain_actor"), companions=companions)
                height_package = scene["native_terrain"].get("height_package")
                if height_package:
                    paint.packages[height_package["name"]] = height_package
                companion_receipts.extend(dict(receipt, origin=name) for name, receipt in sorted(paint.packages.items()))
            if game == "vengeance":
                models, issues = read_bsp_models(report, stream, units)
                if issues:
                    scene["bsp_review"] = issues
                artwork.read_package({f"unrealmesh:bsp.{geometry['source']['export']}": (f"{level} BSP", geometry, slots)
                                      for level, geometry, slots in models}, report, stream)
                root, = [node for node in scene["scene"] if node.get("block", "").casefold() == "map"]
                blocks = {node["line"]: node for node in descendants(root)}
                brushes, brush_models, brush_issues, trigger_chains = read_native_brushes(
                    report, stream, units, scene["actors"], blocks, assignments)
                if brushes:
                    scene["native_brushes"] = brushes
                    artwork.read_package(brush_models, report, stream)
                if brush_issues:
                    scene["native_brush_review"] = brush_issues
                if trigger_chains:
                    scene["trigger_chains"] = trigger_chains
        for namespace, path in companions.items():
            pending = references - mesh_docs.keys() - mesh_issues.keys()
            pending = {ref for ref in pending if imported_meshes.get(ref, "").casefold() == namespace}
            if not pending:
                continue
            with open_package(path, game) as (report, stream):
                resolved, issues = read_meshes(report, stream, units, pending, companion=True)
                mesh_docs.update(resolved)
                mesh_issues.update(issues)
                companion_receipts.append(dict(name=report["source"]["name"], origin=path.name,
                                               sha256=report["source"]["sha256"]))
        unresolved = references - mesh_docs.keys() - mesh_issues.keys()
        suffix = "pkg" if game == "vengeance" else "upk"
        for ref in unresolved & imported_meshes.keys():
            namespace = imported_meshes[ref]
            mesh_issues[ref] = (f"List the {artwork.profile} mesh package {namespace}.{suffix} in asset_packages."
                                if namespace.casefold() not in companions else
                                f"No exact static mesh export matched {ref.removeprefix('unrealmesh:')}.")
    else:
        scene = read_unreal_export(source, game, units)
    if native:
        cells_x, cells_y = (vertices - 1 for vertices in scene["native_terrain"]["vertices"])
    else:
        height_path = metadata.with_name(settings["heightmap"])
        raw = bounded(height_path, 16 * 1024 * 1024)
        terrain = load_heightmap(raw, {key: settings[key] for key in METRICS})
        terrain["origin_cm"][1] = settings["origin_m"][1] * 100
        terrain["y_sign"] = settings["raster_y"]
        cells_x = cells_y = CELLS
        extra_sources.append((height_path, raw))
    x, y, _ = terrain["origin_cm"]
    far_y = y + terrain["step_cm"] * cells_y * terrain["y_sign"]
    bounds = [[x, min(y, far_y)], [x + terrain["step_cm"] * cells_x, max(y, far_y)]]
    if "bounds_m" in settings:
        source_bounds = settings["bounds_m"]
        if (not isinstance(source_bounds, list) or len(source_bounds) != 2 or any(
                not isinstance(pair, list) or len(pair) != 2 or not all(finite(v, 20_000) for v in pair)
                for pair in source_bounds)):
            raise ValueError("Enter two metric XY corners for the playable bounds.")
        clip = [[v * 100 for v in pair] for pair in source_bounds]
        if not all(low <= start < end <= high for low, start, end, high in zip(bounds[0], clip[0], clip[1], bounds[1])):
            raise ValueError("Playable bounds must be within the exported terrain.")
        bounds = clip
    minimap = framing(bounds)
    validate_minimap(minimap)
    markers = project_markers(scene, settings["teams"])
    if any(not all(lo <= value <= hi for lo, value, hi in zip(bounds[0], marker["position_cm"], bounds[1]))
           for marker in markers.values()):
        raise ValueError("Every CTF marker must be within the playable bounds.")
    if native and scene["native_terrain"]["export_index"] in markers:
        raise ValueError("A terrain actor cannot be assigned as a CTF marker.")
    placement_components = [] if binary and game == "ascend" else None
    rows = project_rows(scene, markers, settings.get("heightmap"), placement_components=placement_components)
    next(row for row in rows if row["status"] == "boundary")["properties"][PLAYABLE_BOUNDS] = bounds
    interiors, files = {}, dict(paint.files)
    placed = {row["model_reference"] for row in rows if "model_reference" in row}
    mesh_docs = {reference: geometry for reference, geometry in mesh_docs.items() if reference in placed}
    if mesh_docs.keys() & brush_models.keys():
        raise ValueError("Vengeance static mesh path conflicts with an authored brush reference.")
    if len(mesh_docs) + len(models) + len(brush_models) > MAX_INTAKE_INTERIORS:
        raise ValueError(f"Drafts support up to {MAX_INTAKE_INTERIORS} distinct Unreal meshes and BSP models.")
    mesh_bytes = 0
    brush_geometry = {reference: model[1] for reference, model in brush_models.items()}
    for reference, geometry in (mesh_docs | brush_geometry).items():
        data = encoded(geometry)
        mesh_bytes += len(data)
        if len(data) > MAX_GEOMETRY_BYTES or mesh_bytes > MAX_INTAKE_INTERIOR_BYTES:
            raise ValueError("Oversized Unreal draft mesh geometry.")
        filename = f"interior-{len(interiors) + 1:04d}.json"
        interiors[reference] = filename
        files[filename] = data
    for row in rows:
        reference = row.get("model_reference")
        if reference in mesh_docs:
            if any("file" in record for record in artwork.bindings[reference].values()):
                row["reason"] = "Imported static mesh geometry, collision and available source artwork."
            else:
                row["reason"] = "Imported static mesh geometry and collision. Assign destination materials as needed."
        elif reference in mesh_issues:
            row["reason"] += " · " + mesh_issues[reference]
    placement_models = {}
    if placement_components:
        with open_package(source, game) as (report, stream):
            if scene["source"]["sha256"] != report["source"]["sha256"]:
                raise ValueError("Ascend map changed while resolving its component materials.")
            placement_models, issues = ascend_placed_materials(placement_components, mesh_docs, stream)
            artwork.read_package(placement_models, report, stream, component_materials=True)
        for row, reason in issues:
            row["reason"] = f'{row["reason"]} · {reason}'
    if artwork:
        companion_receipts.extend(dict(receipt, origin=name) for name, receipt in sorted(artwork.packages.items()))
    bsp_references = []
    for level, geometry, _ in models:
        reference = f"unrealmesh:bsp.{geometry['source']['export']}"
        if reference in interiors:
            raise ValueError(f"Vengeance static mesh path conflicts with the BSP reference {reference}.")
        filename = f"interior-{len(interiors) + 1:04d}.json"
        interiors[reference] = filename
        files[filename] = encoded(geometry)
        bsp_references.append(reference)
        rows.append(dict(id=len(rows) + 1, parent=None, source_class="Model", name=f"{level} BSP",
                         status="review", reason="Compiled Vengeance BSP geometry",
                         properties={}, conversion_class="unrealmeshactor", model_reference=reference,
                         transform=dict(position_cm=[0, 0, 0], rotation_xyzw=[0, 0, 0, 1], scale=[1, 1, 1])))
    for issue in scene.get("bsp_review", ()):
        rows.append(dict(id=len(rows) + 1, parent=None, source_class="Model", name=f"{issue['level']} BSP",
                         status="review", reason=issue["reason"], properties={}))
    mesh_artwork = extended_materials = surface_detail = False
    if artwork:
        mesh_artwork = any("file" in binding for reference in (*mesh_docs, *placement_models)
                           for binding in artwork.bindings[reference].values())
        extended_materials = any(has_shader_inputs(binding) for reference in interiors
                                 for binding in artwork.bindings.get(reference, {}).values())
        surface_detail = any(binding.keys() & SURFACE_DETAIL for reference in interiors
                             for binding in artwork.bindings.get(reference, {}).values())
        texture_models = interiors if mesh_artwork or extended_materials or brush_models else bsp_references
        texture_index = {interiors[reference]: artwork.bindings[reference] for reference in texture_models
                         if reference in artwork.bindings}
        texture_index.update((key, artwork.bindings[key]) for key in placement_models)
        texture_files = {texture["file"] for slots in texture_index.values() for binding in slots.values()
                         for texture in texture_records(binding)}
        if texture_files:
            files.update((name, artwork.textures[name]) for name in sorted(texture_files))
            files[TEXTURE_INDEX] = encoded(texture_index)
        for reference in (*interiors, *placement_models):
            for issue in artwork.review.get(reference, ()):
                label = f"{issue['model']} material {issue['slot']} · {issue['material']}"
                rows.append(dict(id=len(rows) + 1, parent=None, source_class="Material", name=label[:512],
                                 status="review", reason=issue["reason"], properties={}))
                if reference in bsp_references:
                    source_issue = {key: issue[key] for key in ("material", "slot", "reason")}
                    source_issue["level"] = issue["model"].removesuffix(" BSP")
                    scene.setdefault("bsp_material_review", []).append(source_issue)
                else:
                    scene.setdefault("mesh_material_review", []).append(issue)
    files.update({"objects.json": encoded(rows), "terrain.json": encoded(terrain), "height.png": raw,
                  "minimap.png": terrain_png(terrain, bounds)})
    scripts = [Path(__file__), Path(__file__).with_name("unreal_legacy_reader.py")]
    if binary:
        scripts.extend(Path(__file__).with_name(name) for name in ("unreal_binary_scene.py", "unreal_package_reader.py",
                                                                  "model_textures.py"))
        if game == "vengeance":
            scripts.extend(Path(__file__).with_name(name) for name in (
                "vengeance_bsp.py", "vengeance_materials.py", "vengeance_normal_maps.py", "vengeance_static_mesh.py", "vengeance_terrain.py",
                "vengeance_terrain_layers.py", "vengeance_brushes.py", "vengeance_mover_plan.py", "vengeance_triggers.py"))
        else:
            scripts.extend(Path(__file__).with_name(name) for name in ("ascend_static_mesh.py", "ascend_materials.py",
                                                                      "ascend_texture_cache.py"))
    if native:
        scripts.append(Path(__file__).with_name("unreal_native_terrain.py"))
    version = UNREAL_INTAKE_VERSIONS[0]
    if mesh_docs and game == "ascend":
        version = ASCEND_MODEL_ART_VERSION if mesh_artwork else ASCEND_MESH_INTAKE_VERSION
        if mesh_artwork and placement_models:
            version = ASCEND_PLACEMENT_ART_VERSION
    if paint.masks:
        version = VENGEANCE_PAINT_INTAKE_VERSION
    terrain_art = any("file" in item for item in paint.artwork.values())
    if terrain_art:
        version = VENGEANCE_ART_INTAKE_VERSION
    if TEXTURE_INDEX in files and game == "vengeance":
        version = VENGEANCE_BSP_MATERIALS_VERSION
    if mesh_docs and game == "vengeance":
        version = VENGEANCE_MESH_MATERIALS_VERSION if mesh_artwork else VENGEANCE_STATIC_MESH_VERSION
    if extended_materials:
        version = VENGEANCE_SHADER_INPUTS_VERSION
    if surface_detail:
        version = VENGEANCE_SURFACE_DETAIL_VERSION
    if brush_models:
        brushes = scene["native_brushes"].values()
        elevators = [brush["behavior"] for brush in brushes if brush["type"] == "elevator"]
        if scene.get("trigger_chains"):
            version = VENGEANCE_TRIGGER_CHAIN_VERSION
        elif any(brush["behavior"].get("opens_once") for brush in brushes):
            version = VENGEANCE_PERMANENT_DOOR_VERSION
        elif any(brush["type"] == "mover" and brush["behavior"].get("trigger_senders") for brush in brushes):
            version = VENGEANCE_TRIGGER_DOOR_VERSION
        elif elevators:
            version = (VENGEANCE_ELEVATOR_LINK_VERSION if any(lift["trigger_senders"] for lift in elevators)
                       else VENGEANCE_ELEVATOR_VERSION)
            if version == VENGEANCE_ELEVATOR_VERSION:
                for lift in elevators:
                    del lift["trigger_senders"]
        else:
            timed_doors = any("bump_type" in brush["behavior"] for brush in brushes)
            version = VENGEANCE_DOOR_VERSION if timed_doors else VENGEANCE_MOVER_PLAYBACK_VERSION
    files[UNREAL_SCENE] = encoded(scene)
    receipts = {}
    for receipt in companion_receipts:
        name = receipt["name"]
        if name in receipts and receipts[name]["sha256"] != receipt["sha256"]:
            raise ValueError(f"Companion package changed during import: {name}")
        receipts[name] = receipt
    header = dict(format=version, reader=UNREAL_READER, profile=UNREAL_PROFILES[game],
                  source_game=scene["source_game"], name=source.stem, minimap=minimap,
                  profile_sha256=sha256(b"".join(path.read_bytes() for path in scripts)).hexdigest(),
                  sources=[dict(scene["source"], origin=source.name),
                           *(dict(name=path.name, origin=path.name, sha256=sha256(data).hexdigest())
                             for path, data in extra_sources), *receipts.values()],
                  coverage=dict(Counter(row["status"] for row in rows)),
                  files={name: sha256(data).hexdigest() for name, data in files.items()})
    if interiors:
        header.update(interiors=interiors, interior_review={})
    if paint.masks:
        header["terrain_paint"] = dict(vertices=scene["native_terrain"]["vertices"], masks=paint.masks)
    if terrain_art:
        header.update(terrain_art=paint.artwork, terrain_uv=paint.projection)
    return publish_intake(header, files, Path(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--metadata", type=Path)
    arguments = parser.parse_args()
    try:
        print(convert_unreal(arguments.source, arguments.output, arguments.metadata))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
