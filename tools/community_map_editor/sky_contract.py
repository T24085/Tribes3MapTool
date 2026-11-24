"""Validate the approved face plan and DDS handoff inside and outside Unreal."""

import hashlib
import json
import re
import struct

from intake_contract import bounded
from texture_batches import decisions, inspect, original_png


FACES = ('+X', '-X', '+Y', '-Y', '+Z', '-Z')
ROTATIONS = (0, 90, 180, 270)
BUILD_NAME = re.compile(r'sky-[0-9a-f]{16}')
CONTACT_SHEET = 'SkyFaces.png'
MAX_CONTACT_SHEET_BYTES = 4 * 1024 * 1024


def face_plan(value, items, *, complete=False):
    if not isinstance(value, dict) or value.keys() - set(FACES):
        raise ValueError('Invalid sky face assignments.')
    seen = set()
    for row in value.values():
        if (not isinstance(row, dict) or set(row) != {'id', 'rotation'}
                or not isinstance(row['id'], str) or row['id'] not in items or row['id'] in seen
                or type(row['rotation']) is not int or row['rotation'] not in ROTATIONS):
            raise ValueError('Choose a different texture for each face and a 90-degree rotation.')
        seen.add(row['id'])
    if complete and set(value) != set(FACES):
        raise ValueError('Assign all six sky faces.')
    return {face: value[face] for face in FACES if face in value}


def collect_faces(root, items, plan, *, use_originals=False):
    result = {}
    for face, entry in face_plan(plan, items, complete=True).items():
        row = items[entry['id']]
        if use_originals:
            original = replacement = original_png(root, row)
            current = row['source']
        else:
            original, replacement, current = inspect(root, row)
        result[face] = (entry['rotation'], original, replacement, row['source'], current)
    sizes = {tuple(entry[4]['size']) for entry in result.values()}
    if len(sizes) != 1:
        raise ValueError('The six selected sky faces must have matching dimensions.')
    width, height = sizes.pop()
    if width != height or width & (width - 1):
        raise ValueError('The six selected sky faces must be square and a power of two.')
    return result


def approved_summary(root, items, plan, faces, *, use_originals=False):
    reviewed = {} if use_originals else decisions(root, items)
    summary = []
    for face, (rotation, _, _, _, current) in faces.items():
        ident = plan[face]['id']
        digest = current['sha256']
        if not use_originals and reviewed.get(ident) != {'status': 'approved', 'sha256': digest}:
            raise ValueError(f'Approve {ident}.png for face {face} before building or importing.')
        summary.append({'face': face, 'id': ident, 'rotation': rotation, 'sha256': digest})
    return summary


def sky_build_name(summary, *, use_originals=False):
    document = {'originals': summary} if use_originals else summary
    fingerprint = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    return 'sky-' + fingerprint[:16]


def dds_header(side):
    # Legacy uncompressed BGRA8, one base image per face in DDS face order.
    fields = (124, 0x100F, side, side, side * 4, 0, 0, *([0] * 11),
              32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000,
              0x1008, 0xFE00, 0, 0, 0)
    return b'DDS ' + struct.pack('<31I', *fields)


def verify_sky_build(root, items, name):
    if not isinstance(name, str) or not BUILD_NAME.fullmatch(name):
        raise ValueError('Choose a sky build owned by this texture batch.')
    target = root / name
    report = target / 'sky-report.json'
    output = target / 'SkyCube.dds'
    error = f'The sky build could not be verified at {target}.'
    if any(not path.is_file() or path.resolve(strict=True) != path for path in (report, output)):
        raise ValueError(error)
    receipt = json.loads(bounded(report, 32768))
    if (not isinstance(receipt, dict) or type(receipt.get('format')) is not int or receipt['format'] != 1
            or not isinstance(receipt.get('faces'), list) or len(receipt['faces']) != len(FACES)):
        raise ValueError(error)
    rows = receipt['faces']
    if any(not isinstance(row, dict) or set(row) != {'face', 'id', 'rotation', 'sha256'}
           or row['face'] != face for row, face in zip(rows, FACES)):
        raise ValueError(error)
    plan = {row['face']: {'id': row['id'], 'rotation': row['rotation']} for row in rows}
    use_originals = receipt.get('uses_originals', False)
    if type(use_originals) is not bool:
        raise ValueError(error)
    faces = collect_faces(root, items, plan, use_originals=use_originals)
    summary = approved_summary(root, items, plan, faces, use_originals=use_originals)
    side = faces['+X'][4]['size'][0]
    if (rows != summary or name != sky_build_name(summary, use_originals=use_originals) or type(receipt.get('size')) is not int
            or receipt['size'] != side or output.stat().st_size != 128 + 24 * side * side):
        raise ValueError(error)
    with output.open('rb') as stream:
        header = stream.read(128)
        if header != dds_header(side):
            raise ValueError(error)
        digest = hashlib.sha256(header)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    checksum = digest.hexdigest()
    if receipt.get('dds_sha256') != checksum:
        raise ValueError(error)
    if 'contact_sheet_sha256' in receipt:
        sheet = target / CONTACT_SHEET
        if not sheet.is_file() or sheet.resolve(strict=True) != sheet:
            raise ValueError(error)
        preview = bounded(sheet, MAX_CONTACT_SHEET_BYTES)
        if not preview.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(preview).hexdigest() != receipt['contact_sheet_sha256']:
            raise ValueError(error)
    return output, checksum
