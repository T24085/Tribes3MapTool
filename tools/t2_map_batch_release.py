"""Stage a finished Map Tool export batch in one separate Direct Host bundle."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from map_sync_contract import map_catalog, map_groups
from portable_maps import load_json, merge_map_manifests
from tools.t2_map_release import (
    CENTRAL, DRAFT, bundle_members, descriptor_paths, flag_boundary_features, projected_manifest,
    publish_directory, regular_row, release_plan, supports_shared_file_runtime,
    verify_descriptors, verify_draft_world_settings, verify_flag_boundary_runtime, verify_minimap_runtime,
)
from verified_file_cache import MAX_PACKAGE_MANIFEST_BYTES, verify_package


def batch_exports(batch):
    summary = load_json(batch/'results.json', 256*1024)
    if (not isinstance(summary, dict) or summary.get('format') != 1 or summary.get('remaining') != []
            or not re.fullmatch(r'batch-[a-f0-9]{12}', str(summary.get('batch', '')))
            or not isinstance(summary.get('complete'), list) or not 1 <= len(summary['complete']) <= 128):
        raise ValueError('Choose a completed Map Tool export batch.')
    ids, exports = set(), []
    for entry in summary['complete']:
        draft = entry.get('draft') if isinstance(entry, dict) else None
        if (not isinstance(draft, str) or not DRAFT.fullmatch(draft) or draft in ids
                or entry.get('folder') != f'{draft}-portable'):
            raise ValueError('The batch contains an invalid or duplicate draft.')
        package = batch/entry['folder']
        if (package.resolve() != package or not package.is_dir()
                or regular_row(package, 'export-receipt.json')['sha256'] != entry.get('receipt_sha256')):
            raise ValueError(f'The export changed since the batch finished: {draft}')
        plan = release_plan(package)
        if plan['map'] != draft:
            raise ValueError(f'The export does not match the batch draft: {draft}')
        ids.add(draft)
        exports.append((package, plan))
    return exports


def shared_changes(payload, central, changes, groups, draft):
    if CENTRAL not in changes:
        return central, [], {}
    proposed = load_json(payload/CENTRAL, MAX_PACKAGE_MANIFEST_BYTES)
    if not isinstance(proposed, dict) or not isinstance(proposed.get('files'), list):
        raise ValueError('Invalid central inventory in a batch export.')
    original = central['files']
    rows = proposed['files']
    mutable = {'files', 'shared_content'}
    if ({key: val for key, val in proposed.items() if key not in mutable}
            != {key: val for key, val in central.items() if key not in mutable}
            or rows[:len(original)] != original):
        raise ValueError('A batch export can only add shared files or assign them to the new map.')
    added = rows[len(original):]
    seen = set()
    for row in added:
        if not isinstance(row, dict) or row.keys() != {'path', 'bytes', 'sha256'}:
            raise ValueError('Invalid shared inventory row in a batch export.')
        name = row['path']
        if (not isinstance(name, str) or name.casefold() in seen or name == CENTRAL or name not in changes
                or not name.startswith('content/Community/')
                or changes[name]['after'] != {'bytes': row['bytes'], 'sha256': row['sha256']}):
            raise ValueError(f'Unverified shared file in batch export: {name}')
        seen.add(name.casefold())
    previous = central.get('shared_content', {})
    scopes = proposed.get('shared_content', {})
    if not isinstance(scopes, dict) or not previous.keys() <= scopes.keys():
        raise ValueError('A batch export cannot remove existing shared ownership.')
    scope_additions = {name: owners for name, owners in scopes.items() if previous.get(name) != owners}
    for name, owners in scope_additions.items():
        if not isinstance(name, str) or name.endswith('/') or not isinstance(owners, list):
            raise ValueError('A batch export must assign individual cooked files to the new map.')
        expected = {map_id for map_id, paths in groups.items() if name in paths} | {draft}
        if owners != sorted(expected):
            raise ValueError(f'A batch export changes existing shared file ownership: {name}')
    if not (added or scope_additions):
        raise ValueError('The batch export has no shared inventory changes.')
    return proposed, added, scope_additions


def release_additions(package, plan, source, central, initial, old_catalog, baseline_hash, inventory, groups):
    draft, changes = plan['map'], plan['changes']
    if draft in old_catalog or plan['before_catalog'] != old_catalog or plan['central_before'] != baseline_hash:
        raise ValueError(f'The batch draft does not match this host baseline: {draft}')
    verify_descriptors(source, plan, 'before')
    payload = package/'payload'
    proposed, shared, scopes = shared_changes(payload, central, changes, groups, draft)
    original_rows = initial['files']
    with TemporaryDirectory(prefix='t3-map-catalog-') as projection:
        declared = projected_manifest(source, payload, Path(projection), proposed, draft)
    if draft in declared.get('_portable_minimaps', {}):
        verify_minimap_runtime(source, inventory)
    rows = {row['path']: row for row in declared['files']}
    if (map_catalog(declared) != plan['after_catalog']
            or any(rows.get(row['path']) != row for row in original_rows)):
        raise ValueError(f'The draft would change another catalog entry: {draft}')
    additions = {name: {'bytes': row['bytes'], 'sha256': row['sha256']}
                 for name, row in rows.items() if name not in inventory}
    for name in descriptor_paths(draft):
        if name not in changes or changes[name]['after'] is None:
            raise ValueError(f'The batch draft is missing its descriptors: {draft}')
        additions[name] = changes[name]['after']
    planned = {name: state['after'] for name, state in changes.items() if name != CENTRAL}
    if planned != additions or any(state['before'] is not None for name, state in changes.items() if name != CENTRAL):
        raise ValueError(f'The batch draft changes files outside its new inventory: {draft}')
    level = f'content/Maps/Community/{draft}/Arena_{draft}_P.umap'
    if features := flag_boundary_features(payload/level):
        verify_flag_boundary_runtime(source, inventory, features)
    return payload, shared, scopes, additions


def stage_batch(batch, source, destination, *, link_content=False):
    batch, source = (Path(path).resolve(strict=True) for path in (batch, source))
    destination = Path(destination).parent.resolve(strict=True)/Path(destination).name
    if any(destination.is_relative_to(root) for root in (source, batch)):
        raise ValueError('Choose a staging folder outside the input bundles.')
    if destination.exists() or destination.is_symlink() or destination.is_junction():
        raise FileExistsError(destination)
    exports = batch_exports(batch)
    baseline_hash = regular_row(source, CENTRAL)['sha256']
    central = load_json(source/CENTRAL, MAX_PACKAGE_MANIFEST_BYTES)
    initial = verify_package(source, merge_manifest=merge_map_manifests)
    old_catalog = map_catalog(initial)
    _, groups = map_groups(initial)
    inventory = {row['path']: row for row in initial['files']}
    verify_draft_world_settings(source, inventory)
    members = bundle_members(source, initial)
    baseline_paths = {name.casefold() for name in members}
    baseline_descriptors = {name: regular_row(source, name) for name in members - inventory.keys() - {CENTRAL}}
    additions, shared, scopes, paths, exact_catalog = {}, {}, {}, {}, {}
    for package, plan in exports:
        payload, new_shared, new_scopes, new_files = release_additions(
            package, plan, source, central, initial, old_catalog, baseline_hash, inventory, groups)
        new_shared = {row['path']: row for row in new_shared}
        for name, row in new_files.items():
            key = name.casefold()
            target = source/name
            if key in baseline_paths or target.exists() or target.is_symlink() or target.is_junction():
                raise ValueError(f'A batch file is already occupied on the baseline: {name}')
            if key in paths:
                earlier = paths[key]
                if earlier != name or name not in shared or shared[name] != new_shared.get(name):
                    raise ValueError(f'Batch exports disagree on shared content: {name}')
                continue
            paths[key] = name
            additions[name] = (payload, row)
        shared.update(new_shared)
        for name, owners in new_scopes.items():
            scopes.setdefault(name, set()).update(owners)
        if new_scopes:
            exact_catalog[plan['map']] = plan['after_catalog'][plan['map']]
    if scopes and not supports_shared_file_runtime(source, inventory):
        raise ValueError('Use a Direct Host bundle that supports per-file shared map assets.')

    with TemporaryDirectory(dir=destination.parent) as staging:
        candidate = Path(staging)/'host'
        (candidate/'content').mkdir(parents=True)
        for name in sorted(members - {CENTRAL}):
            original, target = source/name, candidate/name
            if original.resolve(strict=True) != original or not original.is_file():
                raise ValueError(f'The baseline contains a linked or unsupported file: {name}')
            target.parent.mkdir(parents=True, exist_ok=True)
            if link_content and name.startswith('content/') and target.parent.stat().st_dev == original.stat().st_dev:
                os.link(original, target)
            else:
                shutil.copy2(original, target)
        for name, (payload, row) in sorted(additions.items()):
            target = candidate/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(payload/name, target)
            if regular_row(candidate, name) != row:
                raise ValueError(f'A batch file changed while staging: {name}')
        if shared or scopes:
            combined = dict(central, files=[*central['files'], *(shared[name] for name in sorted(shared))])
            if scopes:
                combined['shared_content'] = central.get('shared_content', {}) | {
                    name: sorted(owners) for name, owners in scopes.items()}
            (candidate/CENTRAL).write_text(json.dumps(combined, indent=2) + '\n', encoding='utf-8')
        else:
            shutil.copyfile(source/CENTRAL, candidate/CENTRAL)

        installed = verify_package(candidate, merge_manifest=merge_map_manifests)
        new_descriptors = {path for _, plan in exports for path in plan['guards']}
        expected = dict(inventory)
        expected.update({name: {'path': name, **row} for name, (_, row) in additions.items()
                         if name not in new_descriptors})
        if ({row['path']: row for row in installed['files']} != expected
                or bundle_members(candidate, installed) != members | additions.keys()):
            raise ValueError('The combined bundle has a different inventory.')
        catalog = map_catalog(installed)
        if (catalog.keys() != old_catalog.keys() | {plan['map'] for _, plan in exports}
                or any(catalog[name] != previous for name, previous in old_catalog.items())):
            raise ValueError('The combined bundle changes an existing map catalog entry.')
        if any(catalog[name] != expected for name, expected in exact_catalog.items()):
            raise ValueError('The combined bundle changes a draft’s verified shared files.')
        for _, plan in exports:
            verify_descriptors(candidate, plan, 'after')
        if (regular_row(source, CENTRAL)['sha256'] != baseline_hash
                or merge_map_manifests(source, load_json(source/CENTRAL, MAX_PACKAGE_MANIFEST_BYTES)) != initial
                or any(regular_row(candidate, name) != row or regular_row(source, name) != row
                       for name, row in baseline_descriptors.items())):
            raise ValueError('The host inventory changed while staging. Use a stable baseline.')
        publish_directory(candidate, destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path, help='Completed batch export folder with results.json')
    parser.add_argument('host', type=Path, help='Existing Direct Host bundle used as read-only input')
    parser.add_argument('output', type=Path, help='Unused output folder for the combined bundle')
    parser.add_argument('--link-content', action='store_true', help='Hardlink unchanged cooked content on the same drive')
    args = parser.parse_args()
    try:
        print(stage_batch(args.batch, args.host, args.output, link_content=args.link_content))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'{error}\n')
