"""Translate Darkstar's indexed animation tables to portable clip and mesh data."""

import math
import struct

from intake_contract import finite


MAX_SAMPLES = 200_000


def source_pose(row):
    length = math.hypot(*row[:4])
    if not math.isfinite(length) or not length or not all(finite(value, 20_000) for value in row[4:]):
        raise ValueError("Invalid Darkstar animation transform.")
    return [*(part / length for part in row[:4]), *row[4:]]


def pose_basis(row):
    x, y, z, w, *position = source_pose(row)
    return (
        ((1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)),
         (2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)),
         (2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y))),
        position,
    )


def transform(point, basis):
    matrix, position = basis
    return tuple(sum(value * matrix[k][i] for k, value in enumerate(point)) + position[i] for i in range(3))


def compose(local, parent):
    axes, position = local
    return tuple(transform(axis, (parent[0], (0, 0, 0))) for axis in axes), transform(position, parent)


def index_slice(rows, start, count):
    if not count:
        return ()
    if not 0 <= start < len(rows) or not 0 < count <= len(rows) - start:
        raise ValueError("Invalid Darkstar animation table reference.")
    return rows[start:start + count]


def name_at(names, index):
    if not 0 <= index < len(names):
        raise ValueError("A Darkstar animation record references a missing name.")
    # Names are display text and do not participate in index binding.
    return "".join(char for char in names[index] if char.isprintable())


def shape_motion(version, sequences, subsequences, keys, poses, names, nodes, objects,
                 transitions, triggers, meshes, attachments):
    if not (subsequences or transitions or triggers):
        return None
    if len(subsequences) + len(transitions) + len(triggers) + len(keys) > MAX_SAMPLES:
        raise ValueError("The Darkstar animation tables exceed the intake limits.")
    clips = [dict(name=name_at(names, name), duration_s=duration, loop=bool(loop), priority=priority,
                  nodes=[], objects=[], material_swaps=[],
                  triggers=[[abs(phase), value, phase >= 0]
                            for phase, value in index_slice(triggers, first, count)])
             for name, loop, duration, priority, first, count, _, _ in sequences]
    bits = 12 if version == 8 else 28
    mask = (1 << bits) - 1
    track_keys = 0

    def read_keys(first, count):
        nonlocal track_keys
        track_keys += count
        if track_keys > MAX_SAMPLES:
            raise ValueError("The Darkstar animation tracks exceed the intake limits.")
        return index_slice(keys, first, count)

    for index, (_, _, count, first, _) in enumerate(nodes):
        for seq, length, start in index_slice(subsequences, first, count):
            if not 0 <= seq < len(clips):
                raise ValueError("A Darkstar node track references a missing sequence.")
            track = []
            for phase, pose, flags in read_keys(start, length):
                if not 0 <= pose < len(poses):
                    raise ValueError("A Darkstar node key references a missing pose.")
                visibility = bool(flags >> bits & 8) if flags >> bits & 4 else None
                track.append([phase, source_pose(poses[pose]), visibility])
            clips[seq]["nodes"].append([index, track])
    mesh_ids = sorted({objects[index][2] for index in attachments})
    mesh_index = {index: target for target, index in enumerate(mesh_ids)}
    instances = []
    for index, (render, collide) in attachments.items():
        name, flags, mesh, node, x, y, z, count, first = objects[index]
        target = len(instances)
        instances.append(dict(name=name_at(names, name), node=node, mesh=mesh_index[mesh],
                              offset_m=[x, y, z], visible=not flags & 1, render=render, collide=collide))
        for seq, length, start in index_slice(subsequences, first, count):
            if not 0 <= seq < len(clips):
                raise ValueError("A Darkstar object track references a missing sequence.")
            track = []
            for phase, frame, flags in read_keys(start, length):
                flags, material = flags >> bits, flags & mask
                track.append([phase, frame if flags & 1 else None,
                              bool(flags & 8) if flags & 4 else None,
                              material if flags & 2 else None])
            clips[seq]["objects"].append([target, track])
    for index, (_, _, _, _, _, _, count, first) in enumerate(sequences):
        for _, length, start in index_slice(subsequences, first, count):
            clips[index]["material_swaps"].append(
                [[phase, flags & mask, material] for phase, material, flags in read_keys(start, length)])
    if not transitions and not any(clip["nodes"] or clip["objects"] or clip["material_swaps"] or clip["triggers"]
                                   for clip in clips):
        return None
    payloads = []
    samples = 0
    for index in mesh_ids:
        mesh = meshes[index]
        samples += mesh.vertex_count * len(mesh.frames) + mesh.uv_count * mesh.uv_frames + len(mesh.faces) // 28
        if samples > MAX_SAMPLES:
            raise ValueError("The Darkstar animation mesh samples exceed the intake limit.")
        payloads.append(dict(
            frames=[mesh.positions(frame) for frame in range(len(mesh.frames))],
            uv_frames=[[list(pair) for pair in mesh.texels(frame)] for frame in range(mesh.uv_frames)],
            faces=[list(face) for face in struct.iter_unpack("<6Ii", mesh.faces)]))
    return dict(
        nodes=[dict(name=name_at(names, name), parent=parent, pose=source_pose(poses[base]))
               for name, parent, _, _, base in nodes],
        objects=instances, meshes=payloads, clips=clips,
        transitions=[[start, end, start_phase, end_phase, seconds, source_pose(pose)]
                     for start, end, start_phase, end_phase, seconds, *pose in transitions])


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def vector(value, size, limit):
    return isinstance(value, list) and len(value) == size and all(finite(part, limit) for part in value)


def valid_pose(pose):
    return (isinstance(pose, list) and len(pose) == 7 and vector(pose[:4], 4, 1)
            and abs(math.hypot(*pose[:4]) - 1) <= 1e-6 and vector(pose[4:], 3, 20_000))


def fields(record, *names):
    return isinstance(record, dict) and record.keys() == set(names)


def label(value):
    return isinstance(value, str) and len(value) <= 24 and (not value or value.isprintable())


def phase(value):
    return finite(value, 1) and value >= 0


def valid_keys(rows, size):
    return (isinstance(rows, list) and 0 < len(rows) <= MAX_SAMPLES
            and all(isinstance(row, list) and len(row) == size and phase(row[0]) for row in rows)
            and all(left[0] <= right[0] for left, right in zip(rows, rows[1:])))


def validate_motion(motion, sequences):
    if not fields(motion, "nodes", "objects", "meshes", "clips", "transitions"):
        raise ValueError("Invalid DTS animation fields.")
    nodes, objects, meshes, clips, transitions = (motion[key] for key in
                                                 ("nodes", "objects", "meshes", "clips", "transitions"))
    if (any(not isinstance(rows, list) or not 1 <= len(rows) <= 8192 for rows in (nodes, objects, meshes))
            or not isinstance(clips, list) or len(clips) != sequences
            or not isinstance(transitions, list) or len(transitions) > MAX_SAMPLES):
        raise ValueError("Invalid DTS animation table sizes.")
    for index, node in enumerate(nodes):
        if (not fields(node, "name", "parent", "pose") or not label(node["name"])
                or not integer(node["parent"], -1, index - 1) or not valid_pose(node["pose"])):
            raise ValueError("Invalid DTS animation node.")
    for obj in objects:
        if (not fields(obj, "name", "node", "mesh", "offset_m", "visible", "render", "collide")
                or not label(obj["name"]) or not integer(obj["node"], 0, len(nodes) - 1)
                or not integer(obj["mesh"], 0, len(meshes) - 1) or not vector(obj["offset_m"], 3, 20_000)
                or any(type(obj[key]) is not bool for key in ("visible", "render", "collide"))
                or not (obj["render"] or obj["collide"])):
            raise ValueError("Invalid DTS animation attachment.")
    samples = 0
    for mesh in meshes:
        if (not fields(mesh, "frames", "uv_frames", "faces")
                or not all(isinstance(mesh[key], list) for key in mesh)
                or min(len(mesh["frames"]), len(mesh["uv_frames"])) == 0
                or max(len(mesh[key]) for key in mesh) > MAX_SAMPLES):
            raise ValueError("Invalid DTS animation mesh tables.")
        for key, width, low, limit in (("frames", 3, 3, 20_000), ("uv_frames", 2, 0, 1e6)):
            frames = mesh[key]
            if not isinstance(frames[0], list) or not low <= len(frames[0]) <= MAX_SAMPLES:
                raise ValueError("Invalid DTS animation frame dimensions.")
            samples += len(frames) * len(frames[0])
            if samples > MAX_SAMPLES or any(not isinstance(row, list) or len(row) != len(frames[0])
                                            or not all(vector(part, width, limit) for part in row) for row in frames):
                raise ValueError("Invalid DTS animation frame samples.")
        points, texels = len(mesh["frames"][0]), len(mesh["uv_frames"][0])
        samples += len(mesh["faces"])
        if samples > MAX_SAMPLES or any(
                not isinstance(face, list) or len(face) != 7
                or not all(integer(index, 0, points - 1) for index in face[:6:2])
                or not all(integer(index, 0, 2**32 - 1) for index in face[1:6:2])
                or texels and face[-1] != -1 and any(index >= texels for index in face[1:6:2])
                or not integer(face[-1], -1, 255) for face in mesh["faces"]):
            raise ValueError("Invalid DTS animation face.")
    samples = 0
    for clip in clips:
        if (not fields(clip, "name", "duration_s", "loop", "priority", "nodes", "objects", "material_swaps", "triggers")
                or not label(clip["name"]) or not finite(clip["duration_s"], 1e6) or clip["duration_s"] < 0
                or not integer(clip["priority"], -2**31, 2**31 - 1) or type(clip["loop"]) is not bool
                or any(not isinstance(clip[key], list) for key in ("nodes", "objects", "material_swaps", "triggers"))):
            raise ValueError("Invalid DTS animation clip.")
        for kind, targets, length in (("nodes", nodes, 3), ("objects", objects, 4)):
            seen = set()
            for track in clip[kind]:
                if (not isinstance(track, list) or len(track) != 2
                        or not integer(track[0], 0, len(targets) - 1) or track[0] in seen
                        or not valid_keys(track[1], length)):
                    raise ValueError("Invalid or duplicate DTS animation track.")
                target, keys = track
                seen.add(target)
                samples += len(keys)
                mesh = meshes[objects[target]["mesh"]] if kind == "objects" else None
                for key in keys:
                    if key[2] is not None and type(key[2]) is not bool:
                        raise ValueError("Invalid DTS animation visibility key.")
                    if mesh is None:
                        if not valid_pose(key[1]):
                            raise ValueError("Invalid DTS animation pose key.")
                    elif ((key[1] is not None and not integer(key[1], 0, len(mesh["frames"]) - 1))
                          or (key[3] is not None and not integer(key[3], 0, len(mesh["uv_frames"]) - 1))):
                        raise ValueError("A DTS animation key references a missing mesh frame.")
        for track in clip["material_swaps"]:
            if not valid_keys(track, 3) or not all(integer(row[1], 0, 4095) and integer(row[2], 0, 65535)
                                                   for row in track):
                raise ValueError("Invalid DTS material swap track.")
            samples += len(track)
        for row in clip["triggers"]:
            if (not isinstance(row, list) or len(row) != 3 or not phase(row[0])
                    or not integer(row[1], -2**31, 2**31 - 1) or type(row[2]) is not bool):
                raise ValueError("Invalid DTS animation frame trigger.")
        samples += len(clip["triggers"])
        if samples > MAX_SAMPLES:
            raise ValueError("Too many DTS animation keys.")
    for row in transitions:
        if (not isinstance(row, list) or len(row) != 6 or not all(integer(index, 0, len(clips) - 1) for index in row[:2])
                or not all(phase(value) for value in row[2:4]) or not finite(row[4], 1e6) or row[4] < 0
                or not valid_pose(row[5])):
            raise ValueError("Invalid DTS animation transition.")
    if not transitions and not any(clip["nodes"] or clip["objects"] or clip["material_swaps"] or clip["triggers"]
                                   for clip in clips):
        raise ValueError("DTS animation contains no authored keys or triggers.")
