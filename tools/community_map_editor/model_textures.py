"""Portable texture records bound to material slots in imported Tribes models."""

from hashlib import sha256
import json
from math import isfinite
import re


TEXTURE_INDEX = 'model-textures.json'
TEXTURE_FILE = re.compile(r'texture_[a-f0-9]{32}\.png')
MAX_TEXTURES = 256
MAX_TEXTURE_BYTES = 96 * 1024 * 1024
MAX_SIDE = 4096
MAX_BINDINGS = 2048
MAX_TEXTURE_INDEX = 1024 * 1024
DIGEST = re.compile(r'[a-f0-9]{64}')
SOURCE_FIELDS = {'file', 'source', 'source_sha256', 'size'}
SURFACE_DETAIL = ('normal', 'specular_mask')


def validate_png(raw):
    size = tuple(int.from_bytes(raw[i:i+4], 'big') for i in (16, 20))
    if (len(raw) < 33 or raw[:16] != bytes.fromhex('89504e470d0a1a0a0000000d49484452')
            or raw[24:26] != b'\x08\x06'
            or not all(1 <= side <= MAX_SIDE for side in size)):
        raise ValueError('Invalid model texture PNG header or dimensions.')
    return size


def has_shader_inputs(record):
    return record.get('mode') == 'translucent' or bool(record.keys() & {'opacity', 'glow', *SURFACE_DETAIL})


def texture_records(record, *, detail=True):
    if 'file' in record:
        yield record
        if 'opacity' in record:
            yield record['opacity']
        if 'glow' in record:
            yield record['glow']['color']
            yield record['glow']['mask']
        if detail:
            yield from (record[channel] for channel in SURFACE_DETAIL if channel in record)


def shader_signature(record, inventory):
    glow = record.get('glow')
    signature = (record['mode'], record['two_sided'], record.get('opacity', {}).get('pan'),
                 glow['brightness'] if glow else None, [inventory[row['file']] for row in texture_records(record, detail=False)])
    detail = [(key, inventory[record[key]['file']]) for key in SURFACE_DETAIL if key in record]
    return (*signature, detail) if detail else signature


def validate_texture_receipt(record, textures, inventory, sizes, receipts):
    name, source, digest = (record[key] for key in ('file', 'source', 'source_sha256'))
    if (not isinstance(name, str) or name not in textures or name[8:40] != inventory[name][:32]
            or not isinstance(source, str) or not 0 < len(source) <= 255 or not source.isprintable()
            or any(char in source for char in '/\\:')
            or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
        raise ValueError('Invalid model texture provenance.')
    if sizes is not None:
        size = record['size']
        if (not isinstance(size, list) or not all(type(side) is int for side in size)
                or tuple(size) != sizes[name] or (source, digest) not in receipts):
            raise ValueError('Invalid model texture dimensions or source receipt.')
    return name


def validate_shader_inputs(record):
    if 'opacity' in record:
        opacity = record['opacity']
        if (record['mode'] == 'opaque' or not isinstance(opacity, dict)
                or opacity.keys() not in (SOURCE_FIELDS, SOURCE_FIELDS | {'pan'})):
            raise ValueError('Invalid Vengeance Shader opacity input.')
        if 'pan' in opacity:
            pan = opacity['pan']
            if (not isinstance(pan, list) or len(pan) != 2 or any(
                    type(speed) not in (int, float) or abs(speed) > 1024 or not isfinite(speed) for speed in pan)):
                raise ValueError('Invalid Vengeance Shader opacity panning speed.')
    if 'glow' in record:
        glow = record['glow']
        if (not isinstance(glow, dict) or glow.keys() != {'brightness', 'color', 'mask'}
                or type(glow['brightness']) is not int or not 0 <= glow['brightness'] <= 255
                or any(not isinstance(glow[key], dict) or glow[key].keys() != SOURCE_FIELDS for key in ('color', 'mask'))):
            raise ValueError('Invalid Vengeance Shader glow inputs.')


def validate_bindings(bindings, models, inventory, *, legacy_sizes=None, receipts=(), native_bsp=False,
                      shader_inputs=False, surface_detail=False, require_extended=True):
    textures = {name for name in inventory if TEXTURE_FILE.fullmatch(name)}
    legacy = legacy_sizes is not None
    if (not isinstance(bindings, dict) or not bindings or bindings.keys() - models.keys()
            or legacy and bindings.keys() != models.keys()):
        raise ValueError('Model texture index refers to an unknown model.')
    used = set()
    total = 0
    has_native_shader = has_detail = False
    for filename, slots in bindings.items():
        if not isinstance(slots, dict) or not slots or legacy and slots.keys() != models[filename]:
            raise ValueError('Invalid model texture slot index.')
        total += len(slots)
        if total > MAX_BINDINGS:
            raise ValueError('Too many model texture bindings.')
        for slot, record in slots.items():
            if slot not in models[filename] or not isinstance(record, dict):
                raise ValueError('Model texture refers to an unknown material slot.')
            if record.keys() == {'reason'}:
                reason = record['reason']
                if not isinstance(reason, str) or not 0 < len(reason) <= 240:
                    raise ValueError('Invalid model texture review reason.')
                continue
            required = {'file', 'source', 'source_sha256'}
            if legacy:
                required.update(('size', 'mode'))
                if 'palette' in record:
                    required.update(('palette', 'palette_sha256'))
            if native_bsp:
                required.add('two_sided')
                if 'warning' in record:
                    required.add('warning')
                    if not isinstance(record['warning'], str) or not 0 < len(record['warning']) <= 240:
                        raise ValueError('Invalid Vengeance material review warning.')
                if type(record.get('two_sided')) is not bool or 'palette' in record:
                    raise ValueError('Invalid Vengeance material sidedness.')
            if shader_inputs:
                required.update(record.keys() & {'opacity', 'glow'})
            if surface_detail:
                detail = record.keys() & SURFACE_DETAIL
                required.update(detail)
                for key in detail:
                    if not isinstance(record[key], dict) or record[key].keys() != SOURCE_FIELDS:
                        raise ValueError('Invalid Vengeance normal or specular mask binding.')
                has_detail |= bool(detail)
            if record.keys() != required:
                raise ValueError('Incomplete model texture provenance.')
            modes = ('opaque', 'masked', 'translucent') if shader_inputs else ('opaque', 'masked')
            if legacy:
                if record['mode'] not in modes:
                    raise ValueError('Invalid model texture blend mode.')
                if 'palette' in record:
                    palette, palette_hash = record['palette'], record['palette_sha256']
                    if (not isinstance(palette, str) or not isinstance(palette_hash, str)
                            or (palette, palette_hash) not in receipts):
                        raise ValueError('Tribes interior texture is missing its palette receipt.')
            if shader_inputs:
                validate_shader_inputs(record)
                has_native_shader |= has_shader_inputs(record)
            for source_record in texture_records(record):
                used.add(validate_texture_receipt(source_record, textures, inventory, legacy_sizes, receipts))
    if (not legacy and not used) or used != textures:
        raise ValueError('Model texture inventory does not match the slot bindings.')
    if require_extended and shader_inputs and not has_native_shader:
        raise ValueError('Vengeance shader intake requires an extended material binding.')
    if require_extended and surface_detail and not has_detail:
        raise ValueError('Vengeance surface detail intake requires a normal or specular mask binding.')


def load_texture_index(folder, inventory):
    if TEXTURE_INDEX not in inventory:
        return {}
    index = (folder / TEXTURE_INDEX).resolve(strict=True)
    if index.parent != folder:
        raise ValueError('Model texture index outside the intake.')
    with index.open('rb') as stream:
        raw = stream.read(MAX_TEXTURE_INDEX + 1)
    if len(raw) > MAX_TEXTURE_INDEX or sha256(raw).hexdigest() != inventory[TEXTURE_INDEX]:
        raise ValueError('The model texture index changed after validation.')
    return json.loads(raw)
