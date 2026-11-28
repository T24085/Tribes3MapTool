"""Prepare a cooked legacy editor draft for a Direct Host staging bundle."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from community_maps import MapDefinition, valid_label
from map_sync_contract import (MINIMAP_MAX_IMAGE, OUTSIDE_AUDIO_FILE, OUTSIDE_AUDIO_MAX, map_catalog, map_groups,
                               minimap_payload, outside_audio_document, png_dimensions)
from portable_maps import host_definitions, load_json, merge_map_manifests, owned_path
from tools.t2_map_release import (DRAFT, create_plan, descriptor_paths, flag_boundary_features, projected_manifest, publish_directory,
                                  regular_row, verified_host_source, verify_draft_world_settings, verify_flag_boundary_runtime,
                                  verify_minimap_runtime, supports_shared_file_runtime)
from verified_file_cache import MAX_PACKAGE_MANIFEST_BYTES


def file_row(root, name):
    return {'path': name, **regular_row(root, name)}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def draft_layout(request):
    if (not isinstance(request, dict) or set(request) != {'map', 'markers'}
            or not isinstance(request['map'], str) or not DRAFT.fullmatch(request['map'])):
        raise ValueError('Expected the saved CTF build request from the map editor.')
    markers = request['markers']
    if not isinstance(markers, dict) or set(markers) != {'T3Spawn1', 'T3Spawn2', 'T3Flag1', 'T3Flag2'}:
        raise ValueError('The draft needs both teams’ starts and flags.')
    for kind, points in markers.items():
        maximum = 24 if kind.startswith('T3Spawn') else 1
        if not isinstance(points, list) or not 1 <= len(points) <= maximum:
            raise ValueError(f'Invalid marker count for {kind}.')
        for point in points:
            if (not isinstance(point, list) or len(point) != 4 or any(
                    type(value) not in (int, float) or not -2_000_000 <= value <= 2_000_000 for value in point)):
                raise ValueError(f'Invalid marker coordinates for {kind}.')
    starts = [[team, *point] for team in (1, 2) for point in markers[f'T3Spawn{team}']]
    flags = [[f'Team{side}', *markers[f'T3Flag{team}'][0][:3]] for team, side in ((1, 'A'), (2, 'B'))]
    return request['map'], starts, flags


def objectives_lua(starts, flags):
    def lua_rows(rows):
        return '{' + ',\n            '.join(
            '{' + ', '.join(map(json.dumps, row)) + '}' for row in rows
        ) + '}'

    return (
        'return function(factory)\n'
        '    return function(report, descriptor)\n'
        '        return factory(report, {\n'
        '            world = descriptor.world,\n'
        '            scripted_objectives = true,\n'
        '            objectives = {\n'
        f'                starts = {lua_rows(starts)},\n'
        f'                flags = {lua_rows(flags)},\n'
        '            },\n'
        f'        }}, {len(starts)})\n'
        '    end\n'
        'end\n'
    )


def copy_member(source, payload, row):
    name = row['path']
    dest = payload / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / name, dest)
    if file_row(payload, name) != row:
        raise ValueError(f'File differs from the recorded cook: {name}')


def cooked_members(preview, manifest, definition):
    map_id = definition.id
    if (manifest.get('authoring_preview') != map_id or manifest.get('community_maps') != [map_id]
            or not isinstance(manifest.get('files'), list)):
        raise ValueError('The preview inventory does not match the saved draft request.')
    level_prefix = f'content/Maps/Community/{map_id}/{definition.level}'
    terrain_prefix = f'content/Community/{map_id}/Terrain'
    required = {level_prefix + ext for ext in ('.umap', '.uexp')}
    required.update(terrain_prefix + ext for ext in ('.uasset', '.uexp'))
    seen = set()
    for row in manifest['files']:
        name = row['path']
        if not name.startswith('content/'):
            continue
        parts = PurePosixPath(name).parts
        shared_group = parts[2] if len(parts) > 3 and parts[:2] == ('content', 'Community') else None
        ownership = definition if shared_group is None else MapDefinition(map_id, definition.label, shared_group, definition.level)
        if (not owned_path(name, ownership) or PurePosixPath(name).as_posix() != name
                or name.casefold() in seen or row != file_row(preview, name)):
            raise ValueError(f'Unexpected or changed draft cook member: {name}')
        seen.add(name.casefold())
        required.discard(name)
        yield row, shared_group if shared_group != map_id else None
    if required:
        raise ValueError('The draft cook is missing: ' + ', '.join(sorted(required)))


def add_minimap(payload, overlay, map_id):
    overlay = overlay.resolve(strict=True)
    settings = load_json(overlay / 'map-minimap.json', 16384)
    inventory = load_json(overlay / 'manifest-minimap.json', 16384)
    if (overlay / 'minimap.png').stat().st_size > MINIMAP_MAX_IMAGE:
        raise ValueError('The minimap PNG is larger than the host supports.')
    original = file_row(overlay, 'minimap.png')
    target = dict(original, path=f'lua/{map_id}/minimap.png')
    if (not isinstance(settings, dict) or set(settings) != {'minimap'}
            or not isinstance(settings['minimap'], dict)
            or settings['minimap'].get('image') != 'minimap.png'
            or inventory != {'files': [target]}):
        raise ValueError('The minimap export does not match this draft ID and image.')
    minimap_payload(map_id, {'settings': settings['minimap'], 'image': target})
    image = payload / target['path']
    shutil.copyfile(overlay / 'minimap.png', image)
    if file_row(payload, target['path']) != target:
        raise ValueError('The exported minimap changed during copying.')
    with image.open('rb') as stream:
        png_dimensions(stream.read(33))
    return settings, target


EQUIPMENT_RUNTIME = b'-- T3_EDITOR_GROUPED_EQUIPMENT_V1'
SHIELD_RUNTIME = b'-- T3_EDITOR_GROUPED_SHIELDS_V1'
SHIELD_POLICIES_RUNTIME = b'-- T3_EDITOR_SHIELD_POWER_POLICIES_V1'
STATION_RUNTIME = b'-- T3_EDITOR_INVENTORY_STATIONS_V2'
MAX_EQUIPMENT_REVIEW_BYTES = 256 * 1024


MAX_LIQUIDS = 32
MAX_LIQUIDS_BYTES = 64 * 1024


# Liquid types the client's liquid hook knows: converted lava and water (Tartarus's swamp is stagnant water).
LIQUID_TYPES = frozenset({'lava', 'stagnant_water', 'river_water'})


def draft_liquids(run, map_id):
    """Validated lava and water rows the editor wrote for the client's liquid hook, or None."""
    path = run / 'liquids.json'
    if not path.is_file():
        return None
    data = load_json(path, MAX_LIQUIDS_BYTES)
    rows = data.get('liquids') if isinstance(data, dict) else None
    if data.get('map') != map_id or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_LIQUIDS:
        raise ValueError('The draft liquids must belong to the exported draft.')
    for row in rows:
        box = [row.get('box_min_cm'), row.get('box_max_cm')] if isinstance(row, dict) else None
        if (not isinstance(row, dict) or set(row) != {'id', 'liquid_type', 'splash_pool_tag', 'box_min_cm', 'box_max_cm', 'surface_z_cm'}
                or row['liquid_type'] not in LIQUID_TYPES or not all(isinstance(row[key], str) and 0 < len(row[key]) <= 64
                                                            for key in ('id', 'splash_pool_tag'))
                or not all(isinstance(corner, list) and len(corner) == 3 and all(
                    type(value) in (int, float) and abs(value) <= 2_000_000 for value in corner) for corner in box)
                or not all(low < high for low, high in zip(*box))
                or type(row['surface_z_cm']) not in (int, float)
                or not box[0][2] <= row['surface_z_cm'] <= box[1][2]):
            raise ValueError('A draft liquid row is invalid.')
    return rows


def with_client_liquids(script, map_id, liquids):
    """Add the client-read map identity and liquids to objectives.lua. The host reads only the
    editor layout (or the CTF factory) and ignores these fields."""
    def lua_data(value):
        if isinstance(value, dict):
            return '{' + ','.join('[' + lua_data(key) + ']=' + lua_data(item) for key, item in value.items()) + '}'
        if isinstance(value, list):
            return '{' + ','.join(map(lua_data, value)) + '}'
        return json.dumps(value, allow_nan=False)

    level = f'Arena_{map_id}_P'
    package = f'/Game/Maps/Community/{map_id}/{level}'
    client = dict(format=1, map_id=map_id, package=package, world=f'{package}.{level}:', liquids=liquids)
    marker = 'return setmetatable({editor_layout='
    if script.count(marker) == 1:
        fields = lua_data(client)[1:-1]
        return script.replace(marker, 'return setmetatable({' + fields + ',editor_layout=')
    if script.startswith('return function(factory)'):
        return 'local adapter=(function()\n' + script + 'end)()\nreturn adapter, ' + lua_data(client) + '\n'
    raise ValueError('The objectives script has no place for the draft liquids.')


def write_objectives(ctf, target, run, host, existing, preview_manifest, map_id, adapter):
    result = write_objectives_script(ctf, target, run, host, existing, preview_manifest, map_id, adapter)
    liquids = draft_liquids(run, map_id)
    if liquids:
        target.write_text(with_client_liquids(target.read_text(encoding='utf-8'), map_id, liquids),
                          encoding='utf-8', newline='\n')
    return result


def write_objectives_script(ctf, target, run, host, existing, preview_manifest, map_id, adapter):
    source, review = run/'equipment.json', run/'equipment-review.json'
    if not (source.exists() or review.exists()):
        target.write_text(ctf, encoding='utf-8', newline='\n')
        return None, [], []
    if adapter is None or not adapter.is_file() or not source.is_file() or not review.is_file():
        raise ValueError('An equipment export needs the source, approved placements and native equipment adapter.')
    if source.stat().st_size > 16*1024*1024 or review.stat().st_size > MAX_EQUIPMENT_REVIEW_BYTES:
        raise ValueError('The editor equipment approval files are too large.')
    inputs = [file_row(run, path.name) for path in (source, review)]
    for name in ('lua/objective_layouts.lua', 'lua/network.lua'):
        row = existing.get(name)
        if row is None or row['bytes'] > 2*1024*1024 or file_row(host, name) != row:
            raise ValueError('Choose a verified Direct Host baseline with grouped draft equipment support.')
        if EQUIPMENT_RUNTIME not in (host/name).read_bytes().splitlines():
            raise ValueError('The selected Direct Host baseline cannot run grouped draft equipment.')
    approved = load_json(review, MAX_EQUIPMENT_REVIEW_BYTES)
    if not isinstance(approved, dict) or approved.get('map') != map_id or not isinstance(approved.get('placements'), list):
        raise ValueError('Equipment approval must belong to the exported draft.')
    equipment = load_json(source, 16*1024*1024)
    if any(isinstance(row, dict) and 'half_size_cm' in row for row in approved['placements']):
        module = 'lua/powered_shields.lua'
        module_row = existing.get(module)
        if (module_row is None or module_row['bytes'] > 256*1024 or file_row(host, module) != module_row
                or any(SHIELD_RUNTIME not in (host/name).read_bytes().splitlines()
                       for name in ('lua/objective_layouts.lua', 'lua/network.lua'))):
            raise ValueError('Choose a verified Direct Host baseline with grouped draft shield support.')
        if any(isinstance(row, dict) and row.get('on_power_loss', 'drop') != 'drop'
               for row in approved['placements']):
            if any(SHIELD_POLICIES_RUNTIME not in (host/name).read_bytes().splitlines()
                   for name in ('lua/objective_layouts.lua', module)):
                raise ValueError('Choose a Direct Host baseline that supports custom shield power-loss settings.')
    cooked = {row['path'] for row in preview_manifest['files']}
    if any(isinstance(row, dict) and 'station_floor_z_cm' in row for row in approved['placements']):
        module = 'lua/inventory_stations.lua'
        message = 'Choose a verified Direct Host baseline with draft inventory station support.'
        for name in ('lua/objective_layouts.lua', 'lua/network.lua', module):
            if STATION_RUNTIME not in verified_host_source(host, existing, name, message).splitlines():
                raise ValueError(message)
        launcher = 'playtest.py'
        if launcher in existing or (host/launcher).exists() or (host/launcher).is_symlink():
            launcher_error = 'Choose a Direct Host game launcher that loads portable map adapters.'
            for name, required in ((launcher, (b'merge_map_manifests', b'runtime_paths')),
                                   ('lib/portable_maps.py', (b'_portable_adapters',))):
                data = verified_host_source(host, existing, name, launcher_error)
                if not all(marker in data for marker in required):
                    raise ValueError(launcher_error)
        station = f'content/Community/{map_id}/Equipment/BP_CommunityInventoryStation'
        if not {station+'.uasset', station+'.uexp'} <= cooked:
            raise ValueError('The map’s inventory station Blueprint is missing from the draft cook.')
    for placement in approved['placements']:
        support = placement.get('support') if isinstance(placement, dict) else None
        asset = support.get('asset') if isinstance(support, dict) else None
        if not isinstance(asset, str) or not asset.startswith('/Game/Community/'):
            raise ValueError('An approved equipment support asset is invalid.')
        member = 'content/'+asset.removeprefix('/Game/').partition('.')[0]+'.uasset'
        if member not in cooked:
            raise ValueError(f'Approved equipment support is missing from the draft cook: {member}')
    lighting = approved.get('powered_interiors', [])
    if not isinstance(lighting, list):
        raise ValueError('Powered interior approval must be a list.')
    for interior in lighting:
        pairs = interior.get('materials') if isinstance(interior, dict) else None
        if not isinstance(pairs, list) or not all(isinstance(row, dict) for row in pairs):
            raise ValueError('Powered interior material approval is invalid.')
        for asset in [interior.get('asset'), *(row.get('asset') for row in pairs)]:
            if not isinstance(asset, str) or not asset.startswith('/Game/Community/'):
                raise ValueError('A powered interior needs cooked Community mesh and material assets.')
            member = 'content/'+asset.removeprefix('/Game/').partition('.')[0]+'.uasset'
            if member not in cooked:
                raise ValueError(f'Powered interior asset is missing from the draft cook: {member}')
    with TemporaryDirectory(dir=target.parent) as work:
        base = Path(work)/'ctf.lua'
        base.write_text(ctf, encoding='utf-8', newline='\n')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(adapter), str(base), str(source),
                                 str(review), str(target)], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', check=False)
        if result.returncode or not target.is_file():
            raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip()
                             else 'The native equipment adapter did not produce an objectives script.')
    if inputs != [file_row(run, path.name) for path in (source, review)]:
        raise ValueError('The approved equipment changed during export.')
    objects = {entry['source_id']: entry for entry in equipment['equipment']}
    placements = [(objects[row['source_id']], row) for row in approved['placements']]
    used = {approved.get('shield_power_group', entry['power_group']) for entry, approved in placements}
    groups = [group for group in equipment['power_groups'] if group['source_id'] in used]
    return inputs[1]['sha256'], placements, groups


def playtest_checklist(metadata, starts, flags, equipment, groups, *, flag_features=(), outside_audio=False):
    def position(point):
        return ', '.join(f'{value:g}' for value in point)

    lines = [f"{metadata['label']} ({metadata['id']}) — in-game playtest", '',
             'Use a private Direct Host and a client on each team. Record pass or fail for each check.',
             'Coordinates are Unreal X, Y, Z in centimeters. Spawn yaw is in degrees.', '',
             '[ ] Join from both clients, finish warmup and confirm the match clock starts.',
             '[ ] Respawn at each authored start. Check floor clearance, facing and blocked exits.']
    for team, x, y, z, yaw in starts:
        lines.append(f'    Team {team} start at {position((x, y, z))}, yaw {yaw:g}')
    lines.extend(['', '[ ] Grab, drop and return each flag. Verify both clients see each state.',
                  '[ ] Capture once per team with its home flag present. Verify the score and flag reset.'])
    for team, x, y, z in flags:
        lines.append(f'    {team} flag stand at {position((x, y, z))}')
    for limit in flag_features:
        if limit != 'return timer':
            action = 'the return timer starts' if 'return timer' in flag_features else 'the flag drops at the last legal position'
            lines.append(f'[ ] Cross the flag-only {limit} with the enemy flag. Check that {action}.')
    if 'return timer' in flag_features:
        lines.append('[ ] Carry the flag outside. Reenter to clear the warning, then stay outside until the flag returns home.')
    if equipment:
        lines.extend(['', '[ ] Check each approved equipment placement for floor contact and obstruction.'])
        for entry, approved in equipment:
            role = entry['role'].replace('_', ' ')
            x, y, z = approved['anchor_position_cm']
            point = position((x, y, approved.get('station_floor_z_cm', z)))
            size = ''
            if 'half_size_cm' in approved:
                width, height = approved['half_size_cm']
                size = f' · {2*width:g} × {2*height:g} cm opening'
            group = approved.get('shield_power_group', entry['power_group'])
            circuit = 'independent power' if group == 0 else f'circuit #{group}'
            if 'half_size_cm' in approved and group != 0 and 'on_power_loss' in approved:
                size += ' · power loss: ' + {
                    'drop': 'drops', 'weaken': 'keeps the lower half blocking', 'stay': 'stays up',
                }[approved['on_power_loss']]
            lines.append(f"    Team {entry['team']} {role} #{entry['source_id']} at {point}"
                         f" ({circuit}){size}")
        if any(entry['role']=='force_shield' for entry, _ in equipment):
            lines.append('[ ] Confirm powered shields pass friendly players and block enemies from both sides.')
        if any(entry['role']=='inventory_station' for entry, _ in equipment):
            lines.append('[ ] Use an inventory station on each team. Check that an enemy cannot use it and that it stops and resumes working as its circuit loses and regains power.')
    if groups:
        lines.extend(['', '[ ] Disable every generator in each powered circuit. Check its equipment and the listed shield settings.',
                      '[ ] Restore one generator per circuit. Verify equipment comes back online and shields are at full height.'])
        for group in groups:
            generators = ', '.join(f'#{ident}' for ident in group['generators']) or 'always on'
            lines.append(f"    Team {group['team']} circuit #{group['source_id']}: {generators}")
    if 'minimap' in metadata:
        lines.extend(['', '[ ] Check minimap alignment against both flag stands and a distinctive terrain edge.'])
    if outside_audio:
        lines.extend(['', '[ ] On an acoustic-capable Starter, check the outside-only sounds outdoors, deep inside, '
                          'and near an exterior portal.'])
    lines.extend(['', '[ ] Check skiing and jetting along a base-to-base route and at the edge of the playable area.',
                  '[ ] Finish the match and verify the result on both clients.', ''])
    return '\n'.join(lines)


def installed_release(host, central, merged, definition, *, replace_existing):
    map_id = definition.id
    definitions = host_definitions(merged)
    for name, row in definitions.items():
        if name == map_id:
            continue
        if (name.casefold() == map_id.casefold() or row.label.casefold() == definition.label.casefold()
                or row.asset_group.casefold() == map_id.casefold() or (row.shared_group or '').casefold() == map_id.casefold()):
            raise ValueError('The draft ID, asset group or display label conflicts with another map.')
    existing = definitions.get(map_id)
    if existing is None:
        if replace_existing:
            raise ValueError('The selected baseline does not have this draft ID to update.')
        return {}, {}
    if not replace_existing:
        raise ValueError('The draft ID is already in the catalog. Select Update this map ID to prepare a replacement.')
    if (existing.native or existing.asset_group != map_id or existing.level != definition.level
            or merged['map_runtime'].get(map_id)
            or any(scope.casefold().startswith(f'content/community/{map_id.casefold()}/') and set(owners) != {map_id}
                   for scope, owners in merged.get('shared_content', {}).items())
            or any(owned_path(row['path'], definition) for row in central['files'])):
        raise ValueError('This update requires an independently owned portable draft.')
    folder = host / 'lua' / map_id
    metadata = load_json(folder/'map.json', 16384)
    manifest = load_json(folder/'manifest.json', 2 * 1024 * 1024)
    if metadata.get('adapter') != 'scripted_ctf' or manifest.get('remove'):
        raise ValueError('This update requires a standalone scripted CTF draft inventory.')
    members = {row['path']: row for row in manifest['files']}
    for name, row in members.items():
        if file_row(host, name) != row:
            raise ValueError(f'The installed map failed verification: {name}')
    return metadata, members


def export_draft(run, host, destination, *, label, minimap=None, equipment_adapter=None, replace_existing=False):
    run = run.resolve(strict=True)
    host = host.resolve(strict=True)
    preview = run / 'preview'
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination.is_relative_to(host) or destination.is_relative_to(run):
        raise ValueError('Choose a separate destination outside the host and editor run.')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if not valid_label(label):
        raise ValueError('The display label must contain 1–64 printable characters.')

    map_id, starts, flags = draft_layout(load_json(run / 'build-request.json', 32768))
    definition = MapDefinition(map_id, label, map_id, f'Arena_{map_id}_P')
    preview_manifest = load_json(preview / 'package-manifest.json', MAX_PACKAGE_MANIFEST_BYTES)
    base_path = host / 'package-manifest.json'
    base_row = file_row(host, base_path.name)
    central = load_json(base_path, MAX_PACKAGE_MANIFEST_BYTES)
    if not isinstance(central, dict) or not isinstance(central.get('files'), list):
        raise ValueError('Invalid Direct Host package manifest.')
    merged = merge_map_manifests(host, central)
    previous, old_members = installed_release(host, central, merged, definition, replace_existing=replace_existing)
    existing = {row['path'].casefold(): row for row in merged['files']}
    verify_draft_world_settings(host, existing)
    exact_shared = supports_shared_file_runtime(host, existing)
    old_scopes = central.get('shared_content', {})
    scopes = dict(old_scopes)
    if minimap is not None or 'minimap' in previous:
        verify_minimap_runtime(host, existing)
    prior_keys = {name.casefold() for name in old_members}
    if any(name.startswith(f'content/community/{map_id.casefold()}/') and name not in prior_keys for name in existing):
        raise ValueError('The draft asset group contains files outside this map inventory.')
    if not previous and ((host/'lua'/map_id).exists() or (host/'lua'/map_id).is_symlink()):
        raise ValueError('The target already contains this map’s runtime folder.')

    with TemporaryDirectory(dir=destination.parent) as work:
        result = Path(work) / 'export'
        payload = result / 'payload'
        sidecar = payload / 'lua' / map_id
        sidecar.mkdir(parents=True)
        members, shared_additions, cooked_paths, shared_paths = [], [], set(), set()
        dependencies = set(previous.get('shared_content', []))
        for row, group in cooked_members(preview, preview_manifest, definition):
            name = row['path']
            cooked_paths.add(name)
            old = existing.get(name.casefold())
            if old is not None and name not in old_members:
                if group is None or old != row or old != file_row(host, name):
                    raise ValueError(f'The target contains different bytes or ownership for {name}.')
            else:
                if old is None and ((host / name).exists() or (host / name).is_symlink()):
                    raise ValueError(f'The target contains an uninventoried file: {name}')
                copy_member(preview, payload, row)
                (members if group is None else shared_additions).append(row)
            if group is not None:
                if exact_shared:
                    shared_paths.add(name)
                else:
                    dependencies.add(group)

        retired = set()
        if exact_shared:
            _, installed_files = map_groups(merged)
            for name in shared_paths:
                owners = {owner for owner, paths in installed_files.items() if name in paths}
                if map_id not in owners:
                    scopes[name] = sorted(owners | {map_id})
            for name, owners in old_scopes.items():
                if name.endswith('/') or name in cooked_paths or map_id not in owners:
                    continue
                remaining = sorted(owner for owner, paths in installed_files.items() if owner != map_id and name in paths)
                if remaining:
                    scopes[name] = remaining
                else:
                    del scopes[name]
                    retired.add(name)

        level = payload/f'content/Maps/Community/{map_id}/{definition.level}.umap'
        flag_features = flag_boundary_features(level)
        if flag_features:
            verify_flag_boundary_runtime(host, existing, flag_features)
        metadata = {'format': 1, 'id': map_id, 'label': label, 'asset_group': map_id,
                    'level': definition.level, 'native': False, 'adapter': 'scripted_ctf'}
        if dependencies:
            metadata['shared_content'] = sorted(dependencies)
        if minimap is not None:
            settings, image = add_minimap(payload, minimap, map_id)
            metadata.update(settings)
            members.append(image)
        elif 'minimap' in previous:
            metadata['minimap'] = previous['minimap']
        previous_image = previous.get('minimap', {}).get('image')
        acoustic_name = f'lua/{map_id}/{OUTSIDE_AUDIO_FILE}'
        acoustic_source = run / OUTSIDE_AUDIO_FILE
        has_acoustic = acoustic_source.exists() or acoustic_source.is_symlink()
        if has_acoustic:
            if acoustic_source.stat().st_size > OUTSIDE_AUDIO_MAX:
                raise ValueError('The outside-only audio layout exceeds the export size limit.')
            before = file_row(run, acoustic_source.name)
            outside_audio_document(load_json(acoustic_source, OUTSIDE_AUDIO_MAX))
            member = dict(before, path=acoustic_name)
            if acoustic_name not in old_members and ((host / acoustic_name).exists() or (host / acoustic_name).is_symlink()):
                raise ValueError('The host contains an uninventoried outside-only audio layout.')
            shutil.copyfile(acoustic_source, payload / acoustic_name)
            if member != file_row(payload, acoustic_name) or before != file_row(run, acoustic_source.name):
                raise ValueError('The outside-only audio layout changed during export.')
            members.append(member)
        for name, row in old_members.items():
            if (name.startswith(f'lua/{map_id}/') and name not in (f'lua/{map_id}/objectives.lua', acoustic_name)
                    and (minimap is None or name != f'lua/{map_id}/{previous_image}')):
                if (payload/name).exists():
                    raise ValueError(f'The exported minimap conflicts with an existing map-local file: {name}')
                copy_member(host, payload, row)
                members.append(row)
        equipment_review, equipment, groups = write_objectives(
            objectives_lua(starts, flags), sidecar/'objectives.lua', run,
            host, existing, preview_manifest, map_id, equipment_adapter)
        members.append(file_row(payload, f'lua/{map_id}/objectives.lua'))
        write_json(sidecar / 'map.json', metadata)
        write_json(sidecar / 'manifest.json', {'format': 1, 'files': members})

        before_catalog = map_catalog(merged)
        proposed = dict(central, files=[row for row in central['files'] if row['path'] not in retired] + shared_additions)
        if scopes != old_scopes:
            proposed['shared_content'] = scopes
        after_catalog = map_catalog(projected_manifest(host, payload, Path(work)/'catalog', proposed, map_id))
        if {key: value for key, value in after_catalog.items() if key != map_id} != {
                key: value for key, value in before_catalog.items() if key != map_id}:
            if not exact_shared:
                raise ValueError('This release needs a Direct Host bundle that supports per-file shared map assets.')
            raise ValueError('This release would change another map’s catalog entry.')
        central_changed = proposed != central
        if central_changed:
            write_json(payload / base_path.name, proposed)
        descriptors = descriptor_paths(map_id)
        prior_names = set(old_members) | retired | (descriptors if previous else set())
        next_names = descriptors | {row['path'] for row in (*members, *shared_additions)}
        if central_changed:
            prior_names.add(base_path.name)
            next_names.add(base_path.name)
        plan_hash = create_plan(result, host, map_id, prior_names, next_names, before_catalog, after_catalog)
        if file_row(host, base_path.name) != base_row or merge_map_manifests(host, central) != merged:
            raise ValueError('The target host inventory changed during export. Export again.')
        receipt = {
            'format': 1, 'map': map_id, 'base_inventory_sha256': base_row['sha256'],
            'release_plan_sha256': plan_hash,
            'new_shared_files': [row['path'] for row in shared_additions],
        }
        if equipment_review is not None:
            receipt['equipment_review_sha256'] = equipment_review
        write_json(result / 'export-receipt.json', receipt)
        (result / 'playtest-checklist.txt').write_text(
            playtest_checklist(metadata, starts, flags, equipment, groups, flag_features=flag_features,
                               outside_audio=has_acoustic),
            encoding='utf-8', newline='\n')
        publish_directory(result, destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path, help='Editor run-N folder containing build-request.json and preview/')
    parser.add_argument('host', type=Path, help='Read-only Direct Host match bundle used as the staging baseline')
    parser.add_argument('destination', type=Path, help='New export folder outside the host bundle and editor run')
    parser.add_argument('--label', required=True, help='Catalog label, for example My Map (T2 2026)')
    parser.add_argument('--minimap', type=Path, help='Optional minimap export folder made with the same draft ID')
    parser.add_argument('--equipment-adapter', type=Path, help='Adapter for approved equipment in the editor run')
    parser.add_argument('--replace-existing', action='store_true', help='Prepare a new revision of this existing portable map ID')
    args = parser.parse_args()
    try:
        print(export_draft(args.run, args.host, args.destination, label=args.label, minimap=args.minimap,
                           equipment_adapter=args.equipment_adapter, replace_existing=args.replace_existing))
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
