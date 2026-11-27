"""Attach reviewed T2 native equipment placements to a portable CTF adapter."""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portable_maps import load_json


CLASSES = {
    'generator': ('generator', '/Game/Topaz/Game/Objectives/BP_Objective_Generator.BP_Objective_Generator_C'),
    'sensor': ('radar', '/Game/Topaz/Game/Objectives/BP_Objective_RadarSensor.BP_Objective_RadarSensor_C'),
    'base_turret': ('turret', '/Game/Topaz/Game/AI/BotPawns/BP_Topaz_BaseTurret.BP_Topaz_BaseTurret_C'),
    'force_shield': ('shield', None),
    'inventory_station': ('station', None),
}
# Conversion rules per source game, for placing retail TRIBES 3 models where the source models
# stood. Each source engine gets its own measured rules (Tribes: Ascend is Unreal 3 to Unreal 5):
# - yaw_offset: degrees added to the source yaw so the retail model faces the way the source model did;
# - scale: uniform scale (the host allows 4 for generators only);
# - face_enemy: roles that start aimed at the enemy base (the centre of the enemy team's equipment)
#   instead of the source yaw. Turrets rotate in play, so this is only their starting pose.
# Tribes 2 drafts keep the source yaw until their own rules are measured in game.
SOURCE_MODEL_RULES = {
    'DraftTA': dict(yaw_offset={'generator': -90.0, 'radar': -90.0}, scale={'generator': 4},
                    face_enemy={'turret'}),
}
NO_RULES = dict(yaw_offset={}, scale={}, face_enemy=set())


def enemy_bearings(sources):
    """Yaw in degrees from a point toward the centre of each team's opponents' equipment."""
    centres = {}
    for team in (1, 2):
        points = [row['transform']['position_cm'][:2] for row in sources.values()
                  if row.get('team') == 3 - team and isinstance(row.get('transform'), dict)]
        if points:
            centres[team] = [sum(axis) / len(points) for axis in zip(*points)]

    def bearing(team, anchor):
        target = centres.get(team)
        if target is None or math.dist(target, anchor[:2]) < 1:
            return None
        return math.degrees(math.atan2(target[1] - anchor[1], target[0] - anchor[0]))
    return bearing
ASSET = re.compile(r'/Game/Community/(?:[A-Za-z0-9_]+/)+([A-Za-z0-9_]+)\.\1')
ACTOR = re.compile(r'[A-Za-z0-9_]{1,128}')
BAKED = re.compile(r'/Game/Community/T2ModelTextures/(M_T2Baked_[a-f0-9]{32})\.\1')


def bounded_number(value, bound):
    return type(value) in (int, float) and -bound <= value <= bound and math.isfinite(value)


def indexed(rows):
    if not isinstance(rows, list):
        raise ValueError('Invalid T2 equipment source records.')
    result = {}
    for row in rows:
        key = row.get('source_id') if isinstance(row, dict) else None
        if type(key) is not int or not 1 <= key <= 50000 or key in result:
            raise ValueError('Invalid or duplicate source object ID.')
        result[key] = row
    return result


def powered_interiors(review, groups):
    rows = review.get('powered_interiors', [])
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError('Invalid powered interior placement list.')
    result, seen, slots = [], set(), 0
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {'actor', 'asset', 'group_id', 'materials'}:
            raise ValueError('Invalid powered interior placement.')
        actor, asset, number, materials = (row[key] for key in ('actor', 'asset', 'group_id', 'materials'))
        group = f'T2Group{number}'
        if (not isinstance(actor, str) or not ACTOR.fullmatch(actor) or actor in seen
                or not isinstance(asset, str) or not ASSET.fullmatch(asset)
                or type(number) is not int or groups.get(group, {}).get('mode') != 'generators'
                or not isinstance(materials, list) or not materials):
            raise ValueError('A powered interior needs an exact cooked mesh and a generator circuit.')
        seen.add(actor)
        slots += len(materials)
        if slots > 1024:
            raise ValueError('Too many powered interior material slots in this draft.')
        selected = set()
        for material in materials:
            if (not isinstance(material, dict) or material.keys() != {'slot', 'asset'}
                    or type(material['slot']) is not int or not 0 <= material['slot'] <= 1023
                    or material['slot'] in selected or not isinstance(material['asset'], str)
                    or not BAKED.fullmatch(material['asset'])):
                raise ValueError('Invalid powered interior material slot.')
            selected.add(material['slot'])
        result.append(dict(actor=actor, asset=asset, power_group=group, materials=materials))
    return result


def native_layout(equipment, review):
    map_id = review.get('map')
    if (not isinstance(map_id, str) or not re.fullmatch(r'DraftT[2A][0-9]{3}', map_id)
            or not isinstance(equipment, dict) or equipment.get('format') != 1):
        raise ValueError('Expected a Tribes 2 or Ascend draft and version-1 equipment projection.')
    rules = SOURCE_MODEL_RULES.get(map_id[:7], NO_RULES)
    sources = indexed(equipment.get('equipment'))
    toward_enemy = enemy_bearings(sources)
    power = indexed(equipment.get('power_groups'))
    approvals = indexed(review.get('placements'))
    if not approvals or len(approvals) > 60 or approvals.keys() - sources.keys():
        raise ValueError('Review must select known source objects within host capacity.')
    used_groups, supports, geometry, counts, entries, shields = {}, {}, {}, Counter(), [], []
    stations = []
    for source_id, approved in approvals.items():
        source = sources[source_id]
        source_role = source.get('role')
        role, native_class = CLASSES.get(source_role, (None, None)) if isinstance(source_role, str) else (None, None)
        team, group_id = source.get('team'), source.get('power_group')
        if (role is None or type(team) is not int or team not in (1, 2) or source.get('transform') is None
                or role != 'shield' and {'shield_power_group', 'on_power_loss'} & approved.keys()
                or role != 'station' and 'station_floor_z_cm' in approved):
            raise ValueError(f'Source object {source_id} needs a native role, valid transform and team.')
        if role == 'shield':
            group_id = approved.get('shield_power_group', group_id)
            if 'shield_power_group' in approved and (type(group_id) is not int or not 0 <= group_id <= 50000):
                raise ValueError(f'Source shield {source_id} has an invalid circuit link.')
        independent = role == 'shield' and type(group_id) is int and group_id == 0 and 'shield_power_group' in approved
        group_key = f'T2IndependentShieldTeam{team}' if independent else f'T2Group{group_id}'
        group = (dict(team=team, declared_power=True, generators=[])
                 if independent else power.get(group_id) if type(group_id) is int else None)
        if group is None or group.get('team') != team:
            raise ValueError(f'Source object {source_id} needs a team power group.')
        providers = group.get('generators')
        if (not isinstance(providers, list) or len(providers) > 3
                or any(type(key) is not int or key not in sources for key in providers)
                or len(providers) != len(set(providers))
                or any(sources[key].get('role') != 'generator' or sources[key].get('team') != team
                       or sources[key].get('power_group') != group_id for key in providers)
                or (role == 'generator') != (source_id in providers)):
            raise ValueError(f'Invalid generator source records in group {group_id}.')
        declared = group.get('declared_power')
        if declared is True and not providers:
            mode = 'always_on'
        elif declared is None and providers and all(key in approvals for key in providers):
            mode = 'generators'
        else:
            raise ValueError(f'Power group {group_id} needs all its generator providers or an explicit always-on source.')
        anchor, yaw, support = approved.get('anchor_position_cm'), approved.get('yaw_degrees'), approved.get('support')
        if (not isinstance(anchor, list) or len(anchor) != 3 or not all(bounded_number(n, 2e6) for n in anchor)
                or not bounded_number(yaw, 360) or not isinstance(support, dict)
                or support.keys() != {'actor', 'asset', 'kind'} or support['kind'] not in ('terrain', 'structures')
                or not isinstance(support['actor'], str) or not ACTOR.fullmatch(support['actor'])
                or not isinstance(support['asset'], str) or not ASSET.fullmatch(support['asset'])):
            raise ValueError(f'Source object {source_id} needs a reviewed native anchor, yaw and exact support mesh.')
        actor = support['actor']
        if actor in geometry and geometry[actor] != support:
            raise ValueError(f'Conflicting geometry for support actor {actor}.')
        ident = f'T2Object{source_id}'
        geometry[actor] = support
        used_groups[group_key] = dict(team=team, mode=mode)
        if role == 'shield':
            half_size = approved.get('half_size_cm')
            if (not isinstance(half_size, list) or len(half_size) != 2
                    or not all(bounded_number(n, 1000) and n > 0 for n in half_size)):
                raise ValueError(f'Source shield {source_id} needs a reviewed doorway width and height.')
            policy = approved.get('on_power_loss', 'drop')
            if policy not in ('drop', 'weaken', 'stay'):
                raise ValueError(f'Source shield {source_id} has an invalid power-loss setting.')
            shield = dict(id=ident, team=team, position_cm=anchor, yaw_degrees=yaw,
                          half_size_cm=half_size, power_group=group_key)
            if policy != 'drop':
                shield['on_power_loss'] = policy
            shields.append(shield)
            continue
        if 'half_size_cm' in approved:
            raise ValueError(f'Source object {source_id} is not a shield opening.')
        if role == 'station':
            floor = approved.get('station_floor_z_cm')
            if not bounded_number(floor, 2e6) or not anchor[2] - 240.1 <= floor <= anchor[2] + 30.1:
                raise ValueError(f'Inventory station {source_id} needs a measured floor below or near its anchor.')
            stations.append(dict(id=ident, team=team, position_cm=[anchor[0], anchor[1], floor],
                                 yaw_degrees=yaw, power_group=group_key,
                                 support={key: support[key] for key in ('actor', 'asset')}))
            continue
        supports[ident] = {key: support[key] for key in ('actor', 'asset')}
        counts[team, role] += 1
        facing = toward_enemy(team, anchor) if role in rules['face_enemy'] else None
        if facing is None:
            facing = yaw + rules['yaw_offset'].get(role, 0.0)
        entry = dict(id=ident, team=team, role=role, native_class=native_class, anchor_position_cm=anchor,
                     candidate_upright_yaw_degrees=math.remainder(facing, 360), power_group=group_key)
        if scale := rules['scale'].get(role):
            entry['authored_actor_scale3d'] = [scale] * 3
        entries.append(entry)
    for role, cap in (('generator', 3), ('radar', 1), ('turret', 10)):
        if counts[1, role] != counts[2, role] or counts[1, role] > cap:
            raise ValueError(f'Native host requires balanced {role} counts up to {cap} per team.')
    if len(shields) > 32:
        raise ValueError('The native host supports up to 32 powered shields per draft.')
    level = f'Arena_{map_id}_P'
    package = f'/Game/Maps/Community/{map_id}/{level}'
    terrain = f'/Game/Community/{map_id}/Terrain.Terrain'
    layout = dict(entries=entries, generators_per_team=counts[1, 'generator'],
                  radars_per_team=counts[1, 'radar'], turrets_per_team=counts[1, 'turret'],
                  power_groups=used_groups)
    if shields:
        layout['powered_shields'] = shields
    if stations:
        layout['inventory_stations'] = stations
    if interiors := powered_interiors(review, used_groups):
        layout['powered_interiors'] = interiors
    return dict(format=1, map_id=map_id, package=package, world=f'{package}.{level}:',
                layout=layout, base_supports=supports,
                descriptor=dict(asset_group=map_id, assembly_asset=terrain, geometry=list(geometry.values())))


def lua_data(value):
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua_data(key) + ']=' + lua_data(item) for key, item in value.items()) + '}'
    if isinstance(value, list):
        return '{' + ','.join(map(lua_data, value)) + '}'
    return json.dumps(value, allow_nan=False)


def attach(ctf, equipment_path, review_path):
    review = load_json(review_path, 256 * 1024)
    with equipment_path.open('rb') as source:
        raw = source.read(16 * 1024 * 1024 + 1)
    equipment_sha = hashlib.sha256(raw).hexdigest()
    if (not isinstance(review, dict) or review.get('format') != 1 or len(raw) > 16 * 1024 * 1024
            or equipment_sha != review.get('equipment_sha256')):
        raise ValueError('The reviewed placements do not match the equipment intake.')
    layout = native_layout(json.loads(raw), review)
    return (f'-- T2 equipment SHA-256: {equipment_sha}\n'
            'local bind=(function()\n' + ctf + '\nend)()\n'
            'assert(type(bind)=="function","expected_editor_ctf_factory")\n'
            'return setmetatable({editor_layout=' + lua_data(layout) + '},\n'
            ' {__call=function(_,factory) return bind(factory) end})\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ctf_adapter', type=Path, help='Generated CTF-only objectives.lua')
    parser.add_argument('equipment', type=Path, help='Version-7 intake equipment.json')
    parser.add_argument('review', type=Path, help='Source-bound native anchor review JSON')
    parser.add_argument('output', type=Path, help='New combined objectives.lua')
    args = parser.parse_args()
    with args.ctf_adapter.open(encoding='utf-8') as source:
        ctf = source.read(65537)
    if len(ctf) > 65536:
        raise ValueError('The input CTF adapter is too large.')
    generated = attach(ctf, args.equipment, args.review)
    with args.output.open('x', encoding='utf-8', newline='\n') as result:
        result.write(generated)
