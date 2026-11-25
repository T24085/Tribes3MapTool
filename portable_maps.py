"""Load operator-installed per-map manifests into the host's legacy inventory view."""
from dataclasses import replace
import json
from pathlib import Path
import re

from community_maps import BY_ID, LEGACY_MAPS, MAX_MAPS, declared_definitions, declared_shared_content, valid_id, valid_label


from map_sync_contract import OUTSIDE_AUDIO_FILE, minimap_settings
from verified_file_cache import MAX_MERGED_HOST_FILES, MAX_PACKAGE_MANIFEST_BYTES


MAX_JSON = 2 * 1024 * 1024
MEMBER = re.compile(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*')
COOKED_SUFFIXES = ('.umap', '.uexp', '.uasset', '.ubulk', '.uptnl')
ADAPTERS = {'native_ctf', 'scripted_ctf', 'edition_2026', 'bundled'}


def load_json(path, limit):
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError(f'Expected a regular map inventory file: {path.name}')
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'Map inventory file is too large: {path.name}')

    def object_pairs(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError(f'Duplicate JSON key in {path.name}')
        return result

    return json.loads(data, object_pairs_hook=object_pairs)


def owned_path(path, definition):
    """A sidecar owns one level, its asset family, and its private Lua directory."""
    parts = path.split('/')
    if len(path) > 240 or not all(MEMBER.fullmatch(part) for part in parts):
        return False
    if path.startswith(f'content/Maps/Community/{definition.asset_group}/{definition.level}.'):
        return len(parts) == 5 and path.endswith(COOKED_SUFFIXES)
    if path.startswith(f'content/Community/{definition.asset_group}/'):
        return path.endswith(COOKED_SUFFIXES)
    return (path.startswith(f'lua/{definition.id}/') and path.endswith(('.lua', '.json'))
            and path not in (f'lua/{definition.id}/map.json', f'lua/{definition.id}/manifest.json'))


def host_definitions(manifest):
    labels = manifest.get('_portable_labels', {})
    return {name: replace(row, label=labels.get(name, f'{row.label} (T3)' if row.native else row.label))
            for name, row in declared_definitions(manifest).items()}


def merge_map_manifests(root, manifest):
    lua = root / 'lua'
    if not lua.exists():
        return manifest
    if not lua.is_dir() or lua.is_symlink() or lua.is_junction():
        raise ValueError('Invalid host Lua directory.')
    folders = sorted((path.parent for path in lua.glob('*/map.json')), key=lambda item: item.name.casefold())
    orphaned = {path.parent for path in lua.glob('*/manifest.json')} - set(folders)
    if orphaned or len(folders) > MAX_MAPS:
        raise ValueError('A map manifest requires a sibling map.json and a valid catalog size.')
    if not folders:
        return manifest

    existing = declared_definitions(manifest)
    extras = dict(manifest.get('map_definitions', {}))
    maps = list(manifest.get('community_maps', LEGACY_MAPS))
    runtime = dict(manifest.get('map_runtime', {name: [] for name in maps}))
    labels, adapters, sidecars, minimaps = {}, {}, {}, {}
    scopes = manifest.get('shared_content', {})
    if not isinstance(scopes, dict):
        raise ValueError('Shared content ownership must be an object.')
    shared = dict(scopes)
    ids = {name.casefold(): name for name in maps}
    builtins = {name.casefold() for name in BY_ID}
    for folder in folders:
        name = folder.name
        if (not valid_id(name) or ids.get(name.casefold(), name) != name
                or folder.is_symlink() or folder.is_junction() or folder.resolve().parent != lua):
            raise ValueError(f'Invalid or duplicate portable map folder: {name}')
        data = load_json(folder / 'map.json', 16384)
        required = {'format', 'id', 'label', 'asset_group', 'level', 'native'}
        if (not isinstance(data, dict) or not required <= data.keys() <= required | {'adapter', 'shared_content', 'minimap'}
                or type(data['format']) is not int or data['format'] != 1 or data['id'] != name
                or data['native'] is not False or not valid_label(data['label'])):
            raise ValueError(f'Invalid portable map metadata: {name}')
        if 'minimap' in data:
            minimaps[name] = minimap_settings(data['minimap'])
        dependencies = data.get('shared_content', [])
        if not isinstance(dependencies, list) or len(dependencies) > MAX_MAPS:
            raise ValueError(f'Invalid portable map dependencies: {name}')
        for group in dependencies:
            if not isinstance(group, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', group):
                raise ValueError(f'Invalid portable map asset dependency: {name}')
            prefix = f'content/Community/{group}/'
            owners = shared.get(prefix, [])
            if not isinstance(owners, list):
                raise ValueError(f'Invalid portable map asset owners: {name}')
            if name not in owners:
                shared[prefix] = [*owners, name]
        adapter = data.get('adapter')
        if adapter is not None and (not isinstance(adapter, str) or adapter not in ADAPTERS):
            raise ValueError(f'Unknown map adapter: {name}')
        if adapter == 'native_ctf' and name in BY_ID:
            raise ValueError(f'{name} requires its bundled map definition or a scripted adapter.')
        if adapter == 'bundled' and name not in BY_ID:
            raise ValueError(f'No bundled map adapter for {name}')
        old = existing.get(name)
        if old is not None:
            if old.native or (old.asset_group, old.level) != (data['asset_group'], data['level']):
                raise ValueError(f'Portable metadata changes the existing map identity: {name}')
        else:
            if name.casefold() in builtins:
                raise ValueError(f'Portable metadata cannot register an absent built-in map: {name}')
            runtime[name] = []
            maps.append(name)
        if name not in BY_ID:
            extras[name] = {key: data[key] for key in ('label', 'asset_group', 'level')}
        ids[name.casefold()] = name
        labels[name] = data['label']
        if adapter is not None:
            adapters[name] = adapter
        index = folder / 'manifest.json'
        if index.exists() or index.is_symlink():
            sidecars[name] = index
        elif old is None:
            raise ValueError(f'New map requires its own manifest.json: {name}')

    merged = dict(manifest, community_maps=maps, map_definitions=extras, map_runtime=runtime,
                  shared_content=shared, _portable_labels=labels, _portable_adapters=adapters,
                  _portable_minimaps=minimaps)
    definitions = host_definitions(merged)
    declared_shared_content(merged, definitions)
    if len({row.label.casefold() for row in definitions.values()}) != len(definitions):
        raise ValueError('Duplicate host map label.')
    original = manifest['files']
    files = {row['path'].casefold(): row for row in original}
    if len(files) != len(original):
        raise ValueError('Duplicate package inventory path.')
    def is_owned(name, path):
        image = minimaps.get(name, {}).get('image')
        return owned_path(path, definitions[name]) or image is not None and path == f'lua/{name}/{image}'

    portable_files, removed = {}, set()
    for name, sidecar in sidecars.items():
        data = load_json(sidecar, MAX_JSON)
        if (not isinstance(data, dict) or not {'format', 'files'} <= data.keys() <= {'format', 'files', 'remove'}
                or type(data['format']) is not int or data['format'] != 1
                or not isinstance(data['files'], list) or len(data['files']) > 10000):
            raise ValueError(f'Invalid portable map inventory: {name}')
        removals = data.get('remove', [])
        if not isinstance(removals, list) or len(removals) > 10000:
            raise ValueError(f'Invalid portable inventory removals: {name}')
        for path in removals:
            if not isinstance(path, str) or not is_owned(name, path):
                raise ValueError(f'Invalid or unowned portable inventory removal: {name}')
            key = path.casefold()
            if key in removed or key in portable_files or key not in files or files[key]['path'] != path:
                raise ValueError(f'Invalid or conflicting portable inventory removal: {path}')
            removed.add(key)
            del files[key]
        seen = set()
        for row in data['files']:
            if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}:
                raise ValueError(f'Invalid portable map inventory member: {name}')
            path = row['path']
            if (not isinstance(path, str) or not is_owned(name, path)
                    or type(row['bytes']) is not int or row['bytes'] < 0
                    or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
                raise ValueError(f'Invalid or unowned portable map inventory member: {name}')
            key = path.casefold()
            if key in seen or key in removed or (key in files and files[key]['path'] != path):
                raise ValueError(f'Duplicate portable inventory path: {path}')
            if key in portable_files and portable_files[key] != row:
                raise ValueError(f'Conflicting portable inventory bytes: {path}')
            seen.add(key)
            portable_files[key] = files[key] = row
        if len(files) > MAX_MERGED_HOST_FILES:
            raise ValueError('The merged host map inventory is too large.')
    present_content = {key.split('/')[2] for key in files if key.startswith('content/community/')}
    for prefix in shared.keys() - manifest.get('shared_content', {}).keys():
        group = prefix.split('/')[2]
        if group.casefold() not in present_content:
            raise ValueError(f'Shared map assets are missing: {group}')
    for folder in folders:
        for filename in ('objectives.lua', 'bot-approaches.json', OUTSIDE_AUDIO_FILE):
            path = folder / filename
            if ((path.exists() or path.is_symlink()) and
                    (path.is_symlink() or path.is_junction() or not path.is_file()
                     or path.relative_to(root).as_posix().casefold() not in files)):
                raise ValueError(f'Unverified portable map file: {folder.name}/{filename}')
    for name, adapter in adapters.items():
        if adapter in ('scripted_ctf', 'edition_2026') and f'lua/{name}/objectives.lua'.casefold() not in files:
            raise ValueError(f'{name} requires a verified objectives.lua for {adapter}.')
    return dict(merged, files=list(files.values()),
                _portable_members=sorted(row['path'] for row in portable_files.values()),
                _portable_removed=sorted(removed))


def read_host_manifest(bundle):
    root = Path(bundle).resolve(strict=True)
    manifest = load_json(root / 'package-manifest.json', MAX_PACKAGE_MANIFEST_BYTES)
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list)
            or {'_portable_labels', '_portable_adapters', '_portable_members', '_portable_removed', '_portable_minimaps'} & manifest.keys()):
        raise ValueError('Invalid host package manifest.')
    return root, merge_map_manifests(root, manifest)
