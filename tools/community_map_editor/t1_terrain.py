"""Read the Tribes GridFile, GridBlock and terrain volume formats."""

import json
import math
from pathlib import PurePosixPath
import struct

from intake_contract import bounded, finite, intake_file_limit
from minimap import grayscale_png
from t1_lzh import decode
from t1_materials import material_names
from terrain_grid import CELLS as SQUARES, VERTICES as GRID, legacy_holes, preview_samples


SCALE = 100
ARCHIVE_LIMIT = 64 * 1024 * 1024
MEMBER_LIMIT = 16 * 1024 * 1024
VOLUME_ENTRY = struct.Struct("<IIIIB")
FRAME = struct.Struct("<3I6f2i2f2I")
BLOCK = struct.Struct("<I16s2I2f2I")


def resource_name(value):
    name = PurePosixPath(value.replace("\\", "/"))
    if (name.is_absolute() or not name.parts or ".." in name.parts or ":" in value
            or len(value) > 255 or not value.isprintable()):
        raise ValueError("Invalid Tribes terrain resource name.")
    return name.as_posix()


def _size(data, offset, tag):
    if offset < 0 or offset + 8 > len(data) or data[offset:offset + 4] != tag:
        raise ValueError(f"Missing Tribes {tag.decode()} header.")
    return struct.unpack_from("<I", data, offset + 4)[0]


def _chunk(data, offset, tag, *, final=False):
    encoded = _size(data, offset, tag)
    size = encoded & 0x7fffffff
    start = offset + 8
    alignment = 4 if encoded & 0x80000000 else 2
    end = start + size
    following = start + ((size + alignment - 1) // alignment) * alignment
    if end > len(data) or following > len(data) and not (final and end == len(data)):
        raise ValueError(f"Truncated Tribes {tag.decode()} chunk.")
    return data[start:end], following


class Volume:
    def __init__(self, data):
        self.data = data
        self.entries = {}
        self.footer = _size(data, 0, b"PVOL")
        if self.footer < 8:
            raise ValueError("Invalid Tribes volume directory offset.")
        names, following = _chunk(data, self.footer, b"vols")
        index, _ = _chunk(data, following, b"voli", final=True)
        if len(index) % VOLUME_ENTRY.size or not 0 < len(index) // VOLUME_ENTRY.size <= 4096:
            raise ValueError("Invalid Tribes volume directory size.")
        for _, name_offset, offset, size, method in VOLUME_ENTRY.iter_unpack(index):
            if name_offset >= len(names) or (name_offset > 0 and names[name_offset - 1] != 0):
                raise ValueError("Invalid Tribes volume name offset.")
            end = names.find(b"\0", name_offset)
            if end < 0:
                raise ValueError("Unterminated Tribes volume resource name.")
            name = resource_name(names[name_offset:end].decode("latin-1"))
            key = name.casefold()
            if key in self.entries:
                raise ValueError(f"Duplicate Tribes volume resource {name}.")
            if not 8 <= offset <= self.footer - 8:
                raise ValueError(f"Invalid Tribes volume resource offset for {name}.")
            self.entries[key] = (name, offset, size, method)

    def find(self, name):
        key = resource_name(name).casefold()
        if key in self.entries:
            return self.entries[key][0]
        basename = PurePosixPath(key).name
        matches = [entry[0] for entry in self.entries.values() if PurePosixPath(entry[0]).name.casefold() == basename]
        if len(matches) > 1:
            raise ValueError(f"Several resources in the Tribes volume match {basename}.")
        return next(iter(matches), None)

    def read(self, name):
        name, offset, size, method = self.entries[name.casefold()]
        if size > MEMBER_LIMIT:
            raise ValueError(f"Tribes volume resource {name} exceeds the supported size.")
        encoded, following = _chunk(self.data, offset, b"VBLK")
        if following > self.footer:
            raise ValueError(f"Tribes volume resource {name} overlaps its directory.")
        if method == 0 and len(encoded) == size:
            return encoded
        if method == 3:
            decoded, consumed = decode(encoded, size)
            if len(encoded) - consumed <= 1:
                return decoded
        raise ValueError(f"Unsupported or invalid Tribes volume data for {name} (codec {method}).")


def volume_missions(path):
    volume = Volume(bounded(path, ARCHIVE_LIMIT))
    return sorted(entry[0] for entry in volume.entries.values() if entry[0].casefold().endswith(".mis"))


def _sized_file(data, tag):
    size = _size(data, 0, tag)
    if not 4 <= size <= len(data) - 8 or len(data) - size - 8 > 3:
        raise ValueError(f"Invalid Tribes {tag.decode()} data size.")
    return memoryview(data)[8:8 + size]


def _index(data):
    data = _sized_file(data, b"GFIL")
    version, name_size = struct.unpack_from("<2I", data) if len(data) >= 8 else (-1, 0)
    offset = 8 + name_size
    if version not in (0, 1) or name_size > 255 or len(data) < offset + FRAME.size + 4 * version:
        raise ValueError("Unsupported or truncated Tribes terrain index.")
    name = bytes(data[8:offset]).rstrip(b"\0").decode("latin-1")
    material_list = resource_name(name) if name else None
    _, details, shift, *rest = FRAME.unpack_from(data, offset)
    origin_x, origin_y, _, _, width, height = rest[6:]
    offset += FRAME.size + 4 * version
    if (details != 9 or shift > 7 or not 1 <= width <= 64 or not 1 <= height <= 64
            or len(data) < offset + 4 * width * height + 4):
        raise ValueError("Unsupported size or scale in the Tribes terrain index.")
    if (origin_x, origin_y) != (0, 0):
        raise ValueError("This Tribes terrain index uses a nonzero grid origin that requires review.")
    grid = struct.unpack_from(f"<{width * height}i", data, offset)
    offset += 4 * len(grid)
    count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    names = {}
    if not 0 < count <= 4096:
        raise ValueError("Invalid Tribes terrain block inventory size.")
    for _ in range(count):
        if offset + 8 > len(data):
            break
        ident, size = struct.unpack_from("<iI", data, offset)
        offset += 8
        if not 0 <= ident or ident in names or size > 255 or size > len(data) - offset:
            break
        name = bytes(data[offset:offset + size]).rstrip(b"\0").decode("latin-1")
        names[ident] = name
        offset += size
    if count != len(names) or not set(grid) <= names.keys() or offset != len(data):
        raise ValueError("Invalid Tribes terrain block inventory.")
    return 1 << shift, width, height, grid, material_list


def _block(data):
    data = _sized_file(data, b"GBLK")
    if len(data) < BLOCK.size:
        raise ValueError("Truncated Tribes terrain block.")
    version, _, details, _, low, high, width, height = BLOCK.unpack_from(data)
    if (version not in (0, 4, 5) or details != 9 or (width, height) != (SQUARES, SQUARES)
            or not all(finite(v, 2048) for v in (low, high)) or low > high):
        raise ValueError("Unsupported Tribes terrain block version or dimensions.")
    offset = BLOCK.size
    decoded = []
    for size in (GRID**2 * 4, SQUARES**2 * 2):
        if version == 0 and offset + size <= len(data):
            decoded.append(data[offset:offset + size])
            offset += size
        elif version != 0 and offset + 4 <= len(data) and struct.unpack_from("<I", data, offset)[0] == size:
            values, consumed = decode(data[offset + 4:], size)
            decoded.append(values)
            offset += 4 + consumed
        else:
            raise ValueError("Invalid height or material array in the Tribes terrain block.")
    heights, materials = decoded
    heights = list(struct.unpack(f"<{GRID**2}f", heights))
    if not all(finite(v, 2048) for v in heights):
        raise ValueError("The Tribes terrain block contains invalid heights.")
    return version, heights, bytes(materials)


def _playable_window(origin, step, width, height, bounds):
    if bounds is None:
        return range(width), range(height)
    span = SQUARES * step
    axes = []
    for start, count, (lo, hi) in zip(origin, (width, height), zip(*bounds)):
        if lo < start or hi > start + span * count:
            raise ValueError("The playable area extends outside the Tribes terrain grid.")
        first = min(count - 1, math.floor((lo - start) / span))
        end = max(first + 1, math.ceil((hi - start) / span))
        axes.append(range(first, end))
    return axes


def load_binary(path, reference, origin, bounds, *, volume=None, mission_volume=None):
    """Return the selected metric terrain, PNG preview and raw source receipts."""
    sources = []

    def read_file(file):
        data = bounded(file, ARCHIVE_LIMIT if file.suffix.casefold() in (".vol", ".ted") else MEMBER_LIMIT)
        sources.append((file.name, file.name, data))
        return data

    def resource(name):
        if volume is not None:
            found = volume.find(name)
            if found:
                data = volume.read(found)
                sources.append((found, f"{path.name}:{found}", data))
                return found, data
        candidate = path.with_name(PurePosixPath(name).name)
        return candidate.name, read_file(candidate)

    def load_materials(name):
        if name is None:
            return []
        archives = [(path.name, volume)] if volume is not None else []
        if mission_volume is not None:
            archives.append(mission_volume)
        for archive_name, archive in archives:
            if found := archive.find(name):
                data = archive.read(found)
                sources.append((found, f"{archive_name}:{found}", data))
                return material_names(data)
        basename = PurePosixPath(name).name
        stem = PurePosixPath(basename).stem.casefold()
        siblings = {file.name.casefold(): file for file in path.parent.iterdir() if file.is_file()}
        if loose := siblings.get(basename.casefold()):
            return material_names(read_file(loose))
        for candidate in (stem + "terrain.vol", stem + "dml.vol"):
            archive_path = siblings.get(candidate)
            if archive_path is None or archive_path == path:
                continue
            raw = bounded(archive_path, ARCHIVE_LIMIT)
            archive = Volume(raw)
            if found := archive.find(name):
                data = archive.read(found)
                sources.extend(((archive_path.name, archive_path.name, raw), (found, f"{archive_path.name}:{found}", data)))
                return material_names(data)
        return []

    suffix = path.suffix.casefold()
    if suffix in (".vol", ".ted"):
        volume = volume if volume is not None else Volume(read_file(path))
    reference = resource_name(reference) if reference else path.with_suffix(".dtf").name
    if PurePosixPath(reference).suffix.casefold() != ".dtf":
        raise ValueError("The SimTerrain source must name a Tribes .dtf terrain index.")
    if suffix == ".dtf":
        index_name, index = path.name, read_file(path)
    else:
        index_name, index = resource(reference)
    step, width, height, grid, material_list = _index(index)
    columns, rows = _playable_window(origin, step, width, height, bounds)
    span = step * SQUARES
    x, y, z = origin
    terrain = dict(origin_cm=[(x + columns.start * span) * SCALE, -(y + rows.start * span) * SCALE, z * SCALE],
                   step_cm=step * SCALE, cm_per_m=SCALE, y_sign=-1, material_layers=[])
    index_stem = str(PurePosixPath(index_name).with_suffix(""))
    block_slots = {}
    blocks, tiles = [], []
    material_slots = set()
    block_bytes = 0
    names = {grid[row * width + col]: f"{index_stem}#{grid[row * width + col]}.dtb"
             for row in rows for col in columns}
    if suffix == ".dtb" and path.name.casefold() not in {PurePosixPath(name).name.casefold() for name in names.values()}:
        raise ValueError("The selected Tribes terrain block is outside the playable area.")
    for row in rows:
        tile_row = []
        for col in columns:
            ident = grid[row * width + col]
            if ident not in block_slots:
                name = names[ident]
                if suffix == ".dtb" and path.name.casefold() == PurePosixPath(name).name.casefold():
                    data = read_file(path)
                else:
                    _, data = resource(name)
                version, heights, materials = _block(data)
                flags, indices = materials[::2], materials[1::2]
                material_slots.update(indices)
                block_slots[ident] = len(blocks)
                block = dict(height_m=heights, holes=legacy_holes(flags), legacy_grid=dict(
                    block_version=version, tile=[col, row], grid_size=[width, height], block_id=ident,
                    material_list=material_list, height_range_m=[min(heights), max(heights)],
                    material_flags=flags.hex(), material_indices=indices.hex()))
                block_bytes += len(json.dumps(block, allow_nan=False))
                if block_bytes > intake_file_limit("terrain.json", 13):
                    raise ValueError("The selected Tribes terrain exceeds the intake size limit.")
                blocks.append(block)
            tile_row.append(block_slots[ident])
        tiles.append(tile_row)
    names = load_materials(material_list)
    terrain.update(tile_grid=tiles, terrain_blocks=blocks, material_layers=[
        [slot, (names[slot] if slot < len(names) else None) or f"Material slot {slot}"]
        for slot in sorted(material_slots)])
    samples, px_width, px_height = preview_samples(terrain)
    pix = grayscale_png(samples, 16, px_width, px_height)
    return terrain, pix, sources
