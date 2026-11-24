"""Find user-supplied T2 model artwork and normalize it for Unreal intake."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
from zipfile import BadZipFile

from interior_geometry import material_slots
from mission_interiors import indexed_assets, normalized_path, pick_exact, read_asset, unique
from model_textures import MAX_BINDINGS, MAX_SIDE, MAX_TEXTURES, MAX_TEXTURE_BYTES, TEXTURE_INDEX, validate_png


EXTENSIONS = ('.png', '.jpg', '.jpeg', '.bmp', '.tga', '.dds')


def texture_asset(material, directory, sources, basenames):
    name = material.strip().replace('\\', '/').casefold()
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or ':' in name:
        raise ValueError('The source material name is not a relative texture reference.')
    if path.suffix.casefold() in EXTENSIONS:
        names = [path]
    else:
        names = [PurePosixPath(name + suffix) for suffix in EXTENSIONS]
    # Prefer the model's directory for a short name and the authored path for
    # qualified names before falling back to a unique name in the archives.
    root = PurePosixPath('.')
    parents = (root, directory) if len(path.parts) > 1 else (directory, root)
    candidates = [parent / item for parent in parents for item in names]
    candidates.extend(PurePosixPath('textures') / item.name for item in names)
    for item in dict.fromkeys(candidates):
        candidate = normalized_path(item.as_posix(), EXTENSIONS)
        asset = pick_exact(candidate, *sources)
        if asset:
            return asset
    for item in names:
        primary, secondary = basenames
        asset = unique(primary.get(item.name, ()), item.name)
        if asset is None:
            asset = unique(secondary.get(item.name, ()), item.name)
        if asset:
            return asset
    raise ValueError('Source texture was not found.')


def archive_basenames(index):
    result = {}
    for path, entries in index.items():
        result.setdefault(PurePosixPath(path).name, []).extend(entries)
    return result


def rgba_png(raw):
    from PIL import Image

    try:
        with Image.open(BytesIO(raw)) as image:
            if min(image.size) < 1 or max(image.size) > MAX_SIDE:
                raise ValueError('Source texture dimensions exceed the import limit.')
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Animated source textures require manual review.')
            result = BytesIO()
            with image.convert('RGBA') as pixels:
                pixels.save(result, format='PNG')
    except Image.DecompressionBombError as exc:
        raise ValueError('Source texture dimensions exceed the import limit.') from exc
    png = result.getvalue()
    if len(png) > 16 * 1024 * 1024:
        raise ValueError('The converted texture exceeds the intake file limit.')
    validate_png(png)
    return png


def collect_textures(source, files, groups):
    bindings, textures = {}, {}
    total, count = 0, 0
    for references, root in groups:
        if not references:
            continue
        with indexed_assets(source, root, EXTENSIONS) as sources:
            basenames = tuple(archive_basenames(index) for index in sources[:2])
            cache = {}
            for reference, filename in references.items():
                directory = PurePosixPath(reference.split(':', 1)[1]).parent
                slots = {}
                geometry = json.loads(files[filename])
                for slot in material_slots(geometry):
                    material = geometry['materials'][slot]
                    count += 1
                    if count > MAX_BINDINGS:
                        raise ValueError('This mission has too many model texture bindings.')
                    key = directory, material
                    if key not in cache:
                        try:
                            asset = texture_asset(material, directory, sources, basenames)
                            raw, name = read_asset(asset)
                            if not name.isprintable() or not 0 < len(name) <= 255:
                                raise ValueError('Unsupported source texture filename.')
                            png = rgba_png(raw)
                            target = 'texture_' + sha256(png).hexdigest()[:32] + '.png'
                            if target not in textures:
                                if len(textures) >= MAX_TEXTURES or total + len(png) > MAX_TEXTURE_BYTES:
                                    raise ValueError('The intake texture size limit was reached.')
                                textures[target] = png
                                total += len(png)
                            cache[key] = dict(file=target, source=name, source_sha256=sha256(raw).hexdigest())
                        except (ImportError, OSError, ValueError, RuntimeError, BadZipFile) as exc:
                            cache[key] = {'reason': (str(exc) or 'Source texture could not be decoded.')[:240]}
                    slots[str(slot)] = cache[key]
                if slots:
                    bindings[filename] = slots
    if textures:
        textures[TEXTURE_INDEX] = json.dumps(bindings, sort_keys=True, ensure_ascii=False).encode('utf8')
    return textures
