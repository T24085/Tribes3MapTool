"""Source-room and exterior-portal data from the primary Tribes 2 DIF detail."""

import math


MAX_NODES = 32768
MAX_ZONES = 4095
MAX_SURFACES = 65536
MAX_PORTAL_CENTERS = 8192
MAX_SOLID_LEAVES = 16384
MAX_LEAF_FACES = 524288
OUTSIDE_VISIBLE = 1 << 4
NULL_SURFACE = 1 << 31


def point(source):
    return [source.x, source.y, source.z]


def indexed(rows, index):
    if type(index) is not int or not 0 <= index < len(rows):
        raise ValueError('Invalid DIF room or portal reference.')
    return rows[index]


def leaf_reference(index, leaf, solid):
    if not leaf:
        return index
    zone = index & 0x3fff
    return -1 if solid or zone == 0x0fff else -2 - zone


def exterior_centers(interior, zone, index):
    start, count = zone.portalStart, zone.portalCount
    if not 0 <= start <= start + count <= len(interior.zonePortalList):
        raise ValueError('Invalid DIF room portal range.')
    centers = []
    members = interior.zonePortalList[start:start + count]
    if len(set(members)) != len(members):
        raise ValueError('DIF room lists a portal more than once.')
    for member in members:
        portal = indexed(interior.portals, member)
        sides = (portal.zoneFront, portal.zoneBack)
        if index not in sides or any(not 0 <= side < len(interior.zones) for side in sides):
            raise ValueError('DIF portal is disconnected from its room.')
        if 0 not in sides:
            continue
        first, count = portal.triFanStart, portal.triFanCount
        if not 0 <= first <= first + count <= len(interior.windingIndices):
            raise ValueError('Invalid DIF portal fan range.')
        center = [0, 0, 0]
        for fan in interior.windingIndices[first:first + count]:
            begin, size = fan.windingStart, fan.windingCount
            if not 0 <= begin <= begin + size <= len(interior.windings) or size > 4096:
                raise ValueError('Invalid DIF portal winding range.')
            if size:
                for vertex in interior.windings[begin:begin + size]:
                    center = [a + b for a, b in zip(center, point(indexed(interior.points, vertex)))]
                center = [axis / size for axis in center]
                centers.append(center)
                if len(centers) > MAX_PORTAL_CENTERS:
                    raise ValueError('Too many DIF exterior portal centers.')
    return centers


def decode_acoustics(interior, surface_ranges, triangle_count):
    if (not 1 <= len(interior.bspNodes) <= MAX_NODES or not 1 <= len(interior.zones) <= MAX_ZONES
            or not 1 <= len(interior.surfaces) <= MAX_SURFACES):
        raise ValueError('DIF acoustic inventory is missing or exceeds the supported size.')
    nodes = []
    for node in interior.bspNodes:
        plane = indexed(interior.planes, node.planeIndex & 0x7fff)
        normal = indexed(interior.normals, plane.normalIndex)
        sign = -1 if node.planeIndex & 0x8000 else 1
        nodes.append([*(sign * axis for axis in (*point(normal), plane.planeDistance)),
                      leaf_reference(node.frontIndex, node.isFrontLeaf, node.isFrontSolid),
                      leaf_reference(node.backIndex, node.isBackLeaf, node.isBackSolid)])
    outside = [bool(surface.surfaceFlags & OUTSIDE_VISIBLE) for surface in interior.surfaces]
    centers = [[]]
    portal_total = 0
    for index, zone in enumerate(interior.zones[1:], 1):
        room = exterior_centers(interior, zone, index)
        portal_total += len(room)
        if portal_total > MAX_PORTAL_CENTERS:
            raise ValueError('Too many DIF exterior portal centers.')
        centers.append(room)
    data = dict(nodes=nodes, outside_surfaces=outside, portal_centers_m=centers,
                surface_runs=[list(row) for row in surface_ranges])
    vertex_count = len(interior.points)
    validate_acoustics(data, triangle_count, vertex_count)
    try:
        collision = decode_collision(interior)
        validate_collision(collision, nodes, len(outside), vertex_count)
    except (AttributeError, TypeError, ValueError):
        # Older decoder builds can expose rooms without the solid-leaf tables.
        pass
    else:
        data['collision'] = collision
    return data


def decode_collision(interior):
    solid = interior.bspSolidLeaves
    solid_faces = interior.solidLeafSurfaces
    if not 1 <= len(solid) <= MAX_SOLID_LEAVES or len(solid_faces) > MAX_LEAF_FACES:
        raise ValueError('Invalid DIF collision leaf inventory.')
    node_planes, solid_children, leaves, faces = [], [], [], []
    for node in interior.bspNodes:
        node_planes.append(node.planeIndex)
        solid_children.append([
            node.frontIndex & 0x3fff if node.isFrontLeaf and node.isFrontSolid else -1,
            node.backIndex & 0x3fff if node.isBackLeaf and node.isBackSolid else -1,
        ])
    count = 0
    for leaf in solid:
        begin, end = leaf.surfaceStart, leaf.surfaceStart + leaf.surfaceCount
        count += leaf.surfaceCount
        if not 0 <= begin <= end <= len(solid_faces) or count > MAX_LEAF_FACES:
            raise ValueError('Invalid DIF collision leaf surface range.')
        leaves.append([face for face in solid_faces[begin:end] if not face & NULL_SURFACE])
    for surface in interior.surfaces:
        begin, size = surface.windingStart, surface.windingCount
        if not 0 <= begin <= begin + size <= len(interior.windings) or not 3 <= size <= 32:
            raise ValueError('Invalid DIF collision fan winding range.')
        perimeter = [0, *range(1, size, 2), *range((size - 1) & ~1, 0, -2)]
        winding = [interior.windings[begin + offset] for offset in perimeter]
        fan = [vertex for index, vertex in enumerate(winding) if surface.fanMask & (1 << index)]
        plane = (surface.planeIndex & 0x7fff) | (0x8000 if surface.planeFlipped else 0)
        faces.append([plane, *fan])
    return dict(node_planes=node_planes, solid_children=solid_children, leaves=leaves, faces=faces)


def coordinates(row, length, bound):
    return (isinstance(row, list) and len(row) == length
            and all(type(value) in (float, int) and -bound <= value <= bound for value in row))


def validate_acoustics(data, triangle_count, vertex_count):
    required = {'nodes', 'outside_surfaces', 'portal_centers_m', 'surface_runs'}
    if not isinstance(data, dict) or not required <= data.keys() <= required | {'collision'}:
        raise ValueError('Invalid DIF acoustic fields.')
    nodes, outside, centers, ranges = (data[key] for key in (
        'nodes', 'outside_surfaces', 'portal_centers_m', 'surface_runs'))
    if (not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES
            or not isinstance(outside, list) or not 1 <= len(outside) <= MAX_SURFACES
            or any(type(flag) is not bool for flag in outside)
            or not isinstance(centers, list) or not 1 <= len(centers) <= MAX_ZONES or centers[0] != []
            or any(not isinstance(zone, list) for zone in centers)
            or sum(map(len, centers)) > MAX_PORTAL_CENTERS
            or any(not coordinates(center, 3, 1e7) for zone in centers for center in zone)
            or not isinstance(ranges, list) or not 1 <= len(ranges) <= len(outside)):
        raise ValueError('Invalid DIF acoustic inventory.')
    for node in nodes:
        if (not isinstance(node, list) or len(node) != 6 or not coordinates(node[:4], 4, 1e7)
                or not .8 <= math.hypot(*node[:3]) <= 1.2
                or any(type(index) is not int or not -len(centers) - 1 <= index < len(nodes)
                       for index in node[4:])):
            raise ValueError('Invalid DIF acoustic BSP node.')
    visited = bytearray(len(nodes))
    for root in range(len(nodes)):
        if visited[root] == 2:
            continue
        todo = [(root, False)]
        while todo:
            index, leaving = todo.pop()
            if leaving:
                visited[index] = 2
            elif visited[index] == 1:
                raise ValueError('Cyclic DIF acoustic BSP tree.')
            elif not visited[index]:
                visited[index] = 1
                todo.append((index, True))
                todo.extend((child, False) for child in nodes[index][4:] if child >= 0)
    position, previous_surface = 0, -1
    for row in ranges:
        if (not isinstance(row, list) or len(row) != 3 or any(type(value) is not int for value in row)
                or not previous_surface < row[0] < len(outside) or row[1] != position or row[2] <= 0):
            raise ValueError('Invalid DIF acoustic face mapping.')
        previous_surface = row[0]
        position += row[2]
    if position != triangle_count:
        raise ValueError('DIF acoustic faces do not cover the primary geometry.')
    if 'collision' in data:
        validate_collision(data['collision'], nodes, len(outside), vertex_count)


def validate_collision(data, nodes, face_count, vertex_count):
    if not isinstance(data, dict) or data.keys() != {'node_planes', 'solid_children', 'leaves', 'faces'}:
        raise ValueError('Invalid DIF acoustic collision fields.')
    planes, children, leaves, faces = (data[key] for key in ('node_planes', 'solid_children', 'leaves', 'faces'))
    if (any(not isinstance(items, list) for items in (planes, children, leaves, faces))
            or len(planes) != len(nodes) or len(children) != len(nodes)
            or not 1 <= len(leaves) <= MAX_SOLID_LEAVES or len(faces) != face_count
            or any(type(plane) is not int or not 0 <= plane <= 0xffff for plane in planes)):
        raise ValueError('Invalid DIF acoustic collision inventory.')
    for node, row in zip(nodes, children):
        if (not isinstance(row, list) or len(row) != 2
                or any(type(leaf) is not int or not -1 <= leaf < len(leaves)
                       or (leaf >= 0 and child != -1) for leaf, child in zip(row, node[4:]))):
            raise ValueError('Invalid DIF acoustic collision child.')
    if (any(not isinstance(row, list) for row in leaves) or sum(map(len, leaves)) > MAX_LEAF_FACES
            or any(type(face) is not int or not 0 <= face < face_count for row in leaves for face in row)):
        raise ValueError('Invalid DIF acoustic collision leaf face.')
    for face in faces:
        if (not isinstance(face, list) or not 1 <= len(face) <= 33
                or type(face[0]) is not int or not 0 <= face[0] <= 0xffff
                or any(type(vertex) is not int or not 0 <= vertex < vertex_count for vertex in face[1:])):
            raise ValueError('Invalid DIF acoustic collision fan.')
