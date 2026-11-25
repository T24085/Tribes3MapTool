"""Export validated map intake meshes as standalone glTF 2.0 assets."""

import argparse
from array import array
from hashlib import sha256
from io import BytesIO
from itertools import chain
import json
from math import hypot
import os
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from intake_contract import bounded, load_intake, load_intake_geometry
from interior_geometry import UNREAL_GEOMETRY, VENGEANCE_BSP, dif_detail_mesh, render_sections, visible_material
from model_textures import MAX_TEXTURE_BYTES, load_texture_index


def gltf_axes(vector, factor=1):
    x, y, z = vector
    return x * factor, z * factor, y * factor


def gltf_base_color(data):
    from PIL import Image, ImageCms

    with Image.open(BytesIO(data)) as image:
        profile = image.info.get('icc_profile')
        if not profile:
            return data
        try:
            color = ImageCms.profileToProfile(image, BytesIO(profile), ImageCms.createProfile('sRGB'), outputMode='RGBA')
        except ImageCms.PyCMSError as error:
            raise ValueError('An embedded source texture color profile could not be converted to sRGB.') from error
        output = BytesIO()
        # Saving with an empty profile leaves glTF's default sRGB interpretation in charge.
        color.save(output, 'PNG', icc_profile=None)
        return output.getvalue()


def make_glb(name, sections, bindings, read_texture):
    binary = bytearray()
    views, accessors, primitives, materials, images, textures = [], [], [], [], [], []
    texture_indices = {}
    extensions = set()

    def buffer_view(payload, target=None):
        binary.extend(b'\0' * (-len(binary) % 4))
        view = dict(buffer=0, byteOffset=len(binary), byteLength=len(payload))
        if target is not None:
            view['target'] = target
        binary.extend(payload)
        views.append(view)
        return len(views) - 1

    def accessor(values, size, kind, *, indices=False, bounds=False):
        data = array('I' if indices else 'f', values)
        limits = {}
        if bounds:
            axes = [data[axis::size] for axis in range(size)]
            limits = dict(min=[min(axis) for axis in axes], max=[max(axis) for axis in axes])
        if sys.byteorder != 'little':
            data.byteswap()
        accessors.append(dict(bufferView=buffer_view(data.tobytes(), 34963 if indices else 34962),
                              componentType=5125 if indices else 5126, count=len(data) // size, type=kind, **limits))
        return len(accessors) - 1

    for slot, label, positions, uv0, triangles, attributes in sections:
        fields = {'POSITION': accessor(chain.from_iterable(positions), 3, 'VEC3', bounds=True)}
        binding = bindings.get(str(slot), {})
        base = binding.get('ascend_base', {})
        if uv0:
            fields['TEXCOORD_0'] = accessor(chain.from_iterable(uv0), 2, 'VEC2')
        for key, values in attributes.items():
            if key == 'normals':
                normals = (gltf_axes(normal, 1 / hypot(*normal)) for normal in values)
                fields['NORMAL'] = accessor(chain.from_iterable(normals), 3, 'VEC3')
            elif key == 'vertex_colors':
                fields['_SOURCE_COLOR'] = accessor((channel / 255 for color in values for channel in color), 4, 'VEC4')
                if base.get('vertex_rgb'):
                    fields['COLOR_0'] = accessor((channel / 255 for color in values for channel in color[:3]), 3, 'VEC3')
            else:
                fields['TEXCOORD_' + key[2:]] = accessor(chain.from_iterable(values), 2, 'VEC2')

        color = dict(metallicFactor=0, roughnessFactor=1)
        if 'file' in binding:
            filename = binding['file']
            if filename not in texture_indices:
                images.append(dict(bufferView=buffer_view(read_texture(filename)), mimeType='image/png',
                                   name=Path(filename).stem))
                textures.append(dict(source=len(images) - 1))
                texture_indices[filename] = len(textures) - 1
            info = dict(index=texture_indices[filename])
            if 'uv' in base:
                channel, u, v = base['uv']
                info['texCoord'] = channel
                if (u, v) != (1, 1):
                    info['extensions'] = {'KHR_texture_transform': {'scale': [u, v]}}
                    extensions.add('KHR_texture_transform')
            color['baseColorTexture'] = info

        material = dict(name=label, pbrMetallicRoughness=color)
        if binding.get('two_sided'):
            material['doubleSided'] = True
        if binding.get('mode') == 'masked' and 'opacity' not in binding:
            material.update(alphaMode='MASK', alphaCutoff=1 / 3)
        materials.append(material)
        primitives.append(dict(attributes=fields, indices=accessor(chain.from_iterable(triangles), 1, 'SCALAR',
                                                                     indices=True), material=len(materials) - 1))

    document = dict(asset=dict(version='2.0', generator='TRIBES 3 Map Tool'), scene=0, scenes=[dict(nodes=[0])],
                    nodes=[dict(name=name, mesh=0)], meshes=[dict(name=name, primitives=primitives)],
                    materials=materials, buffers=[dict(byteLength=len(binary))], bufferViews=views, accessors=accessors)
    if textures:
        document.update(images=images, textures=textures)
    if extensions:
        document['extensionsUsed'] = sorted(extensions)
    data = json.dumps(document, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    data += b' ' * (-len(data) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    return b''.join((struct.pack('<4sII', b'glTF', 2, 28 + len(data) + len(binary)),
                     struct.pack('<I4s', len(data), b'JSON'), data,
                     struct.pack('<I4s', len(binary), b'BIN\0'), binary))


def render_mesh(data, bindings):
    sizes = {int(slot): record['size'] for slot, record in bindings.items() if 'size' in record}
    sections = render_sections(data, texture_sizes=sizes if data['format'] in (3, VENGEANCE_BSP) else None)
    for slot, label, points, uv, triangles, _, _, _, *extra in sections:
        values = extra[0] if extra and isinstance(extra[0], dict) else {}
        # The intake projection uses Unreal's triangle order and centimetres. The axis swap yields glTF winding.
        yield slot, label, [gltf_axes(point, .01) for point in points], uv, triangles, values


def export_meshes(manifest, destination):
    manifest = Path(manifest).resolve(strict=True)
    destination = Path(destination)
    if destination.suffix.casefold() != '.zip':
        raise ValueError('Save the private mesh assets as a ZIP file.')
    header, _, _ = load_intake(manifest)
    models = {**header.get('interiors', {}), **header.get('shapes', {})}
    if not models:
        raise ValueError('This intake contains no decoded model geometry.')
    inventory = header['files']
    folder = manifest.parent
    artwork = load_texture_index(folder, inventory)

    def read_texture(filename):
        path = (folder / filename).resolve(strict=True)
        if path.parent != folder:
            raise ValueError('Source texture outside the intake.')
        data = bounded(path, MAX_TEXTURE_BYTES)
        if sha256(data).hexdigest() != inventory[filename]:
            raise ValueError('Source texture changed after validation: ' + filename)
        return gltf_base_color(data)

    records, files = [], {}
    with TemporaryDirectory(dir=destination.parent) as work:
        archive = Path(work) / 'meshes.zip'
        with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
            for reference, filename in sorted(models.items()):
                checksum = inventory[filename]
                model, _ = load_intake_geometry(folder, filename, checksum)
                stem = Path(filename).stem
                bindings = artwork.get(filename, {})
                record = dict(reference=reference, geometry_sha256=checksum, levels=[])

                def add(suffix, sections, materials):
                    name = f'assets/meshes/{stem}{suffix}.glb'
                    data = make_glb(reference + suffix, sections, materials, read_texture)
                    bundle.writestr(name, data)
                    files[name] = sha256(data).hexdigest()
                    return name

                if model.get('render', True) and any(visible_material(model, row[0]) for row in model['triangles']):
                    record['levels'].append(dict(lod=0, file=add('', render_mesh(model, bindings), bindings)))
                for index, level in enumerate(model.get('lods', {}).get('secondary', ()), 1):
                    detail = dif_detail_mesh(model, level)
                    if any(visible_material(detail, row[0]) for row in detail['triangles']):
                        record['levels'].append(dict(lod=index, source_detail=level['source_detail'],
                                                     min_pixels=level['min_pixels'],
                                                     file=add(f'-lod{index}', render_mesh(detail, bindings), bindings)))
                collision = model.get('collision', {})
                if collision.get('triangles'):
                    sign = 1 if model['format'] in UNREAL_GEOMETRY else -1
                    points = [(x, z, sign * y) for x, y, z in collision['vertices_m']]
                    record['triangle_collision'] = add('-collision', [(0, 'Collision', points, (),
                                                                        collision['triangles'], {})], {})
                records.append(record)
            if not files:
                raise ValueError('The intake has no static render or collision triangles to export.')
            receipt = dict(format=1, kind='tribes-private-mesh-export', profile=header['profile'],
                           units='metres', up='+Y', handedness='right', models=records, files=files)
            bundle.writestr('mesh-export.json', json.dumps(receipt, indent=2, ensure_ascii=False))
        os.replace(archive, destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output_zip', type=Path)
    args = parser.parse_args()
    print(export_meshes(args.manifest, args.output_zip))
