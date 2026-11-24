"""Create reusable Unreal static meshes from reviewed Tribes geometry."""

import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

import unreal

from interior_geometry import (ASCEND_STATIC_MESH, UNREAL_GEOMETRY, UNREAL_STATIC_MESHES, VENGEANCE_BSP,
                               VENGEANCE_STATIC_MESH, animated_only_slots, load_geometry, render_sections)
from source_profiles import BRUSH_PREVIEW_FOLDER


FOLDERS = {1: '/Game/Community/T2Interiors', 2: '/Game/Community/T2Shapes', 3: '/Game/Community/T1Interiors',
           VENGEANCE_BSP: '/Game/Community/VengeanceBSP', ASCEND_STATIC_MESH: '/Game/Community/AscendMeshes',
           VENGEANCE_STATIC_MESH: '/Game/Community/VengeanceMeshes'}
HASH_TAG = 'T3InteriorGeometryHash'


def mesh_package(data, digest, *, brush_preview=False):
    source = (data['source']['object_path'].rsplit('.', 1)[-1] if data['format'] in UNREAL_STATIC_MESHES
              else data['source']['name'][:-4])
    stem = re.sub(r'[^A-Za-z0-9_]', '_', source)[:42]
    family = {VENGEANCE_BSP: 'TV', VENGEANCE_STATIC_MESH: 'TV', ASCEND_STATIC_MESH: 'TA'}.get(
        data['format'], 'T1' if data['format'] >= 3 else 'T2')
    folder = BRUSH_PREVIEW_FOLDER if brush_preview else FOLDERS.get(data['format'], '/Game/Community/T1Shapes')
    return f'{folder}/SM_{family}_{stem}_{digest[:16]}'


def asset_mesh(data, digest, lighting=None, texture_sizes=None, *, surface_materials=None, brush_preview=False):
    package = mesh_package(data, digest, brush_preview=brush_preview)
    collision = data.get('collision')
    proxy = None
    simple = collision.get('simple') if collision else None
    if collision and collision['triangles'] and data.get('render', True) and (not simple or simple['mode'] == 'both'):
        collision_format = data['format'] if data['format'] in UNREAL_GEOMETRY else 5
        proxy_data = dict(format=collision_format, source=data['source'], vertices_m=collision['vertices_m'], materials=['Collision'],
                          triangles=[[0, *face, 0, 0, 0, 0, 0, 0] for face in collision['triangles']])
        proxy = build_asset(proxy_data, digest, package + '_Collision')
    return build_asset(data, digest, package, lighting, texture_sizes, proxy, surface_materials)


def material_slot_label(source_slot, material):
    name = material.replace('\\', '/').rsplit('/', 1)[-1]
    label = re.sub(r'[^A-Za-z0-9_]', '_', name)[:64]
    return f'{source_slot:03}_{label}'


def convex_element(vertices_m):
    points = [tuple(value * 100 for value in point) for point in vertices_m]
    axes = list(zip(*points))

    def vector_text(point):
        x, y, z = point
        return f'(X={x:.17g},Y={y:.17g},Z={z:.17g})'

    vertices = ','.join(map(vector_text, points))
    lower = vector_text(map(min, axes))
    upper = vector_text(map(max, axes))
    # UE 5.3 exposes whole-struct import for the convex data and bounds.
    item = unreal.KConvexElem()
    if not item.import_text(f'(VertexData=({vertices}),ElemBox=(Min={lower},Max={upper},IsValid=True))'):
        raise ValueError('Unreal could not import the authored convex hull.')
    return item


def install_simple_collision(asset, simple):
    body = asset.get_editor_property('body_setup')
    aggregate = unreal.KAggregateGeom()
    spheres, boxes, capsules, hulls = [], [], [], []
    for shape in simple['shapes']:
        kind = shape['kind']
        if kind == 'convex':
            hulls.append(convex_element(shape['vertices_m']))
            continue
        center = unreal.Vector(*(100 * value for value in shape['center_m']))
        sizes = [100 * value for value in shape['size_m']]
        if kind == 'sphere':
            item = unreal.KSphereElem()
            fields = {'radius': sizes[0]}
            spheres.append(item)
        else:
            x, y, _ = shape['axes']
            rotation = unreal.MathLibrary.make_rot_from_xy(unreal.Vector(*x), unreal.Vector(*y))
            if kind == 'box':
                item = unreal.KBoxElem()
                fields = {'x': sizes[0], 'y': sizes[1], 'z': sizes[2]}
                boxes.append(item)
            else:
                item = unreal.KSphylElem()
                fields = {'radius': sizes[0], 'length': sizes[1]}
                capsules.append(item)
            fields['rotation'] = rotation
        item.set_editor_property('center', center)
        for name, value in fields.items():
            item.set_editor_property(name, value)
    aggregate.set_editor_property('sphere_elems', spheres)
    aggregate.set_editor_property('box_elems', boxes)
    aggregate.set_editor_property('sphyl_elems', capsules)
    aggregate.set_editor_property('convex_elems', hulls)
    body.set_editor_property('agg_geom', aggregate)
    return bool(hulls)


def build_asset(data, digest, package, lighting=None, texture_sizes=None, proxy=None, surface_materials=None,
                *, convex_hull=None):
    assets = unreal.EditorAssetLibrary
    name = package.rsplit('/', 1)[-1]
    path = f'{package}.{name}'
    if assets.does_asset_exist(path):
        asset = assets.load_asset(path)
        if (not isinstance(asset, unreal.StaticMesh) or assets.get_metadata_tag(asset, HASH_TAG) != digest
                or proxy and asset.get_editor_property('complex_collision_mesh') != proxy
                or surface_materials and any(asset.get_material(slot) != material
                                             for slot, material in surface_materials.items())):
            raise ValueError(f'The draft model asset path is already in use: {path}')
        return asset

    mesh = unreal.DynamicMesh()
    slots = []
    for source_slot, material, vertices, uv, triangles, light_uv, normal, alarm, *extra_uv in render_sections(
            data, lighting, texture_sizes):
        alarm_uv = extra_uv[0] if extra_uv else ()
        mesh.append_buffers_to_mesh(
            unreal.GeometryScriptSimpleMeshBuffers(
                vertices=[unreal.Vector(*value) for value in vertices],
                uv0=[unreal.Vector2D(*value) for value in uv],
                uv1=[unreal.Vector2D(*value) for value in light_uv],
                uv2=[unreal.Vector2D(*value) for value in alarm_uv],
                triangles=[unreal.IntVector(*value) for value in triangles]),
            material_id=len(slots))
        suffix = f'_N{normal}_A{alarm}' if light_uv else ''
        slots.append(material_slot_label(source_slot, material) + suffix)
    collision = data.get('collision')
    simple = collision.get('simple') if collision else None
    has_collision = collision is None or bool(collision['triangles'] or simple)
    mode = unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
    if simple:
        mode = (unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AND_COMPLEX if simple['mode'] == 'both'
                else unreal.CollisionTraceFlag.CTF_USE_SIMPLE_AS_COMPLEX)
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=has_collision,
        collision_mode=mode)
    asset, status = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(mesh, package, options)
    if not isinstance(asset, unreal.StaticMesh) or status != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise ValueError('Unreal could not build the source model mesh.')

    try:
        material_slots = list(asset.get_editor_property('static_materials'))
        if len(material_slots) != len(slots):
            raise ValueError('Unreal did not preserve the interior material slots.')
        for slot, label in zip(material_slots, slots):
            slot.set_editor_property('material_slot_name', label)
        if data['format'] == 7:
            for index in animated_only_slots(data):
                slot = unreal.StaticMaterial()
                slot.set_editor_property('material_slot_name', material_slot_label(index, data['materials'][index]))
                material_slots.append(slot)
        asset.set_editor_property('static_materials', material_slots)
        for index, surface in (surface_materials or {}).items():
            asset.set_material(index, surface)
        convex = simple and install_simple_collision(asset, simple)
        if convex:
            # Recreate cooked convex physics even if the complex proxy is unchanged.
            asset.set_editor_property('complex_collision_mesh', proxy, unreal.PropertyAccessChangeNotifyMode.ALWAYS)
        elif proxy:
            asset.set_editor_property('complex_collision_mesh', proxy)
        if convex_hull is not None and not unreal.CommunityMapMotionLibrary.set_convex_volume_mesh(
                asset, [unreal.Vector(*point) for point in convex_hull]):
            raise ValueError("Unreal could not save the exact elevator collision hull.")
        assets.set_metadata_tag(asset, HASH_TAG, digest)
        assets.set_metadata_tag(asset, 'T3InteriorSourceHash', data['source']['sha256'])
        if not assets.save_loaded_asset(asset):
            raise ValueError('Unreal could not save the source model mesh.')
    except Exception:
        assets.delete_loaded_asset(asset)
        raise
    return asset


def import_interior():
    with TemporaryDirectory() as staging:
        output = Path(staging) / 'geometry.json'
        script = Path(os.environ['T3_CONVERTER_TOOL']) / 't2_dif_reader.py'
        args = [os.environ['T3_CONVERTER_PYTHON'], '-E', '-X', 'utf8', str(script), str(output), '--choose']
        if os.environ.get('T3_DIF_DECODER'):
            args += ['--decoder', os.environ['T3_DIF_DECODER']]
        result = subprocess.run(args, capture_output=True, encoding='utf-8', check=False, timeout=900)
        if result.returncode:
            raise ValueError(result.stderr.strip()[-800:] or 'The Tribes 2 interior decoder failed.')
        if not output.is_file():
            return None
        data, digest = load_geometry(output)
    asset = asset_mesh(data, digest)
    unreal.EditorAssetLibrary.sync_browser_to_objects([asset.get_path_name()])
    return asset
