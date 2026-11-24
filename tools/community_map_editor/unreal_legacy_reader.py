"""Read legacy Unreal text exports into neutral scene and transform records."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import chain
import json
import math
from pathlib import Path
import re

from intake_contract import bounded, finite


GAMES = {'vengeance': 'Tribes: Vengeance', 'ascend': 'Tribes: Ascend'}
MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 250_000
MAX_DEPTH = 32
BLOCK = re.compile(r'(Begin|End)\s+([A-Za-z][\w]*)(?:\s+(.*))?\Z', re.ASCII | re.I)
PROPERTY = re.compile(r'([A-Za-z_]\w*(?:\(\d+\)|\[\d+\])?)\s*=\s*(.*)\Z', re.ASCII)
ATTRIBUTE = re.compile(r'\s*([A-Za-z_]\w*)\s*=\s*("(?:[^"\\]|\\.)*"|\S+)', re.ASCII)
IDENTITY = re.compile(r'[A-Za-z0-9_./:$-]{1,512}\Z', re.ASCII)
ORDER = ('x', 'y', 'z')
ANGLES = ('pitch', 'yaw', 'roll')
ACTOR_REFERENCES = ('base', 'baseinfo', 'team', 'attachparent', 'rootcomponent')
MODEL_REFERENCES = ('staticmesh', 'skeletalmesh', 'mesh')
PLACEMENT_FIELDS = {'location', 'rotation', 'drawscale', 'drawscale3d'}
ABSOLUTE_COMPONENT_FIELDS = {
    f'{prefix}absolute{field}' for prefix in ('', 'b') for field in ('location', 'translation', 'rotation', 'scale')
}
COMMON_CLASSES = {
    'staticmeshactor': 'static-mesh', 'playerstart': 'spawn', 'utteamplayerstart': 'spawn', 'terraininfo': 'terrain',
    'terrain': 'terrain', 'brush': 'brush', 'pointlight': 'light', 'directionallight': 'light',
}
VENGEANCE_CLASSES = {
    'mpflag': 'flag', 'mpflagcapturepoint': 'capture-point', 'teaminfo': 'team', 'baseinfo': 'base',
    'multiplayerstart': 'spawn', 'baseinfodefault': 'base', 'light': 'light', 'spotlight': 'light',
    **{f'captureflag{team}': 'flag' for team in ('beagle', 'phoenix', 'imperial')},
    **{f'capturestand{team}': 'capture-point' for team in ('beagle', 'phoenix', 'imperial')},
}
ASCEND_CLASSES = {'trctfbase_bloodeagle': 'flag', 'trctfbase_diamondsword': 'flag'}


def extract_text(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        result = raw.decode('utf-16')
    elif b'\0' in raw:
        raise ValueError('Select a T3D text export. Binary map packages require a package reader.')
    else:
        try:
            result = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            result = raw.decode('cp1252')
    if any(ord(character) < 32 and character not in '\t\r\n' for character in result):
        raise ValueError('The Unreal text export contains a binary control character.')
    return result


def parse_scene(text):
    scene = []
    stack = []
    count = 0
    for number, source in enumerate(text.splitlines(), 1):
        line = source.strip()
        if not line:
            continue
        count += 1
        if count > MAX_RECORDS:
            raise ValueError('The Unreal export has too many source records.')
        edge = BLOCK.fullmatch(line)
        if edge and edge[1].casefold() == 'begin':
            if len(stack) == MAX_DEPTH:
                raise ValueError(f'Unreal export nesting is too deep at line {number}.')
            block = dict(block=edge[2], header=edge[3] or '', line=number, entries=[])
            entries = stack[-1]['entries'] if stack else scene
            entries.append(block)
            stack.append(block)
        elif edge:
            if not stack or edge[2].casefold() != stack[-1]['block'].casefold() or edge[3]:
                raise ValueError(f'Unmatched End {edge[2]} at line {number}.')
            stack.pop()['end_line'] = number
        else:
            assignment = PROPERTY.fullmatch(line)
            entry = dict(line=number, key=assignment[1], value=assignment[2]) if assignment else dict(
                line=number, statement=line)
            entries = stack[-1]['entries'] if stack else scene
            entries.append(entry)
    if stack:
        raise ValueError(f'Unclosed {stack[-1]["block"]} from line {stack[-1]["line"]}.')
    maps = [item for item in scene if item.get('block', '').casefold() == 'map']
    if len(maps) != 1:
        raise ValueError('The Unreal text export needs one complete Begin Map block.')
    return scene, maps[0]


def attributes(text):
    props = {}
    offset = 0
    while offset < len(text):
        match = ATTRIBUTE.match(text, offset)
        if match is None:
            raise ValueError('Invalid actor declaration.')
        key = match[1].casefold()
        if key in props:
            raise ValueError(f'Duplicate actor declaration field {match[1]}.')
        props[key] = match[2]
        offset = match.end()
    return props


def reference(value):
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value.casefold() == 'none':
        return None
    if "'" in value:
        prefix, _, suffix = value.partition("'")
        if not IDENTITY.fullmatch(prefix) or not suffix.endswith("'"):
            return None
        value = suffix[:-1]
    return value if IDENTITY.fullmatch(value) else None


def number(value):
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError('Transform fields need numeric values.') from exc
    if not finite(parsed, 1e12):
        raise ValueError('Transform fields need finite, bounded values.')
    return parsed


def tuple_field(text, keys, default):
    if not text.startswith('(') or not text.endswith(')'):
        raise ValueError('A transform tuple needs parentheses.')
    fields = {}
    for item in text[1:-1].strip().split(',') if text[1:-1].strip() else ():
        key, separator, value = item.partition('=')
        key = key.strip().casefold()
        if not separator or key not in keys or key in fields:
            raise ValueError('A transform tuple contains an invalid or duplicate axis.')
        fields[key] = number(value)
    return tuple(fields.get(key, default) for key in keys)


def quaternion(angles):
    pitch, yaw, roll = (math.remainder(angle, 65536) * math.pi / 65536 for angle in angles)
    sp, sy, sr = map(math.sin, (pitch, yaw, roll))
    cp, cy, cr = map(math.cos, (pitch, yaw, roll))
    result = [cr*sp*sy - sr*cp*cy, -cr*sp*cy - sr*cp*sy,
              cr*cp*sy - sr*sp*cy, cr*cp*cy + sr*sp*sy]
    if result[3] < 0:
        return [-value for value in result]
    return result


def direct_fields(block):
    values = {}
    duplicates = set()
    for entry in block['entries']:
        if 'key' in entry:
            key = entry['key'].casefold()
            if key in values:
                duplicates.add(key)
            values[key] = entry['value']
    return values, duplicates


def transform(block, units_per_m, *, component=False):
    fields, duplicates = direct_fields(block)
    location_keys = ('relativelocation', 'translation', 'location') if component else ('location',)
    rotation_keys = ('relativerotation', 'rotation') if component else ('rotation',)
    scale_keys = ('relativescale3d', 'scale3d', 'drawscale3d') if component else ('drawscale3d',)
    uniform = ('scale', 'drawscale') if component else ('drawscale',)
    candidates = set(location_keys + rotation_keys + scale_keys + uniform)
    if duplicates & candidates:
        raise ValueError('A transform field is defined more than once.')
    if component and any(fields[key].casefold() != 'false' for key in ABSOLUTE_COMPONENT_FIELDS & fields.keys()):
        raise ValueError('The component uses an absolute transform and needs review.')

    def pick(keys, default):
        values = [fields[key] for key in keys if key in fields]
        if len(values) > 1:
            raise ValueError('Multiple properties describe the same transform field.')
        return values[0] if values else default

    location = tuple_field(pick(location_keys, '()'), ORDER, 0)
    angles = tuple_field(pick(rotation_keys, '()'), ANGLES, 0)
    scalar = number(pick(uniform, '1'))
    scale = [part * scalar for part in tuple_field(pick(scale_keys, '()'), ORDER, 1)]
    if any(not finite(part, 1e6) or part == 0 for part in scale):
        raise ValueError('Mesh scale must be finite, nonzero and bounded.')
    position = [coordinate / units_per_m for coordinate in location]
    if not all(finite(coordinate, 20_000) for coordinate in position):
        raise ValueError('Placement is outside the supported draft coordinates.')
    return dict(position_m=position, rotation_xyzw=quaternion(angles), scale=scale,
                space='component-relative' if component else 'actor',
                authored=bool(candidates & fields.keys()))


def descendants(node):
    for entry in node['entries']:
        if 'block' in entry:
            yield entry
            yield from descendants(entry)


def scan_actor(block, units_per_m, game):
    record = dict(line=block['line'], end_line=block['end_line'], source_class=None, name=None,
                  role=None, transform=None, assets=[], components=[], references={}, unmapped_fields=[], review=[])
    review = record['review']
    try:
        fields = attributes(block['header'])
        record['source_class'] = reference(fields.get('class', ''))
        record['name'] = reference(fields.get('name', ''))
        if not record['source_class'] or not record['name']:
            raise ValueError('The actor declaration is missing a usable Class or Name.')
    except ValueError as exc:
        review.append(str(exc))
    kind = (record['source_class'] or '').rsplit('.', 1)[-1].casefold()
    profile_classes = VENGEANCE_CLASSES if game == 'vengeance' else ASCEND_CLASSES
    record['role'] = COMMON_CLASSES.get(kind) or profile_classes.get(kind)
    if not record['role']:
        review.append('No class conversion has been registered for this actor.')
    direct, _ = direct_fields(block)
    for key in ACTOR_REFERENCES:
        if key in direct and (identity := reference(direct[key])) is not None:
            record['references'][key] = identity
    understood = PLACEMENT_FIELDS | set(ACTOR_REFERENCES + MODEL_REFERENCES)
    for entry in block['entries']:
        if 'key' in entry and entry['key'].casefold() not in understood:
            record['unmapped_fields'].append(dict(key=entry['key'], line=entry['line']))
    if record['unmapped_fields']:
        review.append('Unmapped actor properties were retained for review.')
    try:
        record['transform'] = transform(block, units_per_m)
    except ValueError as exc:
        review.append(str(exc))
    attached = {'base', 'attachparent'} & record['references'].keys()
    if attached:
        review.append('Resolve the source attachment before placing this actor.')

    for item in chain((block,), descendants(block)):
        properties, _ = direct_fields(item)
        for key in MODEL_REFERENCES:
            if key in properties and (identity := reference(properties[key])) is not None:
                record['assets'].append(dict(kind=key, source=identity, line=item['line']))
        if item.get('block', '').casefold() == 'object':
            component = dict(line=item['line'], declaration=item['header'], transform=None)
            try:
                placement = transform(item, units_per_m, component=True)
                if placement['authored']:
                    component['transform'] = placement
            except ValueError as exc:
                review.append(f'Subobject at line {item["line"]}. {exc}')
            record['components'].append(component)
    if record['assets']:
        review.append('Resolve referenced source models against the authoring project.')
    if record['role'] == 'brush':
        review.append('Source brush polygons were retained for geometry conversion.')
    if record['components'] and 'location' not in direct:
        if record['references'].get('rootcomponent') or any(part['transform'] for part in record['components']):
            review.append('Check the root component transform before placing this actor.')
    return record


def scope_name(level):
    try:
        return reference(attributes(level['header']).get('name', ''))
    except ValueError:
        return None


def actor_records(level, units_per_m, game, scopes=()):
    for entry in level['entries']:
        kind = entry.get('block', '').casefold()
        if kind == 'actor':
            record = scan_actor(entry, units_per_m, game)
            record['level_lines'] = [line for line, _ in scopes]
            record['level_name'] = scopes[-1][1] if scopes else scope_name(level)
            yield record
        elif kind:
            next_scopes = (*scopes, (entry['line'], scope_name(entry))) if kind == 'level' else scopes
            yield from actor_records(entry, units_per_m, game, next_scopes)


def relate_actors(actors):
    scopes = defaultdict(list)
    for actor in actors:
        if actor['name']:
            scopes[tuple(actor['level_lines']), actor['name'].casefold()].append(actor['line'])
    for actor in actors:
        links = {}
        for key, name in actor['references'].items():
            if key == 'rootcomponent':
                continue
            owner, _, short_name = name.rpartition('.')
            level = actor['level_name'] or ''
            local = not owner or owner.casefold() in (level.casefold(), actor.get('level_path', '').casefold())
            hits = scopes.get((tuple(actor['level_lines']), short_name.casefold()), ()) if local else ()
            if len(hits) != 1:
                actor['review'].append(f'Could not resolve {key} within this exported level.')
            else:
                links[key] = hits[0]
        actor['links'] = links


def read_unreal_export(path, game, units_per_m):
    if game not in GAMES:
        raise ValueError('Choose the Vengeance or Ascend source profile.')
    if not finite(units_per_m, 1e6) or units_per_m <= 0:
        raise ValueError('Specify a positive source units-per-metre value.')
    path = Path(path)
    if path.suffix.casefold() != '.t3d':
        raise ValueError('Select a T3D text export. Native Vengeance and Ascend packages need their own package reader.')
    data = bounded(path, MAX_EXPORT_BYTES)
    scene, root = parse_scene(extract_text(data))
    actors = list(actor_records(root, units_per_m, game))
    if not actors:
        raise ValueError('No authored actors were found in the Unreal text export.')
    relate_actors(actors)
    return dict(format='legacy-unreal-scene', version=1, reader='legacy-unreal-text-v1',
                source_game=GAMES[game], profile=game,
                source=dict(name=path.name, sha256=sha256(data).hexdigest()),
                coordinate_frame=dict(up='positive-z', handedness='left', source_units_per_m=units_per_m,
                                      source_turn_units=65536),
                coverage=dict(roles=dict(Counter(actor['role'] or 'unmapped' for actor in actors)),
                              actors_with_review=sum(bool(actor['review']) for actor in actors)),
                actors=actors, scene=scene)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--game', choices=GAMES, required=True)
    parser.add_argument('--units-per-metre', type=float, required=True)
    args = parser.parse_args()
    try:
        data = read_unreal_export(args.source, args.game, args.units_per_metre)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
