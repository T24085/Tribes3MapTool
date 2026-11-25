"""Build a separate verified Direct Host bundle from an editor release or its rollback."""

import argparse
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from community_maps import MapDefinition
from map_sync_contract import map_catalog
from portable_maps import COOKED_SUFFIXES, load_json, merge_map_manifests, owned_path
from verified_file_cache import MAX_PACKAGE_MANIFEST_BYTES, verify_package


PLAN = 'map-release.json'
CENTRAL = 'package-manifest.json'
SHA = re.compile(r'[0-9a-f]{64}')
DRAFT = re.compile(r'DraftT[12VA][0-9]{3}')
FLAG_RUNTIME = {
    'match_roster.py': b'# ',
    'runtime-fixes/mission_flag_limit.lua': b'-- ',
}


def regular_row(root, name):
    path = root / name
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError(f'Missing or linked file: {name}')
    before = path.stat()
    with path.open('rb') as stream:
        digest = file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f'File changed during verification: {name}')
    return {'bytes': after.st_size, 'sha256': digest}


def flag_boundary_features(level):
    # UE 5.3 stores TargetPoint tag names as length-prefixed FStrings in the cooked .umap.
    markers = {}
    for feature, suffix in (('floor', 'Floor'), ('ceiling', 'Ceiling'), ('return timer', 'ReturnTimer')):
        name = f'T3MissionFlagNeeds{suffix}\0'
        markers[feature] = (
            len(name).to_bytes(4, 'little') + name.encode('ascii'),
            (-len(name)).to_bytes(4, 'little', signed=True) + name.encode('utf-16-le'),
        )
    overlap = max(len(marker) for encodings in markers.values() for marker in encodings) - 1
    pending = set(markers)
    tail = b''
    with level.open('rb') as stream:
        while pending and (chunk := stream.read(65536)):
            data = tail + chunk
            pending.difference_update(kind for kind, encodings in markers.items() if any(value in data for value in encodings))
            tail = data[-overlap:]
    return tuple(kind for kind in markers if kind not in pending)


def verified_host_source(host, files, name, message):
    maximum = 2*1024*1024
    row = files.get(name)
    path = host/name
    if row is None or row['bytes'] > maximum or path.resolve() != path or not path.is_file():
        raise ValueError(message)
    with path.open('rb') as stream:
        data = stream.read(maximum+1)
    if len(data) != row['bytes'] or sha256(data).hexdigest() != row['sha256']:
        raise ValueError(message)
    return data


def verify_draft_world_settings(host, files):
    network = 'lua/network.lua'
    if network not in files and not ((host/network).exists() or (host/network).is_symlink()):
        return
    message = 'Choose a verified Direct Host baseline that supports authored draft World Settings.'
    source = verified_host_source(host, files, network, message)
    # Older hosts do not impose the retail-only ValWorldSettings requirement.
    if (b'releaseNativeWorldSettings(gm,assert(mapDefinitions[baseKitSelectedMap]))' not in source
            or b'native_settings_actual_same_world_owner_required' not in source):
        return
    if b'native_settings_authored_draft_world_owner_required' not in source:
        raise ValueError(message)
    catalog = verified_host_source(host, files, 'lib/host_catalog_runtime.py', message)
    if b'.editor_draft=true' not in catalog:
        raise ValueError(message)


def verify_minimap_runtime(host, files):
    launcher = 'playtest.py'
    if launcher not in files and not ((host/launcher).exists() or (host/launcher).is_symlink()):
        # Inventory-only bundles are also used for editor staging rehearsals.
        return
    message = 'Choose a verified Direct Host bundle with a minimap-compatible game launcher.'
    components = (
        (launcher, b'merge_map_manifests'),
        ('lib/portable_maps.py', b'_portable_minimaps'),
        ('lib/map_sync_contract.py', b'def minimap_settings('),
    )
    for name, required in components:
        if required not in verified_host_source(host, files, name, message):
            raise ValueError(message)


def supports_shared_file_runtime(host, files):
    launcher = 'playtest.py'
    if launcher not in files and not (host/launcher).exists() and not (host/launcher).is_symlink():
        return True  # Inventory-only staging rehearsal.
    message = 'Choose a verified Direct Host bundle that supports per-file shared map assets.'
    components = {
        'lib/community_maps.py': b'def shared_content_owners(',
        'lib/compare_map_packages.py': b'shared_content_owners',
        'lib/portable_maps.py': b'declared_shared_content(merged, definitions)',
    }
    try:
        return all(marker in verified_host_source(host, files, path, message) for path, marker in components.items())
    except ValueError:
        return False


def verify_flag_boundary_runtime(host, files, features):
    capabilities = {}
    if 'floor' in features or 'ceiling' in features:
        capabilities['height limits'] = b'T3_EDITOR_FLAG_HEIGHT_LIMITS_V1'
    if 'return timer' in features:
        capabilities['return timer'] = b'T3_EDITOR_FLAG_RETURN_TIMER_V1'
    needed = ' and '.join(capabilities)
    message = f'Choose a verified Direct Host baseline that supports flag-only {needed}.'
    for name, prefix in FLAG_RUNTIME.items():
        data = verified_host_source(host, files, name, message)
        markers = {prefix + marker for marker in capabilities.values()}
        if not markers <= {line.strip() for line in data.splitlines()}:
            raise ValueError(message)


def sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def safe_member(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 240:
        return False
    parts = PurePosixPath(name)
    return (parts.as_posix() == name and not parts.is_absolute() and '..' not in parts.parts
            and '\\' not in name and ':' not in name)


def descriptor_paths(map_id):
    return {f'lua/{map_id}/map.json', f'lua/{map_id}/manifest.json'}


def projected_manifest(host, payload, projection, central, map_id):
    # Retain the installed descriptors when checking a proposed release.
    for root in (host, payload):
        for descriptor in (root/'lua').glob('*/map.json'):
            if root == host and descriptor.parent.name == map_id:
                continue
            target = projection / descriptor.relative_to(root)
            target.parent.mkdir(parents=True)
            shutil.copyfile(descriptor, target)
            inventory = descriptor.with_name('manifest.json')
            if inventory.is_file():
                shutil.copyfile(inventory, target.with_name(inventory.name))
    return merge_map_manifests(projection, central)


def create_plan(result, host, map_id, prior_names, next_names, catalog_before, catalog_after):
    payload = result / 'payload'
    guards, changes = {}, {}
    descriptors = descriptor_paths(map_id)
    for name in sorted(prior_names | next_names):
        before = regular_row(host, name) if name in prior_names else None
        after = regular_row(payload, name) if name in next_names else None
        if name in descriptors:
            guards[name] = {'before': before and before['sha256'], 'after': after and after['sha256']}
        if before == after:
            continue
        changes[name] = {'before': before, 'after': after}
        if before is not None:
            backup = result / 'previous' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(host / name, backup)
            if regular_row(result / 'previous', name) != before:
                raise ValueError(f'The previous release changed during backup: {name}')
    if not changes:
        raise ValueError('The staged draft already matches the installed release.')
    if len(changes) > 20_000:
        raise ValueError('The release contains too many file changes.')
    plan = {'format': 1, 'map': map_id, 'before_catalog': catalog_before, 'after_catalog': catalog_after, 'guards': guards,
            'central_before': regular_row(host, CENTRAL)['sha256'], 'changes': changes}
    (result / PLAN).write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    return regular_row(result, PLAN)['sha256']


def release_plan(package):
    receipt = load_json(package / 'export-receipt.json', 256 * 1024)
    if not isinstance(receipt, dict) or receipt.get('release_plan_sha256') != regular_row(package, PLAN)['sha256']:
        raise ValueError('The release plan differs from its export receipt.')
    plan = load_json(package / PLAN, 16 * 1024 * 1024)
    if (not isinstance(plan, dict) or set(plan) != {'format', 'map', 'central_before', 'before_catalog',
                                                   'after_catalog', 'guards', 'changes'} or type(plan['format']) is not int
            or plan['format'] != 1 or not isinstance(plan['map'], str) or not DRAFT.fullmatch(plan['map'])
            or receipt.get('map') != plan['map'] or receipt.get('base_inventory_sha256') != plan['central_before']
            or not sha(plan['central_before'])):
        raise ValueError('Invalid release plan.')
    map_id = plan['map']
    old, new = plan['before_catalog'], plan['after_catalog']
    if (not isinstance(old, dict) or not isinstance(new, dict) or len(new) > 128
            or new.keys() != old.keys() | {map_id}
            or not all(sha(value) for value in (*old.values(), *new.values()))
            or any(old[name] != new[name] for name in old.keys() - {map_id})):
        raise ValueError('The release changes another map or carries an invalid catalog.')
    changes = plan['changes']
    if not isinstance(changes, dict) or not 1 <= len(changes) <= 20_000:
        raise ValueError('The release has no file changes or too many changes.')
    definition = MapDefinition(map_id, map_id, map_id, f'Arena_{map_id}_P')
    descriptors = descriptor_paths(map_id)
    retired = set()
    seen = set()
    for name, states in changes.items():
        if (not safe_member(name) or name.casefold() in seen or not isinstance(states, dict)
                or set(states) != {'before', 'after'} or states['before'] == states['after']):
            raise ValueError('Invalid or duplicate release file change.')
        seen.add(name.casefold())
        local_png = bool(re.fullmatch(rf'lua/{map_id}/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.png', name))
        if not (name == CENTRAL or name in descriptors or local_png or owned_path(name, definition)):
            if (not name.startswith('content/Community/') or not name.endswith(COOKED_SUFFIXES)
                    or (states['before'] is not None and states['after'] is not None)):
                raise ValueError(f'The release file is outside {map_id}: {name}')
            if states['after'] is None:
                retired.add(name)
        for state, directory in (('before', 'previous'), ('after', 'payload')):
            row = states[state]
            if row is None:
                continue
            if (not isinstance(row, dict) or set(row) != {'bytes', 'sha256'}
                    or type(row['bytes']) is not int or row['bytes'] < 0 or not sha(row['sha256'])
                    or regular_row(package / directory, name) != row):
                raise ValueError(f'The release copy failed verification: {name}')
    if CENTRAL in changes and (changes[CENTRAL]['after'] is None or not changes[CENTRAL]['before']
                               or changes[CENTRAL]['before']['sha256'] != plan['central_before']):
        raise ValueError('The release must preserve a verified central manifest in both states.')
    if retired:
        if CENTRAL not in changes:
            raise ValueError('Retiring shared files requires a central inventory change.')
        before = load_json(package/'previous'/CENTRAL, MAX_PACKAGE_MANIFEST_BYTES)
        after = load_json(package/'payload'/CENTRAL, MAX_PACKAGE_MANIFEST_BYTES)
        if (not isinstance(before, dict) or not isinstance(before.get('shared_content'), dict)
                or not isinstance(after, dict) or not isinstance(after.get('shared_content'), dict)
                or not isinstance(after.get('files'), list)
                or any(not isinstance(row, dict) or not isinstance(row.get('path'), str) for row in after['files'])):
            raise ValueError('Invalid central inventory for retired shared files.')
        remaining = {row['path'] for row in after['files']} | after['shared_content'].keys()
        if retired & remaining or any(before['shared_content'].get(name) != [map_id] for name in retired):
            raise ValueError('The release can only retire shared files owned solely by this map.')
    guards = plan['guards']
    if not isinstance(guards, dict) or guards.keys() != descriptors:
        raise ValueError('The release is missing its verified map descriptors.')
    for name, values in guards.items():
        if (not isinstance(values, dict) or values.keys() != {'before', 'after'} or not sha(values['after'])
                or (not sha(values['before']) if map_id in old else values['before'] is not None)):
            raise ValueError('Invalid release descriptor checksum.')
        if name in changes:
            for state, row in changes[name].items():
                if values[state] != (row and row['sha256']):
                    raise ValueError('The release descriptor changed outside its recorded checksums.')
        elif values['before'] != values['after']:
            raise ValueError('The release descriptor change is missing its backup.')
    return plan


def publish_directory(candidate, destination):
    for attempt in range(20):
        if destination.exists() or destination.is_symlink() or destination.is_junction():
            raise FileExistsError(destination)
        try:
            candidate.rename(destination)
            return
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 19:
                raise
            time.sleep(.05)


def bundle_members(root, inventory):
    members = {row['path'] for row in inventory['files']} | {CENTRAL}
    for path in (root/'lua').glob('*/map.json'):
        members.add(path.relative_to(root).as_posix())
        if path.with_name('manifest.json').is_file():
            members.add(path.with_name('manifest.json').relative_to(root).as_posix())
    return members


def verify_descriptors(root, plan, state):
    for name, hashes in plan['guards'].items():
        expected = hashes[state]
        path = root / name
        if expected is None:
            if path.exists() or path.is_symlink() or path.is_junction():
                raise ValueError(f'A map descriptor already exists: {name}')
        elif regular_row(root, name)['sha256'] != expected:
            raise ValueError(f'The map descriptor no longer matches the release: {name}')


def stage_release(package, source, destination, *, rollback=False, link_content=False):
    package, source = (Path(path).resolve(strict=True) for path in (package, source))
    destination = Path(destination).parent.resolve(strict=True) / Path(destination).name
    if any(destination.is_relative_to(root) for root in (source, package)):
        raise ValueError('Use a separate output folder outside the input bundles.')
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise FileExistsError(destination)
    plan = release_plan(package)
    old, new, replacement = ('after', 'before', 'previous') if rollback else ('before', 'after', 'payload')
    map_id = plan['map']
    changes = plan['changes']
    expected_central = (changes[CENTRAL][old]['sha256'] if CENTRAL in changes else plan['central_before'])
    initial = verify_package(source, merge_manifest=merge_map_manifests)
    catalog = map_catalog(initial)
    if (regular_row(source, CENTRAL)['sha256'] != expected_central
            or catalog.get(map_id) != plan[f'{old}_catalog'].get(map_id)):
        raise ValueError('This host bundle does not match the expected release baseline.')
    verify_descriptors(source, plan, old)
    members = bundle_members(source, initial)
    for name, states in changes.items():
        path = source / name
        expected = states[old]
        if expected is None:
            if path.exists() or path.is_symlink() or path.is_junction():
                raise ValueError(f'An untracked file already occupies the release path: {name}')
        elif name not in members or regular_row(source, name) != expected:
            raise ValueError(f'The current map file changed after the release was prepared: {name}')
    if map_id in plan[f'{new}_catalog']:
        inventory = {row['path']: row for row in initial['files']}
        verify_draft_world_settings(source, inventory)
        descriptor = f'lua/{map_id}/map.json'
        target_descriptor = package/replacement/descriptor if descriptor in changes else source/descriptor
        if 'minimap' in load_json(target_descriptor, 16384):
            verify_minimap_runtime(source, inventory)
        level = f'content/Maps/Community/{map_id}/Arena_{map_id}_P.umap'
        selected = package/replacement/level if level in changes else source/level
        if features := flag_boundary_features(selected):
            verify_flag_boundary_runtime(source, inventory, features)

    with TemporaryDirectory(dir=destination.parent) as temp:
        candidate = Path(temp) / 'host'
        (candidate/'content').mkdir(parents=True)
        for name in sorted(members - changes.keys()):
            original, copy = source / name, candidate / name
            if original.resolve(strict=True) != original or not original.is_file():
                raise ValueError(f'The baseline contains a linked or unsupported file: {name}')
            copy.parent.mkdir(parents=True, exist_ok=True)
            if link_content and name.startswith('content/') and copy.parent.stat().st_dev == original.stat().st_dev:
                os.link(original, copy)
            else:
                shutil.copy2(original, copy)
        for name, states in changes.items():
            if states[new] is None:
                continue
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / replacement / name, target)
            if regular_row(candidate, name) != states[new]:
                raise ValueError(f'The release file changed while staging: {name}')
        installed = verify_package(candidate, merge_manifest=merge_map_manifests)
        if (any(not scope.endswith('/') for scope in installed.get('shared_content', {}))
                and not supports_shared_file_runtime(source, {row['path']: row for row in initial['files']})):
            raise ValueError('Use a Direct Host bundle that supports per-file shared map assets.')
        verify_descriptors(candidate, plan, new)
        expected_members = (members - changes.keys()) | {name for name, states in changes.items() if states[new] is not None}
        if bundle_members(candidate, installed) != expected_members:
            raise ValueError('Staging produced a different package inventory.')
        catalog.pop(map_id, None)
        if map_id in plan[f'{new}_catalog']:
            catalog[map_id] = plan[f'{new}_catalog'][map_id]
        if map_catalog(installed) != catalog:
            raise ValueError('Staging produced a different host map catalog.')
        if (regular_row(source, CENTRAL)['sha256'] != expected_central
                or merge_map_manifests(source, load_json(source/CENTRAL, MAX_PACKAGE_MANIFEST_BYTES)) != initial):
            raise ValueError('The host inventory changed while staging. Use a stable baseline.')
        publish_directory(candidate, destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stage', 'rollback'))
    parser.add_argument('release', type=Path, help='Editor export folder with map-release.json')
    parser.add_argument('host', type=Path, help='Existing Direct Host bundle used as read-only input')
    parser.add_argument('output', type=Path, help='Unused output folder for the verified staged bundle')
    parser.add_argument('--link-content', action='store_true', help='Hardlink unchanged cooked content on the same drive')
    args = parser.parse_args()
    try:
        print(stage_release(args.release, args.host, args.output, rollback=args.action == 'rollback',
                            link_content=args.link_content))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'{error}\n')
