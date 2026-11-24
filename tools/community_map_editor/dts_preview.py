"""Sample a stored Darkstar clip in the intake's coordinate frame."""

from bisect import bisect_right
import math
import struct

from t1_dts_motion import compose, phase, pose_basis, transform


def interpolate(left, right, fraction):
    """Interpolate a translation and follow the short arc between unit quaternions."""
    a, b = left[:4], right[:4]
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-value for value in b], -dot
    if dot > .9995:
        weights = 1 - fraction, fraction
    else:
        angle = math.acos(max(0, min(1, dot)))
        length = math.sin(angle)
        weights = math.sin((1 - fraction) * angle) / length, math.sin(fraction * angle) / length
    return [*(x * weights[0] + y * weights[1] for x, y in zip(a, b)),
            *(x + (y - x) * fraction for x, y in zip(left[4:], right[4:]))]


class Track:
    def __init__(self, rows):
        self.rows = rows
        self.positions = [row[0] for row in rows]

    def step(self, progress, loop):
        index = bisect_right(self.positions, progress) - 1
        return self.rows[index if loop else max(0, index)]

    def pose(self, progress, loop):
        index = bisect_right(self.positions, progress)
        if not loop and index == 0:
            return self.rows[0][1:]
        if not loop and index == len(self.rows):
            return self.rows[-1][1:]
        left, right = self.rows[index - 1], self.rows[index % len(self.rows)]
        if len(self.rows) == 1 or left[0] == progress:
            return left[1:]
        lo = left[0] - 1 if index == 0 else left[0]
        hi = right[0] + 1 if index == len(self.rows) else right[0]
        fraction = (progress - lo) / (hi - lo) if hi > lo else 0
        return interpolate(left[1], right[1], fraction), left[2]


class ClipSampler:
    def __init__(self, model, index):
        self.motion = model['animation']
        self.materials = model['materials']
        clip = self.motion['clips'][index]
        self.loop = clip['loop']
        self.duration = clip['duration_s']
        self.node_tracks = {index: Track(rows) for index, rows in clip['nodes']}
        self.object_tracks = {index: Track(rows) for index, rows in clip['objects']}
        self.swaps = [Track(rows) for rows in clip['material_swaps']]

    def sample(self, position):
        if not phase(position):
            raise ValueError('Choose a frame from 0 to 100 percent.')
        bases, visibility = [], []
        for index, node in enumerate(self.motion['nodes']):
            track = self.node_tracks.get(index)
            pose, visible = track.pose(position, self.loop) if track else (node['pose'], None)
            basis = pose_basis(pose)
            parent = max(node['parent'], 0)
            bases.append(compose(basis, bases[parent]) if index else basis)
            visibility.append(visible is not False and (not index or visibility[parent]))
        swaps = dict(track.step(position, self.loop)[1:] for track in self.swaps)
        vertices, triangles = [], []
        for index, entry in enumerate(self.motion['objects']):
            if not entry['render'] or not visibility[entry['node']]:
                continue
            track = self.object_tracks.get(index)
            frame, visible, uv_frame = track.step(position, self.loop)[1:] if track else (None, None, None)
            if not (entry['visible'] if visible is None else visible):
                continue
            mesh = self.motion['meshes'][entry['mesh']]
            points, texels = mesh['frames'][frame or 0], mesh['uv_frames'][uv_frame or 0]
            start = len(vertices)
            basis = bases[entry['node']]
            offset = entry['offset_m']
            vertices.extend(transform([a + b for a, b in zip(point, offset)], basis) for point in points)
            for a, u, b, v, c, w, slot in mesh['faces']:
                if slot < 0 or slot >= len(self.materials) or len({a, b, c}) != 3:
                    continue
                material = swaps.get(slot, slot)
                if material >= len(self.materials):
                    material = slot
                uv = [*texels[u], *texels[w], *texels[v]] if texels else [0] * 6
                triangles.append([material, start + a, start + c, start + b, *uv])
        return dict(format=6, vertices_m=vertices, triangles=triangles, materials=self.materials)


def bake_times(clip):
    duration = clip['duration_s']
    if not 0 < duration <= 600:
        raise ValueError('Bake a DTS clip with a duration between 0 and 600 seconds.')
    samples = math.ceil(duration * 30)
    if samples >= 4096:
        raise ValueError('This DTS clip exceeds the 4,096-frame bake limit at 30 frames per second.')
    phases = {frame / samples for frame in range(samples + 1)}
    tracks = [rows for _, rows in clip['nodes'] + clip['objects']] + clip['material_swaps']
    phases.update(key[0] for rows in tracks for key in rows)
    # The native track indexes float seconds. Keep the last exact source phase at a shared time.
    times = {struct.unpack('<f', struct.pack('<f', value * duration))[0]: value for value in sorted(phases)}
    if len(times) > 4096 or len(times) < 2:
        raise ValueError('The authored keys cannot fit in the Geometry Cache timeline.')
    return list(times.items())
