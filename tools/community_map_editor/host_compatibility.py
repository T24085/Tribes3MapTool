"""Verify selected Direct Host features and prepare portable issue drafts."""

from hashlib import sha256
import json
import re

if __package__:
    from .source_profiles import imported_draft
else:
    from source_profiles import imported_draft


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_RUNTIME_BYTES = 2 * 1024 * 1024
INVENTORY = 'package-manifest.json'
OBJECTIVES = 'lua/objective_layouts.lua'
NETWORK = 'lua/network.lua'
SHIELDS = 'lua/powered_shields.lua'
STATIONS = 'lua/inventory_stations.lua'
FLAG_RUNTIME = {'match_roster.py': b'# ', 'runtime-fixes/mission_flag_limit.lua': b'-- '}
FLAG_CAPABILITIES = {'flag-height-limits': b'T3_EDITOR_FLAG_HEIGHT_LIMITS_V1',
                     'flag-return-timer': b'T3_EDITOR_FLAG_RETURN_TIMER_V1'}
DIGEST = re.compile(r'[0-9a-f]{64}')

# These are the version markers checked by the Direct Host draft exporter.
EQUIPMENT = b'-- T3_EDITOR_GROUPED_EQUIPMENT_V1'
SHIELD = b'-- T3_EDITOR_GROUPED_SHIELDS_V1'
SHIELD_POLICIES = b'-- T3_EDITOR_SHIELD_POWER_POLICIES_V1'
STATION = b'-- T3_EDITOR_INVENTORY_STATIONS_V2'
REQUIREMENTS = {
    'draft-world-settings': ('Authored draft World Settings', (NETWORK, 'lib/host_catalog_runtime.py')),
    'grouped-equipment': ('Generator-linked draft equipment', (OBJECTIVES, NETWORK)),
    'powered-shields': ('Generator-linked draft shields', (OBJECTIVES, NETWORK, SHIELDS)),
    'shield-power-policies': ('Custom shield power-loss behavior', (OBJECTIVES, SHIELDS)),
    'inventory-stations': ('Draft inventory stations', (OBJECTIVES, NETWORK, STATIONS)),
    'portable-equipment-adapters': ('Loading portable equipment adapters', ('playtest.py', 'lib/portable_maps.py')),
    'flag-height-limits': ('Flag-only floor and ceiling limits', tuple(FLAG_RUNTIME)),
    'flag-return-timer': ('Flag-only return timer', tuple(FLAG_RUNTIME)),
}


def bundle_blockers(host, baseline, placements, flag_features):
    """Read only known runtime files, using checksums from the selected baseline."""
    with (host / INVENTORY).open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES or sha256(raw).hexdigest() != baseline:
        raise ValueError('The Direct Host inventory changed or exceeds its size limit.')
    manifest = json.loads(raw)
    rows = manifest.get('files') if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        raise ValueError('The Direct Host package inventory has no file list.')
    known = {name for _, paths in REQUIREMENTS.values() for name in paths}
    inventory = {}
    for row in rows:
        path = row.get('path') if isinstance(row, dict) else None
        if isinstance(path, str) and path in known:
            if path in inventory:
                raise ValueError('The Direct Host inventory repeats a runtime file.')
            inventory[path] = row
    verified = {}

    def runtime(name):
        if name in verified:
            return verified[name]
        row = inventory.get(name)
        path = host / name
        data = None
        limit = 256 * 1024 if name == SHIELDS else MAX_RUNTIME_BYTES
        if (row and type(row.get('bytes')) is int and 0 < row['bytes'] <= limit
                and path.resolve() == path and path.is_file()):
            try:
                with path.open('rb') as stream:
                    payload = stream.read(limit + 1)
            except OSError:
                pass
            else:
                if len(payload) == row['bytes'] and sha256(payload).hexdigest() == row.get('sha256'):
                    data = payload
        verified[name] = data
        return data

    def contains(name, marker):
        return marker in (runtime(name) or b'').splitlines()

    blockers = []
    if placements is not None and any(not contains(name, EQUIPMENT) for name in (OBJECTIVES, NETWORK)):
        blockers.append('grouped-equipment')
    shields = [row for row in placements or () if 'half_size_cm' in row]
    if shields:
        if runtime(SHIELDS) is None or any(not contains(name, SHIELD) for name in (OBJECTIVES, NETWORK)):
            blockers.append('powered-shields')
        if any(row.get('on_power_loss', 'drop') != 'drop' for row in shields) and any(
                not contains(name, SHIELD_POLICIES) for name in (OBJECTIVES, SHIELDS)):
            blockers.append('shield-power-policies')
    if any('station_floor_z_cm' in row for row in placements or ()):
        if any(not contains(name, STATION) for name in (OBJECTIVES, NETWORK, STATIONS)):
            blockers.append('inventory-stations')
        launcher = host / 'playtest.py'
        if 'playtest.py' in inventory or launcher.exists() or launcher.is_symlink():
            if any(marker not in (runtime(name) or b'') for name, marker in (
                    ('playtest.py', b'merge_map_manifests'), ('playtest.py', b'runtime_paths'),
                    ('lib/portable_maps.py', b'_portable_adapters'))):
                blockers.append('portable-equipment-adapters')
    for feature in flag_features:
        if any(not contains(name, prefix + FLAG_CAPABILITIES[feature]) for name, prefix in FLAG_RUNTIME.items()):
            blockers.append(feature)
    return blockers


def read_flag_features(metadata, draft):
    if (not isinstance(metadata, dict) or set(metadata) != {'map', 'features'} or metadata['map'] != draft
            or not isinstance(metadata['features'], list)
            or any(not isinstance(key, str) or key not in FLAG_CAPABILITIES for key in metadata['features'])
            or len(set(metadata['features'])) != len(metadata['features'])):
        raise ValueError('The flag boundary requirements do not match this draft.')
    return metadata['features']


def issue_draft(draft, report):
    """Never include a map title, filesystem path, host address or exporter stderr."""
    if (not imported_draft(draft) or not isinstance(report, dict) or set(report) != {'baseline', 'blockers'}
            or not isinstance(report['baseline'], str) or not DIGEST.fullmatch(report['baseline'])
            or not isinstance(report['blockers'], list) or not report['blockers']
            or any(not isinstance(key, str) or key not in REQUIREMENTS for key in report['blockers'])
            or len(set(report['blockers'])) != len(report['blockers'])):
        raise ValueError('Invalid Direct Host compatibility report.')
    lines = ['# Direct Host support needed for a map draft', '',
             'The Map Tool stopped before cooking because the selected Direct Host bundle could not verify these features.', '',
             f'- Draft ID `{draft}`', f'- Baseline inventory SHA-256 `{report["baseline"]}`', '', '## Required support', '']
    for key in report['blockers']:
        label, files = REQUIREMENTS[key]
        lines.append(f"- {label} (`{key}`) in " + ', '.join(f'`{name}`' for name in files))
    lines.extend(['', '## Verification', '',
                  'Verify that the bundle inventories the matching runtime files, then rebuild the same draft with that baseline.'])
    return '\n'.join(lines) + '\n'
