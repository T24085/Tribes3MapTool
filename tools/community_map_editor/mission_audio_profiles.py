"""Read literal Tribes 2 sound datablocks and their source receipts."""

from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from zipfile import BadZipFile


NAME = re.compile(r'[a-z_][a-z0-9_]{0,95}')
DECIMAL = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
TOKEN = re.compile(r'//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*|'
                   + DECIMAL + r'|\S')
TYPES = {'audioprofile': 'profile', 'audiodescription': 'description'}
DESCRIPTION = frozenset(('volume', 'islooping', 'is3d', 'mindistance', 'maxdistance', 'minloopgap',
                         'maxloopgap', 'loopcount', 'coneinsideangle', 'coneoutsideangle',
                         'coneoutsidevolume', 'conevector'))
FIELDS = {'profile': {'filename', 'description'}, 'description': DESCRIPTION}
MAX_SCRIPT_BYTES = 512 * 1024
MAX_SCRIPT_TOTAL = 16 * 1024 * 1024
MAX_SCRIPT_FILES = 2048
MAX_DEFINITIONS = 512
MAX_CHAIN = 24


def assignments(kind, body):
    allowed = FIELDS[kind]
    result = {}
    start = 0
    for end, token in enumerate(body):
        if token != ';':
            continue
        field = body[start:end]
        start = end + 1
        if not field:
            continue
        key = field[0].casefold()
        if key == 'referencedistance' and kind == 'description':
            key = 'mindistance'
        if len(field) < 3 or field[1] != '=' or not NAME.fullmatch(key):
            return None
        if key not in allowed:
            continue
        value = field[2]
        if key in result or len(field) != 3:
            return None
        if value.startswith('"'):
            value = value[1:-1]
        elif not (NAME.fullmatch(value.casefold()) or re.fullmatch(DECIMAL, value)):
            return None
        if not value or len(value) > 180:
            return None
        result[key] = value
    return result if start == len(body) else None


def declarations(raw):
    try:
        script = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        script = raw.decode('cp1252', errors='replace')
    tokens = [token for match in TOKEN.finditer(script)
              if not (token := match[0]).startswith(('//', '/*'))]
    if '"' in tokens:
        return
    depth = 0
    for index, token in enumerate(tokens):
        if token == '{':
            depth += 1
        elif token == '}':
            depth -= 1
        elif (depth == 0 and token.casefold() in ('datablock', 'new')
              and (index == 0 or tokens[index - 1] in (';', '}'))):
            signature = tokens[index + 1:index + 7]
            if len(signature) < 5 or signature[1] != '(':
                continue
            kind = TYPES.get(signature[0].casefold())
            name = signature[2].casefold()
            if kind is None or not NAME.fullmatch(name):
                continue
            parent = ''
            if signature[3] == ':' and len(signature) == 6 and signature[5] == ')':
                parent = signature[4].casefold()
                if not NAME.fullmatch(parent):
                    continue
                begin = index + 7
            elif signature[3] == ')':
                begin = index + 5
            else:
                continue
            if begin >= len(tokens) or tokens[begin] != '{':
                continue
            end = begin + 1
            while end < len(tokens) and tokens[end] not in ('{', '}'):
                end += 1
            values = (assignments(kind, tokens[begin + 1:end])
                      if tokens[end:end + 2] == ['}', ';'] else None)
            yield name, dict(kind=kind, parent=parent, fields=values) if values is not None else None


def source_scripts(source, asset_root):
    from mission_interiors import indexed_assets, pick_exact, read_asset

    with indexed_assets(source, asset_root, '.cs') as (primary, secondary, _):
        paths = set(primary) | secondary.keys()
        loose_roots = [source.parent]
        if source.parent.name.casefold() in ('maps', 'missions'):
            loose_roots.append(source.parent.parent)
        if asset_root is not None and asset_root.is_dir() and asset_root not in loose_roots:
            loose_roots.append(asset_root)
        for root in loose_roots:
            scripts = root / 'scripts'
            indexed_scripts = scripts.rglob('*') if scripts.is_dir() and not scripts.is_symlink() else ()
            try:
                for names in (root.iterdir(), indexed_scripts):
                    for path in names:
                        if (path.suffix.casefold() == '.cs' and path.is_file() and not path.is_symlink()
                                and path.resolve().is_relative_to(root)):
                            paths.add(path.relative_to(root).as_posix().casefold())
                            if len(paths) > MAX_SCRIPT_FILES:
                                return {}
            except OSError:
                return {}
        if len(paths) > MAX_SCRIPT_FILES:
            return {}
        definitions, ambiguous, total = {}, set(), 0
        for reference in sorted(paths):
            try:
                asset = pick_exact(reference, primary, secondary, loose_roots)
                if asset is None:
                    continue
                data, _ = read_asset(asset, limit=MAX_SCRIPT_BYTES)
            except (ValueError, OSError, BadZipFile, KeyError):
                continue
            total += len(data)
            if total > MAX_SCRIPT_TOTAL:
                return {}
            lowered = data.lower()
            if b'audioprofile' not in lowered and b'audiodescription' not in lowered:
                continue
            origin = asset.name if isinstance(asset, Path) else (
                asset[0].name if isinstance(asset[0], Path) else Path(asset[0].filename).name)
            receipt = dict(name=reference, origin=origin, sha256=sha256(data).hexdigest())
            for name, record in declarations(data):
                if record is None or name in definitions:
                    ambiguous.add(name)
                else:
                    definitions[name] = dict(record, source=receipt)
                if len(definitions) + len(ambiguous) > MAX_DEFINITIONS:
                    return {}
        return {name: record for name, record in definitions.items() if name not in ambiguous}


def resolve(name, kind, definitions, used, chain=()):
    name = name.casefold()
    record = definitions.get(name)
    if record is None or record['kind'] != kind or name in chain or len(chain) >= MAX_CHAIN:
        raise ValueError('The sound datablock is missing, ambiguous or cyclic.')
    inherited = (resolve(record['parent'], kind, definitions, used, (*chain, name))
                 if record['parent'] else {})
    used.add(name)
    return inherited | record['fields']


def project_emitter(explicit, definitions):
    from mission_audio import clip_reference, switch

    used = set()
    profile = resolve(explicit['profile'], 'profile', definitions, used) if 'profile' in explicit else {}
    projected = dict(explicit)
    if 'filename' in profile:
        if ('filename' in explicit
                and clip_reference(explicit['filename']) != clip_reference(profile['filename'])):
            raise ValueError('Conflicting emitter and profile sound files.')
        projected['filename'] = profile['filename']
    if switch(explicit.get('useprofiledescription', '0')):
        authored = explicit.get('description')
        inherited = profile.get('description')
        if authored and inherited and authored.casefold() != inherited.casefold():
            raise ValueError('Conflicting emitter and profile sound descriptions.')
        description = resolve(authored or inherited or '', 'description', definitions, used)
        projected = {name: value for name, value in projected.items() if name not in DESCRIPTION} | description
    return projected, used


def validate_definitions(definitions, receipts):
    if not isinstance(definitions, dict) or not 1 <= len(definitions) <= MAX_DEFINITIONS:
        raise ValueError('Invalid sound datablock inventory.')
    for name, record in definitions.items():
        if (not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(record, dict)
                or set(record) != {'kind', 'parent', 'fields', 'source'}):
            raise ValueError('Invalid sound datablock.')
        kind, parent, fields = record['kind'], record['parent'], record['fields']
        if (not isinstance(kind, str) or kind not in FIELDS or not isinstance(parent, str)
                or parent and not NAME.fullmatch(parent)
                or not isinstance(fields, dict) or fields.keys() - FIELDS[kind]
                or any(not isinstance(value, str) or not 1 <= len(value) <= 180 for value in fields.values())):
            raise ValueError('Invalid literal audio profile values.')
        source = record['source']
        if (not isinstance(source, dict) or set(source) != {'name', 'origin', 'sha256'}
                or not all(isinstance(value, str) for value in source.values())):
            raise ValueError('Missing sound datablock provenance.')
        reference, origin, digest = source['name'], source['origin'], source['sha256']
        path = PurePosixPath(reference)
        if (not 1 <= len(reference) <= 260 or path.as_posix() != reference or path.is_absolute()
                or '..' in path.parts or '\\' in reference or ':' in reference or path.suffix != '.cs'
                or not 1 <= len(origin) <= 260 or any(character in origin for character in '/\\:')
                or not origin.casefold().endswith(('.cs', '.vl2')) or (reference, origin, digest) not in receipts):
            raise ValueError('Invalid sound datablock source receipt.')
