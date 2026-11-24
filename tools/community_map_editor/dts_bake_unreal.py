"""Bake a selected DTS clip into a stock Geometry Cache actor."""

from hashlib import sha256
import json
from pathlib import PurePosixPath
import re

import unreal

from dts_preview import ClipSampler, bake_times
from dts_preview_unreal import VISUAL_TAG, choose_clip, selected_actor, selected_model, source_materials, visuals
from interior_geometry import render_sections


SOURCE_HIDDEN_TAG = 'T3DtsSourceOriginallyHidden'


def prepare_materials(materials):
    shaders = unreal.MaterialEditingLibrary
    usage = unreal.MaterialUsage.MATUSAGE_GEOMETRY_CACHE
    base_materials = {material.get_base_material() for material in materials if material}
    for material in base_materials:
        if not material or shaders.has_material_usage(material, usage):
            continue
        path = material.get_path_name()
        if path.startswith('/Engine/EngineMaterials/') and material.get_name() in {'DefaultMaterial', 'WorldGridMaterial'}:
            continue
        if not path.startswith('/Game/'):
            raise ValueError(f'{material.get_name()} needs a project material that supports Geometry Cache.')
        if not shaders.set_material_usage(material, usage):
            raise ValueError(f'{material.get_name()} cannot be used with Geometry Cache.')
        if not unreal.EditorAssetLibrary.save_loaded_asset(material):
            raise RuntimeError(f'Could not save Geometry Cache shader support for {material.get_name()}.')


def built_asset(actor, model, index):
    source = model['source']
    materials = source_materials(actor, len(model['materials']))
    provenance = ['geometry-cache-v1', source['sha256'], source['decoder_sha256'], index,
                  [material.get_path_name() if material else None for material in materials]]
    digest = sha256(json.dumps(provenance).encode()).hexdigest()
    stem = re.sub(r'[^A-Za-z0-9_]', '_', PurePosixPath(source['name']).stem)[:32]
    package = f'/Game/Community/T1Motion/GC_{stem}_{digest[:16]}'
    assets = unreal.EditorAssetLibrary
    if assets.does_asset_exist(package):
        asset = assets.load_asset(package)
        if isinstance(asset, unreal.GeometryCache) and assets.get_metadata_tag(asset, 'T3DtsBakeHash') == digest:
            return asset
        raise ValueError(f'The animation asset path is already in use: {package}')

    sampler = ClipSampler(model, index)
    times = bake_times(model['animation']['clips'][index])
    frames = []
    corner_count = 0
    with unreal.ScopedSlowTask(len(times), 'Baking DTS animation') as task:
        task.make_dialog(True)
        for seconds, position in times:
            if task.should_cancel():
                return None
            task.enter_progress_frame()
            sample = sampler.sample(position)
            sections = []
            if sample['triangles']:
                for slot, _, vertices, uv, *_ in render_sections(sample):
                    corner_count += len(vertices)
                    if corner_count > 1_200_000:
                        raise ValueError('This animation exceeds the Geometry Cache size limit.')
                    sections.append(unreal.CommunityMotionSection(material_index=slot,
                        corners=[unreal.Vector(*value) for value in vertices],
                        texture_coordinates=[unreal.Vector2D(*value) for value in uv]))
            frames.append(unreal.CommunityMotionFrame(seconds=seconds, sections=sections))
        if not corner_count:
            raise ValueError('This clip has no visible geometry to bake.')
        prepare_materials(materials)
        asset = unreal.CommunityMapMotionLibrary.build_geometry_cache(package, frames, materials)
    if not asset:
        raise RuntimeError('Unreal could not create the DTS Geometry Cache.')
    try:
        assets.set_metadata_tag(asset, 'T3DtsBakeHash', digest)
        if not assets.save_loaded_asset(asset, only_if_is_dirty=False):
            raise RuntimeError('Unreal could not save the DTS Geometry Cache.')
    except Exception:
        assets.delete_loaded_asset(asset)
        raise
    return asset


def bake(editor, actor, model, index):
    if not hasattr(unreal, 'CommunityMapMotionLibrary'):
        raise ValueError('Install the matching Community Map Motion editor plugin to bake DTS animation.')
    existing = visuals(actor)
    if len(existing) > 1:
        raise ValueError('This prop has multiple baked visuals. Keep only one before rebaking.')
    asset = built_asset(actor, model, index)
    if asset is None:
        return None
    mesh = actor.static_mesh_component
    loop = model['animation']['clips'][index]['loop']
    with unreal.ScopedEditorTransaction('Bake DTS animation'):
        if existing:
            visual, = existing
            bind_visual(visual, asset, loop)
        else:
            visible = mesh.get_editor_property('visible')
            visual = editor.ACTORS.spawn_actor_from_class(
                unreal.GeometryCacheActor, actor.get_actor_location(), actor.get_actor_rotation())
            if not visual:
                raise RuntimeError('Unreal could not place the baked DTS animation.')
            try:
                visual.set_actor_scale3d(actor.get_actor_scale3d())
                keep = unreal.AttachmentRule.KEEP_WORLD
                if not visual.attach_to_actor(actor, '', keep, keep, keep, False):
                    raise RuntimeError('Unreal could not attach the baked animation to its prop.')
                visual.set_actor_label(actor.get_actor_label() + ' · Baked animation')
                visual.set_folder_path(actor.get_folder_path())
                visual.set_editor_property('tags', [VISUAL_TAG] if visible else [VISUAL_TAG, SOURCE_HIDDEN_TAG])
                bind_visual(visual, asset, loop)
                mesh.set_editor_property('visible', False)
            except Exception:
                editor.ACTORS.destroy_actor(visual)
                mesh.set_editor_property('visible', visible)
                raise
        editor.ACTORS.set_selected_level_actors([actor])
    return visual


def bind_visual(actor, asset, loop):
    component = actor.get_geometry_cache_component()
    component.modify()
    if component.get_editor_property('geometry_cache') != asset and not component.set_geometry_cache(asset):
        raise RuntimeError('Unreal could not bind the baked DTS animation.')
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    component.set_looping(loop)
    component.play_from_start()


def remove_bake(editor, actor):
    existing = visuals(actor)
    if not existing:
        raise ValueError('The selected prop has no baked DTS animation.')
    if len(existing) != 1:
        raise ValueError('Keep only one baked visual before removing it.')
    visual, = existing
    visible = SOURCE_HIDDEN_TAG not in map(str, visual.tags)
    with unreal.ScopedEditorTransaction('Remove baked DTS animation'):
        if not editor.ACTORS.destroy_actor(visual):
            raise RuntimeError('Unreal could not remove the baked DTS animation.')
        actor.static_mesh_component.set_editor_property('visible', visible)


def selected_action(editor, remove=False):
    actor = selected_actor(editor)
    if remove:
        return remove_bake(editor, actor)
    if not hasattr(unreal, 'CommunityMapMotionLibrary'):
        raise ValueError('Install the matching Community Map Motion editor plugin to bake DTS animation.')
    model = selected_model(editor, actor)
    choice = choose_clip(model, bake=True)
    return bake(editor, actor, model, choice[0]) if choice is not None else None
