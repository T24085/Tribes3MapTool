"""Decode Tribes 2 DIF geometry using a locally installed hxDif reader."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from dif_acoustics import decode_acoustics
from intake_contract import bounded, finite
from interior_geometry import MAX_GEOMETRY_BYTES, validate_geometry


def decode_geometry(dif, source, surface_ranges=None, *, include_acoustics=False):
    if dif.difVersion != 44 or not dif.interiors:
        raise ValueError('Expected a Tribes 2 DIF with a primary interior.')
    primary, *secondary = dif.interiors

    def read_level(interior):
        acoustic_detail = include_acoustics and interior is primary
        ranges = [] if surface_ranges is not None or acoustic_detail else None
        mesh = interior_mesh(interior, ranges)
        if surface_ranges is not None:
            surface_ranges.append(ranges)
        if acoustic_detail:
            try:
                mesh['acoustics'] = decode_acoustics(interior, ranges, len(mesh['triangles']))
            except (AttributeError, TypeError, ValueError):
                # Some DIF readers omit room data. Keep the renderable interior.
                pass
        return mesh

    geometry = {'format': 1, 'source': source, **read_level(primary)}
    if secondary:
        materials = geometry['materials']
        slots = {}
        for slot, material in enumerate(materials):
            slots.setdefault(material, slot)
        levels = []
        for interior in secondary:
            mesh = read_level(interior)
            remap = []
            for material in mesh.pop('materials'):
                if material not in slots:
                    slots[material] = len(materials)
                    materials.append(material)
                remap.append(slots[material])
            for triangle in mesh['triangles']:
                triangle[0] = remap[triangle[0]]
            levels.append({'source_detail': interior.detailLevel, 'min_pixels': interior.minPixels, **mesh})
        geometry['lods'] = {'primary': {'source_detail': primary.detailLevel, 'min_pixels': primary.minPixels},
                            'secondary': levels}
    return validate_geometry(geometry)


def interior_mesh(interior, surface_ranges=None):
    vertices = [[point.x, point.y, point.z] for point in interior.points]
    materials = list(interior.materialList)
    triangles = []
    for surface_id, surface in enumerate(interior.surfaces):
        start, count = surface.windingStart, surface.windingCount
        if not 0 <= start <= start + count <= len(interior.windings) or count < 3:
            raise ValueError('Invalid DIF surface winding.')
        strip = interior.windings[start:start + count]
        if any(not 0 <= index < len(vertices) for index in strip):
            raise ValueError('A DIF surface references a missing point.')
        slot, texgen, plane = surface.textureIndex, surface.texGenIndex, surface.planeIndex & 0x7fff
        if not (0 <= slot < len(materials) and 0 <= texgen < len(interior.texGenEQs)
                and 0 <= plane < len(interior.planes)):
            raise ValueError('A DIF surface references a missing material, UV plane, or normal.')
        normal_id = interior.planes[plane].normalIndex
        if not 0 <= normal_id < len(interior.normals):
            raise ValueError('A DIF surface references a missing normal.')
        normal = interior.normals[normal_id]
        nx, ny, nz = normal.x, normal.y, normal.z
        if not all(finite(axis, 2) for axis in (nx, ny, nz)):
            raise ValueError('Invalid DIF surface normal.')
        facing = -1 if surface.planeFlipped else 1
        uv = interior.texGenEQs[texgen]

        def texcoord(vertex, equation):
            x, y, z = vertices[vertex]
            return x * equation.x + y * equation.y + z * equation.z + equation.d

        first = len(triangles)
        for i in range(len(strip) - 2):
            a, b, c = strip[i:i + 3]
            if len({a, b, c}) < 3:
                continue
            ux, uy, uz = (vertices[b][axis] - vertices[a][axis] for axis in range(3))
            vx, vy, vz = (vertices[c][axis] - vertices[a][axis] for axis in range(3))
            aligned = facing * (nx * (uy * vz - uz * vy) + ny * (uz * vx - ux * vz) + nz * (ux * vy - uy * vx))
            if abs(aligned) <= 1e-10:
                continue
            if aligned < 0:
                b, c = c, b
            row = [slot, a, b, c]
            for vertex in (a, b, c):
                row.extend((texcoord(vertex, uv.planeX), texcoord(vertex, uv.planeY)))
            triangles.append(row)
        if surface_ranges is not None and first < len(triangles):
            surface_ranges.append((surface_id, first, len(triangles) - first))
    return {'materials': materials, 'vertices_m': vertices, 'triangles': triangles}


def load_decoder(decoder):
    if decoder.suffix.casefold() != '.py':
        raise ValueError('Choose the installed hxDif.py decoder.')
    decoder_hash = hashlib.sha256(bounded(decoder, MAX_GEOMETRY_BYTES)).hexdigest()
    spec = importlib.util.spec_from_file_location('t3_external_hxdif', decoder)
    if spec is None or spec.loader is None:
        raise ValueError('Could not load the installed hxDif.py decoder.')
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module.Dif.LoadFromByteArray, decoder_hash
    except AttributeError as error:
        raise ValueError('The decoder does not expose Dif.LoadFromByteArray.') from error
    except (ImportError, SyntaxError) as error:
        raise ValueError(f'The installed DIF decoder could not load: {error}') from error


def decode_bytes(raw, name, decoder, *, include_lighting=False, include_acoustics=False):
    if not name.casefold().endswith('.dif') or raw[:4] != b'\x2c\0\0\0':
        raise ValueError('Expected a version 44 Tribes 2 DIF.')
    read, decoder_hash = decoder
    try:
        dif = read(bytearray(raw))
        source = {'name': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'decoder_sha256': decoder_hash, 'dif_version': 44, 'lod': 0}
        ranges = [] if include_lighting else None
        geometry = decode_geometry(dif, source, ranges, include_acoustics=include_acoustics)
        if not include_lighting:
            return geometry
        return geometry, level_lighting(dif.interiors, source['sha256'], ranges)
    except Exception as error:
        raise ValueError(f'{name} could not be decoded: {error}') from error


def level_lighting(interiors, source_hash, ranges):
    from interior_lighting import archive_lighting

    for position, (interior, surface_ranges) in enumerate(zip(interiors, ranges, strict=True)):
        record, images = archive_lighting(interior, source_hash, surface_ranges)
        if record is not None:
            yield position, record, images


def decode(source, decoder):
    return decode_bytes(bounded(source, MAX_GEOMETRY_BYTES), source.name, load_decoder(decoder))


def geometry_bytes(document):
    raw = json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_GEOMETRY_BYTES:
        raise ValueError('The decoded interior exceeds the supported geometry size.')
    return raw


def write_geometry(document, output):
    raw = geometry_bytes(document)
    with NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def choose(decoder):
    import tkinter as tk
    from tkinter import filedialog
    from tk_runtime import configure_tk

    configure_tk()
    window = tk.Tk()
    window.withdraw()
    try:
        source = filedialog.askopenfilename(parent=window, title='Open a Tribes 2 interior',
                                            filetypes=[('Tribes 2 interior', '*.dif')])
        if source and (decoder is None or not decoder.is_file()):
            decoder = filedialog.askopenfilename(parent=window, title='Select the installed hxDif.py decoder',
                                                 filetypes=[('Python decoder', '*.py')])
        return (Path(source), Path(decoder)) if source and decoder else None
    finally:
        window.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('source', type=Path, nargs='?')
    parser.add_argument('--decoder', type=Path)
    parser.add_argument('--choose', action='store_true', help='Open the map editor file picker')
    args = parser.parse_args()
    if args.choose:
        selection = choose(args.decoder)
        if selection is None:
            return 0
        source, decoder = selection
    elif args.source is None or args.decoder is None:
        parser.error('Specify a DIF source and --decoder, or use --choose.')
    else:
        source, decoder = args.source, args.decoder
    try:
        write_geometry(decode(source, decoder), args.output)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
