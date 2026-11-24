"""Decode a Tribes 1 interior's default-state static mesh from DIS and DIG."""

from hashlib import sha256
from pathlib import Path, PurePosixPath
import struct

from intake_contract import finite
from interior_geometry import material_slots, validate_geometry
from t1_materials import material_names


DIG_VERSION = 7
MAX_ENTRIES = 250_000
SURFACE = struct.Struct("<6BH2I2B2x")
VERTEX = struct.Struct("<HH")


class Binary:
    def __init__(self, data, tag=None):
        self.data = memoryview(data)
        self.offset = 0
        if tag is None:
            return
        token, size = self.unpack("<4sI")
        if token != tag or not 0 <= len(data) - 8 - size <= 3:
            raise ValueError(f"Invalid Tribes {tag.decode()} record.")
        self.data = self.data[:8 + size]

    def take(self, count):
        stop = self.offset + count
        if count < 0 or stop > len(self.data):
            raise ValueError("Truncated Tribes interior record.")
        data = self.data[self.offset:stop]
        self.offset = stop
        return data

    def unpack(self, pattern):
        row = struct.Struct(pattern)
        return row.unpack(self.take(row.size))

    def array(self, pattern, count, limit=MAX_ENTRIES):
        if not 0 <= count <= limit:
            raise ValueError("Tribes interior array exceeds its supported size.")
        row = struct.Struct(pattern)
        return list(row.iter_unpack(self.take(row.size * count)))

    def uint(self):
        return self.unpack("<I")[0]


def dis_index(raw):
    reader = Binary(raw, b"ITRs")
    if reader.uint() != 3:
        raise ValueError("Expected a version 3 Tribes DIS index.")
    states = reader.array("<3I", reader.uint(), 256)
    lods = reader.array("<4I", reader.uint(), 4096)
    light_files = reader.array("<I", reader.uint(), 4096)
    light_names = reader.array("<I", reader.uint(), 256)
    pool_size = reader.uint()
    if not 1 <= pool_size <= 1024 * 1024 or not states or not lods:
        raise ValueError("The Tribes DIS has no usable state or name table.")
    pool = bytes(reader.take(pool_size))
    material_offset, linked = reader.unpack("<IB")
    if reader.offset != len(reader.data) or linked not in (0, 1):
        raise ValueError("Invalid Tribes DIS index trailer.")

    def name(offset, suffix=None):
        stop = pool.find(b"\0", offset)
        if not 0 <= offset < stop or offset > 0 and pool[offset - 1] != 0:
            raise ValueError("Invalid Tribes DIS resource offset.")
        value = pool[offset:stop].decode("latin-1")
        if not value.isprintable() or len(value) > 240:
            raise ValueError("Invalid Tribes DIS name.")
        if suffix and not value.casefold().endswith(suffix):
            raise ValueError(f"Expected a {suffix} resource in the Tribes DIS.")
        return value

    _, first, count = states[0]
    if not count or first + count > len(lods):
        raise ValueError("The default Tribes interior state has no usable LOD.")
    detail = max(range(first, first + count), key=lambda index: lods[index][0])
    lights = []
    start = lods[detail][2]
    for index, (offset,) in enumerate(light_names):
        try:
            label = name(offset)
            if start + index >= len(light_files):
                raise ValueError("Missing DIL reference in this DIS detail level.")
            lights.append((label, name(light_files[start + index][0], ".dil"), None))
        except ValueError as error:
            lights.append((f"Light state {index}", None, str(error)))
    return detail - first, name(lods[detail][1], ".dig"), name(material_offset, ".dml"), lights


def _dig_arrays(raw):
    reader = Binary(raw, b"PERS")
    length, = reader.unpack("<H")
    if length != 11 or bytes(reader.take(length)) != b"ITRGeometry":
        raise ValueError("The Tribes DIG does not contain ITRGeometry.")
    reader.take(length & 1)
    if reader.uint() != DIG_VERSION:
        raise ValueError("Expected a version 7 Tribes DIG geometry.")
    build = reader.uint()
    reader.take(28)  # Scale and bounds precede the packed arrays.
    surfaces, nodes, solid, empty, bits, vertices, points, texels, planes = reader.unpack("<9I")
    if (not 1 <= surfaces <= MAX_ENTRIES or not 3 <= vertices <= MAX_ENTRIES
            or not 3 <= points <= 65_536 or not 1 <= texels <= 65_536 or not 1 <= planes <= 65_536):
        raise ValueError("The Tribes DIG has invalid surface or vertex counts.")
    surface_rows = reader.take(surfaces * SURFACE.size)
    reader.take(8 * nodes + 12 * solid + 44 * empty + bits)
    corners = reader.take(vertices * VERTEX.size)
    positions = reader.array("<3f", points)
    uv = reader.array("<2f", texels)
    plane_rows = reader.array("<4f", planes)
    highest_mip, _ = reader.unpack("<2I")
    if highest_mip > 8 or reader.offset != len(reader.data):
        raise ValueError("Invalid Tribes DIG geometry trailer.")
    if (not all(all(finite(value, 20_000) for value in row) for row in positions)
            or not all(all(finite(value, 1000) for value in row) for row in uv)
            or not all(all(finite(value, 100_000) for value in row) for row in plane_rows)):
        raise ValueError("The Tribes DIG contains invalid coordinates or planes.")
    return build, surface_rows, corners, positions, uv, plane_rows, highest_mip


def _orientation(a, b, c, normal):
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    cross = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    return sum(value * axis for value, axis in zip(cross, normal))


def dig_mesh(raw, materials):
    build, surfaces, corners, points, uvs, planes, highest_mip = _dig_arrays(raw)
    triangles, ranges = [], []
    for surface, packed in enumerate(SURFACE.iter_unpack(surfaces)):
        flags, slot, sx, sy, ox, oy, plane, first, _, count, _ = packed
        # Link surfaces describe portals between interiors. They are open to render and collision.
        if flags & 1 or slot == 255:
            continue
        if slot >= len(materials) or plane >= len(planes) or count < 3 or (first + count) * VERTEX.size > len(corners):
            raise ValueError("A Tribes DIG surface refers to an invalid material, plane, or corner range.")
        face = list(VERTEX.iter_unpack(corners[first * VERTEX.size:(first + count) * VERTEX.size]))
        if any(point >= len(points) or uv >= len(uvs) for point, uv in face):
            raise ValueError("A Tribes DIG corner refers to an invalid position or UV.")
        direction = 1 if flags & 0x80 else -1
        normal = tuple(direction * value for value in planes[plane][:3])
        first_triangle = len(triangles)
        for index in range(1, count - 1):
            triangle = (face[0], face[index], face[index + 1])
            sign = _orientation(*(points[corner[0]] for corner in triangle), normal)
            if abs(sign) <= 1e-9:
                continue
            if sign < 0:
                triangle = triangle[0], triangle[2], triangle[1]
            positions = [corner[0] for corner in triangle]
            # Store source pixel coordinates so an artwork binding can normalize them to the bitmap dimensions.
            uv = [value for _, index in triangle for value in (
                uvs[index][0] * ((sx + 1) << highest_mip) + ox,
                uvs[index][1] * ((sy + 1) << highest_mip) + oy)]
            triangles.append([slot, *positions, *uv])
            if len(triangles) > MAX_ENTRIES:
                raise ValueError("The Tribes DIG exceeds the triangle limit.")
        if len(triangles) > first_triangle:
            limits = [(min(uvs[index][axis] for _, index in face) * extent,
                       max(uvs[index][axis] for _, index in face) * extent)
                      for axis, extent in enumerate(((sx + 1) << highest_mip, (sy + 1) << highest_mip))]
            ranges.append((surface, first_triangle, len(triangles) - first_triangle, ox, oy, limits))
    return [list(point) for point in points], triangles, (build, len(surfaces) // SURFACE.size, ranges)


def decode_interior(dis, geometry, material, lod):
    root_name, _, root = dis
    geo_name, _, geo = geometry
    mat_name, _, mat = material
    materials = [name or f"Material slot {index}"
                 for index, name in enumerate(material_names(mat, keep_path=True))]
    vertices, triangles, lighting_geometry = dig_mesh(geo, materials)
    source = dict(name=PurePosixPath(root_name).name, sha256=sha256(root).hexdigest(),
                  decoder_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), dig_version=DIG_VERSION, lod=lod,
                  geometry_name=PurePosixPath(geo_name).name, geometry_sha256=sha256(geo).hexdigest(),
                  material_name=PurePosixPath(mat_name).name, material_sha256=sha256(mat).hexdigest())
    mesh = validate_geometry(dict(format=3, source=source, materials=materials, vertices_m=vertices, triangles=triangles))
    if not material_slots(mesh):
        raise ValueError("The Tribes DIG contains no playable surfaces.")
    return mesh, lighting_geometry
