"""Build editable, translucent cloud layers around a generated map draft."""

import math

import unreal

from draft_sky import draft_backdrop
from mission_weather import CLOUD_ACTOR_TAG
from texture_batches_unreal import source_sky_name


HALF_EDGE = 50
GRID = 17
CLOUD_TIME_OFFSET = 'Cloud time offset'


def canopy(folder):
    vertices, uv, colors = [], [], []
    for row in range(GRID):
        y = 2 * row / (GRID - 1) - 1
        for column in range(GRID):
            x = 2 * column / (GRID - 1) - 1
            radius = math.hypot(x, y)
            vertices.append(unreal.Vector(x * HALF_EDGE, y * HALF_EDGE, max(0, 1 - radius * radius) * HALF_EDGE))
            uv.append(unreal.Vector2D((x + 1) * 1.5, (y + 1) * 1.5))
            alpha = max(0, min(1, (1 - radius) / 0.25))
            colors.append(unreal.LinearColor(1, 1, 1, alpha * alpha * (3 - 2 * alpha)))
    triangles = []
    for row in range(GRID - 1):
        for column in range(GRID - 1):
            i = row * GRID + column
            triangles.extend((unreal.IntVector(i, i + GRID, i + 1),
                              unreal.IntVector(i + 1, i + GRID, i + GRID + 1)))
    mesh = unreal.DynamicMesh()
    mesh.append_buffers_to_mesh(unreal.GeometryScriptSimpleMeshBuffers(
        vertices=vertices, uv0=uv, vertex_colors=colors, triangles=triangles))
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=False)
    asset, result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        mesh, folder + '/SM_CloudCanopy', options)
    if (not isinstance(asset, unreal.StaticMesh) or result != unreal.GeometryScriptOutcomePins.SUCCESS
            or not unreal.EditorAssetLibrary.save_loaded_asset(asset)):
        raise ValueError('Unreal could not save the draft cloud canopy.')
    return asset


def cloud_material(folder, name, texture, drift):
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise ValueError('Unreal could not create the cloud layer material.')
    lib = unreal.MaterialEditingLibrary
    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property('two_sided', True)
    time = lib.create_material_expression(material, unreal.MaterialExpressionTime, -860, 0)
    offset = lib.create_material_expression(material, unreal.MaterialExpressionScalarParameter, -860, 150)
    offset.set_editor_property('parameter_name', CLOUD_TIME_OFFSET)
    phase = lib.create_material_expression(material, unreal.MaterialExpressionAdd, -650, 0)
    panner = lib.create_material_expression(material, unreal.MaterialExpressionPanner, -480, 0)
    panner.set_editor_property('speed_x', drift[0])
    panner.set_editor_property('speed_y', drift[1])
    panner.set_editor_property('fractional_part', True)
    image = lib.create_material_expression(material, unreal.MaterialExpressionTextureSample, -290, 0)
    image.set_editor_property('texture', texture)
    color = lib.create_material_expression(material, unreal.MaterialExpressionVertexColor, -290, 240)
    fade = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -65, 180)
    if not all(lib.connect_material_expressions(*link) for link in (
        (time, '', phase, 'A'), (offset, '', phase, 'B'), (phase, '', panner, 'Time'),
        (panner, '', image, ''), (image, 'A', fade, 'A'), (color, 'A', fade, 'B'),
    )):
        raise ValueError('Unreal could not link the cloud animation material.')
    if (not lib.connect_material_property(image, 'RGB', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
            or not lib.connect_material_property(fade, '', unreal.MaterialProperty.MP_OPACITY)):
        raise ValueError('Unreal could not link cloud color or opacity.')
    # Changing the blend mode compiles the finished graph without the
    # material editor's viewport and texture-streaming debug rebuild.
    material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise ValueError('Unreal could not save the cloud material.')
    return material


def place_clouds(draft, actors, layers):
    if not layers:
        return []
    assets = unreal.EditorAssetLibrary
    _, _, center, radius = draft_backdrop(draft, actors.get_all_level_actors())
    folder = f'/Game/Community/{draft}/SourceSky/Clouds'
    mesh = canopy(folder)
    result = []
    for layer in layers:
        name = source_sky_name(layer['slot'], layer['reference'])
        texture = assets.load_asset(f"/Game/Community/{draft}/SourceSky/Sky_{layer['source_id']}/{name}.{name}")
        if not isinstance(texture, unreal.Texture2D) or assets.get_metadata_tag(texture, 'T3SkySourceReference') != layer['reference']:
            raise ValueError(f"Could not find the imported cloud texture {layer['reference']}.")
        material = cloud_material(folder, f"M_CloudLayer_{layer['layer']}", texture, layer['uv_per_second'])
        position = center + unreal.Vector(0, 0, radius * 0.35 + layer['layer'] * 10)
        actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, position)
        if not actor or not actor.static_mesh_component.set_static_mesh(mesh):
            raise ValueError('Unreal could not place the cloud canopy.')
        half_span = radius * 0.8 / HALF_EDGE
        rise = max(1, radius * 0.45 * layer['height']) / HALF_EDGE
        actor.set_actor_scale3d(unreal.Vector(half_span, half_span, rise))
        actor.set_actor_label(f"Cloud Layer {layer['layer']} · {layer['reference']}")
        actor.set_folder_path('TRIBES Environment/Clouds')
        actor.set_editor_property('tags', [CLOUD_ACTOR_TAG])
        component = actor.static_mesh_component
        component.set_material(0, material)
        component.set_cast_shadow(False)
        component.set_collision_profile_name('NoCollision')
        component.set_editor_property('generate_overlap_events', False)
        component.set_hidden_in_scene_capture(True)
        component.set_editor_property('translucency_sort_priority', int((1 - layer['height']) * 10000))
        result.append(actor)
    return result
