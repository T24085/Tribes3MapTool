"""Build an editable Unreal draft from a verified neutral intake."""

import json
from collections import Counter
from hashlib import sha256
import math
from pathlib import Path
import os
import subprocess

import unreal

from conversion_rules import create_snapshot, draft_rules_path, profile_config, project, write
from profile_exchange import active_profile
from intake_contract import (UNREAL_SCENE, bounded, intake_file_limit, intake_header, load_intake, load_intake_geometry,
                             load_mission_audio, playable_bounds)
from interior_geometry import VENGEANCE_BSP, VENGEANCE_STATIC_MESH
from map_pipeline import Outcome, PipelineRun, pipeline_config
from mesh_mappings import material_reference, placements, source_reference
from mission_audio import audio_playback
from mission_equipment import mission_equipment, power_context
from mission_weather import cloud_layers, mission_weather, precipitation_emitters
from native_terrain_paint import NativeTerrainPaint
from object_catalog import applicable_mappings, build_rows, classify, load_catalog
from perimeter import (POLYGON_REQUIRED, PolygonWalls, flight_ceiling_heights, kill_floor_height, plan_boundaries,
                       place_flag_bounds, place_flight_ceiling, place_kill_floor, place_walls, verify_walls,
                       wall_appearance)
from perimeter_collision import validate_channels
from sky_intake import SKY_INDEX, load_sky_index
from source_library import record_draft
from source_profiles import (BRUSH_PREVIEW_REFERENCE, PREFIX, T2_GENERATOR_SCOPE_VERSIONS, VENGEANCE_BRUSH_VERSIONS,
                             VENGEANCE_ELEVATOR_VERSION, VENGEANCE_MOVER_PLAYBACK_VERSION, imported_draft)
from terrain_art import SOURCE_ART, available_art
from terrain_layers import ART_SLOT_OFFSET, FALLBACK, PALETTE_SURFACES, SIZE, layer_choices, load_weights, painted_cells
from terrain_grid import ARTWORK_UV, TiledTerrain, footprint


def choose(game=None):
    import t3_map_editor as editor
    from game_workflows import game_label

    tool = Path(os.environ["T3_CONVERTER_TOOL"])
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    meshes = sorted(str(data.package_name)
                    for data in registry.get_assets_by_path("/Game/Community", recursive=True)
                    if str(data.asset_class_path.asset_name) == "StaticMesh")
    drafts = editor.list_drafts()
    result = subprocess.run(
        [
            os.environ["T3_CONVERTER_PYTHON"],
            "-E",
            "-X",
            "utf8",
            str(tool / "conversion_window.py"),
            os.environ["T3_CONVERTER_LIBRARY"],
        ],
        input=json.dumps(dict(meshes=meshes, drafts=drafts, game=game_label(game) if game else None)),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()[-700:] or "The conversion window failed."
        )
    if result.stdout.strip():
        selection = json.loads(result.stdout)
        required = {"manifest", "perimeter", "flag_inset_m", "mesh_mappings", "terrain_layers"}
        if (not isinstance(selection, dict)
                or not required <= selection.keys() <= required | {"wall_limits", "flag_polygon_m", "wall_polygon_m",
                                                                    "wall_block_channels", "wall_visual", "warn_players",
                                                                    "warning_polygon_m", "warning_limits", "warning_audience",
                                                                    "flag_limits", "flag_return_delay_s", "kill_floor_m",
                                                                    "flight_ceiling"}
                or not isinstance(selection["manifest"], str) or type(selection["perimeter"]) is not bool):
            raise ValueError("The conversion window returned an invalid import selection.")
        mappings = selection["mesh_mappings"]
        if not isinstance(mappings, dict) or any(
            not isinstance(path, str) or path not in meshes for path in mappings.values()
        ):
            raise ValueError("A selected mesh is no longer in the project.")
        return import_map(Path(selection["manifest"]), perimeter=selection["perimeter"],
                          flag_inset_m=selection["flag_inset_m"], mesh_mappings=mappings,
                          terrain_layers=selection["terrain_layers"], wall_limits=selection.get("wall_limits"),
                          flag_polygon_m=selection.get("flag_polygon_m"), wall_polygon_m=selection.get("wall_polygon_m"),
                          wall_block_channels=selection.get("wall_block_channels"), wall_visual=selection.get("wall_visual"),
                          warn_players=selection.get("warn_players", False),
                          warning_polygon_m=selection.get("warning_polygon_m"), warning_limits=selection.get("warning_limits"),
                          warning_audience=selection.get("warning_audience"), flag_limits=selection.get("flag_limits"),
                          flag_return_delay_s=selection.get("flag_return_delay_s"),
                          kill_floor_m=selection.get("kill_floor_m"),
                          flight_ceiling=selection.get("flight_ceiling"))


def terrain_mesh(ident, t, weights, *, tile_cm=None, name="Terrain", uv_origin=(0, 0), paint=None, materials=None,
                 artwork_flags=None, mask_frame=None, source_material=None):
    if tile_cm is None:
        tile_cm = profile_config()["conversions"]["terrain.tile_cm"]["value"]
    step = t["step_cm"]
    scale = t["cm_per_m"]
    y_sign = t.get("y_sign", -1)
    heights = t["height_m"]
    unwrapped = len(heights) == (SIZE + 1) ** 2
    continuous_paint = weights is not None and len(weights) == (SIZE + 1) ** 2
    holes = set(t["holes"])
    turned = set(t["turned_quads"]) if "turned_quads" in t else None
    verts = []
    uv = []
    mask_uv = []
    if mask_frame is not None:
        x0, y0, width, height = mask_frame
    normals = []
    colors = []
    tri = []
    face_surfaces = []
    for y in range(SIZE + 1):
        for x in range(SIZE + 1):
            sample = (y % SIZE) * SIZE + x % SIZE
            height_sample = y * (SIZE + 1) + x if unwrapped else sample
            verts.append(
                unreal.Vector(
                    x * step,
                    y_sign * y * step,
                    heights[height_sample] * scale,
                )
            )
            uv.append(unreal.Vector2D((x * step + uv_origin[0]) / tile_cm, (y * step + uv_origin[1]) / tile_cm))
            if mask_frame is not None:
                mask_uv.append(unreal.Vector2D(min(x0 + x, width - 1) / (width - 1),
                                               min(y0 + y, height - 1) / (height - 1)))
            if artwork_flags is not None:
                # The UV corners for artwork cells need their own vertices. Share the geometric normal.
                left, right = max(0, x - 1), min(SIZE, x + 1)
                front, back = max(0, y - 1), min(SIZE, y + 1)
                dx = (heights[y * (SIZE + 1) + right] - heights[y * (SIZE + 1) + left]) * scale / ((right - left) * step)
                dy = (heights[back * (SIZE + 1) + x] - heights[front * (SIZE + 1) + x]) * scale / ((back - front) * step * y_sign)
                length = math.sqrt(1 + dx * dx + dy * dy)
                normals.append(unreal.Vector(-dx / length, -dy / length, 1 / length))
            if weights is not None:
                colors.append(unreal.LinearColor(*weights[height_sample if continuous_paint else sample], 1.0))
    for y in range(256):
        for x in range(256):
            cell = y * SIZE + x
            if cell in holes:
                continue
            a = y * 257 + x
            b = a + 1
            c = b + 257
            d = a + 257
            if artwork_flags is not None and paint[cell] >= ART_SLOT_OFFSET:
                first = len(verts)
                for vertex, point in zip((a, b, c, d), ARTWORK_UV[artwork_flags[cell] & 7]):
                    verts.append(verts[vertex])
                    normals.append(normals[vertex])
                    uv.append(unreal.Vector2D(*point))
                a, b, c, d = range(first, first + 4)
            flip = cell in turned if turned is not None else (x ^ y) & 1
            faces = ((a, b, d), (b, c, d)) if flip else ((a, b, c), (a, c, d))
            for first, second, third in faces:
                tri.append(
                    unreal.IntVector(first, second, third)
                    if y_sign < 0
                    else unreal.IntVector(first, third, second)
                )
            if paint is not None:
                face_surfaces.extend([paint[cell]] * len(faces))
    mesh = unreal.DynamicMesh()
    buffers = unreal.GeometryScriptSimpleMeshBuffers(
        vertices=verts, normals=normals, uv0=uv, uv1=mask_uv, vertex_colors=colors, triangles=tri
    )
    mesh.append_buffers_to_mesh(buffers)
    slots = sorted(set(face_surfaces))
    if len(slots) > 1:
        ids = {surface: slot for slot, surface in enumerate(slots)}
        material_ids = unreal.GeometryScript_List.convert_array_to_index_list([ids[surface] for surface in face_surfaces])
        unreal.GeometryScript_Materials.set_all_triangle_material_i_ds(mesh, material_ids)
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=artwork_flags is None,
        enable_recompute_tangents=True,
        enable_collision=True,
        collision_mode=unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE,
    )
    asset, status = (
        unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
            mesh, f"/Game/Community/{ident}/{name}", options
        )
    )
    if not asset or status != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise ValueError("Could not build the terrain asset.")
    if source_material is not None:
        asset.set_material(0, source_material)
    if slots:
        static_slots = list(asset.get_editor_property("static_materials"))
        if len(static_slots) != len(slots):
            raise ValueError("Unreal did not preserve the painted terrain sections.")
        for slot, surface in zip(static_slots, slots):
            slot.set_editor_property("material_interface", materials[surface])
            label = PALETTE_SURFACES[surface] if surface < ART_SLOT_OFFSET else f"Source_{surface - ART_SLOT_OFFSET:03d}"
            slot.set_editor_property("material_slot_name", label)
        asset.set_editor_property("static_materials", static_slots)
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
        raise RuntimeError("Could not save the terrain mesh for the draft.")
    return asset


def terrain_material(editor, ident, channels, *, name="M_Terrain", source_art=None):
    lib = unreal.MaterialEditingLibrary
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, f"/Game/Community/{ident}", unreal.Material, unreal.MaterialFactoryNew())
    if not material:
        raise ValueError("Could not create the terrain blend material.")

    def node(kind, x, y):
        return lib.create_material_expression(material, kind, x, y)

    def connect(source, output, target, pin):
        if not lib.connect_material_expressions(source, output, target, pin):
            raise ValueError(f"Could not connect the terrain material's {pin} input.")

    blend = len(channels) > 1
    vertex = node(unreal.MaterialExpressionVertexColor, -1200, -320) if blend else None
    for group, (suffix, output, prop, sampler) in enumerate(editor.pbr_samples()):
        if source_art is not None and prop == unreal.MaterialProperty.MP_BASE_COLOR:
            from native_terrain_material_unreal import apply_source_color

            apply_source_color(editor, material, ident, *source_art)
            continue
        terms = []
        for position, channel in enumerate(channels):
            y = 900 * group + 260 * position
            surface = PALETTE_SURFACES[channel]
            sample = editor.palette_sample(material, surface, suffix, sampler, -1000, y,
                                           parameter_name=surface + suffix)
            if blend:
                term = node(unreal.MaterialExpressionMultiply, -660, y)
                connect(sample, output, term, "A")
                connect(vertex, "RGB"[channel], term, "B")
                terms.append((term, ""))
            else:
                terms.append((sample, output))
        current, pin = terms[0]
        for position, (term, term_pin) in enumerate(terms[1:]):
            total = node(unreal.MaterialExpressionAdd, -400 + 170 * position, 900 * group + 220)
            connect(current, pin, total, "A")
            connect(term, term_pin, total, "B")
            current, pin = total, ""
        if blend and prop == unreal.MaterialProperty.MP_NORMAL:
            normal = node(unreal.MaterialExpressionNormalize, -30, 900 * group + 220)
            connect(current, pin, normal, "")
            current, pin = normal, ""
        if not lib.connect_material_property(current, pin, prop):
            raise ValueError(f"Could not connect the terrain material's {suffix.lower()} output.")
    lib.recompile_material(material)
    if not editor.ASSETS.save_loaded_asset(material, only_if_is_dirty=False):
        raise ValueError("Could not save the terrain blend material.")
    return material


def terrain_art_material(editor, ident, slot, folder, filename, digest):
    from texture_batches_unreal import import_texture

    source = (folder / filename).resolve(strict=True)
    if source.parent != folder or sha256(bounded(source, 16 * 1024 * 1024)).hexdigest() != digest:
        raise ValueError("The terrain artwork changed after the intake was verified.")
    destination = f"/Game/Community/{ident}"
    texture = import_texture(source, destination, unreal.Texture2D, name=f"T_TerrainArt_{slot:03d}")
    texture.set_editor_property("srgb", True)
    for axis in ("address_x", "address_y"):
        texture.set_editor_property(axis, unreal.TextureAddress.TA_CLAMP)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        f"M_TerrainArt_{slot:03d}", destination, unreal.Material, unreal.MaterialFactoryNew())
    if not material:
        raise ValueError("Unreal could not create the terrain artwork material.")
    lib = unreal.MaterialEditingLibrary
    sample = lib.create_material_expression(material, unreal.MaterialExpressionTextureSample, -280, 0)
    sample.set_editor_property("texture", texture)
    if not lib.connect_material_property(sample, "RGB", unreal.MaterialProperty.MP_BASE_COLOR):
        raise ValueError("Unreal could not bind the terrain artwork.")
    lib.recompile_material(material)
    for asset in (texture, material):
        if not editor.ASSETS.save_loaded_asset(asset, only_if_is_dirty=False):
            raise ValueError("Unreal could not save the terrain artwork.")
    return material


def place_static_objects(actors, planned, meshes, material_overrides=None, lighting_links=None, native_modes=None,
                         mover_tracks=None, draft=None, triggers=None, acoustic_rooms=(), light_commands=None):
    material_overrides = material_overrides or {}
    mover_tracks = mover_tracks or {}
    placed = []
    for row, path, location, rotation, scale in planned:
        motion = mover_tracks.get(row["id"])
        if motion:
            index, moves, behavior, door_bump, senders = motion
            start = moves[0][1]
            location = start[:3]
            angle = unreal.Rotator(roll=start[3], pitch=start[4], yaw=start[5])
        else:
            angle = unreal.Quat(*rotation).rotator()
        actor = actors.spawn_actor_from_class(
            unreal.StaticMeshActor, unreal.Vector(*location), angle
        )
        if not actor or not actor.static_mesh_component.set_static_mesh(meshes[path]):
            raise RuntimeError(f"Could not place mesh for {row['name'] or row['id']}.")
        actor.set_actor_scale3d(unreal.Vector(*scale))
        if str(row.get('properties', {}).get('CastShadow')) == 'False':
            # The source mesh cast no shadow (sky domes, skyline backdrops, grass).
            actor.static_mesh_component.set_cast_shadow(False)
        if str(row.get('properties', {}).get('Collision')) == 'False':
            # The source mesh did not collide (grass, small props): players pass through it.
            actor.static_mesh_component.set_collision_profile_name('NoCollision')
        if native_modes and path in native_modes:
            visible, solid, format_version = native_modes[path]
            if format_version == VENGEANCE_STATIC_MESH:
                properties = {key.casefold(): value for key, value in row['properties'].items()}
                visible = visible and properties.get('bhidden') != 'True'
                overlaps_only = all(properties.get(key) == 'False' for key in ('bblockactors', 'bblockplayers'))
                if properties.get('bblockhavok') == 'False' and (overlaps_only or properties.get('bcollideactors') == 'False'):
                    solid = False
            actor.static_mesh_component.set_visibility(visible)
            if not solid:
                # Unreal restores the named profile when the level is reopened.
                actor.static_mesh_component.set_collision_profile_name('NoCollision')
        if (source_reference(row) or '').startswith(BRUSH_PREVIEW_REFERENCE):
            if motion:
                actor.static_mesh_component.set_mobility(unreal.ComponentMobility.MOVABLE)
                actor.static_mesh_component.set_visibility(not behavior["hidden"])
                if native_modes and path in native_modes and native_modes[path][1]:
                    actor.static_mesh_component.set_collision_profile_name('BlockAllDynamic')
                actor.tags = [f"T3MovingBrush_{index}"]
            else:
                actor.set_editor_property('is_editor_only_actor', True)
                actor.static_mesh_component.set_collision_profile_name('NoCollision')
        for slot, material in material_overrides.get(material_reference(row, material_overrides), {}).items():
            actor.static_mesh_component.set_material(slot, material)
        if lighting_links and row['id'] in lighting_links:
            from equipment_unreal import mark_interior_lighting

            mark_interior_lighting(actor, *lighting_links[row['id']])
        if row['id'] in acoustic_rooms:
            from outside_audio import room_tag

            actor.tags = list(actor.tags) + [unreal.Name(room_tag(row["id"], meshes[path].get_path_name()))]
        if light_commands and row["id"] in light_commands:
            from mission_lighting import BINDING_TAG

            actor.tags = list(actor.tags) + [unreal.Name(f"{BINDING_TAG}{ident}") for ident in light_commands[row["id"]]]
        actor.set_actor_label(row["name"] or f"{row['source_class']} {row['id']}")
        actor.set_folder_path("TRIBES Import/Moving brushes" if motion else "TRIBES Import/Mapped meshes")
        if row["source_class"].casefold() in ("interiorinstance", "interiorshape"):
            placed.append(actor)
        if motion:
            from draft_movers import place_mover

            sequence = place_mover(actors, draft, actor, index, moves, door_bump=door_bump, linked=bool(senders),
                                   opens_once=behavior.get("opens_once", False))
            if senders:
                from draft_elevators import add_trigger_receivers

                add_trigger_receivers(triggers, senders, sequence)
    return placed


def plan_native_brushes(folder, header, rows, manual):
    if header["format"] not in VENGEANCE_BRUSH_VERSIONS or header["format"] < VENGEANCE_MOVER_PLAYBACK_VERSION:
        return {}, set(), [], {}
    from vengeance_elevators import linked_elevator_senders, zero_gravity_hull
    from vengeance_mover_plan import bump_track, loop_track, plan_proximity_chains, trigger_track

    path = (folder / UNREAL_SCENE).resolve(strict=True)
    if path.parent != folder:
        raise ValueError("Intake file outside the package.")
    data = bounded(path, intake_file_limit(UNREAL_SCENE, header["format"]))
    if sha256(data).hexdigest() != header["files"][UNREAL_SCENE]:
        raise ValueError("Intake changed while planning the mover tracks.")
    scene = json.loads(data)
    brushes = scene["native_brushes"]
    source_chains = scene.get("trigger_chains", {})
    units = scene.get("coordinate_frame", {}).get("source_units_per_m")
    tracks, lifts = {}, []
    bridge = getattr(unreal, "CommunityMapMotionLibrary", None)
    door_bridge = hasattr(bridge, "bind_timed_door")
    lift_bridge = hasattr(bridge, "set_convex_volume_mesh") and hasattr(bridge, "bind_zero_gravity_volumes")
    linked_bridge = all(hasattr(bridge, symbol)
                        for symbol in ("build_linked_elevator_class", "configure_linked_elevator", "bind_elevator_trigger"))
    repeat_bridge = hasattr(bridge, "bind_repeating_elevator_trigger")
    trigger_bridge = lift_bridge and hasattr(bridge, "bind_brush_trigger")
    chain_ids = {int(index) for index in source_chains} if hasattr(bridge, "build_proximity_receiver_class") else set()
    permanent_bridge = hasattr(bridge, "configure_permanent_door")
    link_groups = Counter(sender["event"] for brush in brushes.values()
                          for sender in brush["behavior"].get("trigger_senders", ()))
    waiting_doors = waiting_lifts = 0
    nonblocking_brushes = 0
    for row in rows:
        reference = source_reference(row)
        if not reference or reference in manual or not reference.startswith(BRUSH_PREVIEW_REFERENCE):
            continue
        index = row["properties"]["Brush export"]
        brush = brushes[index]
        if brush["type"] == "elevator":
            if header["format"] < VENGEANCE_ELEVATOR_VERSION:
                continue
            if type(units) not in (float, int) or not math.isfinite(units) or units <= 0:
                raise ValueError("Invalid source distance units for elevator volumes.")
            name = header["interiors"][reference]
            digest = header["files"][name]
            shape, _ = load_intake_geometry(folder, name, digest)
            behavior = brush["behavior"]
            senders = linked_elevator_senders(behavior, chain_ids)
            if hull := zero_gravity_hull(brush, shape, units, linked=bool(senders), chains=chain_ids):
                repeating = any(sender["proximity"]["repeat_seconds"] > 0 and not sender["proximity"]["once"]
                                for sender in senders)
                if lift_bridge and (not senders or linked_bridge) and (not repeating or repeat_bridge):
                    lifts.append((row, index, shape, digest, hull, behavior, senders))
                else:
                    waiting_lifts += 1
            continue
        door_bump = None
        senders = ()
        moves = loop_track(brush)
        if not moves:
            moves = bump_track(brush)
            if moves:
                door_bump = brush["behavior"]["bump_type"]
            else:
                moves, senders = trigger_track(brush, chain_ids)
        if moves and (senders or door_bump is not None):
            if ((not permanent_bridge and brush["behavior"].get("opens_once"))
                    or not (trigger_bridge if senders else door_bridge)):
                waiting_doors += 1
                continue
            name = header["interiors"][reference]
            shape, _ = load_intake_geometry(folder, name, header["files"][name])
            if not shape["collision"]["triangles"]:
                nonblocking_brushes += 1
                continue
        if moves:
            tracks[row["id"]] = (index, moves, brush["behavior"], door_bump, senders)
    if waiting_doors:
        unreal.log_warning(f"{waiting_doors} timed doors need the updated Community Map Motion editor plugin.")
    if waiting_lifts:
        unreal.log_warning(f"{waiting_lifts} elevator fields need the updated Community Map Motion editor plugin.")
    used_groups = Counter(sender["event"] for *_, senders in (*lifts, *tracks.values()) for sender in senders)
    if incomplete_groups := {tag for tag, used in used_groups.items() if used != link_groups[tag]}:
        lifts = [entry for entry in lifts if not any(sender["event"] in incomplete_groups for sender in entry[-1])]
        tracks = {key: entry for key, entry in tracks.items()
                  if not any(sender["event"] in incomplete_groups for sender in entry[-1])}
        unreal.log_warning(f"{len(incomplete_groups)} brush trigger groups need review of all linked receivers.")
    available = {int(entry[1]) for entry in lifts} | {int(entry[0]) for entry in tracks.values()}
    relays, rejected = plan_proximity_chains(source_chains, brushes, available)
    if rejected:
        lifts = [entry for entry in lifts if int(entry[1]) not in rejected]
        tracks = {key: entry for key, entry in tracks.items() if int(entry[0]) not in rejected}
        unreal.log_warning(f"{len(rejected)} brush receivers have incomplete or cyclic trigger chains. See Object Review.")
    if nonblocking_brushes:
        unreal.log_warning(f"{nonblocking_brushes} timed doors need a solid brush surface to receive hits.")
    if tracks and not hasattr(unreal, "LevelSequenceFactoryNew"):
        raise ValueError("Enable Level Sequence Editor and Sequencer Scripting to import moving brushes.")
    active_references = {brushes[entry[0]]["reference"] for entry in tracks.values()}
    return tracks, active_references, lifts, relays


def place_local_lights(actors, rows):
    for row in rows:
        if row["status"] == "excluded" or "local_light" not in row:
            continue
        light, transform = row["local_light"], row["transform"]
        spotlight = light["kind"] == "spot"
        actor = actors.spawn_actor_from_class(
            unreal.SpotLight if spotlight else unreal.PointLight,
            unreal.Vector(*transform["position_cm"]),
            unreal.Quat(*transform["rotation_xyzw"]).rotator(),
        )
        actor.set_actor_label(row["name"])
        component = actor.light_component
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_use_inverse_squared_falloff(False)
        component.set_attenuation_radius(light["radius_cm"])
        component.set_intensity(component.get_editor_property("intensity") * light["brightness"])
        component.set_editor_property("cast_shadows", light.get("cast_shadows", False))
        if "color" in light:
            component.set_light_color(unreal.LinearColor(*light["color"], 1))
        if "falloff" in light:
            component.set_light_falloff_exponent(light["falloff"])
        if spotlight:
            component.set_outer_cone_angle(light["outer_angle"])
            component.set_inner_cone_angle(light["inner_angle"])


def has_sky_dome(actors):
    """True when a placed mesh uses an Is Sky material: the map brings its own painted sky."""
    for actor in actors.get_all_level_actors():
        if not isinstance(actor, unreal.StaticMeshActor):
            continue
        component = actor.static_mesh_component
        for slot in range(component.get_num_materials()):
            material = component.get_material(slot)
            base = material.get_base_material() if material else None
            if base and base.get_editor_property('is_sky'):
                return True
    return False


def ascend_environment():
    from environment_ascend import convert

    return convert


# One MILF environment converter per source engine (T3BackEndServer#146). Sources not listed here
# still use place_environment until their converter is written.
MILF_ENVIRONMENT_CONVERTERS = {"ascend-ctf": ascend_environment}


def place_environment(actors, settings, values=None):
    if values is None:
        profile = profile_config()
        values = {key: row["value"] for key, row in
                  project(profile, settings, {"objects": {}, "game": None, "map": None}, {}).items()}
    sun = settings.get("sun", {})
    pitch, yaw = -48, 120
    if "direction" in sun:
        x, y, z = sun["direction"]
        pitch = math.degrees(math.atan2(z, math.hypot(x, y)))
        yaw = math.degrees(math.atan2(y, x))
    light = actors.spawn_actor_from_class(
        unreal.DirectionalLight, unreal.Vector(0, 0, 3000), unreal.Rotator(pitch=pitch, yaw=yaw)
    )
    sky = actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 1500))
    actors.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
    if sun:
        light.light_component.set_editor_property("atmosphere_sun_light", True)
    if "color" in sun:
        light.set_light_color(unreal.LinearColor(*sun["color"], 1))
    if "ambient" in sun:
        sky.get_component_by_class(unreal.SkyLightComponent).set_light_color(
            unreal.LinearColor(*sun["ambient"], 1)
        )
    for component, scale in (
        (light.light_component, values["sun.intensity_scale"]),
        (sky.get_component_by_class(unreal.SkyLightComponent), values["sun.ambient_scale"]),
    ):
        if scale != 1:
            component.set_intensity(component.get_editor_property("intensity") * scale)
    fog = settings.get("fog", {})
    start, density = values["fog.start_cm"], values["fog.density"]
    if start is None or density is None:
        return
    actor = actors.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, fog.get("height_cm", 0)))
    component = actor.get_component_by_class(unreal.ExponentialHeightFogComponent)
    component.set_start_distance(start)
    component.set_fog_density(density)
    if "height_falloff" in fog:
        component.set_fog_height_falloff(fog["height_falloff"])
    if "max_opacity" in fog:
        component.set_fog_max_opacity(fog["max_opacity"])
    if "color" in fog:
        component.set_fog_inscattering_color(unreal.LinearColor(*fog["color"], 1))


def place_equipment_markers(actors, records, rows, source_hash=None):
    from equipment_unreal import INTAKE_TAG, SOURCE_TAG, store_source

    if not records['equipment']:
        return
    source_hash = source_hash or store_source(records)
    names = {row['id']: row['name'] for row in rows}
    for record in records['equipment']:
        transform = record['transform']
        if transform is None:
            continue
        shield = record['role'] == 'force_shield'
        actor = actors.spawn_actor_from_class(
            unreal.TriggerBox if shield else unreal.TargetPoint, unreal.Vector(*transform['position_cm']),
            unreal.Quat(*transform['rotation_xyzw']).rotator(),
        )
        if not actor:
            raise ValueError('Could not place the equipment review marker.')
        actor.set_editor_property('is_editor_only_actor', True)
        if shield:
            box = actor.get_component_by_class(unreal.BoxComponent)
            box.set_box_extent(unreal.Vector(25, 200, 160), False)
            box.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            box.set_editor_property('generate_overlap_events', False)
        actor.set_editor_property('tags', [SOURCE_TAG+str(record['source_id']), INTAKE_TAG+source_hash])
        name = names[record['source_id']] or record['datablock']
        role = 'Force shield opening' if shield else record['role'].replace('_', ' ').capitalize()
        actor.set_actor_label(f'{role} · {name}')
        actor.set_folder_path('TRIBES CTF/Equipment review')


class DraftImport:
    def __init__(self, editor, manifest, options, recorded_rules, batch_target):
        self.editor = editor
        self.manifest = Path(manifest)
        self.folder = self.manifest.resolve().parent
        library = os.environ.get("T3_CONVERTER_LIBRARY")
        self.library = Path(library) if library else None
        self.options = options
        self.recorded_rules = recorded_rules
        self.batch_target = batch_target
        self.rendering = "-nullrhi" not in unreal.SystemLibrary.get_command_line().lower().split()
        self.source_profile = intake_header(self.manifest)["profile"]
        self.bundle = {} if recorded_rules is not None else active_profile(self.library, self.source_profile) or {}
        self.profile = pipeline_config(self.library, snapshot=recorded_rules, recorded=recorded_rules is not None,
                                       source_profile=self.source_profile, bundle=self.bundle)
        self.run = PipelineRun(self.profile, "draft", library=self.library, title=self.folder.name)
        self.showcase_ready = False
        self.material_overrides = {}
        self.placed_audio = set()
        self.weather_shelters = None
        self.pending_precipitation = ()

    def verify(self, _):
        self.header, self.terrain, self.rows = load_intake(self.manifest)
        self.catalog = load_catalog(self.library, snapshot=self.recorded_rules, recorded=self.recorded_rules is not None,
                                    source_profile=self.source_profile, bundle=self.bundle)
        self.rules = create_snapshot(self.library, self.header, self.rows, self.manifest, self.options,
                                     replay=self.recorded_rules, pipeline=self.profile, catalog=self.catalog.document,
                                     bundle=self.bundle)
        self.values = {key: row["value"] for key, row in self.rules["results"].items()}
        self.run.report["title"] = self.header["name"]
        self.source_rows = self.rows
        coverage = classify(self.source_rows, self.terrain, self.catalog)
        self.rows = build_rows(self.source_rows, coverage)
        for row in self.source_rows:
            if row["status"] == "terrain" and coverage[row["id"]]["blocked"]:
                raise ValueError("The only terrain object cannot be built under this object profile.")
        self.environment = dict(self.header.get("environment", {}))
        for group in ("sun", "fog"):
            definition = self.rules["profile"]["groups"].get(group)
            if definition is None:
                continue
            source = definition["source"]
            if any(row["source_class"].casefold() == source and coverage[row["id"]]["blocked"]
                   for row in self.source_rows):
                self.environment.pop(group, None)
                for key in self.values:
                    if key.startswith(group + "."):
                        self.values[key] = 1 if group == "sun" else None
        blocked = sum(bool(row["blocked"]) for row in coverage.values())
        if blocked:
            return Outcome("review", f"{blocked} source objects withheld. Open the intake's All tab for details.")

    def plan(self, _):
        header, terrain, rows, editor = self.header, self.terrain, self.rows, self.editor
        terrain_layers = self.options["terrain_layers"]
        manual = self.options["mesh_mappings"]
        surfaces = layer_choices(terrain, terrain_layers, artwork=available_art(header))
        native_paint = None
        if "terrain_paint" in header:
            palette = layer_choices(terrain, terrain_layers)
            native_paint = NativeTerrainPaint(self.folder, header, palette, PALETTE_SURFACES, FALLBACK)
            weights, channels = None, native_paint.channels
        elif "tile_grid" in terrain and surfaces:
            weights, channels = None, ()
        else:
            weights, channels = load_weights(self.folder, header["files"], surfaces)
        bindings = applicable_mappings(manual, self.source_rows, rows)
        self.mover_tracks, self.moving_references, self.lifts, self.trigger_relays = plan_native_brushes(
            self.folder, header, rows, manual)
        references = {ref for row in rows if (ref := source_reference(row))}
        generated = {}
        generated_models = {}
        lighting_index = {}
        models = {**header.get("interiors", {}), **header.get("shapes", {})}
        if models:
            from draft_interiors import mesh_package
            from interior_lighting import load_lighting_index, model_lighting
            from model_textures import has_shader_inputs, load_texture_index, shader_signature

            folder = self.folder
            lighting_index = load_lighting_index(folder, header["files"])
            texture_index = load_texture_index(folder, header["files"])
            for reference, filename in models.items():
                if reference in manual or reference not in references:
                    continue
                digest = header["files"][filename]
                geometry, _ = load_intake_geometry(folder, filename, digest)
                lighting = model_lighting(lighting_index, filename, geometry)
                if lighting and (lighting["surfaces"] or lighting.get("lod_surfaces")):
                    version = json.dumps(lighting, separators=(",", ":"), sort_keys=True).encode()
                    digest = sha256(digest.encode() + version).hexdigest()
                texture_sizes = {}
                bindings_for_texture = texture_index.get(filename, {})
                model_format = geometry["format"]
                if model_format in (3, VENGEANCE_BSP):
                    texture_sizes = {int(slot): row["size"] for slot, row in bindings_for_texture.items()
                                     if "size" in row}
                if model_format == 3 and texture_sizes:
                    version = json.dumps(texture_sizes, separators=(",", ":"), sort_keys=True).encode()
                    digest = sha256(digest.encode() + b":t1-pixel-uv:" + version).hexdigest()
                if model_format in (VENGEANCE_BSP, VENGEANCE_STATIC_MESH):
                    surface_keys = {slot: (row["size"], row["mode"], row["two_sided"], header["files"][row["file"]])
                                    for slot, row in bindings_for_texture.items() if "file" in row}
                    for slot, row in bindings_for_texture.items():
                        if has_shader_inputs(row):
                            surface_keys[slot] += (shader_signature(row, header["files"]),)
                    if surface_keys:
                        salt = (b":vengeance-bsp-materials:" if model_format == VENGEANCE_BSP
                                else b":vengeance-static-materials:")
                        version = json.dumps(surface_keys, separators=(",", ":"), sort_keys=True).encode()
                        digest = sha256(digest.encode() + salt + version).hexdigest()
                preview = reference.startswith(BRUSH_PREVIEW_REFERENCE) and reference not in self.moving_references
                target = mesh_package(geometry, digest, brush_preview=preview)
                generated[target] = (geometry, digest, lighting, texture_sizes, reference)
                generated_models[reference] = (filename, geometry)
                bindings[reference] = target
        planned = placements(rows, terrain, bindings)
        world_scale = terrain["cm_per_m"] / 100
        y_sign = -terrain.get("y_sign", -1)
        planned = [
            (row, path, location, rotation, (scale[0] * world_scale, scale[1] * world_scale * y_sign,
                                             scale[2] * world_scale)
             if source_reference(row) not in manual and path in generated and "render" not in generated[path][0] else scale)
            for row, path, location, rotation, scale in planned
        ]
        meshes = {}
        for path in sorted({item[1] for item in planned} - generated.keys()):
            mesh = editor.ASSETS.load_asset(path)
            if not isinstance(mesh, unreal.StaticMesh):
                raise ValueError(f"Could not load the mapped static mesh {path}.")
            meshes[path] = mesh
        boxes, flag_limit, warning_zone = plan_boundaries(
            header, terrain, rows, perimeter=self.options["perimeter"],
            flag_inset_m=self.options["flag_inset_m"], wall_limits=self.options.get("wall_limits"),
            flag_polygon_m=self.options.get("flag_polygon_m"),
            wall_polygon_m=self.options.get("wall_polygon_m"),
            wall_block_channels=self.options.get("wall_block_channels"),
            wall_visual=self.options.get("wall_visual"),
            warn_players=self.options.get("warn_players", False),
            warning_polygon_m=self.options.get("warning_polygon_m"),
            warning_limits=self.options.get("warning_limits"),
            warning_audience=self.options.get("warning_audience"),
            flag_limits=self.options.get("flag_limits"),
            flag_return_delay_s=self.options.get("flag_return_delay_s"),
            kill_floor_m=self.options.get("kill_floor_m"),
            flight_ceiling=self.options.get("flight_ceiling"),
        )
        if self.batch_target is None and not editor.SAVING.save_dirty_packages_with_dialog(True, True):
            return Outcome("cancelled", "User cancelled the editor save prompt.")
        self.surfaces, self.weights, self.channels = surfaces, weights, channels
        self.native_paint = native_paint
        self.planned, self.meshes = planned, meshes
        self.generated, self.generated_models = generated, generated_models
        self.lighting_index, self.boxes, self.flag_limit = lighting_index, boxes, flag_limit
        self.warning_zone = warning_zone

    def textures(self, _):
        if not self.generated_models:
            return Outcome("skipped", "No decoded models need assets or material textures.")
        from draft_interiors import asset_mesh
        from draft_model_materials import generate_overrides

        from interior_lighting import lighting_coverage
        from mission_lighting import COMMAND_CLASS, startup_bindings

        available = {}
        if any(row["source_class"] == COMMAND_CLASS and row["status"] != "excluded" for row in self.rows):
            coverage = lighting_coverage(self.lighting_index)
            for row, path, *_ in self.planned:
                if path in self.generated:
                    source = self.generated_models[source_reference(row)][0]
                    available[row["id"]] = coverage.get(source, {}).get("triggerable_lights", ())
        startup, self.light_commands = startup_bindings(self.rows, available)
        self.material_overrides = generate_overrides(self.folder, self.header["files"], self.generated_models,
                                                     self.lighting_index, moving_references=self.moving_references,
                                                     placements=self.planned, startup_lights=startup)
        for path, (geometry, digest, lighting, texture_sizes, reference) in self.generated.items():
            surfaces = (self.material_overrides.get(reference)
                        if geometry["format"] in (VENGEANCE_BSP, VENGEANCE_STATIC_MESH) else None)
            preview = reference.startswith(BRUSH_PREVIEW_REFERENCE) and reference not in self.moving_references
            self.meshes[path] = asset_mesh(geometry, digest, lighting, texture_sizes, surface_materials=surfaces,
                                          brush_preview=preview)

    def terrain_step(self, _):
        editor, terrain, header = self.editor, self.terrain, self.header
        batch_target = self.batch_target
        weights, channels, values, boxes = self.weights, self.channels, self.values, self.boxes
        prefix = PREFIX[self.source_profile]
        if batch_target is None:
            ident, level = editor.new_identity(prefix)
        else:
            ident = batch_target
            if not imported_draft(ident) or not ident.startswith(prefix):
                raise ValueError("Invalid target draft for the batch rebuild.")
            level = f"{editor.LEVEL_ROOT}/{ident}/Arena_{ident}_P"
            if editor.ASSETS.does_asset_exist(level) or editor.ASSETS.does_asset_exist(f"/Game/Community/{ident}/Terrain"):
                raise ValueError("The reserved rebuild target is already in use.")
        if not editor.LEVELS.new_level(level):
            raise RuntimeError("Could not create the import draft.")
        size = math.ceil(terrain["step_cm"] * max(footprint(terrain)))
        editor.configure_world(ident, size)
        label = f"{header['name']} · {header['source_game']} terrain"
        repeat = values["terrain.tile_cm"]
        if "tile_grid" in terrain:
            tiled = TiledTerrain(terrain)
            stages = ((col, row) for row in range(tiled.rows) for col in range(tiled.cols))
        else:
            tiled, stages = None, [(0, 0)]
        material, materials, paint_blocks = None, {}, None
        artwork = available_art(header)
        source_art = (self.folder, header, self.surfaces, self.native_paint.mask_sizes) if (
            self.native_paint is not None and any(choice == SOURCE_ART for _, _, choice in self.surfaces)) else None
        if weights is None and self.native_paint is None:
            paint_blocks = [painted_cells(block, self.surfaces) for block in tiled.blocks]
        else:
            material = terrain_material(editor, ident, channels, source_art=source_art)
        x, y, z = terrain["origin_cm"]
        span = terrain["step_cm"] * SIZE
        y_sign = terrain.get("y_sign", -1)
        assets, ground = {}, []
        asset_number = 0
        for col, row in stages:
            offset_x, offset_y = col * span, row * span
            uv_origin = offset_x % repeat, offset_y % repeat
            key = (tiled.tile_key(col, row) if tiled else (), uv_origin)
            if self.native_paint is not None:
                key += (col, row)
            if key not in assets:
                mesh_input = terrain if tiled is None else terrain | tiled.mesh_block(col, row)
                if tiled is not None and len(mesh_input["holes"]) == SIZE**2:
                    assets[key] = None
                else:
                    paint = paint_blocks[tiled.grid[row][col]] if paint_blocks is not None else None
                    artwork_flags = None
                    if paint is not None:
                        surfaces = set(paint)
                        if max(surfaces) >= ART_SLOT_OFFSET:
                            original = tiled.blocks[tiled.grid[row][col]]["legacy_grid"]
                            artwork_flags = bytes.fromhex(original["material_flags"])
                        for surface in sorted(surfaces - materials.keys()):
                            if surface < ART_SLOT_OFFSET:
                                materials[surface] = terrain_material(editor, ident, (surface,),
                                                                     name=f"M_Terrain_{PALETTE_SURFACES[surface]}")
                            else:
                                slot = surface - ART_SLOT_OFFSET
                                filename = artwork[slot]
                                materials[surface] = terrain_art_material(editor, ident, slot, self.folder,
                                                                          filename, header["files"][filename])
                    asset_number += 1
                    name = f"Terrain_{asset_number}" if asset_number > 1 else "Terrain"
                    tile_weights = self.native_paint.tile_weights(col, row) if self.native_paint is not None else weights
                    mask_frame = (col * SIZE, row * SIZE, *header["terrain_paint"]["vertices"]) if source_art else None
                    assets[key] = terrain_mesh(ident, mesh_input, tile_weights, tile_cm=repeat, name=name,
                                               uv_origin=uv_origin, paint=paint, materials=materials,
                                               artwork_flags=artwork_flags, mask_frame=mask_frame,
                                               source_material=material if source_art else None)
            if assets[key] is None:
                continue
            actor = editor.ACTORS.spawn_actor_from_class(
                unreal.StaticMeshActor, unreal.Vector(x + offset_x, y + y_sign * offset_y, z))
            actor.static_mesh_component.set_static_mesh(assets[key])
            actor.set_actor_label(f"{label} ({col + 1}, {row + 1})" if tiled else label)
            if material is not None:
                actor.static_mesh_component.set_material(0, material)
            ground.append(actor)
        if boxes:
            place_walls(editor.ACTORS, boxes, draft=ident, block_channels=self.options.get("wall_block_channels"),
                        visual=self.options.get("wall_visual"))
        if self.warning_zone is not None:
            from perimeter_warning_unreal import place_warning

            place_warning(editor.ACTORS, ident, self.warning_zone, audience=self.options.get("warning_audience"))
        self.ident, self.level, self.size, self.ground = ident, level, size, ground
        self.run.report["draft"] = ident

    def objectives(self, _):
        editor, terrain, rows = self.editor, self.terrain, self.rows
        planned, meshes = self.planned, self.meshes
        material_overrides = self.material_overrides
        lighting_index, generated_models = self.lighting_index, self.generated_models
        boxes, flag_limit = self.boxes, self.flag_limit
        generator_scopes = self.header['format'] in T2_GENERATOR_SCOPE_VERSIONS
        equipment = mission_equipment(rows, terrain, generator_scopes=generator_scopes)
        lighting = {}
        source_hash = None
        if lighting_index:
            from equipment_unreal import store_source
            from interior_lighting import alarm_switches_material, model_lighting

            source_hash = store_source(equipment) if equipment['equipment'] else None
            _, scopes, _ = power_context(rows, generator_scopes=generator_scopes)
            generator_groups = {group['source_id'] for group in equipment['power_groups'] if group['generators']}
            for row, *_ in planned:
                ref = source_reference(row)
                if ref not in generated_models:
                    continue
                filename, geometry = generated_models[ref]
                if alarm_switches_material(model_lighting(lighting_index, filename, geometry)):
                    group = scopes.get(row['id'])
                    lighting[row['id']] = (source_hash, group if group in generator_groups else None)
        native_modes = {path: (geometry["render"], bool(geometry["collision"]["triangles"]
                                                       or geometry["collision"].get("simple")), geometry["format"])
                        for path, (geometry, *_rest) in self.generated.items() if "render" in geometry}
        audio = load_mission_audio(self.manifest, self.header["files"])
        bridge = getattr(unreal, "CommunityMapMotionLibrary", None)
        interior_ids = {row["id"] for row in rows if row["source_class"].casefold() == "interiorinstance"
                        and row["status"] != "excluded"}
        acoustic_rooms = {row["id"] for row, path, *_ in planned
                          if row["id"] in interior_ids and path in self.generated
                          and "collision" in self.generated[path][0].get("acoustics", {})}
        planned_audio = audio_playback(audio, {row["id"] for row in rows if row["status"] != "excluded"},
                                       repeat_tools=hasattr(bridge, "configure_ambient_repeat"),
                                       directional_tools=hasattr(bridge, "configure_directional_ambient"),
                                       outside_tools=self.library is not None and acoustic_rooms == interior_ids)
        if not any(row["settings"]["outside_only"] for row, _, _ in planned_audio):
            acoustic_rooms = set()
        triggers = {}
        self.weather_shelters = place_static_objects(
            editor.ACTORS, planned, meshes, material_overrides, lighting, native_modes,
            self.mover_tracks, self.ident, triggers, acoustic_rooms, getattr(self, "light_commands", None))
        if self.lifts:
            from draft_elevators import place_elevators

            place_elevators(editor.ACTORS, self.ident, self.lifts, triggers)
        if triggers:
            from draft_elevators import place_brush_triggers

            place_brush_triggers(editor.ACTORS, self.ident, triggers, self.trigger_relays)
        place_equipment_markers(editor.ACTORS, equipment, rows, source_hash)
        placed_markers = []
        for row in rows:
            if row["status"] != "marker":
                continue
            pos = list(row["position_cm"])
            kind = "T3Flag" if row["kind"] == "flag" else "T3Spawn"
            if row["kind"] == "spawn":
                # A T2 spawn sphere centre is not necessarily capsule-clear on the imported tile.
                p = unreal.Vector(*pos)
                if editor.capsule_hit(p, p + unreal.Vector(0, 0, 2)):
                    placed = editor.inherited_spawn(*pos)
                    if placed:
                        pos = list(placed.to_tuple())
            marker = editor.marker(kind + str(row["team"]), pos, row["yaw"])
            marker.set_actor_label(
                marker.get_actor_label() + f" · {row['name'] or row['id']}"
            )
            placed_markers.append((marker.get_actor_label(), pos))
        if boxes:
            verify_walls(editor.world(), editor.ACTORS.get_all_level_actors(), placed_markers)
        if flag_limit is not None:
            place_flag_bounds(editor.ACTORS, flag_limit)
        if self.options.get("kill_floor_m") is not None:
            place_kill_floor(editor.ACTORS, playable_bounds(self.header, rows),
                             kill_floor_height(self.header, terrain, rows, self.options["kill_floor_m"]))
        if self.options.get("flight_ceiling") is not None:
            place_flight_ceiling(editor.ACTORS, playable_bounds(self.header, rows),
                                 flight_ceiling_heights(self.header, terrain, rows, self.options["flight_ceiling"]))
        if planned_audio:
            from draft_audio import place_audio

            self.placed_audio = place_audio(editor.ACTORS, self.ident, self.folder, planned_audio)
        self.equipment = equipment
        self.place_pending_precipitation()

    def place_pending_precipitation(self):
        if self.weather_shelters is None or not self.pending_precipitation:
            return
        from draft_precipitation import place_precipitation

        editor = self.editor
        place_precipitation(self.ident, editor.ACTORS, self.pending_precipitation,
                            world=editor.world(), shelters=self.weather_shelters)
        self.pending_precipitation = ()

    def lighting(self, _):
        converter = MILF_ENVIRONMENT_CONVERTERS.get(self.header["profile"])
        if converter:
            # Sources with a MILF environment converter go through the shared UE5 adapter (#146).
            from environment_ue5 import apply as apply_environment

            assets = dict(folder=self.folder, inventory=self.header["files"],
                          destination=f"/Game/Community/{self.ident}/Environment")
            view = apply_environment(self.editor.ACTORS, converter()(self.environment, self.values), assets,
                                     own_sky=has_sky_dome(self.editor.ACTORS))
        else:
            view = None
            place_environment(self.editor.ACTORS, self.environment, self.values)
        place_local_lights(self.editor.ACTORS, self.rows)
        if self.header.get("hazards"):
            # MILF hazards become the native lava kit (T3BackEndServer#150).
            from hazards_ue5 import apply as apply_hazards

            apply_hazards(self.editor.ACTORS, self.header["hazards"], f"/Game/Community/{self.ident}/Hazards", view)

    def weather(self, _):
        header, terrain, rows, editor, ident = self.header, self.terrain, self.rows, self.editor, self.ident
        copied_sky = SKY_INDEX in header["files"]
        weather = mission_weather(rows, terrain)
        if copied_sky:
            from draft_clouds import place_clouds
            from texture_batches_unreal import import_source_sky

            folder = self.folder
            inventory = header["files"]
            batches = import_source_sky(folder, inventory, ident, os.environ["T3_TEXTURE_LIBRARY"])
            for batch in batches:
                unreal.log(f"Source sky artwork is ready for review in {batch}.")
            layers = cloud_layers(weather, load_sky_index(folder, inventory), terrain.get("y_sign", -1))
            place_clouds(ident, editor.ACTORS, layers)
        if emitters := precipitation_emitters(weather, terrain["cm_per_m"]):
            if hasattr(unreal, "CommunityMapMaterialLibrary"):
                self.pending_precipitation = emitters
                self.place_pending_precipitation()
            else:
                message = "Precipitation requires the Community Map Materials editor plugin."
                unreal.log_warning(message)
                return Outcome("review", message)
        if not copied_sky and not emitters:
            return Outcome("skipped", "No usable source sky artwork or precipitation.")

    def save(self, _):
        from dock_review import publish_review
        from lighting_preview_unreal import seed_cameras
        from mission_lighting import placed_command_ids
        from model_library import record_import

        header, ident = self.header, self.ident
        tags = [
            "T3Intake_" + header["profile_sha256"][:12],
            "T3Mission_" + header["sources"][0]["sha256"][:12],
        ]
        if isinstance(self.boxes, PolygonWalls):
            tags.append(POLYGON_REQUIRED)
        if self.library is not None:
            tags.append("T3RuleSet_" + self.rules["sha256"])
            write(draft_rules_path(self.library, ident), self.rules)
        for actor in self.ground:
            actor.tags = tags
        self.lighting_cameras = seed_cameras(self.editor.ACTORS) if self.lighting_index else []
        self.editor.save_draft(ident)
        warnings = []
        if self.library is not None:
            try:
                record_draft(self.library, ident, self.manifest)
            except (OSError, ValueError) as exc:
                warnings.append(f"Could not record the source for {ident}: {exc}")
            try:
                publish_review(self.library, ident, header, self.terrain, self.source_rows, self.catalog,
                               self.options["mesh_mappings"], manifest=self.manifest, placed_audio=self.placed_audio,
                               placed_lights=placed_command_ids(self.editor.ACTORS.get_all_level_actors()))
            except (OSError, ValueError) as exc:
                warnings.append(f"Could not save object review for {ident}: {exc}")
        try:
            record_import(ident, self.planned, self.meshes, self.material_overrides)
        except (ValueError, OSError) as exc:
            warnings.append(f"Could not register shared models for {ident}: {exc}")
        if warnings:
            for warning in warnings:
                unreal.log_warning(warning)
            return Outcome("review", " ".join(warnings))

    def lighting_stills(self, _):
        from lighting_preview_unreal import capture_saved_cameras

        if not self.lighting_cameras or not self.rendering:
            return Outcome("skipped", "No baked interiors or rendering is disabled.")
        try:
            folder = capture_saved_cameras(self.ident, self.editor.world(), self.editor.ACTORS, self.lighting_cameras)
        except (OSError, RuntimeError, ValueError) as exc:
            unreal.log_warning(f"Could not render lighting previews for {self.ident}: {exc}")
            return Outcome("review", str(exc))
        unreal.log(f"T3_LIGHTING_PREVIEWS|{self.ident}|{folder}")

    def showcase(self, _):
        from draft_showcase import build

        try:
            build(self.editor, self.ident, self.level, self.rows, self.planned, self.equipment, self.meshes,
                  self.material_overrides)
        except (RuntimeError, ValueError) as exc:
            if self.editor.world().get_path_name().split(".")[0] != self.level:
                raise
            unreal.log_warning(f"Could not build the model showcase for {self.ident}: {exc}")
            return Outcome("review", str(exc))
        self.showcase_ready = True

    def contact_sheets(self, _):
        from contact_sheets_unreal import export_report

        if not self.rendering or not self.showcase_ready:
            return Outcome("skipped", "Rendering is disabled or there is no saved showcase.")
        try:
            export_report(self.editor, self.ident, intake=self.manifest)
        except (OSError, RuntimeError, ValueError) as exc:
            if self.editor.world().get_path_name().split(".")[0] != self.level:
                raise
            unreal.log_warning(f"Could not generate contact sheets for {self.ident}: {exc}")
            return Outcome("review", str(exc))

    def check_source_classes(self, options):
        names = {row["source_class"].casefold() for row in self.rows if row["status"] != "excluded"}
        missing = [name for name in options["classes"] if name.casefold() not in names]
        if missing:
            raise ValueError("Required source classes are missing: " + ", ".join(missing))

    def check_ctf(self, _):
        self.editor.marker_snapshot()


def import_map(manifest, *, perimeter=False, flag_inset_m=None, wall_limits=None, mesh_mappings=None, terrain_layers=None,
               flag_polygon_m=None, wall_polygon_m=None, wall_block_channels=None, wall_visual=None, warn_players=False,
               warning_polygon_m=None, warning_limits=None, warning_audience=None, flag_limits=None, flag_return_delay_s=None,
               kill_floor_m=None, flight_ceiling=None, recorded_rules=None, batch_target=None, on_finished=None):
    import t3_map_editor as editor

    options = {"perimeter": perimeter, "flag_inset_m": flag_inset_m,
               "mesh_mappings": {} if mesh_mappings is None else mesh_mappings,
               "terrain_layers": {} if terrain_layers is None else terrain_layers}
    if wall_limits is not None:
        options["wall_limits"] = wall_limits
    if flag_polygon_m is not None:
        options["flag_polygon_m"] = flag_polygon_m
    if flag_limits is not None:
        options["flag_limits"] = flag_limits
    if flag_return_delay_s is not None:
        options["flag_return_delay_s"] = flag_return_delay_s
    if wall_polygon_m is not None:
        options["wall_polygon_m"] = wall_polygon_m
    if wall_block_channels is not None:
        options["wall_block_channels"] = list(validate_channels(wall_block_channels))
    if wall_visual is not None:
        options["wall_visual"] = wall_appearance(wall_visual)
    if warn_players is not False:
        options["warn_players"] = warn_players
    if warning_polygon_m is not None:
        options["warning_polygon_m"] = warning_polygon_m
    if warning_limits is not None:
        options["warning_limits"] = warning_limits
    if warning_audience is not None:
        options["warning_audience"] = warning_audience
    if kill_floor_m is not None:
        options["kill_floor_m"] = kill_floor_m
    if flight_ceiling is not None:
        options["flight_ceiling"] = flight_ceiling
    draft = DraftImport(editor, manifest, options, recorded_rules, batch_target)
    operators = {
        "verify": draft.verify, "plan": draft.plan, "textures": draft.textures, "terrain": draft.terrain_step,
        "objectives": draft.objectives, "lighting": draft.lighting, "weather": draft.weather, "save": draft.save,
        "lighting-stills": draft.lighting_stills, "showcase": draft.showcase, "contact-sheets": draft.contact_sheets,
        "check-source-classes": draft.check_source_classes, "check-ctf": draft.check_ctf,
    }
    steps = draft.run.report["steps"]
    with unreal.ScopedSlowTask(len(steps), f"Building draft from {draft.folder.name}") as progress:
        progress.make_dialog(False)
        finished = draft.run.execute(operators, on_step=lambda step: progress.enter_progress_frame(
            1, f"Building draft from {draft.folder.name}: {step.get('label') or step['operation']}…"))
    if not finished:
        return
    editor.EDITOR.set_level_viewport_camera_info(
        unreal.Vector(0, -draft.size * 0.65, draft.size * 0.65), unreal.Rotator(pitch=-42, yaw=90)
    )
    unreal.log(f"T3_INTAKE_IMPORTED|{draft.ident}|{manifest}|mapped_meshes={len(draft.planned)}")
    for slot, name, surface in draft.surfaces:
        unreal.log(f"Terrain layer {slot} {name} uses {surface or FALLBACK}"
                   + (" as a fallback" if surface is None else ""))
    if on_finished is not None:
        on_finished(draft.ident)
    return draft.ident
