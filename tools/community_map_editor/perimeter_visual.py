"""Bake the translucent visual surface of an authored mission perimeter."""

import json
import math

import unreal

from perimeter import (GRID_STYLES, RETAIL_BORDER_FOLDER, RETAIL_BORDER_PLANES, RETAIL_BORDER_STYLES,
                       RETAIL_BORDER_TAG, RETAIL_BORDER_Z_CM, VISUAL_TAG, WALL_STYLES, edges_of, retail_border_pieces,
                       wall_appearance)
from polygon_walls_unreal import dynamic_shell, mesh_geometry


APPEARANCE_TAG = "T3BoundaryAppearance"
MATERIAL_NAME = "M_MissionPerimeter"
MESH_NAME = "SM_MissionPerimeterVisual"


def opacity_shader(walls, center, distance_m, grid_m=None):
    x, y, z = center
    # A concave outline cannot use the facing of the current mesh face to tell
    # whether the viewer is inside. Count crossings of a ray from the viewer.
    tests = ["bool Inside = false;"]
    for (ax, ay), (bx, by) in edges_of(walls.vertices):
        if ay == by:
            continue
        tests.append(
            f"if ((Eye.y < {ay-y:.9f}) != (Eye.y < {by-y:.9f})) "
            f"Inside = Inside != (Eye.x < {ax-x:.9f} + "
            f"(Eye.y - ({ay-y:.9f})) * ({(bx-ax)/(by-ay):.12g}));"
        )
    if walls.floor:
        tests.append(f"Inside = Inside && Eye.z >= {walls.z_min-z:.9f};")
    if walls.ceiling:
        tests.append(f"Inside = Inside && Eye.z <= {walls.z_max-z:.9f};")
    tests.append(f"float Fade = Inside ? 1.0 - smoothstep(0.0, {distance_m*100:.6f}, Range) : 1.0;")
    if grid_m is None:
        tests.append("return 0.42 * Fade;")
    else:
        # Bright grid lines over a faint sheet. Vertical lines run along the wall (x + y), horizontal
        # ones by height, so any wall direction gets a square-ish grid.
        tests += [f"float3 Cell = World / {grid_m*100:.6f};",
                  "float Across = frac(Cell.x + Cell.y);", "float Up = frac(Cell.z);",
                  "float Lines = 1.0 - smoothstep(0.0, 0.035, min(min(Across, 1.0 - Across), min(Up, 1.0 - Up)));",
                  "return Fade * (0.1 + 0.55 * Lines);"]
    return "\n".join(tests)


def build_material(folder, walls, position, appearance, emissive_gain=1.0):
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        MATERIAL_NAME, folder, unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise ValueError("Unreal could not create the mission boundary material.")
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    lib = unreal.MaterialEditingLibrary

    def node(kind, x, y):
        return lib.create_material_expression(material, kind, x, y)

    eye = node(unreal.MaterialExpressionCameraPositionWS, -640, 0)
    anchor = node(unreal.MaterialExpressionActorPositionWS, -640, 110)
    pixel = node(unreal.MaterialExpressionWorldPosition, -640, 220)
    local_eye = node(unreal.MaterialExpressionSubtract, -420, 20)
    distance = node(unreal.MaterialExpressionDistance, -420, 150)
    custom = node(unreal.MaterialExpressionCustom, -180, 50)
    custom.set_editor_property("description", "Fade from inside the playable perimeter")
    custom.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
    grid = GRID_STYLES.get(appearance["style"])
    custom.set_editor_property("code", opacity_shader(walls, position, appearance["distance_m"], grid))
    inputs = []
    for name in ("Eye", "Range", "World") if grid else ("Eye", "Range"):
        pin = unreal.CustomInput()
        pin.set_editor_property("input_name", name)
        inputs.append(pin)
    custom.set_editor_property("inputs", inputs)
    color = node(unreal.MaterialExpressionConstant3Vector, -180, 280)
    color.set_editor_property("constant", unreal.LinearColor(*(value * emissive_gain
                                                             for value in WALL_STYLES[appearance["style"]]), 1.0))
    for source, target, pin in (
        (eye, local_eye, "A"), (anchor, local_eye, "B"), (eye, distance, "A"), (pixel, distance, "B"),
        (local_eye, custom, "Eye"), (distance, custom, "Range"),
    ) + (((pixel, custom, "World"),) if grid else ()):
        if not lib.connect_material_expressions(source, "", target, pin):
            raise ValueError(f"Unreal could not link a mission boundary material input ({pin}).")
    for source, target in ((color, unreal.MaterialProperty.MP_EMISSIVE_COLOR), (custom, unreal.MaterialProperty.MP_OPACITY)):
        if not lib.connect_material_property(source, "", target):
            raise ValueError("Unreal could not link a mission boundary material output.")
    lib.recompile_material(material)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise ValueError("Unreal could not save the mission boundary material.")
    return material


def retail_border_mesh(name):
    """The editor stand-in for one retail energy-wall plane, created once at its game path.

    A two-sided quad with the retail plane's position and height, so the editor shows where the game
    draws it. It is never exported: the cooked map references the path and the game supplies its mesh.
    """
    path = f"{RETAIL_BORDER_FOLDER}/{name}"
    assets = unreal.EditorAssetLibrary
    if assets.does_asset_exist(path):
        asset = assets.load_asset(path)
        preview = retail_border_preview()
        if asset.get_material(0) != preview:  # Stand-ins made before the preview material existed.
            asset.set_material(0, preview)
            assets.save_loaded_asset(asset)
        return asset
    (ax, ay), (bx, by) = RETAIL_BORDER_PLANES[name]
    low, high = RETAIL_BORDER_Z_CM
    points = [(ax, ay, low), (bx, by, low), (bx, by, high), (ax, ay, high)]
    mesh = unreal.DynamicMesh()
    mesh.append_buffers_to_mesh(unreal.GeometryScriptSimpleMeshBuffers(
        vertices=[unreal.Vector(*point) for point in points],
        uv0=[unreal.Vector2D(u, v) for u, v in ((0, 1), (1, 1), (1, 0), (0, 0))],
        triangles=[unreal.IntVector(*t) for t in ((0, 1, 2), (0, 2, 3), (2, 1, 0), (3, 2, 0))],
    ))
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=False)
    asset, result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(mesh, path, options)
    if not isinstance(asset, unreal.StaticMesh) or result != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise ValueError("Unreal could not create the retail border stand-in.")
    asset.set_material(0, retail_border_preview())
    if not assets.save_loaded_asset(asset):
        raise ValueError("Unreal could not save the retail border stand-in.")
    return asset


def retail_border_preview():
    """A faint see-through editor material for the retail border stand-ins (never exported).

    Without it the stand-ins draw Unreal's opaque default grid and hide the map in the editor. The cooked
    map sets no material on the retail pieces, so the game uses the retail mesh's own.
    """
    path = f"{RETAIL_BORDER_FOLDER}/MAT_EnergyWall"
    assets = unreal.EditorAssetLibrary
    if assets.does_asset_exist(path):
        return assets.load_asset(path)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        "MAT_EnergyWall", RETAIL_BORDER_FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    material.set_editor_property("two_sided", True)
    lib = unreal.MaterialEditingLibrary
    color = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -300, 0)
    color.set_editor_property("constant", unreal.LinearColor(*WALL_STYLES["t3"], 1.0))
    opacity = lib.create_material_expression(material, unreal.MaterialExpressionConstant, -300, 150)
    opacity.set_editor_property("r", 0.04)
    if (not lib.connect_material_property(color, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
            or not lib.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)):
        raise ValueError("Unreal could not build the retail border preview material.")
    lib.recompile_material(material)
    if not assets.save_loaded_asset(material):
        raise ValueError("Unreal could not save the retail border preview material.")
    return material


def place_retail_border(actors, walls):
    """Place the retail border planes on a rectangular boundary. Returns False for other outlines."""
    pieces = retail_border_pieces(walls)
    if pieces is None:
        return False
    for index, (name, location, scale) in enumerate(pieces):
        actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*location))
        if not actor:
            raise ValueError("Unreal could not place the retail border.")
        actor.set_actor_label(f"Mission perimeter · Retail border {index // 4 + 1}{name[-1]}")
        actor.set_folder_path("TRIBES CTF/Mission perimeter")
        actor.tags = [unreal.Name(RETAIL_BORDER_TAG)]
        actor.set_actor_scale3d(unreal.Vector(*scale))
        component = actor.static_mesh_component
        component.set_static_mesh(retail_border_mesh(name))
        component.set_cast_shadow(False)
        component.set_collision_profile_name("NoCollision")
        actor.set_actor_enable_collision(False)
    return True


def place_visual(actors, draft, walls, appearance, emissive_gain=1.0):
    from source_profiles import imported_draft

    if not imported_draft(draft):
        raise ValueError("A mission boundary visual must belong to an imported draft.")
    position, mesh = dynamic_shell(walls)
    folder = f"/Game/Community/{draft}"
    assets = unreal.EditorAssetLibrary
    if any(assets.does_asset_exist(f"{folder}/{name}") for name in (MATERIAL_NAME, MESH_NAME)):
        raise ValueError("The reserved mission boundary visual assets already exist.")
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=False)
    asset, result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        mesh, f"{folder}/{MESH_NAME}", options)
    if not isinstance(asset, unreal.StaticMesh) or result != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise ValueError("Unreal could not create the mission boundary visual mesh.")
    assets.set_metadata_tag(asset, APPEARANCE_TAG, json.dumps(appearance, separators=(",", ":")))
    if not assets.save_loaded_asset(asset):
        raise ValueError("Unreal could not save the mission boundary visual mesh.")
    material = build_material(folder, walls, position, appearance, emissive_gain)
    actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*position))
    if not actor:
        raise ValueError("Unreal could not place the mission boundary visual.")
    actor.set_actor_label("Mission perimeter · Visual")
    actor.set_folder_path("TRIBES CTF/Mission perimeter")
    actor.tags = [unreal.Name(VISUAL_TAG)]
    component = actor.static_mesh_component
    component.set_static_mesh(asset)
    component.set_material(0, material)
    component.set_cast_shadow(False)
    component.set_collision_profile_name("NoCollision")
    actor.set_actor_enable_collision(False)
    if appearance["style"] in RETAIL_BORDER_STYLES and place_retail_border(actors, walls):
        # The game draws the retail border; this grid wall stays as the editor preview only.
        actor.set_actor_hidden_in_game(True)


def verify_visual(actor, walls):
    if not isinstance(actor, unreal.StaticMeshActor):
        raise ValueError("The mission boundary visual must be a static mesh actor.")
    component = actor.static_mesh_component
    asset = component.static_mesh
    material = component.get_material(0)
    position, _, triangles, _ = mesh_geometry(walls)
    if (not isinstance(asset, unreal.StaticMesh) or asset.get_name() != MESH_NAME
            or asset.get_num_triangles(0) != len(triangles)
            or actor.get_actor_enable_collision() or str(component.get_collision_profile_name()) != "NoCollision"
            or math.dist(actor.get_actor_location().to_tuple(), position) > 0.25
            or actor.get_actor_scale3d().to_tuple() != (1, 1, 1)
            or any(abs(angle) > 1e-5 for angle in actor.get_actor_rotation().to_tuple())):
        raise ValueError("The mission boundary visual lost its geometry or non-blocking settings.")
    try:
        appearance = wall_appearance(json.loads(unreal.EditorAssetLibrary.get_metadata_tag(asset, APPEARANCE_TAG)))
    except ValueError as exc:
        raise ValueError("The mission boundary visual lost its appearance settings.") from exc
    if (appearance is None or not isinstance(material, unreal.Material) or material.get_name() != MATERIAL_NAME
            or material.get_editor_property("blend_mode") != unreal.BlendMode.BLEND_TRANSLUCENT
            or not material.get_editor_property("two_sided")):
        raise ValueError("The mission boundary visual lost its two-sided translucent material.")
