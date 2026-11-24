"""Read meshes, authored collision and animation from Darkstar DTS shapes."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import struct

from intake_contract import finite
from interior_geometry import animated_only_slots, validate_geometry
from t1_interior import Binary
from t1_materials import material_names
from t1_dts_motion import compose, pose_basis, shape_motion, transform


NONVISUAL = re.compile(r"(?:bounds?|collision|col|loscol(?:lision)?|debris|hulk)(?:[\s_-]*[0-9]+)?\Z", re.I)
MAX_PARTS = 8192


def record(stream, kind):
    token, size = stream.unpack("<4sI")
    if token != b"PERS":
        raise ValueError("Expected a Darkstar PERS record.")
    width = 4 if size & 0x80000000 else 2
    length = size & 0x7fffffff
    raw = stream.take(length)
    padding = -length % width
    if padding and stream.offset < len(stream.data):
        stream.take(padding)
    row = Binary(raw)
    count, = row.unpack("<H")
    if count != len(kind) or bytes(row.take(count)) != kind:
        raise ValueError(f"Expected DTS record {kind.decode()}.")
    row.take(count & 1)
    return row, struct.pack("<4sI", token, length) + raw


@dataclass(frozen=True)
class CelMesh:
    packed: memoryview
    vertex_count: int
    uv: memoryview
    uv_count: int
    faces: memoryview
    frames: tuple

    def positions(self, index):
        first, *basis = self.frames[index]
        raw = self.packed[4 * first:4 * (first + self.vertex_count)]
        return [[value * scale + origin for value, scale, origin in zip(row[:3], basis[:3], basis[3:])]
                for row in struct.iter_unpack("<4B", raw)]

    def texels(self, index):
        start = index * self.uv_count * 8
        return list(struct.iter_unpack("<2f", self.uv[start:start + self.uv_count * 8]))

    @property
    def uv_frames(self):
        return len(self.uv) // (8 * self.uv_count) if self.uv_count else 1


def cel_mesh(reader):
    version = reader.uint()
    if version not in (1, 2, 3):
        raise ValueError("Unsupported Darkstar cel mesh version.")
    vertices, frame_vertices, texels, faces, frames = reader.unpack("<5I")
    frame_texels = reader.uint() if version >= 2 else texels
    older = reader.unpack("<6f") if version < 3 else ()
    reader.take(4)  # Radius
    if (not 3 <= frame_vertices <= min(vertices, 150_000) or vertices > 2_000_000 or texels > 2_000_000
            or frame_texels > texels or faces > 250_000 or frames > 65_536 or not frames and version == 3):
        raise ValueError("Invalid Darkstar cel mesh dimensions.")
    packed = reader.take(4 * vertices)
    uv = reader.take(8 * texels)
    face_rows = reader.take(28 * faces)
    if version == 3:
        frame_data = tuple(reader.array("<I6f", frames))
    else:
        frame_data = tuple((start, *older) for start, in reader.array("<I", frames)) or ((0, *older),)
    if (reader.offset != len(reader.data) or any(first + frame_vertices > vertices
            or not all(finite(value, 20_000) for value in basis) for first, *basis in frame_data)):
        raise ValueError("Invalid Darkstar cel frame bounds.")
    return CelMesh(packed, frame_vertices, uv, frame_texels, face_rows, frame_data)


def world_nodes(nodes, poses, names):
    world, parents = [], []
    for index, (name, parent, _, _, pose) in enumerate(nodes):
        if not 0 <= name < len(names) or not -1 <= parent < index or not 0 <= pose < len(poses):
            raise ValueError("A DTS node refers to an invalid parent, transform, or name.")
        local = pose_basis(poses[pose])  # Darkstar 1.x upgrades older transforms with scale disabled.
        parents.append(parent)
        world.append(compose(local, world[max(parent, 0)]) if index else local)
    return world, parents


def render_objects(details, always, nodes, names, parents, objects, mesh_count):
    if not -1 <= always < len(nodes):
        raise ValueError("Invalid DTS always-visible node.")
    if any(not 0 <= root < len(nodes) or not finite(size, 1e6) for root, size in details):
        raise ValueError("Invalid DTS detail table.")
    node_names = [names[row[0]] for row in nodes]
    choices = [(-size, index, root) for index, (root, size) in enumerate(details)
               if size >= 0 and not NONVISUAL.fullmatch(node_names[root])]
    if choices:
        _, lod, root = min(choices)
    else:
        lod, root = 0, (0 if not details else -1)
    roots = {node for node in (root, always) if node >= 0}
    # Darkstar StaticBase uses the last detail rooted at the node named collision.
    collision_node = next((i for i, name in enumerate(node_names) if name.casefold() == "collision"), None)
    collision_detail = next((i for i in reversed(range(len(details))) if details[i][0] == collision_node), None)
    collision_root = details[collision_detail][0] if collision_detail is not None else None
    active, collidable = [], []
    for index, parent in enumerate(parents):
        active.append(not NONVISUAL.fullmatch(node_names[index])
                      and (index in roots or parent >= 0 and active[parent]))
        collidable.append(index == collision_root or parent >= 0 and collidable[parent])
    visible, collision = [], []
    attachments = {}
    for index, (name, flags, mesh, node, x, y, z, _, _) in enumerate(objects):
        if not 0 <= name < len(names) or not 0 <= mesh < mesh_count or not 0 <= node < len(nodes):
            raise ValueError("A DTS attachment refers to an invalid mesh or node.")
        render, collide = active[node] and not NONVISUAL.fullmatch(names[name]), collidable[node]
        if render or collide:
            attachments[index] = render, collide
        if flags & 1:
            continue
        attachment = mesh, node, (x, y, z)
        if render:
            visible.append(attachment)
        if collide:
            collision.append(attachment)
    return lod, visible, collision_detail, collision, attachments


def parse_shape(raw):
    stream = Binary(raw)
    body, _ = record(stream, b"TS::Shape")
    version = body.uint()
    if not 5 <= version <= 8 or len(stream.data) - stream.offset > 3:
        raise ValueError("Expected a version 5 to 8 Darkstar DTS shape.")
    nn, ns, nsub, nk, nt, nname, no, nd, nm, ntransition, ntrigger = body.unpack("<11I")
    if (not 1 <= nn <= MAX_PARTS or not 1 <= nm <= MAX_PARTS or not 1 <= no <= MAX_PARTS
            or nname > 65_536 or nd > 1024 or any(n > 250_000 for n in (ns, nsub, nk, nt, ntransition, ntrigger))):
        raise ValueError("Invalid Darkstar DTS shape counts.")
    body.take(40 if version == 8 else 16)  # Radius, centre and (v8) bounds.
    nodes = body.array("<5h" if version == 8 else "<5i", nn)
    sequences = body.array("<iifi4i", ns)
    subsequences = body.array("<3h" if version == 8 else "<3i", nsub)
    keys = body.array("<f2H" if version == 8 else "<f2I", nk)
    pose_format = {8: "<4h3f", 7: "<4h3f12x"}.get(version, "<7f12x")
    poses = body.array(pose_format, nt)
    names = [entry.split(b"\0", 1)[0].decode("latin-1") for entry, in body.array("<24s", nname)]
    # The v8 runtime upgrades old object offsets by taking their translation.
    objects = body.array("<HHih2x3f2h" if version == 8 else "<HHii40x3f2i", no)
    details = body.array("<if", nd)
    transitions = body.array("<2i3f" + pose_format[1:], ntransition)
    triggers = body.array("<fi", ntrigger)
    _, = body.unpack("<I")
    always = body.unpack("<i")[0] if version >= 6 else -1
    meshes = [cel_mesh(record(body, b"TS::CelAnimMesh")[0]) for _ in range(nm)]
    has_materials = body.uint()
    if has_materials not in (0, 1):
        raise ValueError("Invalid Darkstar DTS material trailer.")
    materials = material_names(record(body, b"TS::MaterialList")[1], keep_path=True) if has_materials else []
    if body.offset != len(body.data):
        raise ValueError("Unexpected data after the DTS material list.")
    node_world, parents = world_nodes(nodes, poses, names)
    lod, visible, detail, collision, attachments = render_objects(details, always, nodes, names, parents, objects, len(meshes))
    motion = shape_motion(version, sequences, subsequences, keys, poses, names, nodes, objects,
                         transitions, triggers, meshes, attachments)
    return version, lod, materials, meshes, node_world, visible, detail, collision, ns, motion


def geometry_for(meshes, node_world, attachments, materials, *, collision=False):
    vertices, triangles = [], []
    for mesh, node, offset in attachments:
        source = meshes[mesh]
        points, coords, faces = source.positions(0), source.texels(0), source.faces
        first = len(vertices)
        if first + len(points) > 150_000 or len(triangles) + len(faces) // 28 > 250_000:
            raise ValueError("The DTS exceeds the intake geometry limits.")
        vertices.extend(list(transform(tuple(x + dx for x, dx in zip(point, offset)), node_world[node]))
                        for point in points)
        for a, u, b, v, c, w, material in struct.iter_unpack("<6Ii", faces):
            if material == -1 and not collision:
                continue
            if max(a, b, c) >= len(points):
                raise ValueError("A DTS face references a missing vertex.")
            if not collision and (not 0 <= material < 256 or materials and material >= len(materials)):
                raise ValueError("A DTS face references a missing vertex or material.")
            if len({a, b, c}) != 3:
                continue
            # Darkstar stores clockwise faces. The neutral mesh uses outward CCW.
            if collision:
                triangles.append([first + a, first + c, first + b])
                continue
            if coords and max(u, v, w) >= len(coords):
                raise ValueError("A DTS face references a missing texture coordinate.")
            uv = [*coords[u], *coords[w], *coords[v]] if coords else [0] * 6
            triangles.append([material, first + a, first + c, first + b, *uv])
    return vertices, triangles


def decode_dts(name, raw):
    version, lod, materials, meshes, node_world, visible, detail, collision, sequences, motion = parse_shape(raw)
    vertices, triangles = geometry_for(meshes, node_world, visible, materials)
    if not triangles:
        raise ValueError("The DTS has no default-visible triangles.")
    if not materials:
        materials = [None] * (1 + max(row[0] for row in triangles))
    decoder = Path(__file__)
    source = dict(name=PurePosixPath(name).name, sha256=sha256(raw).hexdigest(),
                  decoder_sha256=sha256(decoder.read_bytes() + decoder.with_name("t1_dts_motion.py").read_bytes()).hexdigest(),
                  dts_version=version, lod=lod,
                  sequences=sequences, max_visible_frames=max(len(meshes[mesh].frames) for mesh, _, _ in visible))
    data = dict(format=4, source=source,
        materials=[label or f"Material slot {i}" for i, label in enumerate(materials)],
        vertices_m=vertices, triangles=triangles)
    if detail is not None:
        points, faces = geometry_for(meshes, node_world, collision, materials, collision=True)
        if not faces:
            raise ValueError("The authored DTS collision detail has no usable faces.")
        data.update(format=5, collision=dict(detail=detail, vertices_m=points, triangles=faces))
    if motion:
        data.update(format=6, animation=motion)
        if animated_only_slots(data):
            data['format'] = 7
    return validate_geometry(data)
