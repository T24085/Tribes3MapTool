"""Read version 805 level-owned Ascend BSP and its compiled texture coordinates."""

from hashlib import sha256
import math
from pathlib import Path
import struct

from interior_geometry import ASCEND_BSP
from unreal_binary_scene import read_properties
from unreal_package_reader import Cursor, MAX_RECORDS
from vengeance_bsp import FAKE_BACKDROP, INVISIBLE, PORTAL, TWO_SIDED, project_arrays


def payload(report, stream, row, objects):
    summary = report['summary']
    _, _, issues, start = read_properties(stream, summary, row, report['names'], objects)
    if issues:
        raise ValueError('Unsupported properties on an Ascend BSP object.')
    return Cursor(stream, start, row['serial_offset'] + row['serial_bytes'], summary['endian'])


def array_count(cursor, stride):
    count, = cursor.fields('i')
    if not 0 <= count <= MAX_RECORDS or count * stride > cursor.limit - cursor.offset:
        raise ValueError('Invalid Ascend BSP array length.')
    return count


def bulk(cursor, stride):
    saved_stride, = cursor.fields('i')
    if saved_stride != stride:
        raise ValueError('Unsupported Ascend BSP bulk element layout.')
    return cursor.take(array_count(cursor, stride) * stride)


def level_model(report, stream, level, objects):
    cursor = payload(report, stream, level, objects)
    cursor.take(4)  # Level root reference. The actor reader checks the roster.
    cursor.take(array_count(cursor, 4) * 4)
    if cursor.offset == cursor.limit:
        return None
    for _ in range(4):  # FURL protocol, host, map and portal.
        cursor.string(True)
    for _ in range(array_count(cursor, 4)):
        cursor.string(True)
    cursor.take(8)  # FURL port and validity.
    index, = cursor.fields('i')
    if index == 0:
        return None
    model = objects.get(index)
    if (index < 0 or not model or model['class_name'].casefold() != 'model'
            or model['outer'] != level['index']):
        raise ValueError('The Ascend level BSP reference does not point to its own model export.')
    return model


def model_arrays(report, stream, model, objects):
    cursor = payload(report, stream, model, objects)
    endian = report['summary']['endian']
    cursor.take(28)  # FBoxSphereBounds.
    vectors = list(struct.iter_unpack(endian + '3f', bulk(cursor, 12)))
    points = list(struct.iter_unpack(endian + '3f', bulk(cursor, 12)))
    node_data = bulk(cursor, 64)
    if not node_data:
        return None, None
    cursor.take(4)  # Editor Polys export, which is separate from the compiled model.
    nodes = []
    for offset in range(0, len(node_data), 64):
        first, surface = struct.unpack_from(endian + '2i', node_data, offset + 16)
        nodes.append((first, surface, node_data[offset + 54]))
    surfaces = []
    for _ in range(array_count(cursor, 60)):
        material, flags, base, normal, u_axis, v_axis = cursor.fields('iI4i')
        cursor.take(36)  # Brush references, plane, and lighting settings.
        if (material and material not in objects or not 0 <= base < len(points)
                or any(not 0 <= index < len(vectors) for index in (normal, u_axis, v_axis))):
            raise ValueError('Invalid Ascend BSP surface references.')
        surfaces.append((material, flags, points[base], vectors[normal], vectors[u_axis], vectors[v_axis]))
    pool_data = bulk(cursor, 24)
    vertex_pool = [struct.unpack_from(endian + 'i', pool_data, offset)[0]
                   for offset in range(0, len(pool_data), 24)]
    bulk(cursor, 4)  # Legacy vertex indices.
    cursor.take(4)  # Shared side count.
    bulk(cursor, 4)  # Leaf hulls.
    bulk(cursor, 4)  # Leaves.
    cursor.take(8)  # RootOutside and Linked.
    bulk(cursor, 4)  # Portal nodes.
    declared, = cursor.fields('i')
    rendered = bulk(cursor, 36)
    if declared != len(rendered) // 36:
        raise ValueError('Invalid Ascend BSP render vertex count.')
    texture_uv = {}
    next_vertex = 0
    # Some saved maps leave each node's cached render offset at zero. The
    # compiled buffer follows node order, including the two-sided backfaces.
    for first, surface, size in nodes:
        if not size:
            continue
        if not 0 <= surface < len(surfaces) or not 0 <= first <= len(vertex_pool) - size:
            raise ValueError('An Ascend BSP polygon references a missing surface or vertex range.')
        flags = surfaces[surface][1]
        render_start = next_vertex
        next_vertex += size * (2 if flags & TWO_SIDED else 1)
        if size < 3 or flags & (INVISIBLE | FAKE_BACKDROP | PORTAL):
            continue
        if not 0 <= render_start <= declared - size:
            raise ValueError('An Ascend BSP polygon is missing compiled render vertices.')
        for index in range(size):
            start = (render_start + index) * 36
            position = struct.unpack_from(endian + '3f', rendered, start)
            uv = struct.unpack_from(endian + '2f', rendered, start + 20)
            point = vertex_pool[first + index]
            if (not 0 <= point < len(points) or not all(math.isfinite(value) for value in (*position, *uv))
                    or any(abs(a - b) > 0.05 for a, b in zip(position, points[point]))
                    or texture_uv.setdefault(first + index, uv) != uv):
                raise ValueError('Compiled Ascend BSP texture coordinates do not match their polygon.')
    if declared and next_vertex != declared:
        raise ValueError('The Ascend BSP polygon sizes do not match its render vertex count.')
    return (points, nodes, surfaces, vertex_pool), texture_uv


def read_bsp_models(report, stream, units):
    objects = {row['index']: row for row in report['imports'] + report['exports']}
    shared = Path(__file__).with_name('vengeance_bsp.py')
    decoder_hash = sha256(Path(__file__).read_bytes() + shared.read_bytes()).hexdigest()
    models, issues = [], []
    for level in report['exports']:
        if level['class_name'].casefold() != 'level':
            continue
        try:
            if report['summary']['version'] != 805:
                raise ValueError('Unsupported native Ascend BSP package version.')
            model = level_model(report, stream, level, objects)
            if model is None:
                continue
            arrays, texture_uv = model_arrays(report, stream, model, objects)
            if arrays is not None:
                decoded = project_arrays(report, model, objects, units, decoder_hash, ASCEND_BSP,
                                         arrays, texture_uv=texture_uv)
                if decoded:
                    models.append((level['object_path'], *decoded))
        except ValueError as error:
            issues.append({'level': level['object_path'], 'reason': str(error)})
    return models, issues
