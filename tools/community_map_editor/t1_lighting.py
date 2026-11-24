"""Decode Darkstar interior lightmaps and pack the default and alarm states."""

from collections import defaultdict
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import re
import struct

from interior_lighting import MAX_LIGHTMAP_BYTES, MAX_LIGHTMAPS
from t1_interior import Binary
from t1_lzh import decode as decode_lzh


ATLAS_SIDE = 512
ALARM_NAMES = {"alarm", "alarmlighting", "emergency", "emergencylighting", "nopower", "poweroff", "unpowered"}


class Lightmaps:
    """Bind version 6 and 7 DIL samples to a specific DIG build."""

    def __init__(self, raw, build, surface_count):
        reader = Binary(raw, b"PERS")
        length, = reader.unpack("<H")
        kind = bytes(reader.take(length))
        self.mission = kind == b"ITRMissionLighting"
        if not self.mission and kind != b"ITRLighting":
            raise ValueError("The DIL does not contain a supported lighting resource.")
        reader.take(length & 1)
        self.version = reader.uint()
        if self.version not in (6, 7):
            raise ValueError("Expected a version 6 or 7 Tribes DIL.")
        build_id, self.shift, slots, states, state_data, lights, surfaces, data_size = reader.unpack("<8I")
        if (build_id != build or self.shift > 16 or slots > 250_000
                or not 0 <= surfaces <= surface_count or not self.mission and surfaces != surface_count):
            raise ValueError("The DIL does not match the DIG build, surface count, or light scale.")
        self.slots = slots
        frames = reader.array("<4Hfhh", states)
        affected_surfaces = reader.array("<hhi", state_data)
        tracks = reader.array("<iiiifI", lights, 16_384)
        self.track_count = len(tracks)
        self.surfaces = reader.array("<Ihh4B", surfaces)
        self.data = reader.take(data_size)
        reader.take(reader.uint())
        compressed, = reader.unpack("<B")
        self.tree, self.leaves = [], []
        if compressed == 1:
            nodes, leaves = reader.unpack("<2I")
            self.tree = reader.array("<ii", nodes, 65_536)
            self.leaves = [value & 0xffff for value, in reader.array("<I", leaves, 65_536)]
            if not self.tree or not self.leaves:
                raise ValueError("The compressed DIL is missing its Huffman table.")
        if compressed not in (0, 1):
            raise ValueError("Invalid Tribes DIL trailer.")
        self.replacements = {}
        if self.mission:
            self.read_mission_index(reader)
        elif reader.offset != len(reader.data):
            raise ValueError("Invalid Tribes DIL trailer.")

        offsets = {offset & 0x3fffffff for offset, *_ in self.surfaces if offset < 0x80000000}
        offsets.update(offset for _, _, offset in affected_surfaces if offset >= 0)
        ordered = sorted(value for value in offsets if value < len(self.data))
        self.ends = dict(zip(ordered, ordered[1:] + [len(self.data)]))
        self.initial = {}
        for _, _, count, first, _, _ in tracks:
            if not 0 <= first < first + count <= len(frames):
                raise ValueError("A DIL track has an invalid frame range.")
            *colors, _, affected, start = frames[first]
            if not 0 <= affected <= len(affected_surfaces) or affected and not 0 <= start <= len(affected_surfaces) - affected:
                raise ValueError("A DIL frame has an invalid surface table.")
            for surface, slot, offset in affected_surfaces[start:start + affected] if affected else ():
                if not 0 <= surface < surfaces or not 0 <= slot < slots:
                    raise ValueError("A DIL frame has an invalid light slot.")
                self.initial[slot] = (offset, [value >> 8 for value in colors[:3]])

    def read_mission_index(self, reader):
        count = reader.uint()
        if count > len(self.surfaces):
            raise ValueError("Too many Tribes mission lightmap replacements.")
        packed = reader.take(len(reader.data) - reader.offset)
        if not count:
            if len(packed) > 3 or any(packed):
                raise ValueError("An empty mission lighting index has trailing data.")
            return
        raw, consumed = decode_lzh(packed, 8 * count)
        if len(packed) - consumed > 3:
            raise ValueError("Unexpected data after the mission lighting index.")
        by_offset = {row[0] & 0x3fffffff: row
                     for row in self.surfaces if row[0] < 0x80000000}
        for source, target in struct.iter_unpack("<II", raw):
            # Some source surfaces use a constant color instead of a data offset.
            if source >= 0x80000000:
                continue
            row = by_offset.get(target)
            if row is None or source in self.replacements and self.replacements[source] != row:
                raise ValueError("Invalid mission lighting replacement index.")
            self.replacements[source] = row

    def decode(self, offset, count):
        if offset >= 0x80000000:
            return [offset & 0xffff] * count
        start = offset & 0x3fffffff
        if start not in self.ends:
            raise ValueError("A DIL lightmap offset is outside its data.")
        chunk = self.data[start:self.ends[start]]
        if not offset & 0x40000000:
            if len(chunk) < 2 * count:
                raise ValueError("A DIL lightmap is truncated.")
            return [word for word, in struct.iter_unpack("<H", chunk[:count * 2])]
        if not self.tree:
            raise ValueError("A DIL compressed lightmap has no Huffman table.")
        values = []
        bit = 0
        for _ in range(count):
            node = 0
            for _ in range(64):
                if not 0 <= node < len(self.tree) or bit >= len(chunk) * 8:
                    raise ValueError("A DIL lightmap has an invalid Huffman stream.")
                one = (chunk[bit // 8] >> (bit % 8)) & 1
                node = self.tree[node][1 - one]
                bit += 1
                if node < 0:
                    leaf = -node - 1
                    if leaf >= len(self.leaves):
                        raise ValueError("A DIL Huffman branch points to a missing color.")
                    values.append(self.leaves[leaf])
                    break
            else:
                raise ValueError("A DIL Huffman tree exceeds the depth limit.")
        return values

    def surface(self, index, overlay=None):
        offset, count, first, width, height, x, y = self.surfaces[index]
        if (not width or not height or count < 0
                or count and not 0 <= first < first + count <= self.slots):
            raise ValueError("A DIL surface has invalid lightmap dimensions or slots.")
        area = width * height
        pixels = bytearray()
        replacement = overlay.replacements.get(offset & 0x3fffffff) if overlay and offset < 0x80000000 else None
        if replacement is None:
            values = self.decode(offset, area)
        else:
            if replacement[3:] != (width, height, x, y):
                raise ValueError("Mission and base lighting dimensions or offsets disagree.")
            values = overlay.decode(replacement[0], area)
        for word in values:
            pixels.extend(((word >> 8) & 15, (word >> 4) & 15, word & 15, 255))
        for slot in range(first, first + count):
            source = self.initial.get(slot)
            if not source or source[0] < 0 or not any(source[1]):
                continue
            start, color = source
            if start not in self.ends or self.ends[start] - start < area:
                raise ValueError("A DIL initial light mask is truncated.")
            for index, intensity in enumerate(self.data[start:start + area]):
                if intensity >= 16:
                    pixel = index * 4
                    for channel, value in enumerate(color):
                        pixels[pixel + channel] = min(15, pixels[pixel + channel] + (value * intensity >> 12))
        for pixel in range(0, len(pixels), 4):
            for channel in range(3):
                pixels[pixel + channel] *= 17
        return width, height, x, y, bytes(pixels)


def _requested_state(label):
    return "".join(char for char in label.casefold() if char.isalnum())


def _pages(patches):
    """Pack padded samples deterministically, one image at a time."""
    from PIL import Image

    placed = []
    image = Image.new("RGBA", (ATLAS_SIDE, ATLAS_SIDE))
    x = y = row_height = right = 0
    for surface, width, height, pixels, projection in patches:
        if x + width + 2 > ATLAS_SIDE:
            x, y, row_height = 0, y + row_height, 0
        if y + height + 2 > ATLAS_SIDE:
            yield image.crop((0, 0, 1 << (right - 1).bit_length(), 1 << (y + row_height - 1).bit_length())), placed
            image = Image.new("RGBA", (ATLAS_SIDE, ATLAS_SIDE))
            placed, x, y, row_height, right = [], 0, 0, 0, 0
        patch = Image.frombytes("RGBA", (width, height), pixels)
        image.paste(patch, (x + 1, y + 1))
        image.paste(patch.crop((0, 0, 1, height)), (x, y + 1))
        image.paste(patch.crop((width - 1, 0, width, height)), (x + width + 1, y + 1))
        image.paste(image.crop((x, y + 1, x + width + 2, y + 2)), (x, y))
        image.paste(image.crop((x, y + height, x + width + 2, y + height + 1)), (x, y + height + 1))
        placed.append((surface, x + 1, y + 1, projection))
        x += width + 2
        right = max(right, x)
        row_height = max(row_height, height + 2)
    if placed:
        yield image.crop((0, 0, 1 << (right - 1).bit_length(), 1 << (y + row_height - 1).bit_length())), placed


def _patches(lightmaps, ranges, review, state, overlay=None):
    stride = 1 << lightmaps.shift
    area = 0
    for surface, _, _, ox, oy, limits in ranges:
        # Darkstar stores the lightmap offset separately from the base bitmap offset.
        _, _, _, width, height, x, y = lightmaps.surfaces[surface]
        if (not width or not height or any(lo + off < -stride or hi + off > size * stride
                                          for (lo, hi), off, size in zip(limits, (x, y), (width, height)))):
            if len(review) < 256:
                review.append(f"{state}, surface {surface}: source lightmap bounds do not cover the face."[:240])
            continue
        area += width * height
        if area * 4 > MAX_LIGHTMAP_BYTES // 2:
            raise ValueError("The DIL exceeds the supported decoded lightmap size.")
        try:
            _, _, _, _, pixels = lightmaps.surface(surface, overlay)
        except ValueError as error:
            if len(review) < 256:
                review.append(f"{state}, surface {surface}: {error}"[:240])
            continue
        yield surface, width, height, pixels, (1 / stride, 1 / stride, (x - ox) / stride + .5, (y - oy) / stride + .5)


def archive_lights(sources, geometry, source_hash, resolve):
    build, surface_count, ranges = geometry
    alarm = next((index for index, (label, _, _) in enumerate(sources[1:], 1)
                  if _requested_state(label) in ALARM_NAMES), None)
    states, atlases, files, receipts, review = [], [], {}, [], []
    projections = defaultdict(dict)
    size = 0
    for index, (label, filename, issue) in enumerate(sources):
        role = "normal" if index == 0 else "alarm" if index == alarm else "review"
        state = dict(label=label, role=role, reference=PurePosixPath(filename.replace("\\", "/")).name if filename else None)
        states.append(state)
        if issue:
            review.append(f"{label}: {issue}"[:240])
            continue
        if role == "review":
            review.append(f"{label}: alternate light state needs an Unreal state binding."[:240])
            continue
        try:
            name, origin, raw = resolve(filename)
            lightmaps = Lightmaps(raw, build, surface_count)
            source = dict(name=PurePosixPath(name).name, origin=origin, sha256=sha256(raw).hexdigest())
            overlay = None
            if lightmaps.mission:
                base_reference = re.sub(r"(-[0-9]+)-[^-]+\.dil$", r"\1.dil", filename, flags=re.I)
                if base_reference == filename:
                    raise ValueError("The mission DIL name does not identify its base lightmap.")
                base_name, base_origin, base_raw = resolve(base_reference)
                overlay = lightmaps
                lightmaps = Lightmaps(base_raw, build, surface_count)
                if lightmaps.mission or lightmaps.shift != overlay.shift:
                    raise ValueError("The mission and base DIL resources disagree.")
                base = dict(name=PurePosixPath(base_name).name, origin=base_origin, sha256=sha256(base_raw).hexdigest())
                receipts.append(base)
                state.update(base_source=base["name"], base_sha256=base["sha256"])
            receipts.append(source)
            state.update(source=source["name"], sha256=source["sha256"], version=(overlay or lightmaps).version,
                         shift=lightmaps.shift, light_tracks=lightmaps.track_count)
            if lightmaps.track_count:
                review.append(f"{label}: initial light frames baked. Timed light changes need review."[:240])
            patches = _patches(lightmaps, ranges, review, label, overlay)
            for page, placed in _pages(patches):
                png = BytesIO()
                page.save(png, format="PNG")
                data = png.getvalue()
                target = f"lightmap_{sha256(data).hexdigest()[:32]}.png"
                if len(atlases) >= MAX_LIGHTMAPS or target not in files and size + len(data) > MAX_LIGHTMAP_BYTES:
                    raise ValueError("The DIL reached the intake lightmap limits.")
                if target not in files:
                    size += len(data)
                    files[target] = data
                atlas = len(atlases)
                atlases.append({"color": dict(file=target, source_sha256=source["sha256"])})
                for surface, x, y, (sx, sy, bx, by) in placed:
                    projections[surface][role] = (atlas, [sx / page.width, sy / page.height,
                                                           (x + bx) / page.width, (y + by) / page.height])
        except (ImportError, OSError, ValueError) as error:
            review.append(f"{label}: {error}"[:240])
    surfaces = []
    for surface, first, count, *_ in ranges:
        binding = projections.get(surface)
        if binding is None:
            continue
        normal, normal_uv = binding.get("normal", (None, None))
        emergency, alarm_uv = binding.get("alarm", (None, None))
        surfaces.append([surface, first, count, normal, emergency, [normal_uv, alarm_uv]])
    return dict(source_sha256=source_hash, has_alarm=alarm is not None,
                animated_lights=sum(state.get("light_tracks", 0) for state in states),
                atlases=atlases, surfaces=surfaces, review=review, legacy_states=states), files, receipts
