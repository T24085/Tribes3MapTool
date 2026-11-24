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


def power_context(rows):
    teams, scopes, contexts = {}, {}, {}
    for row in rows:
        ident, parent = row['id'], row['parent']
        properties = {key.casefold(): value for key, value in row['properties'].items()
                      if isinstance(value, str)}
        name = row['name'].casefold()
        team = teams.get(parent, 0)
        if row['status'] == 'group' and name in ('team1', 'team2'):
            team = int(name[-1])
        if row['source_class'].casefold() == 'teamgroup' and name in ('team0', 'team1'):
            team = int(name[-1]) + 1
        if 'team' in properties:
            source_team = properties['team'].strip()
            team = int(source_team) if source_team in ('1', '2') else 0
        teams[ident] = team
        scopes[ident] = scopes.get(parent)
        if row['status'] == 'group':
            power = properties.get('providespower', '').strip().casefold()
            declared = {'0': False, '1': True, 'false': False, 'true': True}.get(power)
            powered_name = name.endswith('powered') and not name.endswith('unpowered')
            if declared is not None or powered_name:
                scopes[ident] = ident
                contexts[ident] = dict(source_id=ident, name=row['name'], team=team,
                                       declared_power=declared, generators=[])
    for ident, scope in scopes.items():
        team = teams[ident]
        if scope is not None and team and contexts[scope]['team'] not in (0, team):
            scopes[ident] = None
    return teams, scopes, contexts


def mission_equipment(rows, terrain):
    """Keep exact source IDs and transforms, with auditable group evidence."""
    teams, scopes, contexts = power_context(rows)
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
