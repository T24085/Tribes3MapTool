"""Inventory contract for optional GLB files attached to a private intake ZIP."""

from hashlib import file_digest, sha256
import json
from pathlib import PurePosixPath
import re
import struct

from intake_contract import INTERIOR_FILE, MAX_INTAKE_INTERIORS, SHAPE_FILE
from interior_geometry import MAX_DIF_LEVELS, MAX_GEOMETRY_BYTES
from model_textures import MAX_TEXTURE_BYTES


RECEIPT = 'mesh-export.json'
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_ASSET_BYTES = MAX_TEXTURE_BYTES + 4 * MAX_GEOMETRY_BYTES
MAX_ASSET_TOTAL = 1024 * 1024 * 1024
MAX_MODELS = 2 * MAX_INTAKE_INTERIORS
MAX_ASSET_FILES = MAX_MODELS * (MAX_DIF_LEVELS + 1)
ASSET_PATH = re.compile(r'assets/meshes/(?:interior|shape)-[0-9]{4}(?:-lod[1-9][0-9]?|-collision)?\.glb')
DIGEST = re.compile(r'[a-f0-9]{64}')


def mesh_models(header):
    interiors, shapes = header.get('interiors', {}), header.get('shapes', {})
    if not isinstance(interiors, dict) or not isinstance(shapes, dict):
        raise ValueError('Invalid source mesh inventory.')
    return {**interiors, **shapes}


def validate_receipt(raw, original, header):
    data = json.loads(raw)
    identity = dict(kind='tribes-private-mesh-export', format=1, units='metres', up='+Y', handedness='right',
                    profile=header.get('profile'), intake_sha256=sha256(original).hexdigest())
    if (not isinstance(data, dict) or data.keys() != {*identity, 'models', 'files'} or type(data['format']) is not int
            or any(data[key] != value for key, value in identity.items())):
        raise ValueError('GLB assets do not belong to this private intake.')
    models = mesh_models(header)
    records, inventory = data['models'], data['files']
    if (not isinstance(records, list) or len(records) != len(models) or len(models) > MAX_MODELS
            or not isinstance(inventory, dict) or not 1 <= len(inventory) <= MAX_ASSET_FILES
            or any(not ASSET_PATH.fullmatch(path) or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                   for path, digest in inventory.items())):
        raise ValueError('Invalid GLB asset inventory.')
    names, paths = set(), set()
    for record in records:
        if (not isinstance(record, dict) or set(record) not in (
                {'reference', 'geometry_sha256', 'levels'}, {'reference', 'geometry_sha256', 'levels', 'triangle_collision'})
                or not isinstance(record['reference'], str) or record['reference'] not in models
                or record['reference'] in names or not isinstance(record['levels'], list)
                or len(record['levels']) > MAX_DIF_LEVELS):
            raise ValueError('Invalid GLB source model record.')
        reference = record['reference']
        filename = models[reference]
        if (not isinstance(filename, str) or not (INTERIOR_FILE.fullmatch(filename) or SHAPE_FILE.fullmatch(filename))
                or filename not in header['files'] or header['files'][filename] != record['geometry_sha256']):
            raise ValueError('GLB source model checksum does not match the intake.')
        names.add(reference)
        stem = 'assets/meshes/' + PurePosixPath(filename).stem
        for level in record['levels']:
            if not isinstance(level, dict) or type(level.get('lod')) is not int or not 0 <= level['lod'] < MAX_DIF_LEVELS:
                raise ValueError('Invalid GLB level of detail.')
            index = level['lod']
            name = f'{stem}-lod{index}.glb' if index else stem + '.glb'
            keys = {'lod', 'file', 'source_detail', 'min_pixels'} if index else {'lod', 'file'}
            if (level.keys() != keys or level['file'] != name or name in paths
                    or index and any(type(level[field]) is not int for field in ('source_detail', 'min_pixels'))):
                raise ValueError('Invalid or duplicate GLB level path.')
            paths.add(name)
        if 'triangle_collision' in record:
            collision = stem + '-collision.glb'
            if record['triangle_collision'] != collision or collision in paths:
                raise ValueError('Invalid or duplicate GLB collision path.')
            paths.add(collision)
    if paths != inventory.keys():
        raise ValueError('GLB model paths do not match the asset inventory.')
    return inventory


def verify_files(root, inventory):
    total = 0
    for name, expected in inventory.items():
        member = root / name
        if not member.is_file() or member.resolve(strict=True) != member:
            raise ValueError('Missing or linked GLB asset: ' + name)
        size = member.stat().st_size
        total += size
        if total > MAX_ASSET_TOTAL:
            raise ValueError('Oversized GLB asset inventory.')
        with member.open('rb') as stream:
            header = stream.read(20)
            if (not 28 < size <= MAX_ASSET_BYTES or len(header) != 20
                    or struct.unpack_from('<4sII', header) != (b'glTF', 2, size)):
                raise ValueError('Invalid GLB header: ' + name)
            length, kind = struct.unpack_from('<I4s', header, 12)
            if kind != b'JSON' or length % 4 or not 0 < length <= size - 28:
                raise ValueError('Invalid GLB document chunk: ' + name)
            stream.seek(20 + length)
            binary_length = size - 28 - length
            if binary_length % 4 or stream.read(8) != struct.pack('<I4s', binary_length, b'BIN\x00'):
                raise ValueError('Invalid GLB binary chunk: ' + name)
            stream.seek(0)
            if file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('GLB asset checksum mismatch: ' + name)
