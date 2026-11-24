"""Review six oriented sky faces and assemble the approved DDS cubemap."""

from collections import defaultdict
import hashlib
import io
import json
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps

from intake_contract import bounded
from sky_contract import (
    CONTACT_SHEET, FACES, MAX_CONTACT_SHEET_BYTES, approved_summary, collect_faces, dds_header,
    face_plan, sky_build_name, verify_sky_build,
)
from texture_batches import MAX_TEXTURE_BYTES, save_json


MEAN_WARNING = 8
PEAK_WARNING = 28


def infer_faces(items):
    candidates = defaultdict(list)
    axes = {'p': '+', 'pos': '+', 'positive': '+', 'n': '-', 'neg': '-', 'negative': '-'}
    for ident, row in items.items():
        name = row['asset'].rsplit('/', 1)[-1].split('.')[0]
        match = re.search(r'_(p|pos|positive|n|neg|negative)([xyz])$', name, re.IGNORECASE)
        if match:
            sign, axis = match.groups()
            candidates[axes[sign.lower()] + axis.upper()].append(ident)
    return {face: {'id': ids[0], 'rotation': 0} for face, ids in candidates.items() if len(ids) == 1}


def load_plan(root, items):
    path = root / 'sky-faces.json'
    if not path.exists():
        return infer_faces(items)
    if not path.is_file() or path.resolve(strict=True) != path:
        raise ValueError('Expected a regular sky face plan.')
    return face_plan(json.loads(bounded(path, 4096)), items)


def direction(face, x, y):
    """Face axes with X increasing right and Y increasing down in the PNG."""
    return {
        '+X': (1, -y, -x), '-X': (-1, -y, x), '+Y': (x, 1, y),
        '-Y': (x, -1, -y), '+Z': (x, -y, 1), '-Z': (-x, -y, -1),
    }[face]


def edge_pairs():
    edges = defaultdict(list)
    for face in FACES:
        for label, (start, end) in {
            'top': ((-1, -1), (1, -1)), 'right': ((1, -1), (1, 1)),
            'bottom': ((-1, 1), (1, 1)), 'left': ((-1, -1), (-1, 1)),
        }.items():
            corners = (direction(face, *start), direction(face, *end))
            edges[tuple(sorted(corners))].append((face, label, corners))
    return tuple(((a[0], a[1]), (b[0], b[1]), a[2] != b[2]) for a, b in edges.values())


EDGES = edge_pairs()


def decode_face(path, digest, rotation):
    raw = bounded(path, MAX_TEXTURE_BYTES)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f'{path.name} changed during sky assembly.')
    with Image.open(io.BytesIO(raw)) as source:
        source.load()
        image = source.convert('RGBA')
    turn = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}
    if rotation:
        rotated = image.transpose(turn[rotation])
        image.close()
        image = rotated
    return image


def seam_report(faces, *, replacement):
    ribbons = {}
    samples = min(min(entry[4 if replacement else 3]['size']) for entry in faces.values())
    for face, (rotation, original, revised, source, current) in faces.items():
        path, metadata = (revised, current) if replacement else (original, source)
        with decode_face(path, metadata['sha256'], rotation) as image:
            width, height = image.size
            for side, box in {
                'top': (0, 0, width, 1), 'right': (width - 1, 0, width, height),
                'bottom': (0, height - 1, width, height), 'left': (0, 0, 1, height),
            }.items():
                with image.crop(box) as edge:
                    wanted = (samples, 1) if side in ('top', 'bottom') else (1, samples)
                    with edge.resize(wanted, Image.Resampling.BILINEAR) as strip:
                        pixels = strip.tobytes()
                        ribbons[(face, side)] = tuple(zip(pixels[0::4], pixels[1::4], pixels[2::4], pixels[3::4]))
    result = []
    for left, right, reverse in EDGES:
        other = ribbons[right][::-1] if reverse else ribbons[right]
        differences = [abs(a - b) for one, two in zip(ribbons[left], other) for a, b in zip(one, two)]
        mean, peak = sum(differences) / len(differences), max(differences)
        result.append({'first': ' '.join(left), 'second': ' '.join(right),
                       'mean': round(mean, 2), 'peak': peak,
                       'review': mean > MEAN_WARNING or peak > PEAK_WARNING})
    return result


def save_contact_sheet(path, faces, *, use_originals):
    tile, margin, gap, header = 192, 20, 12, 43
    pair = 2 * tile + gap
    card_height = header + tile + margin
    with Image.new('RGB', (3 * pair + 4 * margin, 2 * card_height + margin), '#101922') as sheet:
        canvas = ImageDraw.Draw(sheet)
        for index, (face, (rotation, original, approved, source, current)) in enumerate(faces.items()):
            row, column = divmod(index, 3)
            left = margin + column * (pair + margin)
            top = margin + row * card_height
            canvas.text((left, top), f'{face}  |  {rotation} deg clockwise', fill='#ecf4fc')
            for slot, (label, filename, info) in enumerate((
                ('Original', original, source), ('Cubemap input' if use_originals else 'Approved', approved, current),
            )):
                x, y = left + slot * (tile + gap), top + header
                canvas.text((x, y - 19), label, fill='#b3c6d8')
                canvas.rectangle((x, y, x + tile - 1, y + tile - 1), fill='#223142')
                with decode_face(filename, info['sha256'], rotation) as image:
                    with ImageOps.contain(image, (tile, tile), Image.Resampling.LANCZOS) as preview:
                        sheet.paste(preview, (x + (tile - preview.width) // 2, y + (tile - preview.height) // 2), preview)
        sheet.save(path, format='PNG')
    return hashlib.sha256(bounded(path, MAX_CONTACT_SHEET_BYTES)).hexdigest()


def build_cube(root, items, plan, *, use_originals=False):
    faces = collect_faces(root, items, plan, use_originals=use_originals)
    summary = approved_summary(root, items, plan, faces, use_originals=use_originals)
    side = faces['+X'][4]['size'][0]
    target = root / sky_build_name(summary, use_originals=use_originals)
    output_bytes = 128 + 24 * side * side + MAX_CONTACT_SHEET_BYTES
    if target.exists():
        verify_sky_build(root, items, target.name)
        return target
    if shutil.disk_usage(root).free < output_bytes:
        raise ValueError('There is not enough space to write the uncompressed sky cubemap.')
    seams = seam_report(faces, replacement=True)
    with TemporaryDirectory(dir=root) as work:
        staged = Path(work) / 'sky'
        staged.mkdir()
        digest = hashlib.sha256()
        with (staged / 'SkyCube.dds').open('xb') as output:
            header = dds_header(side)
            output.write(header)
            digest.update(header)
            for rotation, _, path, _, info in faces.values():
                with decode_face(path, info['sha256'], rotation) as image:
                    for y in range(0, side, 64):
                        with image.crop((0, y, side, min(y + 64, side))) as rows:
                            pixels = rows.tobytes('raw', 'BGRA')
                            output.write(pixels)
                            digest.update(pixels)
        save_json(staged / 'sky-report.json', {
            'format': 1, 'size': side, 'faces': summary, 'seams': seams, 'dds_sha256': digest.hexdigest(),
            'uses_originals': use_originals,
            'contact_sheet_sha256': save_contact_sheet(staged / CONTACT_SHEET, faces, use_originals=use_originals),
        })
        staged.rename(target)
    return target
