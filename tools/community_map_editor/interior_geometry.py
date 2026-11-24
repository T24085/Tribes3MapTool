"""Neutral Tribes model geometry and the playable mesh projection."""

import hashlib
import json
from pathlib import Path
import re

from intake_contract import bounded, finite
from t1_dts_motion import validate_motion


MAX_GEOMETRY_BYTES = 16 * 1024 * 1024
MAX_CONVEX_POINTS = 256
MAX_DIF_LEVELS = 64
TOOL_MATERIALS = {'null', 'origin', 'trigger', 'forcefield'}
VENGEANCE_BSP = 8
ASCEND_STATIC_MESH = 9
VENGEANCE_STATIC_MESH = 10
UNREAL_STATIC_MESHES = {ASCEND_STATIC_MESH, VENGEANCE_STATIC_MESH}
UNREAL_GEOMETRY = {VENGEANCE_BSP, *UNREAL_STATIC_MESHES}


def validate_geometry(data):
    if not isinstance(data, dict) or type(data.get('format')) is not int or not 1 <= data['format'] <= VENGEANCE_STATIC_MESH:
        raise ValueError('Unsupported Tribes geometry.')
    native = data['format'] in UNREAL_GEOMETRY
    fields = {'format', 'source', 'materials', 'vertices_m', 'triangles'}
    if data['format'] == 1 and 'lods' in data:
        fields.add('lods')
    if data['format'] == 5 or data['format'] >= 6 and 'collision' in data:
        fields.add('collision')
    if native:
        fields.update(('collision', 'render'))
    elif data['format'] >= 6:
        fields.add('animation')
    if set(data) != fields:
        raise ValueError('Invalid Tribes geometry fields.')
    source = data['source']
    format_key, suffix = {1: ('dif_version', 'dif'), 3: ('dig_version', 'dis')}.get(
        data['format'], ('dts_version', 'dts'))
    names, digests = {'name': suffix}, {'sha256', 'decoder_sha256'}
    if data['format'] == 3:
        names.update(geometry_name='dig', material_name='dml')
        digests.update(('geometry_sha256', 'material_sha256'))
    fields = {format_key, 'lod'}
    if data['format'] >= 4:
        fields.update(('sequences', 'max_visible_frames'))
    if native:
        source_fields = {'name', 'sha256', 'decoder_sha256', 'export'}
        extensions = 'tvm|pkg'
        if data['format'] in UNREAL_STATIC_MESHES:
            source_fields.add('object_path')
        if data['format'] == ASCEND_STATIC_MESH:
            extensions = 'fmap|udk|upk'
        if (not isinstance(source, dict) or set(source) != source_fields
                or not isinstance(source['name'], str)
                or not re.fullmatch(rf'[^/\\:\x00-\x1f]{{1,240}}\.(?:{extensions})', source['name'], re.I)
                or not all(isinstance(source[key], str) and re.fullmatch('[a-f0-9]{64}', source[key]) for key in digests)
                or type(source['export']) is not int or not 1 <= source['export'] <= 250_000
                or type(data['render']) is not bool
                or data['format'] in UNREAL_STATIC_MESHES and (
                    not data['render'] or not isinstance(source['object_path'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_./:$-]{1,512}', source['object_path']))):
            raise ValueError('Invalid Unreal geometry source provenance.')
    elif (not isinstance(source, dict) or set(source) != names.keys() | digests | fields
            or not all(isinstance(source[key], str) and re.fullmatch(rf'[^/\\:\x00-\x1f]{{1,240}}\.{ext}', source[key], re.I)
                       for key, ext in names.items())
            or not all(isinstance(source[k], str) and re.fullmatch(r'[a-f0-9]{64}', source[k]) for k in digests)
            or not all(type(source[key]) is int for key in fields)):
        raise ValueError('Invalid model source provenance.')
    if (data['format'] == 1 and (source[format_key] != 44 or source['lod'] != 0)
            or data['format'] == 2 and not (15 <= source[format_key] <= 24 and 0 <= source['lod'] <= 127)
            or data['format'] == 3 and not (source[format_key] == 7 and 0 <= source['lod'] < 4096)
            or 4 <= data['format'] < VENGEANCE_BSP and not (5 <= source[format_key] <= 8 and 0 <= source['lod'] < 1024
                                                and 0 <= source['sequences'] <= 250_000
                                                and 1 <= source['max_visible_frames'] <= 65536)):
        raise ValueError('Unsupported model version or detail level.')
    materials, vertices, triangles = (data[key] for key in ('materials', 'vertices_m', 'triangles'))
    material_limit = 256 if data['format'] >= 3 else 1024
    point_limit = 65_536 if data['format'] == 3 else 150_000
    if (not isinstance(materials, list) or not 1 <= len(materials) <= material_limit
            or not all(isinstance(name, str) and 0 < len(name) <= 256 and name.isprintable() for name in materials)
            or not valid_vertices(vertices, point_limit)
            or not isinstance(triangles, list) or not 1 <= len(triangles) <= 250_000):
        raise ValueError('Invalid interior mesh dimensions or materials.')
    validate_mesh_triangles(triangles, len(vertices), len(materials))
    if 'lods' in data:
        validate_dif_lods(data['lods'], len(materials))
    if 'collision' in data:
        collision = data['collision']
        standard_fields = {'detail', 'vertices_m', 'triangles'}
        allowed = (standard_fields, standard_fields | {'simple'}) if data['format'] == ASCEND_STATIC_MESH else (standard_fields,)
        if (not isinstance(collision, dict) or set(collision) not in allowed
                or type(collision['detail']) is not int or not 0 <= collision['detail'] < 1024
                or not valid_vertices(collision['vertices_m'], point_limit)
                or not isinstance(collision['triangles'], list) or not (0 if native and data['render'] else 1) <= len(collision['triangles']) <= 250_000
                or not all(valid_face(face, len(collision['vertices_m'])) for face in collision['triangles'])):
            raise ValueError('Invalid authored DTS collision detail.')
        if 'simple' in collision:
            validate_simple_collision(collision['simple'], collision['triangles'])
    if 'animation' in data:
        validate_motion(data['animation'], source['sequences'])
        if data['format'] == 7 and not animated_only_slots(data):
            raise ValueError('A version 7 DTS shape needs at least one animated-only material slot.')
    return data


def validate_mesh_triangles(triangles, vertex_count, material_count):
    for row in triangles:
        if (not isinstance(row, list) or len(row) != 10 or type(row[0]) is not int
                or not 0 <= row[0] < material_count or not valid_face(row[1:4], vertex_count)
                or not all(finite(value, 1e6) for value in row[4:])):
            raise ValueError('Invalid interior triangle, material, or UV.')


def validate_dif_lods(lods, material_count):
    def detail_value(value):
        return type(value) is int and -(2**31) <= value < 2**31

    if (not isinstance(lods, dict) or set(lods) != {'primary', 'secondary'}
            or not isinstance(lods['primary'], dict) or set(lods['primary']) != {'source_detail', 'min_pixels'}
            or not all(detail_value(value) for value in lods['primary'].values())
            or not isinstance(lods['secondary'], list) or not 1 <= len(lods['secondary']) < MAX_DIF_LEVELS):
        raise ValueError('Invalid DIF detail level inventory.')
    for level in lods['secondary']:
        if (not isinstance(level, dict) or set(level) != {'source_detail', 'min_pixels', 'vertices_m', 'triangles'}
                or not all(detail_value(level[key]) for key in ('source_detail', 'min_pixels'))
                or not valid_vertices(level['vertices_m'], 150_000)
                or not isinstance(level['triangles'], list) or not 1 <= len(level['triangles']) <= 250_000):
            raise ValueError('Invalid DIF detail level geometry.')
        validate_mesh_triangles(level['triangles'], len(level['vertices_m']), material_count)


def validate_simple_collision(simple, triangles):
    if (not isinstance(simple, dict) or simple.keys() != {'mode', 'shapes'}
            or simple['mode'] not in ('simple', 'both') or simple['mode'] == 'both' and not triangles
            or not isinstance(simple['shapes'], list) or not 1 <= len(simple['shapes']) <= 128):
        raise ValueError('Invalid Ascend simple collision.')
    for shape in simple['shapes']:
        if isinstance(shape, dict) and shape.get('kind') == 'convex':
            validate_convex_hull(shape)
            continue
        if (not isinstance(shape, dict) or shape.keys() != {'kind', 'center_m', 'axes', 'size_m'}
                or shape['kind'] not in ('sphere', 'box', 'capsule')
                or not isinstance(shape['center_m'], list) or len(shape['center_m']) != 3
                or not all(finite(value, 20_000) for value in shape['center_m'])
                or not isinstance(shape['size_m'], list)
                or len(shape['size_m']) != {'sphere': 1, 'box': 3, 'capsule': 2}[shape['kind']]
                or not all(finite(value, 20_000) and (value > 0 or shape['kind'] == 'capsule' and index == 1 and value == 0)
                           for index, value in enumerate(shape['size_m']))):
            raise ValueError('Invalid Ascend simple collision dimensions.')
        axes = shape['axes']
        if (not isinstance(axes, list) or len(axes) != 3
                or not all(isinstance(axis, list) and len(axis) == 3 and all(finite(v, 1.0001) for v in axis)
                           for axis in axes)
                or any(abs(sum(a * b for a, b in zip(left, right)) - (i == j)) > 1e-4
                       for i, left in enumerate(axes) for j, right in enumerate(axes))):
            raise ValueError('Invalid Ascend simple collision axes.')
        x, y, z = axes
        cross = (x[1]*y[2] - x[2]*y[1], x[2]*y[0] - x[0]*y[2], x[0]*y[1] - x[1]*y[0])
        if sum(a * b for a, b in zip(cross, z)) < .999:
            raise ValueError('Invalid Ascend simple collision handedness.')


def validate_convex_hull(shape):
    vertices = shape.get('vertices_m')
    if (shape.keys() != {'kind', 'vertices_m'} or not valid_vertices(vertices, MAX_CONVEX_POINTS)
            or len(vertices) < 4 or len({tuple(point) for point in vertices}) != len(vertices)):
        raise ValueError('Invalid Ascend convex hull vertices.')
    origin = vertices[0]
    edges = [tuple(b - a for a, b in zip(origin, point)) for point in vertices[1:]]
    axis = max(edges, key=squared_length)
    x, y, z = axis
    perpendiculars = [(y * c - z * b, z * a - x * c, x * b - y * a) for a, b, c in edges]
    normal = max(perpendiculars, key=squared_length)
    length = squared_length(axis) ** .5
    height = max(abs(sum(a * b for a, b in zip(normal, edge))) for edge in edges)
    if height <= 1e-8 * length ** 3:
        raise ValueError('Ascend convex hull has insufficient volume.')


def squared_length(vector):
    return sum(value * value for value in vector)


def valid_vertices(vertices, limit):
    return (isinstance(vertices, list) and 3 <= len(vertices) <= limit
            and all(isinstance(point, list) and len(point) == 3
                    and all(finite(value, 20_000) for value in point) for point in vertices))


def valid_face(face, vertex_count):
    return (isinstance(face, list) and len(face) == 3
            and all(type(index) is int and 0 <= index < vertex_count for index in face)
            and len(set(face)) == 3)


def load_geometry(path):
    path = Path(path).resolve(strict=True)
    raw = bounded(path, MAX_GEOMETRY_BYTES)
    return validate_geometry(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def visible_material(data, slot):
    name = data['materials'][slot].replace('\\', '/').rsplit('/', 1)[-1]
    return data['format'] not in (1, 3) or name.split('.', 1)[0].casefold() not in TOOL_MATERIALS


def material_slots(data):
    meshes = [data, *data.get('lods', {}).get('secondary', ())]
    resting = sorted({row[0] for mesh in meshes for row in mesh['triangles'] if visible_material(data, row[0])})
    if data['format'] == 7:
        return resting + animated_only_slots(data)
    return resting


def animated_only_slots(data):
    motion = data['animation']
    material_count = len(data['materials'])
    meshes = {obj['mesh'] for obj in motion['objects'] if obj['render']}
    authored = {
        slot for index in meshes for a, _, b, _, c, _, slot in motion['meshes'][index]['faces']
        if 0 <= slot < material_count and len({a, b, c}) == 3
    }
    swapped = {
        replacement for clip in motion['clips'] for track in clip['material_swaps']
        for _, slot, replacement in track if slot in authored and 0 <= replacement < material_count
    }
    return sorted((authored | swapped) - {row[0] for row in data['triangles']})


def triangle_groups(data, lighting=None):
    """Group triangles by source material and the two baked lighting states."""
    grouped = {}
    surfaces = iter(lighting['surfaces'] if lighting else ())
    surface = next(surfaces, None)
    for index, row in enumerate(data['triangles']):
        while surface is not None and index >= surface[1] + surface[2]:
            surface = next(surfaces, None)
        slot = row[0]
        if not visible_material(data, slot):
            continue
        binding = surface if surface is not None and surface[1] <= index else None
        normal, alarm = binding[3:5] if binding else (None, None)
        key = slot, -1 if normal is None else normal, -1 if alarm is None else alarm
        grouped.setdefault(key, []).append((row, binding[5] if binding else None))
    return sorted(grouped.items())


def material_sections(data, lighting=None):
    if 'lods' not in data:
        if lighting and lighting['surfaces']:
            return [key for key, _ in triangle_groups(data, lighting)]
        return [(slot, -1, -1) for slot in material_slots(data)]
    sections = [key for key, _ in triangle_groups(data, lighting)]
    secondary = set()
    for position, level in enumerate(data['lods']['secondary'], 1):
        model = dif_detail_mesh(data, level)
        secondary.update(key for key, _ in triangle_groups(model, dif_detail_lighting(lighting, position)))
    secondary = sorted(secondary)
    return sections + [key for key in secondary if key not in sections]


def dif_detail_mesh(data, level):
    return {'format': data['format'], 'materials': data['materials'],
            'vertices_m': level['vertices_m'], 'triangles': level['triangles']}


def dif_detail_lighting(lighting, position):
    surfaces = lighting.get('lod_surfaces', {}).get(position) if lighting else None
    return {'surfaces': surfaces} if surfaces else None


def render_groups(data):
    """Group visible triangles by source material in UE centimetres and UV coordinates."""
    return [group[:5] for group in render_sections(data)]


def render_sections(data, lighting=None, texture_sizes=None):
    materials, vertices = data['materials'], data['vertices_m']
    result = []
    for (slot, normal, alarm), triangles in triangle_groups(data, lighting):
        width, height = texture_sizes.get(slot, (1, 1)) if texture_sizes else (1, 1)
        points, coords, faces, light_uv, alarm_uv = [], [], [], [], []
        for (_, a, b, c, *uv), projection in triangles:
            start = len(points)
            source = vertices[a], vertices[b], vertices[c]
            y_sign = 1 if data['format'] in UNREAL_GEOMETRY else -1
            points.extend((x * 100, y_sign * y * 100, z * 100) for x, y, z in source)
            coords.extend((u / width, v / height) for u, v in zip(uv[::2], uv[1::2]))
            faces.append((start, start + 1, start + 2))
            if projection and data['format'] == 3:
                for channel, target in zip(projection, (light_uv, alarm_uv)):
                    sx, sy, dx, dy = channel or (0, 0, 0, 0)
                    target.extend((u * sx + dx, v * sy + dy) for u, v in zip(uv[::2], uv[1::2]))
            elif projection:
                ax, ay, sx, sy, dx, dy = projection
                light_uv.extend((point[ax] * sx + dx, point[ay] * sy + dy) for point in source)
        section = (slot, materials[slot], points, coords, faces, light_uv, normal, alarm)
        result.append((*section, alarm_uv) if data['format'] == 3 else section)
    if not result:
        raise ValueError('The interior contains no playable surfaces.')
    return result
