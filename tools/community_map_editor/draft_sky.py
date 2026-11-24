"""Place a reviewed cubemap behind a generated draft's playable world."""

import math
import os
import re

import unreal

from mission_weather import CLOUD_ACTOR_TAG, PRECIPITATION_ACTOR_TAG
from texture_batches_unreal import SKY_DDS_TAG


SKY_ACTOR_TAG = 'T3DraftSkyBackdrop'
SKY_MATERIAL_TAG = 'T3DraftSkyCubemap'
SKY_MESH_TAG = 'T3DraftSkyShell'
SHELL_HALF_EDGE = 50


def draft_backdrop(draft, actors):
    level = f'Arena_{draft}_P'
    prefix = f'/Game/Maps/Community/{draft}/{level}.{level}:PersistentLevel.'
    backdrop = None
    bounds = []
    for actor in actors:
        if not actor.get_path_name().startswith(prefix):
            continue
        if actor.actor_has_tag(CLOUD_ACTOR_TAG) or actor.actor_has_tag(PRECIPITATION_ACTOR_TAG):
            continue
        if actor.actor_has_tag(SKY_ACTOR_TAG):
            if backdrop is not None or not isinstance(actor, unreal.StaticMeshActor):
                raise ValueError('The draft must have at most one generated sky backdrop.')
            backdrop = actor
            continue
        components = actor.get_components_by_class(unreal.MeshComponent)
        if not components:
            continue
        for component in components:
            for material in component.get_materials():
                base = material.get_base_material() if material else None
                if base and base.get_editor_property('is_sky'):
                    raise ValueError('This draft already has a sky mesh. Set the cubemap in its existing material.')
        center, extent = actor.get_actor_bounds(False)
        bounds.append(((center - extent).to_tuple(), (center + extent).to_tuple()))
    if not bounds:
        raise ValueError('The draft needs a terrain or other mesh before placing a sky backdrop.')
    low = [min(box[0][axis] for box in bounds) for axis in range(3)]
    high = [max(box[1][axis] for box in bounds) for axis in range(3)]
    center = unreal.Vector(*[(lo + hi) * 0.5 for lo, hi in zip(low, high)])
    # Leave clearance for jetting as well as the editor camera outside the terrain footprint.
    radius = max(100_000, 1.5 * math.dist(low, high))
    return prefix, backdrop, center, radius


def backdrop_material(cube):
    assets = unreal.EditorAssetLibrary
    cube_path = cube.get_path_name()
    folder = cube_path.rsplit('/', 1)[0]
    name = 'M_DraftSky'
    path = f'{folder}/{name}.{name}'
    if assets.does_asset_exist(path):
        material = assets.load_asset(path)
        if not isinstance(material, unreal.Material) or assets.get_metadata_tag(material, SKY_MATERIAL_TAG) != cube_path:
            raise ValueError(f'The draft sky material path is already in use: {path}')
        return material
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise ValueError('Unreal could not create the draft sky material.')
    library = unreal.MaterialEditingLibrary
    try:
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property('two_sided', True)
        camera = library.create_material_expression(material, unreal.MaterialExpressionCameraVectorWS, -680, 0)
        direction = library.create_material_expression(material, unreal.MaterialExpressionMultiply, -460, 0)
        direction.set_editor_property('const_b', -1.0)
        sample = library.create_material_expression(material, unreal.MaterialExpressionTextureSample, -240, 0)
        sample.set_editor_property('texture', cube)
        sampler = (unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if cube.get_editor_property('srgb')
                   else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
        sample.set_editor_property('sampler_type', sampler)
        # Use UE's first-input fallback for Multiply.A and the sample's UVs.
        if not library.connect_material_expressions(camera, '', direction, ''):
            raise ValueError('Unreal could not connect the sky camera vector.')
        if not library.connect_material_expressions(direction, '', sample, ''):
            raise ValueError('Unreal could not connect the cubemap coordinates.')
        if not library.connect_material_property(sample, 'RGB', unreal.MaterialProperty.MP_EMISSIVE_COLOR):
            raise ValueError('Unreal could not connect the cubemap to sky emissive color.')
        # The property change compiles the completed graph. The material editor's
        # UV-density debug pass is unnecessary for camera-direction sampling.
        material.set_editor_property('is_sky', True)
        assets.set_metadata_tag(material, SKY_MATERIAL_TAG, cube_path)
        if not assets.save_loaded_asset(material):
            raise ValueError('Unreal could not save the draft sky material.')
    except Exception:
        if os.environ.get('T3_EDITOR_INTERACTIVE') == '1':
            assets.delete_loaded_asset(material)
        raise
    return material


def backdrop_mesh(folder):
    assets = unreal.EditorAssetLibrary
    package = f'{folder}/SM_DraftSky'
    path = package + '.SM_DraftSky'
    if assets.does_asset_exist(path):
        existing = assets.load_asset(path)
        if not isinstance(existing, unreal.StaticMesh) or assets.get_metadata_tag(existing, SKY_MESH_TAG) != '1':
            raise ValueError(f'The draft sky mesh path is already in use: {path}')
        return existing
    # Camera-direction cubemap sampling makes the shell geometry independent of the image.
    vertices, triangles, uv = [], [], []
    for axis in range(3):
        for sign in (-1, 1):
            start = len(vertices)
            for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                point = [0, 0, 0]
                point[axis], point[(axis + 1) % 3], point[(axis + 2) % 3] = sign, u, v
                vertices.append(unreal.Vector(*(value * SHELL_HALF_EDGE for value in point)))
                uv.append(unreal.Vector2D((u + 1) / 2, (v + 1) / 2))
            indices = ((0, 1, 2), (0, 2, 3)) if sign < 0 else ((0, 2, 1), (0, 3, 2))
            triangles.extend(unreal.IntVector(*(start + index for index in face)) for face in indices)
    mesh = unreal.DynamicMesh()
    mesh.append_buffers_to_mesh(unreal.GeometryScriptSimpleMeshBuffers(vertices=vertices, uv0=uv, triangles=triangles))
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=False)
    asset, status = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(mesh, package, options)
    if not isinstance(asset, unreal.StaticMesh) or status != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise ValueError('Unreal could not create the draft sky shell.')
    assets.set_metadata_tag(asset, SKY_MESH_TAG, '1')
    if not assets.save_loaded_asset(asset):
        if os.environ.get('T3_EDITOR_INTERACTIVE') == '1':
            assets.delete_loaded_asset(asset)
        raise ValueError('Unreal could not save the draft sky shell.')
    return asset


def apply_selected_sky(draft, actor_subsystem):
    chosen = unreal.EditorUtilityLibrary.get_selected_assets()
    if len(chosen) != 1:
        raise ValueError('Select one imported TextureCube in the Content Browser.')
    return apply_sky(draft, chosen[0], actor_subsystem)


def apply_sky(draft, cube, actor_subsystem):
    if not re.fullmatch(r'Draft(?:CTF|T2)[0-9]{3}', draft):
        raise ValueError('Open a generated Tribes 2 or Blank CTF draft to place a sky backdrop.')
    if not isinstance(cube, unreal.TextureCube):
        raise ValueError('Select one imported TextureCube in the Content Browser.')
    folder = f'/Game/Community/{draft}/TextureRevisions/Sky'
    path = cube.get_path_name()
    if (not re.fullmatch(re.escape(folder) + r'/B_[0-9a-f]{12}/R_[0-9a-f]{16}/SkyCube\.SkyCube', path)
            or not re.fullmatch(r'[0-9a-f]{64}', unreal.EditorAssetLibrary.get_metadata_tag(cube, SKY_DDS_TAG) or '')):
        raise ValueError('Choose a reviewed sky cubemap imported into this draft.')
    prefix, backdrop, center, radius = draft_backdrop(draft, actor_subsystem.get_all_level_actors())
    mesh = backdrop_mesh(folder) if backdrop is None else None
    material = backdrop_material(cube)

    with unreal.ScopedEditorTransaction('Apply reviewed draft sky') as transaction:
        created = backdrop is None
        if created:
            backdrop = actor_subsystem.spawn_actor_from_class(unreal.StaticMeshActor, center)
            if backdrop is None:
                raise ValueError('Unreal could not place the draft sky backdrop.')
        component = backdrop.static_mesh_component
        original = list(component.get_editor_property('override_materials'))
        try:
            if created:
                if not backdrop.get_path_name().startswith(prefix) or not component.set_static_mesh(mesh):
                    raise ValueError('Unreal could not place the sky shell in the active draft level.')
                scale = radius / SHELL_HALF_EDGE
                backdrop.set_actor_scale3d(unreal.Vector(scale, scale, scale))
                backdrop.set_actor_label('Reviewed Sky Backdrop')
                backdrop.set_folder_path('TRIBES Environment')
                backdrop.set_editor_property('tags', [SKY_ACTOR_TAG])
                component.set_cast_shadow(False)
                component.set_collision_profile_name('NoCollision')
                component.set_editor_property('generate_overlap_events', False)
                component.set_hidden_in_scene_capture(True)
                component.set_editor_property('allow_cull_distance_volume', False)
            component.modify()
            component.set_material(0, material)
            assigned = component.get_material(0)
            if not assigned or assigned.get_path_name() != material.get_path_name():
                raise ValueError('The draft sky backdrop did not accept its material.')
        except Exception:
            if created:
                actor_subsystem.destroy_actor(backdrop)
            else:
                component.set_editor_property('override_materials', original)
            transaction.cancel()
            raise
    return backdrop
