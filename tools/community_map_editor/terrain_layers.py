"""Resolve source terrain paint to the editor's PBR palette."""

import hashlib
from pathlib import PurePosixPath
from intake_contract import bounded
from native_terrain_paint import decode_mask
from terrain_art import SOURCE_ART


PALETTE_SURFACES = ('Grass', 'Rock', 'Forest')
ART_SLOT_OFFSET = len(PALETTE_SURFACES)
# Unknown source names use rock until the author assigns a replacement.
FALLBACK = 'Rock'
SIZE = 256


def suggested_surface(name):
    path = PurePosixPath(name.lower().replace('\\', '/'))
    for stem in (path.suffix[1:], path.stem):
        for surface, terms in (
            ('Forest', ('forest', 'moss', 'leaf', 'woodland')),
            ('Grass', ('grass', 'meadow', 'lush')),
            ('Rock', ('rock', 'stone', 'cliff', 'gravel')),
        ):
            if any(term in stem for term in terms):
                return surface
    return None


def layer_choices(terrain, overrides=None, *, artwork=()):
    rows = terrain.get('material_layers', [])
    limit = 256 if 'tile_grid' in terrain else 8
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError('Invalid terrain layer table.')
    overrides = {} if overrides is None else overrides
    slots = set()
    choices = []
    for row in rows:
        if (not isinstance(row, list) or len(row) != 2 or type(row[0]) is not int
                or not 0 <= row[0] < limit or row[0] in slots or not isinstance(row[1], str)
                or not 1 <= len(row[1]) <= 255 or not row[1].isprintable()):
            raise ValueError('Invalid or duplicate terrain layer.')
        slot, name = row
        slots.add(slot)
        choices.append((slot, name))
    if (not isinstance(overrides, dict) or set(overrides) - {str(slot) for slot in slots}
            or any(surface not in PALETTE_SURFACES for surface in overrides.values())):
        raise ValueError('Terrain layer assignments must use this intake and the editor palette.')
    return [(slot, name, overrides.get(str(slot)) or (SOURCE_ART if slot in artwork else suggested_surface(name)))
            for slot, name in choices]


def painted_cells(block, choices):
    """Project each original cell to a palette surface index."""
    fallback = PALETTE_SURFACES.index(FALLBACK)
    palette = [fallback] * 256
    for slot, _, surface in choices:
        if surface == SOURCE_ART:
            palette[slot] = ART_SLOT_OFFSET + slot
        elif surface is not None:
            palette[slot] = PALETTE_SURFACES.index(surface)
    indices = bytes.fromhex(block['legacy_grid']['material_indices'])
    return indices.translate(bytes(palette)) if max(palette) < 256 else [palette[index] for index in indices]


def weight_png(raw):
    """Read the fixed 256-square, 8-bit grayscale masks emitted by the T2 reader."""
    return decode_mask(raw, size=(SIZE, SIZE))[2]


def load_weights(folder, inventory, choices):
    def read(name):
        if name not in inventory:
            return None
        source = (folder / name).resolve(strict=True)
        if source.parent != folder:
            raise ValueError(f'Terrain weight file is outside the intake: {name}')
        raw = bounded(source, 512 * 1024)
        if hashlib.sha256(raw).hexdigest() != inventory[name]:
            raise ValueError(f'Terrain weight file changed after intake verification: {name}')
        return weight_png(raw)

    channels = {}
    grouped_masks = [[] for _ in PALETTE_SURFACES]
    for slot, _, surface in choices:
        channel = PALETTE_SURFACES.index(surface or FALLBACK)
        channels[slot] = channel
        mask = read(f'material-{slot}.png')
        if mask is not None:
            grouped_masks[channel].append(mask)
    indices = read('material-index.png')
    fallback = PALETTE_SURFACES.index(FALLBACK)
    pure = tuple(tuple(float(channel == index) for channel in range(3)) for index in range(3))
    weights, used = [], set()
    for index in range(SIZE * SIZE):
        raw = [sum(mask[index] for mask in masks) for masks in grouped_masks]
        total = sum(raw)
        if total:
            weights.append(tuple(weight / total for weight in raw))
            used.update(channel for channel, weight in enumerate(raw) if weight)
        else:
            channel = channels.get(indices[index], fallback) if indices is not None else fallback
            weights.append(pure[channel])
            used.add(channel)
    return weights, tuple(sorted(used))
