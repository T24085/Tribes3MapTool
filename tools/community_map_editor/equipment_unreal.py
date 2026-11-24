"""Keep source equipment provenance and measure approved Direct Host anchors."""

import hashlib
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import unreal

from intake_contract import bounded


SOURCE_TAG = 'T3EquipmentSource_'
INTAKE_TAG = 'T3EquipmentIntake_'
APPROVED = 'T3EquipmentApproved'
SHIELD_GROUP = 'T3ShieldCircuit_'
SHIELD_LOSS = 'T3ShieldPowerLoss_'
SHIELD_POLICIES = {'drop', 'weaken', 'stay'}
ROLES = {'generator', 'sensor', 'base_turret', 'force_shield'}
INTERIOR = 'T3BakedInterior'
INTERIOR_INTAKE = 'T3InteriorPowerIntake_'
INTERIOR_GROUP = 'T3InteriorPowerGroup_'


def source_folder():
    saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    return Path(saved)/'T3DraftEquipment'


def store_source(equipment):
    raw = json.dumps(equipment, ensure_ascii=False).encode('utf-8')
    sha = hashlib.sha256(raw).hexdigest()
    folder = source_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/(sha+'.json')
    if not target.exists():
        with NamedTemporaryFile(dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        try:
            temporary.rename(target)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    if bounded(target, 16*1024*1024) != raw:
        raise ValueError('The stored equipment source has changed.')
    return sha


def marker_identity(actor):
    tags = [str(tag) for tag in actor.tags]
    source = [tag[len(SOURCE_TAG):] for tag in tags if tag.startswith(SOURCE_TAG)]
    intake = [tag[len(INTAKE_TAG):] for tag in tags if tag.startswith(INTAKE_TAG)]
    if (not (isinstance(actor, unreal.TargetPoint) or isinstance(actor, unreal.TriggerBox))
            or len(source) != 1 or len(intake) != 1
            or not re.fullmatch(r'[1-9][0-9]{0,4}', source[0]) or not re.fullmatch('[a-f0-9]{64}', intake[0])):
        raise ValueError(f'{actor.get_actor_label()} has no bound equipment source. Import the T2 intake into a new draft to bind its markers.')
    return int(source[0]), intake[0]


def read_source(sha):
    raw = bounded(source_folder()/(sha+'.json'), 16*1024*1024)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('The saved equipment source no longer matches the draft markers.')
    return raw, {row['source_id']: row for row in json.loads(raw)['equipment']}


def shield_settings(actor):
    tags = list(map(str, actor.tags))
    groups = [tag[len(SHIELD_GROUP):] for tag in tags if tag.startswith(SHIELD_GROUP)]
    policies = [tag[len(SHIELD_LOSS):] for tag in tags if tag.startswith(SHIELD_LOSS)]
    if (len(groups) > 1 or len(policies) > 1
            or groups and not re.fullmatch(r'(?:0|[1-9][0-9]{0,4})', groups[0])
            or policies and policies[0] not in SHIELD_POLICIES):
        raise ValueError(f'{actor.get_actor_label()} has invalid shield power settings.')
    settings = {}
    if groups:
        settings['shield_power_group'] = int(groups[0])
    if policies:
        settings['on_power_loss'] = policies[0]
    return settings


def set_shield_settings(selected, setting):
    if not selected:
        raise ValueError('Select one or more shield opening boxes.')
    records = [(actor, *marker_identity(actor)) for actor in selected]
    sources = {sha: read_source(sha)[1] for sha in {sha for _, _, sha in records}}
    shields, generators = [], []
    for actor, ident, sha in records:
        row = sources[sha].get(ident, {})
        if row.get('role') == 'force_shield' and isinstance(actor, unreal.TriggerBox):
            shields.append((actor, row, sha))
        elif setting == 'link' and row.get('role') == 'generator' and isinstance(actor, unreal.TargetPoint):
            generators.append((row, sha))
        else:
            raise ValueError('Use shield opening boxes and, when linking, one generator marker.')
    if not shields:
        raise ValueError('Select at least one shield opening box.')
    if setting in SHIELD_POLICIES:
        prefix, value = SHIELD_LOSS, setting
    else:
        prefix = SHIELD_GROUP
        if setting == 'link':
            if len(generators) != 1:
                raise ValueError('Select exactly one generator with the shield opening boxes.')
            generator, source = generators[0]
            value = generator['power_group']
            if value is None or not generator['team'] or any(
                sha != source or shield['team'] != generator['team'] for _, shield, sha in shields
            ):
                raise ValueError('Link shields to a same-team generator circuit from the same intake.')
        elif setting in ('unlink', 'source'):
            value = 0 if setting == 'unlink' else None
        else:
            raise ValueError('Unknown shield power setting.')
    with unreal.ScopedEditorTransaction('Set force shield power'):
        for actor, _, _ in shields:
            tags = [tag for tag in map(str, actor.tags) if not tag.startswith(prefix)]
            if value is not None:
                tags.append(prefix + str(value))
            actor.modify()
            actor.set_editor_property('tags', tags)


def mark_interior_lighting(actor, source_hash, group):
    tags = [str(tag) for tag in actor.tags
            if str(tag) != INTERIOR and not str(tag).startswith((INTERIOR_INTAKE, INTERIOR_GROUP))]
    tags.append(INTERIOR)
    if group is not None:
        tags.extend((INTERIOR_INTAKE + source_hash, INTERIOR_GROUP + str(group)))
    actor.set_editor_property('tags', tags)


def interior_circuit(actor):
    tags = list(map(str, actor.tags))
    if INTERIOR not in tags:
        return None
    sources = [tag[len(INTERIOR_INTAKE):] for tag in tags if tag.startswith(INTERIOR_INTAKE)]
    groups = [tag[len(INTERIOR_GROUP):] for tag in tags if tag.startswith(INTERIOR_GROUP)]
    if not sources and not groups:
        return None
    if (len(sources) != 1 or len(groups) != 1 or not re.fullmatch(r'[a-f0-9]{64}', sources[0])
            or not re.fullmatch(r'[1-9][0-9]{0,4}', groups[0])):
        raise ValueError(f'{actor.get_actor_label()} has an invalid lighting circuit.')
    return sources[0], int(groups[0])


def set_interior_link(selected, linked):
    interiors = [actor for actor in selected if INTERIOR in map(str, actor.tags)]
    markers = [actor for actor in selected if actor not in interiors]
    if not interiors or (linked and len(markers) != 1) or (not linked and markers):
        raise ValueError('Select baked interiors and one generator marker to link them, or select interiors only to clear a link.')
    sha = group = None
    if linked:
        ident, sha = marker_identity(markers[0])
        raw = bounded(source_folder()/(sha+'.json'), 16*1024*1024)
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('The saved equipment source no longer matches the generator marker.')
        source = next((row for row in json.loads(raw)['equipment'] if row['source_id'] == ident), None)
        if not source or source['role'] != 'generator' or not source['team'] or source['power_group'] is None:
            raise ValueError('Select a source generator marker on an authored team power group.')
        group = source['power_group']
    with unreal.ScopedEditorTransaction('Set interior lighting circuit'):
        for actor in interiors:
            actor.modify()
            mark_interior_lighting(actor, sha, group)


def interior_reviews(world, actors, sha, groups):
    from draft_model_materials import ALARM_BLEND, BAKED_MATERIAL_PREFIXES

    records = []
    for actor in actors:
        if INTERIOR_INTAKE + sha not in map(str, actor.tags):
            continue
        circuit = interior_circuit(actor)
        if circuit is None:
            continue
        _, group = circuit
        if group not in groups:
            continue
        if (not isinstance(actor, unreal.StaticMeshActor)
                or actor.get_path_name() != world.get_path_name()+':PersistentLevel.'+actor.get_name()):
            raise ValueError(f'{actor.get_actor_label()} must be a static mesh in the draft’s persistent level.')
        component = actor.static_mesh_component
        mesh = component.static_mesh
        materials = []
        for slot, material in enumerate(component.get_materials()):
            if (material and material.get_name().startswith(BAKED_MATERIAL_PREFIXES)
                    and ALARM_BLEND in map(str, unreal.MaterialEditingLibrary.get_scalar_parameter_names(material))):
                materials.append({'slot': slot, 'asset': material.get_path_name()})
        if materials and mesh:
            records.append({'actor': actor.get_name(), 'asset': mesh.get_path_name(),
                            'group_id': group, 'materials': materials})
    return records


def finish_support_compilation():
    # Reading render data waits for a pending static mesh build in Unreal.
    seen = set()
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in actors.get_all_level_actors():
        if not isinstance(actor, unreal.StaticMeshActor):
            continue
        component = actor.static_mesh_component
        mesh = component.static_mesh
        if mesh and component.get_collision_object_type() == unreal.CollisionChannel.ECC_WORLD_STATIC:
            name = mesh.get_path_name()
            if name not in seen:
                mesh.get_num_triangles(0)
                seen.add(name)


def review(world, actors, map_id, *, interiors=()):
    identities = {marker_identity(actor): actor for actor in actors}
    if len(identities) != len(actors):
        raise ValueError('Only one approved marker can represent each T2 source object.')
    hashes = {sha for _, sha in identities}
    if len(hashes) != 1:
        raise ValueError('Approved equipment must come from one T2 intake.')
    sha, = hashes
    raw, source = read_source(sha)
    finish_support_compilation()
    placements = []
    for (ident, _), actor in sorted(identities.items()):
        record = source.get(ident)
        if not record or record['role'] not in ROLES or not record['team']:
            raise ValueError(f'{actor.get_actor_label()} needs a supported native role and an authored team.')
        point = actor.get_actor_location()
        rotation = actor.get_actor_rotation()
        placement = dict(source_id=ident, anchor_position_cm=list(point.to_tuple()), yaw_degrees=rotation.yaw)
        foot = point
        if record['role'] == 'force_shield':
            if not isinstance(actor, unreal.TriggerBox):
                raise ValueError(f'Re-import {actor.get_actor_label()} to create its shield opening box.')
            box = actor.get_component_by_class(unreal.BoxComponent)
            extent = box.get_scaled_box_extent()
            if (any(not math.isfinite(value) or not 0 < value <= 1000 for value in (extent.y, extent.z))
                    or any(abs(angle) > .1 for angle in (rotation.pitch, rotation.roll))):
                raise ValueError(f'{actor.get_actor_label()} needs an upright opening box with positive width and height.')
            placement['half_size_cm'] = [extent.y, extent.z]
            placement.update(shield_settings(actor))
            if placement.get('shield_power_group', record['power_group']) is None:
                raise ValueError(f'Link {actor.get_actor_label()} to a generator or set it to independent power.')
            foot = point - unreal.Vector(0, 0, extent.z)
        elif not isinstance(actor, unreal.TargetPoint):
            raise ValueError(f'{actor.get_actor_label()} needs a point marker for its native equipment role.')
        elif record['power_group'] is None:
            raise ValueError(f'{actor.get_actor_label()} needs an authored team power group.')
        result = unreal.SystemLibrary.line_trace_single(
            world, foot+unreal.Vector(0, 0, 30), foot-unreal.Vector(0, 0, 240),
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [actor], unreal.DrawDebugTrace.NONE, True,
        )
        hit = result.to_tuple() if result else None
        if (not hit or not hit[0] or hit[1] or hit[7].z < 0.55
                or not isinstance(hit[9], unreal.StaticMeshActor)):
            raise ValueError(f'Move {actor.get_actor_label()} just above the intended walkable support surface.')
        support_actor = hit[9]
        component = support_actor.static_mesh_component
        mesh = component.static_mesh
        if (not mesh or hit[10] != component
                or component.get_collision_object_type() != unreal.CollisionChannel.ECC_WORLD_STATIC
                or support_actor.get_path_name() != world.get_path_name()+':PersistentLevel.'+support_actor.get_name()):
            raise ValueError(f'{actor.get_actor_label()} needs a solid static mesh in the draft’s persistent level.')
        asset = mesh.get_path_name()
        if not asset.startswith('/Game/Community/'):
            raise ValueError(f'{actor.get_actor_label()} needs support from a cooked Community asset.')
        is_ground = (asset == f'/Game/Community/{map_id}/Terrain.Terrain'
                     or any(str(tag).startswith('T3Intake_') for tag in support_actor.tags))
        kind = 'terrain' if is_ground else 'structures'
        if 'half_size_cm' in placement and abs(hit[5].z-foot.z) > 30:
            raise ValueError(f'Align the bottom of {actor.get_actor_label()} with the doorway floor.')
        placement['support'] = dict(actor=support_actor.get_name(),asset=asset,kind=kind)
        placements.append(placement)
    result = dict(format=1, map=map_id, equipment_sha256=sha, placements=placements)
    if interiors:
        approved_ids = {row['source_id'] for row in placements}
        generators = {row['power_group'] for row in source.values()
                      if row['source_id'] in approved_ids and row['role'] == 'generator'}
        if links := interior_reviews(world, interiors, sha, generators):
            result['powered_interiors'] = links
    return raw, result


def set_approval(world, selected, map_id, approved):
    if not selected:
        raise ValueError('Select one or more equipment markers in the World Outliner.')
    if approved:
        review(world, selected, map_id)
    else:
        for actor in selected:
            marker_identity(actor)
    with unreal.ScopedEditorTransaction('Set Direct Host equipment approval'):
        for actor in selected:
            tags = [str(tag) for tag in actor.tags if str(tag) != APPROVED]
            if approved:
                tags.append(APPROVED)
            actor.modify()
            actor.set_editor_property('tags', tags)
            folder = 'Approved equipment' if approved else 'Equipment review'
            actor.set_folder_path('TRIBES CTF/'+folder)
