"""Project authored Tribes 2 equipment and power groups into neutral records."""

from mesh_mappings import source_transform


EQUIPMENT_ROLES = {
    ('staticshape', 'generatorlarge'): 'generator',
    ('staticshape', 'sensorlargepulse'): 'sensor',
    ('staticshape', 'sensormediumpulse'): 'sensor',
    ('turret', 'turretbaselarge'): 'base_turret',
    ('turret', 'sentryturret'): 'sentry_turret',
    ('staticshape', 'stationinventory'): 'inventory_station',
    ('staticshape', 'stationvehiclepad'): 'vehicle_station',
    ('forcefieldbare', 'defaultforcefieldbare'): 'force_shield',
}
# Tribes: Ascend base equipment, by class without its team suffix. The host spawns the retail
# TRIBES 3 object for each role; the source supplies only placement, team and power.
ASCEND_ROLES = {
    'tribesgame.trpowergenerator': 'generator',
    'tribesgame.trradarstation': 'sensor',
    'tribesgame.trbaseturret': 'base_turret',
    'tribesgame.trinventorystation': 'inventory_station',
    'tribesgame.trvehiclestation': 'vehicle_station',
}
ASCEND_TEAMS = ('_bloodeagle', '_diamondsword')
# One power circuit per team: every Ascend base runs on its team's generators.
ASCEND_GROUP_BASE = 49990


def ascend_equipment_class(source_class):
    """Return (role, team suffix) for a Tribes: Ascend equipment class, else (None, None)."""
    cls = source_class.casefold()
    for suffix in ASCEND_TEAMS:
        if cls.endswith(suffix):
            return ASCEND_ROLES.get(cls.removesuffix(suffix)), suffix
    return None, None


def ascend_flag_teams(rows):
    """Map each Ascend team suffix to the CTF team whose assigned flag carries it."""
    found = {}
    for row in rows:
        if row['status'] == 'marker' and row.get('kind') == 'flag':
            cls = row['source_class'].casefold()
            for suffix in ASCEND_TEAMS:
                if cls.endswith(suffix):
                    found.setdefault(suffix, set()).add(row['team'])
    teams = {suffix: next(iter(values)) for suffix, values in found.items() if len(values) == 1}
    # Both suffixes on one team cannot say which base is whose.
    return teams if len(set(teams.values())) == len(teams) else {}


def ascend_equipment(rows):
    teams = ascend_flag_teams(rows)
    equipment, groups = [], {}
    for row in rows:
        role, suffix = ascend_equipment_class(row['source_class'])
        if role is None or row['status'] != 'review':
            continue
        team = teams.get(suffix, 0)
        issues = []
        if not team:
            issues.append('Source team does not match an assigned CTF flag.')
        transform = row.get('transform')
        if transform is None:
            issues.append('Re-import this map to record the equipment transform.')
        group = ASCEND_GROUP_BASE + team if team else None
        if group is not None:
            groups.setdefault(group, dict(source_id=group, name=f'Team {team} base power', team=team,
                                          declared_power=None, generators=[]))
            if role == 'generator':
                groups[group]['generators'].append(row['id'])
        equipment.append(dict(source_id=row['id'], role=role, datablock=row['source_class'].rsplit('.', 1)[-1],
                              team=team, transform=transform, power_group=group, review=issues))
    for group in groups.values():
        if not group['generators']:
            # A base with no source generator stays powered, as it would be in the source map.
            group['declared_power'] = True
    return dict(format=1, equipment=equipment, power_groups=list(groups.values()))


def power_context(rows, *, generator_scopes=False):
    teams, scopes, contexts = {}, {}, {}
    group_power = {}
    generator_parents = set()
    for row in rows:
        ident, parent = row['id'], row['parent']
        properties = {key.casefold(): value for key, value in row['properties'].items()
                      if isinstance(value, str)}
        name = row['name'].casefold()
        team = teams.get(parent, 0)
        if row['status'] == 'group':
            group_power[ident] = properties.get('providespower', '').strip().casefold()
        if row['status'] == 'group' and name in ('team1', 'team2'):
            team = int(name[-1])
        if row['source_class'].casefold() == 'teamgroup' and name in ('team0', 'team1'):
            team = int(name[-1]) + 1
        if 'team' in properties:
            source_team = properties['team'].strip()
            team = int(source_team) if source_team in ('1', '2') else 0
        teams[ident] = team
        key = (row['source_class'].casefold(), properties.get('datablock', '').casefold())
        if (generator_scopes and team in (1, 2) and teams.get(parent) == team
                and row['status'] == 'review' and EQUIPMENT_ROLES.get(key) == 'generator'):
            generator_parents.add(parent)
    for row in rows:
        ident, parent = row['id'], row['parent']
        scopes[ident] = scopes.get(parent)
        if row['status'] == 'group':
            declared = {'0': False, '1': True, 'false': False, 'true': True}.get(group_power[ident])
            name = row['name'].casefold()
            powered_name = name.endswith('powered') and not name.endswith('unpowered')
            if declared is not None or powered_name or ident in generator_parents:
                scopes[ident] = ident
                contexts[ident] = dict(source_id=ident, name=row['name'], team=teams[ident],
                                       declared_power=declared, generators=[])
    for ident, scope in scopes.items():
        team = teams[ident]
        if scope is not None and team and contexts[scope]['team'] not in (0, team):
            scopes[ident] = None
    return teams, scopes, contexts


def mission_equipment(rows, terrain, *, generator_scopes=False):
    """Keep exact source IDs and transforms, with auditable group evidence."""
    if any(ascend_equipment_class(row['source_class'])[0] for row in rows):
        return ascend_equipment(rows)
    teams, scopes, contexts = power_context(rows, generator_scopes=generator_scopes)
    equipment = {}
    for row in rows:
        ident = row['id']
        team = teams[ident]
        properties = {key.casefold(): value for key, value in row['properties'].items()
                      if isinstance(value, str)}
        datablock = properties.get('datablock', '')
        role = EQUIPMENT_ROLES.get((row['source_class'].casefold(), datablock.casefold()))
        if role is None or row['status'] != 'review':
            continue
        scope = scopes[ident]
        issues = []
        transform = None
        if not team:
            issues.append('Source team is unassigned or neutral.')
        if scope is None:
            issues.append('No authored power group identified.')
        try:
            position, rotation, scale = source_transform(properties, terrain)
            transform = dict(position_cm=list(position), rotation_xyzw=list(rotation), scale=list(scale))
        except ValueError as exc:
            issues.append(str(exc))
        equipment[ident] = dict(source_id=ident, role=role, datablock=datablock, team=team,
                                transform=transform, power_group=scope, review=issues)
        if scope is not None and role == 'generator':
            contexts[scope]['generators'].append(ident)

    used = {row['power_group'] for row in equipment.values()}
    return dict(format=1, equipment=list(equipment.values()),
                power_groups=[group for ident, group in contexts.items() if ident in used])
