"""Play a private Darkstar animation on a transient editor overlay."""

import json
import os
from pathlib import Path
import subprocess

import unreal

from draft_interiors import mesh_package
from dts_preview import ClipSampler
from intake_contract import intake_header, load_intake_geometry
from interior_geometry import render_sections
from source_library import verified_draft_intake


_preview = None
INTERVAL = 1 / 24
VISUAL_TAG = 'T3DtsCookedVisual'


def valid(obj):
    return obj is not None and unreal.SystemLibrary.is_valid(obj)


def stop():
    global _preview
    current, _preview = _preview, None
    if current is not None:
        current.close()


def selected_model(editor, actor):
    library = Path(os.environ['T3_CONVERTER_LIBRARY'])
    manifest = verified_draft_intake(library, editor.draft_id())
    if manifest is None:
        raise ValueError('Open a Tribes 1 draft with a private map intake.')
    inventory = intake_header(manifest)
    static_mesh = actor.static_mesh_component.static_mesh
    if static_mesh:
        selected = static_mesh.get_path_name().split('.')[0]
        for file in inventory.get('shapes', {}).values():
            digest = inventory['files'][file]
            if not selected.endswith('_' + digest[:16]):
                continue
            data, _ = load_intake_geometry(manifest.parent, file, digest)
            if 'animation' in data and mesh_package(data, digest) == selected:
                return data
    raise ValueError('Select an imported DTS prop with animation in the World Outliner.')


def visuals(actor):
    return [child for child in actor.get_attached_actors() if VISUAL_TAG in map(str, child.tags)]


def source_materials(actor, slot_count):
    mesh = actor.static_mesh_component
    materials = [None] * slot_count
    for component_slot, slot in enumerate(mesh.static_mesh.get_editor_property('static_materials')):
        prefix = str(slot.get_editor_property('material_slot_name')).split('_', 1)[0]
        if prefix.isdecimal() and (source_slot := int(prefix)) < slot_count:
            materials[source_slot] = mesh.get_material(component_slot)
    return materials


def choose_clip(model, *, bake=False):
    details = [{'name': clip['name'], 'seconds': clip['duration_s'], 'loop': clip['loop']}
               for clip in model['animation']['clips']]
    command = Path(os.environ['T3_CONVERTER_TOOL']) / 'dts_preview_window.py'
    args = [os.environ['T3_CONVERTER_PYTHON'], '-E', '-X', 'utf8', str(command)]
    if bake:
        args.append('--bake')
    result = subprocess.run(args,
                            input=json.dumps(details), capture_output=True, encoding='utf-8',
                            check=False, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-800:] or 'Could not open the DTS clip picker.')
    if result.stdout.strip():
        index, position = json.loads(result.stdout)
        return index, position
    return None


def selected_actor(editor):
    selected = list(editor.ACTORS.get_selected_level_actors())
    if _preview is not None and (not selected or selected == [_preview.overlay]):
        selected = [_preview.original]
    stop()
    if len(selected) != 1:
        raise ValueError('Select one imported DTS prop in the World Outliner.')
    actor, = selected
    if valid(actor) and VISUAL_TAG in map(str, actor.tags):
        actor = actor.get_attach_parent_actor()
    if not isinstance(actor, unreal.StaticMeshActor):
        raise ValueError('Select one imported DTS prop in the World Outliner.')
    return actor


def show_selected(editor):
    actor = selected_actor(editor)
    model = selected_model(editor, actor)
    choice = choose_clip(model)
    if choice is not None:
        begin(editor, actor, model, *choice)


def begin(editor, actor, model, clip, position=None):
    global _preview
    stop()
    current = Preview(editor, actor, ClipSampler(model, clip))
    try:
        current.open(position)
    except Exception:
        current.close()
        raise
    _preview = current
    return current


class Preview:
    def __init__(self, editor, actor, sampler):
        self.editor = editor
        self.original = actor
        self.world = editor.world()
        self.sampler = sampler
        self.visibility = [(target, target.is_temporarily_hidden_in_editor()) for target in [actor, *visuals(actor)]]
        self.selection = list(editor.ACTORS.get_selected_level_actors())
        self.overlay = None
        self.handle = None
        self.elapsed = self.last_draw = 0.0
        self.still = False

    def open(self, position):
        original = self.original
        self.overlay = self.editor.ACTORS.spawn_actor_from_class(
            unreal.DynamicMeshActor, original.get_actor_location(), original.get_actor_rotation(), transient=True)
        if not valid(self.overlay):
            raise RuntimeError('Unreal did not create the DTS preview overlay.')
        self.overlay.set_editor_property('is_editor_only_actor', True)
        self.overlay.set_actor_label('DTS preview')
        component = self.overlay.get_dynamic_mesh_component()
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        self.mesh = component.get_dynamic_mesh()
        for slot, material in enumerate(source_materials(original, len(self.sampler.materials))):
            if material:
                component.set_material(slot, material)
        self.draw(0.0 if position is None else position)
        for target, _ in self.visibility:
            target.set_is_temporarily_hidden_in_editor(True)
        self.still = position is not None or self.sampler.duration == 0
        self.handle = unreal.register_slate_post_tick_callback(self.tick)

    def draw(self, position):
        original, overlay = self.original, self.overlay
        overlay.set_actor_location_and_rotation(original.get_actor_location(), original.get_actor_rotation(), False, False)
        overlay.set_actor_scale3d(original.get_actor_scale3d())
        sampled = self.sampler.sample(position)
        self.mesh.reset()
        if sampled['triangles']:
            for slot, _, vertices, uv, triangles, *_ in render_sections(sampled):
                self.mesh.append_buffers_to_mesh(
                    unreal.GeometryScriptSimpleMeshBuffers(
                        vertices=[unreal.Vector(*value) for value in vertices],
                        uv0=[unreal.Vector2D(*value) for value in uv],
                        triangles=[unreal.IntVector(*value) for value in triangles]),
                    material_id=slot)
            self.mesh.recompute_normals(unreal.GeometryScriptCalculateNormalsOptions())

    def tick(self, delta):
        try:
            if self.editor.world() != self.world or not valid(self.original) or not valid(self.overlay):
                stop()
                return
            if self.still:
                return
            self.elapsed += delta
            phase = self.elapsed / self.sampler.duration
            if phase >= 1 and not self.sampler.loop:
                self.draw(1.0)
                self.still = True
            elif self.elapsed - self.last_draw >= INTERVAL:
                self.draw(phase % 1.0)
                self.last_draw = self.elapsed
        except Exception as exc:
            stop()
            unreal.log_error(f'TRIBES DTS preview: {exc}')

    def close(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        for target, was_hidden in self.visibility:
            if valid(target):
                target.set_is_temporarily_hidden_in_editor(was_hidden)
        if valid(self.overlay):
            self.editor.ACTORS.destroy_actor(self.overlay)
        self.overlay = None
        if self.editor.world() == self.world:
            self.editor.ACTORS.set_selected_level_actors([actor for actor in self.selection if valid(actor)])
