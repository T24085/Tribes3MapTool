"""Resolve mission interior and static shape assets from T2 content."""

from collections import defaultdict
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from intake_contract import MAX_INTAKE_INTERIORS, MAX_INTAKE_INTERIOR_BYTES, bounded
from interior_geometry import MAX_GEOMETRY_BYTES
from interior_lighting import LIGHTING_INDEX, MAX_LIGHTING_INDEX, MAX_LIGHTMAP_BYTES, MAX_LIGHTMAPS, dif_lighting_key
from mesh_mappings import source_reference
from t2_dif_reader import decode_bytes, geometry_bytes, load_decoder


def normalized_path(value, extension):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in value or not path.name.casefold().endswith(extension):
        return None
    return path.as_posix().casefold()


def archive_index(archive, extension):
    index = defaultdict(list)
    for entry in archive.infolist():
        name = normalized_path(entry.filename, extension)
        if name and not entry.is_dir():
            index[name].append((archive, entry))
    return index


def loose_asset(root, name):
    path = root.joinpath(*PurePosixPath(name).parts)
    if not path.is_file():
        path = root
        for part in PurePosixPath(name).parts:
            if not path.is_dir():
                return None
            path = unique([entry for entry in path.iterdir() if entry.name.casefold() == part], name)
            if path is None or not path.resolve().is_relative_to(root):
                return None
        if not path.is_file():
            return None
    path = path.resolve(strict=True)
    return path if path.is_relative_to(root) else None


def unique(candidates, model_path):
    if len(candidates) > 1:
        raise ValueError(f'Multiple {PurePosixPath(model_path).suffix[1:].upper()} files match this reference.')
    return candidates[0] if candidates else None


def pick_exact(name, primary, secondary, loose_roots):
    asset = unique(primary.get(name, ()), name)
    if asset:
        return asset
    for root in loose_roots:
        path = loose_asset(root, name)
        if path:
            return path
    return unique(secondary.get(name, ()), name)


def pick(name, primary, secondary, loose_roots):
    asset = pick_exact(name, primary, secondary, loose_roots)
    if asset is not None:
        return asset
    # Loose mission kits and some VL2 files put models at the archive root.
    return pick_exact(PurePosixPath(name).name, primary, secondary, loose_roots)


def read_asset(asset, *, limit=MAX_GEOMETRY_BYTES):
    if isinstance(asset, Path):
        return bounded(asset, limit), asset.name
    owner, entry = asset
    if isinstance(owner, Path):
        with ZipFile(owner) as archive:
            return read_asset((archive, archive.getinfo(entry)), limit=limit)
    if entry.file_size > limit:
        raise ValueError('The referenced asset exceeds the supported size.')
    with owner.open(entry) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('The referenced asset exceeds the supported size.')
    return raw, PurePosixPath(entry.filename.replace('\\', '/')).name


@contextmanager
def indexed_assets(source, asset_root, extension):
    loose_roots = [source.parent, source.parent.parent]
    archives = []
    if asset_root is not None:
        if asset_root.is_dir():
            loose_roots.append(asset_root)
            archives = sorted(path for path in asset_root.iterdir() if path.is_file()
                              and path.suffix.casefold() == '.vl2' and path.resolve().is_relative_to(asset_root))
        elif asset_root.is_file() and asset_root.suffix.casefold() == '.vl2':
            archives = [asset_root]
        else:
            raise ValueError('Select a Tribes 2 content folder or VL2 asset archive.')
    with ExitStack() as stack:
        primary = (archive_index(stack.enter_context(ZipFile(source)), extension)
                   if source.suffix.casefold() == '.vl2' else {})
        secondary = defaultdict(list)
        for path in archives:
            if path == source:
                continue
            with ZipFile(path) as archive:
                for key, matches in archive_index(archive, extension).items():
                    secondary[key].extend((path, entry.filename) for _, entry in matches)
        yield primary, secondary, loose_roots


def collect_interiors(rows, source, decoder_file, asset_root):
    references = {reference for row in rows if (reference := source_reference(row))
                  and reference.startswith('interiorinstance:')}
    if not references:
        return {}, {}, {}
    decoder = load_decoder(decoder_file)
    return collect_geometry(references, source, asset_root, '.dif', 'interior',
                            lambda raw, name: decode_bytes(raw, name, decoder, include_lighting=True))


def collect_shapes(rows, source, decoder_folder, asset_root):
    from t2_dts_reader import decode_bytes as decode_dts, load_decoder as load_dts

    references = {reference for row in rows if (reference := source_reference(row))
                  and reference.startswith(('tsstatic:', 'staticshape:')) and reference.endswith('.dts')}
    if not references:
        return {}, {}, {}
    decoder = load_dts(decoder_folder)
    return collect_geometry(references, source, asset_root, '.dts', 'shape',
                            lambda raw, name: (decode_dts(raw, name, decoder), ()))


def collect_geometry(references, source, asset_root, extension, prefix, decode):
    if len(references) > MAX_INTAKE_INTERIORS:
        raise ValueError('This mission has too many unique model references for one intake.')
    data, found, review, illumination, atlases = {}, {}, {}, {}, {}
    with indexed_assets(source, asset_root, extension) as (primary, secondary, loose_roots):
        total = atlas_bytes = 0
        lighting_bytes = 2
        for reference in sorted(references):
            try:
                name = normalized_path(reference.split(':', 1)[1], extension)
                asset = pick(name, primary, secondary, loose_roots) if name else None
                if asset is None:
                    raise ValueError(f'Referenced {extension[1:].upper()} was not found.')
                raw, basename = read_asset(asset)
                mesh, records = decode(raw, basename)
                geometry = geometry_bytes(mesh)
                if total + len(geometry) > MAX_INTAKE_INTERIOR_BYTES:
                    raise ValueError('The intake has reached the decoded model size limit.')
            except (OSError, ValueError, RuntimeError, BadZipFile) as error:
                review[reference] = str(error)[:240] or 'The referenced model could not be decoded.'
                continue
            filename = f'{prefix}-{len(data) + 1:04d}.json'
            data[filename] = geometry
            found[reference] = filename
            total += len(geometry)
            for position, lighting, images in records:
                new_images = {name: png for name, png in images.items() if name not in atlases}
                new_bytes = sum(map(len, new_images.values()))
                encoded = json.dumps(lighting, separators=(',', ':'), allow_nan=False).encode()
                binding = dif_lighting_key(filename, position)
                key = json.dumps(binding).encode() + b':'
                if (len(atlases) + len(new_images) > MAX_LIGHTMAPS
                        or atlas_bytes + new_bytes > MAX_LIGHTMAP_BYTES
                        or lighting_bytes + len(key) + len(encoded) + 1 > MAX_LIGHTING_INDEX - 64 * 1024):
                    lighting = {**lighting, 'atlases': [], 'surfaces': [],
                                'review': ['Draft reached the embedded lightmap inventory limit.']}
                    lighting.pop('animation', None)
                    encoded = json.dumps(lighting, separators=(',', ':')).encode()
                    new_images, new_bytes = {}, 0
                if lighting_bytes + len(key) + len(encoded) + 1 > MAX_LIGHTING_INDEX:
                    raise ValueError('The intake exceeds the lighting review size limit.')
                illumination[binding] = key + encoded
                atlases.update(new_images)
                atlas_bytes += new_bytes
                lighting_bytes += len(key) + len(encoded) + 1
    if illumination:
        data[LIGHTING_INDEX] = b'{' + b','.join(illumination.values()) + b'}'
        data.update(atlases)
    return found, review, data
