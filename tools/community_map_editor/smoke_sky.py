"""Unreal-side sky qualification. Run only in the generated scratch project."""

import json
import math
import os
from pathlib import Path

import unreal

import t3_map_editor as editor
from draft_sky import apply_sky
from texture_batches import load_batch, save_json
from texture_batches_unreal import import_sky_cube


def expect(condition, label):
    if not condition:
        raise RuntimeError(label)


def samples(backdrop, floor, probes):
    world = editor.world()
    actor = editor.ACTORS.spawn_actor_from_class(
        unreal.SceneCapture2D, backdrop.get_actor_location() + unreal.Vector(0, 0, 200), transient=True)
    expect(actor is not None, 'Could not create the sky probe camera.')
    capture = actor.capture_component2d
    component = backdrop.static_mesh_component
    renderer = unreal.RenderingLibrary
    target = renderer.create_render_target2d(world, 33, 33)
    expect(target is not None, 'Could not allocate the sky probe render target.')
    for key, value in (
        ('capture_every_frame', False), ('capture_on_movement', False), ('fov_angle', 30.0),
        ('primitive_render_mode', unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST),
        ('texture_target', target),
    ):
        capture.set_editor_property(key, value)

    def read_at(pitch, yaw):
        actor.set_actor_rotation(unreal.Rotator(pitch=pitch, yaw=yaw), False)
        renderer.clear_render_target2d(world, target, unreal.LinearColor(1, 0.25, 0.75, 1))
        capture.capture_scene()
        pixel = renderer.read_render_target_raw_pixel(world, target, 16, 16, False)
        return [float(channel) for channel in (pixel.r, pixel.g, pixel.b)]

    expect(component.get_editor_property('hidden_in_scene_capture'), 'The backdrop must be hidden from normal captures.')
    expect(component.get_collision_enabled() == unreal.CollisionEnabled.NO_COLLISION, 'The backdrop has collision.')
    expect(component.get_num_materials() > 0, 'The generated sky mesh has no material slots.')
    results = {}
    try:
        # A known mesh distinguishes an empty capture from a broken sky material.
        capture.show_only_component(floor.static_mesh_component)
        capture.set_editor_property('capture_source', unreal.SceneCaptureSource.SCS_BASE_COLOR)
        control = read_at(-90, 0)
        expect(max(control) > .02 and abs(control[0] - control[2]) < .08,
               f'The floor probe rendered {control}.')
        capture.clear_show_only_components()
        capture.show_only_component(component)
        capture.set_editor_property('capture_source', unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR)
        component.set_hidden_in_scene_capture(False)
        # Initialize the HDR capture after switching the primitive's visibility.
        # The first capture can precede the ready sky shader in a commandlet.
        read_at(0, 0)
        unreal.AutomationLibrary.finish_loading_before_screenshot()
        for label, probe in probes.items():
            rgb = read_at(*probe['rotation'])
            expect(all(math.isfinite(value) for value in rgb), f'{label} returned invalid pixel data.')
            peak = max(rgb)
            match = peak > .05 and all(
                value >= .6 * peak if wanted else value <= .2 * peak
                for value, wanted in zip(rgb, probe['channels'])
            )
            expect(match, f'{label} rendered {rgb}, expected channels {probe["channels"]}.')
            results[label] = rgb
        component.set_hidden_in_scene_capture(True)
        hidden = read_at(0, 0)
        expect(all(math.isfinite(value) and abs(value) <= .05 for value in hidden),
               f'The backdrop rendered in a regular scene capture: {hidden}.')
        results['hidden capture'] = hidden
    finally:
        component.set_hidden_in_scene_capture(True)
        expect(editor.ACTORS.destroy_actor(actor), 'Could not remove the sky probe camera.')
    return results


def qualify():
    output = Path(os.environ['T3_SKY_QUALIFICATION'])
    fixture = json.loads((output / 'fixture.json').read_bytes())
    expect(editor.LEVELS.new_level(fixture['level']), 'Could not create the scratch map.')
    draft = editor.draft_id()
    expect(draft == fixture['draft'], 'The scratch map has the wrong draft identity.')
    floor = editor.ACTORS.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, -100))
    cube_mesh = editor.ASSETS.load_asset('/Engine/BasicShapes/Cube.Cube')
    expect(floor and cube_mesh and floor.static_mesh_component.set_static_mesh(cube_mesh),
           'Could not create the scratch bounds fixture.')
    floor.set_actor_scale3d(unreal.Vector(400, 400, 2))
    root, items = load_batch(os.environ['T3_TEXTURE_LIBRARY'], draft, fixture['batch'])
    cube = import_sky_cube(draft, root, items, fixture['sky'])
    backdrop = apply_sky(draft, cube, editor.ACTORS)
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    rgb = samples(backdrop, floor, fixture['probes'])
    expect(editor.ACTORS.destroy_actor(floor), 'Could not remove the scratch bounds fixture.')
    expect(editor.LEVELS.save_current_level(), 'Could not save the tested sky and map.')
    save_json(output / 'render-proof.json', {'draft': draft, 'cube': cube.get_path_name(), 'pixels': rgb})


qualify()
