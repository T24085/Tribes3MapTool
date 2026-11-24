"""Private, resumable PNG texture batches for a single Unreal map draft."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
from tempfile import NamedTemporaryFile, TemporaryDirectory
import time
import uuid
import zlib

from intake_contract import bounded
from source_profiles import ANY_DRAFT


MAX_DIMENSION = 8192
MAX_TEXTURE_BYTES = 128 * 1024 * 1024
MAX_ITEMS = 32
ITEM = re.compile(r'T_[0-9]{3}')
ASSET = re.compile(r'/Game/Community/(?:[A-Za-z0-9_]+/)*([A-Za-z0-9_]+)\.\1')
DRAFT = re.compile(ANY_DRAFT)
BATCH = re.compile(r'batch-[0-9a-f]{12}')
ROLES = {'color', 'normal', 'masks', 'sky'}


def save_json(path, document):
    with NamedTemporaryFile(dir=path.parent, delete=False, mode='w', encoding='utf-8') as stream:
        temporary = Path(stream.name)
        json.dump(document, stream, indent=2)
        stream.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def texture_png(path):
    if not path.is_file() or path.resolve(strict=True) != path or path.stat().st_size > MAX_TEXTURE_BYTES:
        raise ValueError('Expected a regular PNG up to 128 MiB.')
    with path.open('rb') as stream:
        header = stream.read(33)
        if len(header) != 33 or header[:16] != bytes.fromhex('89504e470d0a1a0a0000000d49484452'):
            raise ValueError('Expected a PNG header.')
        width, height, depth, color, compression, filters, interlace = struct.unpack('>IIBBBBB', header[16:29])
        if (not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION or depth != 8
                or color not in (0, 2, 4, 6) or (compression, filters, interlace) != (0, 0, 0)
                or zlib.crc32(header[12:29]) != int.from_bytes(header[29:33], 'big')):
            raise ValueError('Expected a non-interlaced 8-bit grayscale or RGB(A) PNG up to 8192×8192.')
        digest = hashlib.sha256(header)
        length = len(header)
        while chunk := stream.read(1024 * 1024):
            length += len(chunk)
            if length > MAX_TEXTURE_BYTES:
                raise ValueError('Texture exceeds 128 MiB.')
            digest.update(chunk)
    return {'sha256': digest.hexdigest(), 'size': [width, height], 'channels': color}


def replacement_png(source, target, replacement):
    result = texture_png(replacement)
    if result['size'] != target:
        raise ValueError(f'Expected {target[0]}×{target[1]} pixels.')
    if result['channels'] != source['channels']:
        raise ValueError('Match the original grayscale/RGB format and alpha channel.')
    if source['sha256'] == result['sha256']:
        raise ValueError('The replacement is identical to the source.')
    return result


def draft_folder(library, draft):
    if not isinstance(draft, str) or not DRAFT.fullmatch(draft):
        raise ValueError('Open a supported map editor draft first.')
    library = Path(library).resolve()
    folder = library / draft
    folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve(strict=True) != folder:
        raise ValueError('The draft texture library must not link outside its directory.')
    return folder


def create_batch(library, draft, entries, export, *, targets=None, replacements=()):
    """Create all originals and their manifest before making a batch visible."""
    if not 1 <= len(entries) <= MAX_ITEMS:
        raise ValueError('Select between 1 and 32 textures.')
    parent = draft_folder(library, draft)
    batch_id = 'batch-' + uuid.uuid4().hex[:12]
    target = parent / batch_id
    with TemporaryDirectory(dir=parent) as work:
        root = Path(work) / 'batch'
        (root / 'originals').mkdir(parents=True)
        (root / 'replacements').mkdir()
        items = []
        for index, (asset, role, notes, handle) in enumerate(entries, 1):
            if not ASSET.fullmatch(asset) or role not in ROLES:
                raise ValueError('Use a texture from the Community content folder.')
            ident = f'T_{index:03}'
            filename = root / 'originals' / (ident + '.png')
            export(handle, filename)
            source = texture_png(filename)
            width, height = source['size']
            factor = min(2, MAX_DIMENSION / max(width, height))
            dimensions = targets[index - 1] if targets is not None else [round(width * factor), round(height * factor)]
            items.append({'id': ident, 'asset': asset, 'role': role, 'notes': notes, 'source': source,
                          'target': dimensions})
        if replacements:
            for row, candidate in zip(items, replacements, strict=True):
                destination = root / 'replacements' / (row['id'] + '.png')
                shutil.copyfile(candidate, destination)
                replacement_png(row['source'], row['target'], destination)
        save_json(root / 'manifest.json', {'format': 1, 'draft': draft, 'batch': batch_id, 'items': items})
        for attempt in range(4):
            try:
                root.rename(target)
                break
            except PermissionError as error:
                if (getattr(error, 'winerror', None) not in (5, 32) or attempt == 3
                        or target.exists() or target.is_symlink()):
                    raise
                time.sleep(.05 * (attempt + 1))
    return target


def load_batch(library, draft, name):
    parent = draft_folder(library, draft)
    if not isinstance(name, str) or not BATCH.fullmatch(name):
        raise ValueError('Choose a texture batch owned by this draft.')
    root = parent / name
    if not root.is_dir() or root.resolve(strict=True) != root:
        raise ValueError('Choose a texture batch owned by this draft.')
    path = root / 'manifest.json'
    if not path.is_file() or path.resolve(strict=True) != path:
        raise ValueError('A regular texture batch manifest is required.')
    header = json.loads(bounded(path, 65536))
    if (not isinstance(header, dict) or set(header) != {'format', 'draft', 'batch', 'items'}
            or type(header['format']) is not int or header['format'] != 1
            or header['draft'] != draft or header['batch'] != name
            or not isinstance(header['items'], list) or not 1 <= len(header['items']) <= MAX_ITEMS):
        raise ValueError('Invalid texture batch manifest.')
    items = {}
    for row in header['items']:
        if (not isinstance(row, dict) or set(row) != {'id', 'asset', 'role', 'notes', 'source', 'target'}
                or not isinstance(row['id'], str) or not ITEM.fullmatch(row['id']) or row['id'] in items
                or not isinstance(row['asset'], str) or not ASSET.fullmatch(row['asset'])
                or not isinstance(row['role'], str) or row['role'] not in ROLES
                or not isinstance(row['notes'], str) or len(row['notes']) > 300
                or not isinstance(row['target'], list) or len(row['target']) != 2
                or any(type(value) is not int or not 1 <= value <= MAX_DIMENSION for value in row['target'])):
            raise ValueError('Invalid texture manifest entry.')
        items[row['id']] = row
    return root, items


def original_png(root, row):
    ident = row['id']
    original = root / 'originals' / (ident + '.png')
    if texture_png(original) != row['source']:
        raise ValueError('The original PNG changed after export.')
    return original


def inspect_replacement(root, row):
    ident = row['id']
    replacement = root / 'replacements' / (ident + '.png')
    if not replacement.exists():
        raise FileNotFoundError('Place the edited PNG in replacements with the same file name.')
    return replacement, replacement_png(row['source'], row['target'], replacement)


def inspect(root, row):
    return original_png(root, row), *inspect_replacement(root, row)


def revision_inputs(root, row):
    original = original_png(root, row)
    try:
        base, _ = inspect_replacement(root, row)
    except FileNotFoundError:
        base = original
    return original, base


def save_revision(library, draft, root, row, original, image):
    with TemporaryDirectory(dir=root) as work:
        candidate = Path(work) / 'texture.png'
        image.save(candidate, format='PNG')
        entries = [(row['asset'], row['role'], row['notes'], original)]
        return create_batch(library, draft, entries, shutil.copyfile,
                            targets=[row['target']], replacements=[candidate])


def make_color_revision(library, draft, root, row, *, strength, tint=None, reference=None):
    """Save a color-adjusted candidate into a new batch for normal review."""
    from PIL import Image, ImageStat

    if row['role'] != 'color' or row['source']['channels'] not in (2, 6):
        raise ValueError('Color adjustments require an RGB or RGBA color texture.')
    if type(strength) is not int or not 1 <= strength <= 100 or (tint is None) == (reference is None):
        raise ValueError('Choose one color target and a strength between 1 and 100.')
    original, base = revision_inputs(root, row)

    def color_channels(path):
        with Image.open(path) as image:
            if getattr(image, 'is_animated', False):
                raise ValueError('Use a single image for color adjustment.')
            return image.convert('RGB'), image.getchannel('A') if 'A' in image.getbands() else None

    def mean(color, alpha):
        if alpha is not None and alpha.getbbox() is None:
            raise ValueError('Color matching needs some visible pixels.')
        return ImageStat.Stat(color, mask=alpha).mean

    if reference is not None:
        reference = Path(reference).resolve(strict=True)
        texture_png(reference)
        target = mean(*color_channels(reference))
    else:
        if not isinstance(tint, (tuple, list)) or len(tint) != 3 or any(
                type(channel) is not int or not 0 <= channel <= 255 for channel in tint):
            raise ValueError('Choose a valid RGB tint color.')
        target = tint

    color, alpha = color_channels(base)
    offsets = [round((after - before) * strength / 100)
               for before, after in zip(mean(color, alpha), target, strict=True)]
    if not any(offsets):
        raise ValueError('This adjustment would leave the average color unchanged.')
    size = tuple(row['target'])
    if color.size != size:
        if alpha is not None:
            color.putalpha(alpha)
        color = color.resize(size, Image.Resampling.LANCZOS)
        alpha = color.getchannel('A') if alpha is not None else None
        color = color.convert('RGB')
    adjusted = color.point([max(0, min(255, value + offset)) for offset in offsets for value in range(256)])
    if alpha is not None:
        adjusted.putalpha(alpha)
    return save_revision(library, draft, root, row, original, adjusted)


def decisions(root, items):
    path = root / 'review.json'
    if not path.exists():
        return {}
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('The texture review must be a regular file.')
    data = json.loads(bounded(path, 16384))
    if not isinstance(data, dict) or not data.keys() <= items.keys():
        raise ValueError('Invalid texture review file.')
    for entry in data.values():
        if (not isinstance(entry, dict) or set(entry) != {'status', 'sha256'}
                or entry['status'] not in ('approved', 'rejected')
                or not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])):
            raise ValueError('Invalid texture review decision.')
    return data


def approved_replacements(root, items):
    for ident, choice in decisions(root, items).items():
        if choice['status'] == 'approved':
            row = items[ident]
            _, path, current = inspect(root, row)
            if current['sha256'] != choice['sha256']:
                raise ValueError(f'{ident} changed after approval. Review it again.')
            yield row, path, current['sha256']


def retry_batch(library, draft, root, items):
    selected = decisions(root, items)
    entries, targets = [], []
    for ident, row in items.items():
        original = root / 'originals' / (ident + '.png')
        if texture_png(original) != row['source']:
            raise ValueError(f'{ident} original changed after export.')
        try:
            valid = {'status': 'approved', 'sha256': inspect(root, row)[2]['sha256']}
            if selected.get(ident) == valid:
                continue
        except (OSError, ValueError):
            pass
        entries.append((row['asset'], row['role'], row['notes'], original))
        targets.append(row['target'])
    if not entries:
        raise ValueError('No unresolved textures to retry.')
    return create_batch(library, draft, entries, shutil.copyfile, targets=targets)
