"""Validate the private source-sky artwork in a neutral map intake."""

from hashlib import sha256
import json
import re


SKY_INDEX = 'sky-artwork.json'
SKY_PNG = re.compile(r'sky_[0-9a-f]{32}\.png')
MAX_SKIES = 8
MAX_SKY_SLOTS = 16
MAX_SKY_BYTES = 64 * 1024 * 1024


def relative_name(name):
    if not isinstance(name, str) or not 0 < len(name) <= 255 or not name.isprintable():
        return False
    return all(part and part not in ('.', '..') for part in name.split('/')) and not any(
        char in name for char in '\\:*?"<>|'
    )


def checked_sky_file(folder, name, inventory, limit):
    from intake_contract import bounded

    file = folder / name
    if file.resolve(strict=True) != file or not file.is_file():
        raise ValueError('The source sky file is outside the intake.')
    raw = bounded(file, limit)
    if sha256(raw).hexdigest() != inventory[name]:
        raise ValueError('The source sky artwork changed after validation.')
    return file, raw


def load_sky_index(folder, inventory):
    if SKY_INDEX not in inventory:
        return []
    _, data = checked_sky_file(folder, SKY_INDEX, inventory, 256 * 1024)
    return json.loads(data)['skies']


def validate_sky_artwork(document, weather, inventory):
    def digest(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)

    expected = {
        row['source_id']: row['values']['material_list'] for row in weather['objects']
        if row['class'] == 'sky' and 'material_list' in row['values']
    }
    if (not isinstance(document, dict) or document.keys() != {'format', 'skies'}
            or type(document['format']) is not int or document['format'] != 1
            or not isinstance(document['skies'], list) or not 1 <= len(document['skies']) <= MAX_SKIES):
        raise ValueError('Invalid intake sky artwork index.')
    seen, used = set(), set()
    for sky in document['skies']:
        if (not isinstance(sky, dict) or sky.keys() != {'source_id', 'material_list', 'source_sha256', 'slots'}
                or type(sky['source_id']) is not int or sky['source_id'] <= 0 or sky['source_id'] in seen
                or not relative_name(sky['material_list']) or expected.get(sky['source_id']) != sky['material_list']
                or not digest(sky['source_sha256']) or not isinstance(sky['slots'], list)
                or not 1 <= len(sky['slots']) <= MAX_SKY_SLOTS):
            raise ValueError('The sky artwork does not match an authored mission sky.')
        seen.add(sky['source_id'])
        for slot in sky['slots']:
            if (not isinstance(slot, dict) or not relative_name(slot.get('reference'))
                    or slot.keys() not in ({'reference', 'reason'}, {'reference', 'file', 'source_sha256'})):
                raise ValueError('Invalid intake sky texture slot.')
            if 'reason' in slot:
                if slot['reason'] not in ('missing', 'unreadable', 'ambiguous', 'capacity'):
                    raise ValueError('Invalid intake sky texture review status.')
                continue
            filename = slot['file']
            if (not isinstance(filename, str) or not SKY_PNG.fullmatch(filename)
                    or not digest(slot['source_sha256']) or not digest(inventory.get(filename))
                    or not inventory[filename].startswith(filename[4:-4])):
                raise ValueError('Sky texture is outside the validated PNG inventory.')
            used.add(filename)
    if used != {name for name in inventory if SKY_PNG.fullmatch(name)}:
        raise ValueError('The intake contains unreferenced sky artwork.')
    return document['skies']
