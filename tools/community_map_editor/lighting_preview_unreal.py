"""Save draft lighting cameras and render reproducible editor-only power previews."""

from collections import defaultdict
from hashlib import sha256
import json
from math import hypot
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import unreal

from draft_model_materials import ALARM_BLEND, BAKED_MATERIAL_PREFIXES
from equipment_unreal import interior_circuit


CAMERA_TAG = 'T3LightingPreviewCamera'
CAMERA_FOLDER = 'TRIBES Lighting Previews'
DEFAULT_FOV = 80
SIZE = (960, 540)


def lighting_slots(actors):
    slots = []
    for actor in actors.get_all_level_actors():
        if not isinstance(actor, unreal.StaticMeshActor):
            continue
        component = actor.static_mesh_component
        circuit = interior_circuit(actor)
        for index, material in enumerate(component.get_materials()):
            base = material.get_base_material() if material else None
            if base and base.get_name().startswith(BAKED_MATERIAL_PREFIXES):
                slots.append((actor, component, index, material, circuit))
    return slots


def spawn_camera(actors, label, location, rotation):
    camera = actors.spawn_actor_from_class(unreal.CameraActor, location, rotation)
    if not camera:
        raise RuntimeError('Unreal did not create a lighting preview camera.')
    camera.set_editor_property('is_editor_only_actor', True)
    camera.set_editor_property('tags', [CAMERA_TAG])
    camera.set_folder_path(CAMERA_FOLDER)
    camera.set_actor_label(label)
    camera.camera_component.set_field_of_view(DEFAULT_FOV)
    return camera


def save_viewport_camera(editor, actors):
    pose = editor.get_level_viewport_camera_info()
    if pose is None:
        raise ValueError('Open a perspective viewport to save a lighting camera.')
    with unreal.ScopedEditorTransaction('Save lighting preview camera'):
        camera = spawn_camera(actors, 'Lighting Camera', *pose)
        actors.set_selected_level_actors([camera])
    return camera


def saved_cameras(actors, *, selected_only=False):
    candidates = actors.get_selected_level_actors() if selected_only else actors.get_all_level_actors()
    return sorted((actor for actor in candidates if isinstance(actor, unreal.CameraActor)
                   and actor.get_editor_property('is_editor_only_actor')
                   and CAMERA_TAG in map(str, actor.tags)),
                  key=lambda camera: (camera.get_actor_label().casefold(), camera.get_name()))


def frame_selected_camera(editor, actors):
    selected = saved_cameras(actors, selected_only=True)
    if len(selected) != 1:
        raise ValueError('Select one lighting camera in the World Outliner.')
    camera = selected[0]
    editor.set_level_viewport_camera_info(camera.get_actor_location(), camera.get_actor_rotation())
    return camera


def seed_cameras(actors):
    """Provide an initial inspection view for each circuit and one for unlinked lighting."""
    representatives = {}
    for actor, _, _, _, circuit in lighting_slots(actors):
        representatives.setdefault(circuit, actor)
    cameras = []
    for actor in representatives.values():
        center, extent = actor.get_actor_bounds(False)
        rotation = unreal.Rotator(pitch=-52, yaw=32)
        distance = max(240, 2.4 * hypot(extent.x, extent.y, extent.z))
        location = center - rotation.get_forward_vector() * distance
        cameras.append(spawn_camera(actors, 'Lighting · ' + actor.get_actor_label(), location, rotation))
    return cameras


def preview_root(draft):
    saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    return Path(saved) / 'T3LightingPreviews' / draft


def power_states(slots, *, include_circuits):
    groups = defaultdict(set)
    for actor, _, _, _, circuit in slots:
        if circuit is not None:
            groups[circuit].add(actor.get_actor_label())
    states = [
        ('normal', set(), None),
        ('alarm', {circuit for _, _, _, _, circuit in slots}, None),
    ]
    if include_circuits:
        for number, (source, group) in enumerate(sorted(groups), 1):
            identity = (source, group)
            states.append((f'circuit-{number:02d}-offline', {identity}, {
                'source_sha256': source, 'power_group': group, 'interiors': sorted(groups[identity]),
            }))
    return states


def capture_images(renderer, world, capture, target, instances, states, folder):
    frames = []
    for name, offline, circuit in states:
        for instance, assigned in instances:
            instance.set_scalar_parameter_value(ALARM_BLEND, float(assigned in offline))
        capture.capture_scene()
        filename = name + '.png'
        renderer.export_render_target(world, target, str(folder), filename)
        raw = (folder / filename).read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise RuntimeError(f'Unreal did not export the {name} lighting preview.')
        frames.append({'state': name, 'file': filename, 'circuit': circuit, 'sha256': sha256(raw).hexdigest()})
    return frames


def render_lighting(world, actors, views, destination, *, separate_views):
    slots = lighting_slots(actors)
    if not slots:
        raise ValueError('This draft has no imported interior lightmaps.')
    if not views:
        raise ValueError('Save a lighting camera or open a draft with lighting cameras.')
    if '-nullrhi' in unreal.SystemLibrary.get_command_line().lower().split():
        raise ValueError('Lighting previews need an editor session with rendering enabled.')

    renderer = unreal.RenderingLibrary
    _, location, rotation, _ = views[0]
    selected = actors.get_selected_level_actors()
    camera = actors.spawn_actor_from_class(unreal.SceneCapture2D, location, rotation, transient=True)
    if not camera:
        raise RuntimeError('Unreal did not create the lighting preview capture.')
    target = None
    restore = []
    active = []
    try:
        target = renderer.create_render_target2d(world, *SIZE, unreal.TextureRenderTargetFormat.RTF_RGBA8)
        if not target:
            raise RuntimeError('Unreal did not allocate the lighting preview image.')
        capture = camera.capture_component2d
        capture.set_editor_property('capture_every_frame', False)
        capture.set_editor_property('capture_on_movement', False)
        capture.set_editor_property('capture_source', unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
        capture.set_editor_property('texture_target', target)
        for _, component, index, material, circuit in slots:
            instance = component.create_dynamic_material_instance(index, material)
            if not instance:
                raise RuntimeError('Unreal could not preview an interior material instance.')
            restore.append((component, index, material))
            active.append((instance, circuit))

        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=destination.parent) as temp:
            pending = Path(temp) / 'capture'
            pending.mkdir()
            capture.capture_scene()
            unreal.AutomationLibrary.finish_loading_before_screenshot()
            states = power_states(slots, include_circuits=separate_views)
            cameras = []
            for number, (label, location, rotation, fov) in enumerate(views, 1):
                folder = pending
                if separate_views:
                    folder = pending / f'view-{number:02d}'
                    folder.mkdir()
                camera.set_actor_location_and_rotation(location, rotation, False, False)
                capture.set_editor_property('fov_angle', fov)
                report = {
                    'label': label, 'location_cm': location.to_tuple(),
                    'rotation_deg': [rotation.roll, rotation.pitch, rotation.yaw],
                    'fov_deg': fov, 'size': SIZE,
                    'frames': capture_images(renderer, world, capture, target, active, states, folder),
                }
                (folder / 'camera.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                cameras.append({'label': label, 'report': (folder / 'camera.json').relative_to(pending).as_posix()})
            if separate_views:
                (pending / 'index.json').write_text(json.dumps({'format': 1, 'cameras': cameras}, indent=2) + '\n',
                                                     encoding='utf-8')
            pending.rename(destination)
    finally:
        for component, index, material in restore:
            component.set_material(index, material)
        actors.destroy_actor(camera)
        actors.set_selected_level_actors(selected)
        if target:
            renderer.release_render_target2d(target)
    return destination


def capture_lighting(world, actors, location, rotation, destination):
    return render_lighting(world, actors, [('Viewport', location, rotation, 90)], destination, separate_views=False)


def capture_viewport(draft, editor, actors):
    pose = editor.get_level_viewport_camera_info()
    if pose is None:
        raise ValueError('Open a perspective viewport to capture the interior lighting.')
    return capture_lighting(editor.get_editor_world(), actors, *pose, preview_root(draft) / uuid4().hex[:12])


def capture_saved_cameras(draft, world, actors, cameras):
    views = [(camera.get_actor_label(), camera.get_actor_location(), camera.get_actor_rotation(),
              camera.camera_component.get_editor_property('field_of_view')) for camera in cameras]
    return render_lighting(world, actors, views, preview_root(draft) / uuid4().hex[:12], separate_views=True)
