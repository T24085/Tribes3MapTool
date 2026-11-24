"""Translate ambient DIF light loops into timed colors and atlas masks."""

import math


MAX_TRACKS_PER_ATLAS = 8
MAX_MASK_PIXELS = 8 * 1024 * 1024


def ambient_atlas_tracks(record):
    animation = record.get("animation", {})
    if "surface_rects" not in animation:
        return {}, ()
    masks = bytes.fromhex(animation["state_masks_hex"])
    rectangles = {row[0]: row[1:5] for row in animation["surface_rects"]}
    surfaces = {row[0]: row[3:5] for row in record["surfaces"]}
    atlases, warnings = {}, []
    for track_id, (_, start, count, flags, duration) in enumerate(animation["tracks"]):
        if flags not in (3, 11):
            warnings.append(f"Light track {track_id} uses a trigger or flicker that needs review.")
            continue
        role = int(bool(flags & 8))
        try:
            frames = loop_frames(animation, start, count, duration, rectangles, surfaces, masks, role)
        except ValueError as error:
            warnings.append(f"Light track {track_id}: {error}")
            continue
        for atlas, states in frames.items():
            tracks = atlases.setdefault((role, atlas), [])
            if len(tracks) == MAX_TRACKS_PER_ATLAS:
                warnings.append(f"Light track {track_id} exceeds the animated tracks supported per atlas.")
                continue
            tracks.append([duration, states])
    return atlases, warnings


def loop_frames(animation, start, count, duration, rectangles, surfaces, masks, role):
    states, data = animation["states"], animation["surface_data"]
    if not (0 <= start < start + count <= len(states) and count <= 64 and 0 < duration <= 86_400_000):
        raise ValueError("The loop state range or duration is unsupported.")
    previous_time, expected_slots, frames = -1, None, {}
    for red, green, blue, time, first, length in states[start:start + count]:
        color = [red, green, blue]
        if (not previous_time < time < duration or previous_time == -1 and time != 0
                or not all(0 <= channel <= 255 for channel in color) or not 0 <= first <= first + length <= len(data)):
            raise ValueError("The loop has invalid timing, color, or mask bindings.")
        previous_time = time
        bindings = {(surface, slot): offset for surface, offset, slot in data[first:first + length]}
        if expected_slots is not None and bindings.keys() != expected_slots:
            raise ValueError("The loop changes its set of state slots.")
        expected_slots = bindings.keys()
        per_atlas = {}
        for (surface, slot), offset in sorted(bindings.items()):
            atlas = surfaces[surface][role] if surface in surfaces else None
            if atlas is None:
                continue
            x, y, width, height = rectangles[surface]
            rows = per_atlas.setdefault(atlas, {})
            if offset != -1:
                if surface in rows:
                    raise ValueError("The loop uses more than one mask on a surface in the same track.")
                rows[surface] = [x, y, width, height, masks[offset:offset + width * height].hex()]
        for atlas, rows in per_atlas.items():
            frames.setdefault(atlas, []).append([time, color, list(rows.values())])
    return frames


def mask_texture(states, width, height):
    """Pack distinct masks for source keyframes into one texture, keeping UV1 intact."""
    from minimap import grayscale_png

    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise ValueError("Unsupported DIF animation atlas dimensions.")
    frames, tiles = {}, []
    for _, _, rectangles in states:
        key = tuple(map(tuple, rectangles))
        if key not in frames:
            frames[key] = len(frames)
        tiles.append(frames[key])
    columns = min(len(frames), 4096 // width)
    rows = math.ceil(len(frames) / columns)
    packed_width, packed_height = columns * width, rows * height
    if packed_height > 4096 or packed_width * packed_height > MAX_MASK_PIXELS:
        raise ValueError("DIF light masks exceed the generated texture size limit.")
    pixels = bytearray(packed_width * packed_height)
    for rectangles, tile in frames.items():
        column, row = tile % columns, tile // columns
        for x, y, patch_width, patch_height, encoded in rectangles:
            if x + patch_width > width or y + patch_height > height:
                raise ValueError("A DIF light mask rectangle falls outside the selected atlas.")
            mask = bytes.fromhex(encoded)
            for line in range(patch_height):
                target = (row * height + y + line) * packed_width + column * width + x
                start = line * patch_width
                pixels[target:target + patch_width] = mask[start:start + patch_width]
    return grayscale_png(pixels, 8, packed_width, packed_height), tiles, columns, rows
