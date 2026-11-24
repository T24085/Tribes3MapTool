"""Verify triangle winding, UVs, and tool-surface handling for DIF decoding."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Record
import unittest


sys.path.insert(0, str(Path(__file__).with_name('tools') / 'community_map_editor'))
from interior_geometry import load_geometry, render_groups, validate_geometry
from t2_dif_reader import decode_geometry, write_geometry


def point(x, y, z, d=0):
    return Record(x=x, y=y, z=z, d=d)


class T2DIFGeometryTest(unittest.TestCase):
    def geometry(self):
        first = Record(windingStart=0, windingCount=4, textureIndex=0, texGenIndex=0,
                       planeIndex=0, planeFlipped=False)
        second = Record(windingStart=4, windingCount=3, textureIndex=1, texGenIndex=0,
                        planeIndex=0x8000, planeFlipped=True)
        interior = Record(
            points=[point(0, 0, 0), point(1, 0, 0), point(0, 1, 0), point(1, 1, 0)],
            materialList=['lush/floor', 'lush/TRIGGER'],
            surfaces=[first, second], windings=[0, 1, 2, 3, 0, 1, 2],
            texGenEQs=[Record(planeX=point(2, 0, 0, 3), planeY=point(0, 4, 0, 5))],
            planes=[Record(normalIndex=0)], normals=[point(0, 0, 1)],
        )
        source = dict(name='fixture.dif', sha256='a'*64, decoder_sha256='b'*64, dif_version=44, lod=0)
        return decode_geometry(Record(difVersion=44, interiors=[interior]), source)

    def test_preserves_planar_uvs_and_flips_only_the_back_facing_winding(self):
        data = self.geometry()
        self.assertEqual(data['triangles'], [
            [0, 0, 1, 2, 3, 5, 5, 5, 3, 9],
            [0, 1, 3, 2, 5, 5, 5, 9, 3, 9],
            [1, 0, 2, 1, 3, 5, 3, 9, 5, 5],
        ])
        groups = render_groups(data)
        self.assertEqual(len(groups), 1)
        slot, name, positions, coordinates, faces = groups[0]
        self.assertEqual((slot, name), (0, 'lush/floor'))
        self.assertEqual(positions[:3], [(0, 0, 0), (100, 0, 0), (0, -100, 0)])
        self.assertEqual(coordinates[:3], [(3, 5), (5, 5), (3, 9)])
        self.assertEqual(faces, [(0, 1, 2), (3, 4, 5)])

    def test_checks_serialized_geometry_before_draft_creation(self):
        data = self.geometry()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'geometry.json'
            write_geometry(data, path)
            loaded, digest = load_geometry(path)
            self.assertEqual(loaded, data)
            self.assertEqual(len(digest), 64)
        data['triangles'][0][2] = len(data['vertices_m'])
        with self.assertRaisesRegex(ValueError, 'Invalid interior triangle'):
            validate_geometry(data)
