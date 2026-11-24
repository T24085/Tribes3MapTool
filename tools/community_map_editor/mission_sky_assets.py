"""Read neutral sky artwork from user-supplied T2 content."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import struct
from zipfile import BadZipFile

from mission_interiors import indexed_assets, pick_exact, read_asset, unique
from mission_textures import EXTENSIONS, archive_basenames, rgba_png, texture_asset
from sky_intake import MAX_SKIES, MAX_SKY_BYTES, MAX_SKY_SLOTS, SKY_INDEX, relative_name


def material_names(data):
    if not data or len(data) > 4096:
        raise ValueError('Invalid sky material-list size.')
    if data[0] == 1:
        if len(data) < 5:
            raise ValueError('The binary material list is truncated.')
        count, = struct.unpack_from('<I', data, 1)
        if not 1 <= count <= MAX_SKY_SLOTS:
            raise ValueError('Invalid material-list slot count.')
        offset, names = 5, []
        for _ in range(count):
            if offset >= len(data):
                raise ValueError('The binary material list is truncated.')
            end = offset + 1 + data[offset]
            if end > len(data):
                raise ValueError('The binary material list is truncated.')
            names.append(data[offset + 1:end].decode('cp1252'))
            offset = end
        if offset != len(data):
            raise ValueError('Unexpected binary material-list data.')
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('cp1252')
        names = text.splitlines()
        while names and not names[-1].strip():
            names.pop()
    names = [name.strip().replace('\\', '/') for name in names]
    if not 1 <= len(names) <= MAX_SKY_SLOTS or not all(relative_name(name) for name in names):
        raise ValueError('Invalid sky material-list texture paths.')
    return names


def material_asset(reference, sources, basenames):
    name = reference.casefold()
    basename = PurePosixPath(name).name
    primary, secondary, loose = sources
    found = (unique(primary.get(name, ()), name) or unique(basenames[0].get(basename, ()), basename)
             or pick_exact(name, {}, {}, loose))
    if found:
        return found
    for root in loose:
        skies = (root / 'skies').resolve()
        if not skies.is_dir() or not skies.is_relative_to(root):
            continue
        found = unique([path for path in skies.rglob('*') if path.name.casefold() == basename
                        and path.is_file() and path.resolve().is_relative_to(skies)], basename)
        if found:
            return found.resolve()
    return unique(secondary.get(name, ()), name) or unique(basenames[1].get(basename, ()), basename)


def texture_scope(asset, sources):
    if isinstance(asset, Path):
        return PurePosixPath('.'), (sources[0], sources[1], [asset.parent, *sources[2]])
    entry = asset[1]
    path = entry if isinstance(entry, str) else entry.filename
    return PurePosixPath(path.replace('\\', '/')).parent, sources


def collect_sky_artwork(source, asset_root, weather):
    sources_wanted = [(row['source_id'], row['values']['material_list']) for row in weather['objects']
                      if row['class'] == 'sky' and 'material_list' in row['values']
                      and relative_name(row['values']['material_list'])]
    if not sources_wanted:
        return {}
    if len(sources_wanted) > MAX_SKIES:
        raise ValueError('Too many sky material lists for one map intake.')
    records, files = [], {}
    total = 0
    with indexed_assets(source, asset_root, ('.dml', *EXTENSIONS)) as sources:
        basenames = tuple(archive_basenames(index) for index in sources[:2])
        for source_id, reference in sources_wanted:
            try:
                asset = material_asset(reference, sources, basenames)
                if not asset:
                    continue
                raw, _ = read_asset(asset)
                names = material_names(raw)
            except (OSError, ValueError, BadZipFile):
                # A mission can still be reviewed without resolvable sky artwork.
                continue
            directory, scoped = texture_scope(asset, sources)
            slots = []
            records.append({'source_id': source_id, 'material_list': reference,
                            'source_sha256': sha256(raw).hexdigest(), 'slots': slots})
            for name in names:
                try:
                    texture = texture_asset(name, directory, scoped, basenames)
                    raw_texture, _ = read_asset(texture)
                    png = rgba_png(raw_texture)
                except (ImportError, OSError, ValueError, RuntimeError, BadZipFile) as error:
                    message = str(error)
                    reason = ('missing' if message == 'Source texture was not found.'
                              else 'ambiguous' if message.startswith('Multiple ') else 'unreadable')
                    slots.append({'reference': name, 'reason': reason})
                    continue
                filename = 'sky_' + sha256(png).hexdigest()[:32] + '.png'
                if filename not in files:
                    if total + len(png) > MAX_SKY_BYTES:
                        slots.append({'reference': name, 'reason': 'capacity'})
                        continue
                    files[filename] = png
                    total += len(png)
                slots.append({'reference': name, 'file': filename, 'source_sha256': sha256(raw_texture).hexdigest()})
    if records:
        files[SKY_INDEX] = json.dumps({'format': 1, 'skies': records}, ensure_ascii=False).encode('utf-8')
    return files
