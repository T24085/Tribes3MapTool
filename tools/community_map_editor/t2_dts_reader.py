"""Project static Torque DTS geometry using a locally installed dtslib codec."""

import hashlib
import importlib.util
import math
from pathlib import Path
import sys

from intake_contract import bounded, finite
from interior_geometry import MAX_GEOMETRY_BYTES, validate_geometry


PRIMITIVE_TYPE = 3 << 30
STRIP = 1 << 30
FAN = 2 << 30
INDEXED = 1 << 29
NO_MATERIAL = 1 << 28
MATERIAL_INDEX = NO_MATERIAL - 1
BILLBOARD = (1 << 31) | (1 << 29)


def load_decoder(path):
    folder = Path(path).resolve(strict=True)
    if (folder / 'dtslib' / '__init__.py').is_file():
        folder = folder / 'dtslib'
    entry = folder / '__init__.py'
    if not entry.is_file() or not (folder / 'reader.py').is_file():
        raise ValueError('Select the installed io_scene_dts or dtslib folder.')
    digest = hashlib.sha256()
    for item in sorted(folder.glob('*.py')):
        digest.update(item.name.encode() + b'\0' + hashlib.sha256(bounded(item, MAX_GEOMETRY_BYTES)).digest())
    pin = digest.hexdigest()
    namespace = '_t3_dtslib_' + pin
    if namespace not in sys.modules:
        spec = importlib.util.spec_from_file_location(namespace, entry, submodule_search_locations=[str(folder)])
        if spec is None or spec.loader is None:
            raise ValueError('Could not load the installed DTS codec.')
        module = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            for name in tuple(sys.modules):
                if name == namespace or name.startswith(namespace + '.'):
                    sys.modules.pop(name, None)
            raise
    try:
        return sys.modules[namespace].read_shape, pin
    except AttributeError as exc:
        raise ValueError('The installed DTS codec does not expose read_shape.') from exc


def cross(a, b):
    x, y, z = a
    u, v, w = b
    return y*w - z*v, z*u - x*w, x*v - y*u


def transform_point(point, chain):
    # Torque stores the conjugate of the quaternion used for vector rotation.
    for axis, scalar, translation in chain:
        tangent = cross(axis, point)
        normal = cross(axis, tangent)
        point = tuple(v + 2*(scalar*t + n) + p for v, t, n, p in zip(point, tangent, normal, translation))
    return list(point)


def node_chain(shape, index):
    chain, visited = [], set()
    while index != -1:
        if not 0 <= index < len(shape.nodes) or index in visited or len(chain) >= 128:
            raise ValueError('Invalid DTS node hierarchy.')
        visited.add(index)
        x, y, z, w = shape.default_rotations[index].normalized_floats()
        translation = shape.default_translations[index]
        if len(translation) != 3 or not all(finite(v, 20_000) for v in translation):
            raise ValueError('Invalid DTS node translation.')
        chain.append(((-x, -y, -z), w, translation))
        index = shape.nodes[index].parent_index
    return chain


def primitive_triangles(mesh, primitive, size):
    start, count, word = primitive.start, primitive.num_elements, primitive.mat_index
    indexed = word & INDEXED
    limit = len(mesh.indices) if indexed else size
    if not 0 <= start <= start + count <= limit:
        raise ValueError('DTS primitive exceeds its mesh buffers.')
    values = mesh.indices[start:start + count] if indexed else range(start, start + count)
    if any(not 0 <= index < size for index in values):
        raise ValueError('DTS primitive references a missing vertex.')
    kind = word & PRIMITIVE_TYPE
    if kind == STRIP:
        for i in range(count - 2):
            a, b, c = values[i:i + 3]
            yield (b, a, c) if i % 2 else (a, b, c)
    elif kind == FAN:
        for i in range(1, count - 1):
            yield values[0], values[i], values[i + 1]
    elif kind == 0 and count % 3 == 0:
        yield from (tuple(values[i:i + 3]) for i in range(0, count, 3))
    else:
        raise ValueError('Unsupported DTS primitive type or triangle count.')


def detail_meshes(shape, detail):
    first = shape.sub_shape_first_object[detail.sub_shape_num]
    end = first + shape.sub_shape_num_objects[detail.sub_shape_num]
    if not 0 <= first <= end <= len(shape.objects):
        raise ValueError('Invalid DTS subshape object range.')
    for obj in shape.objects[first:end]:
        if detail.object_detail_num >= obj.num_meshes:
            continue
        index = obj.start_mesh_index + detail.object_detail_num
        if not 0 <= index < len(shape.meshes):
            raise ValueError('DTS object references a missing mesh.')
        mesh = shape.meshes[index]
        if mesh is None or mesh.flags & BILLBOARD:
            continue
        if mesh.mesh_type not in (0, 3):
            raise ValueError('The visible DTS detail uses skinning or decals instead of static geometry.')
        yield obj, mesh


def decode_geometry(shape, source):
    if shape.sequences:
        raise ValueError('The DTS includes animation sequences that need separate conversion.')
    details = sorted((item for item in shape.details if item.size >= 0 and item.sub_shape_num >= 0
                      and item.object_detail_num >= 0), key=lambda item: item.size, reverse=True)
    if not details:
        raise ValueError('The DTS has no visible mesh detail.')
    detail = details[0]
    materials = [item.name for item in shape.materials]
    untextured = len(materials)
    vertices, triangles = [], []
    for obj, mesh in detail_meshes(shape, detail):
        if mesh.num_frames != 1 or mesh.num_mat_frames != 1:
            raise ValueError('The DTS includes animated mesh or material frames.')
        size = mesh.verts_per_frame or len(mesh.verts)
        if not 3 <= size <= min(len(mesh.verts), 150_000 - len(vertices)):
            raise ValueError('Unsupported DTS mesh vertex count.')
        if len(mesh.tverts) < size and mesh.tverts:
            raise ValueError('DTS mesh texture coordinates are incomplete.')
        base = len(vertices)
        chain = node_chain(shape, obj.node_index)
        vertices.extend(transform_point(point, chain) for point in mesh.verts[:size])
        for primitive in mesh.primitives:
            word = primitive.mat_index
            slot = untextured if word & NO_MATERIAL else word & MATERIAL_INDEX
            if not 0 <= slot < len(materials) and not word & NO_MATERIAL:
                raise ValueError('DTS primitive references a missing material.')
            for a, b, c in primitive_triangles(mesh, primitive, size):
                if a == b or b == c or a == c:
                    continue
                p, q, r = (mesh.verts[i] for i in (a, b, c))
                normal = cross(tuple(v-u for u,v in zip(p,q)), tuple(v-u for u,v in zip(p,r)))
                if not any(normal):
                    continue
                if len(mesh.norms) >= size:
                    facing = sum(math.fsum(mesh.norms[i][axis] for i in (a,b,c))*normal[axis] for axis in range(3))
                    if facing < 0:
                        b, c = c, b
                uv = [value for index in (a,b,c) for value in (mesh.tverts[index] if mesh.tverts else (0,0))]
                triangles.append([slot, base+a, base+b, base+c, *uv])
                if len(triangles) > 250_000:
                    raise ValueError('The DTS exceeds the supported triangle count.')
    if any(row[0] == untextured for row in triangles):
        materials.append('Untextured')
    return validate_geometry({'format': 2, 'source': {**source, 'lod': detail.object_detail_num},
                              'materials': materials, 'vertices_m': vertices, 'triangles': triangles})


def decode_bytes(raw, name, decoder):
    # Version is the low byte of the Torque version word. T2 uses v23.
    if not name.casefold().endswith('.dts') or len(raw) < 16 or not 15 <= raw[0] <= 24:
        raise ValueError('Expected a supported Torque DTS shape.')
    read, decoder_hash = decoder
    try:
        shape = read(raw)
        if shape.source_version != raw[0]:
            raise ValueError('The DTS decoder returned an inconsistent version.')
        return decode_geometry(shape, {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                       'decoder_sha256': decoder_hash, 'dts_version': raw[0]})
    except Exception as exc:
        raise ValueError(f'{name} could not be decoded: {exc}') from exc
