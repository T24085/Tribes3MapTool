"""Regression coverage for static DTS conversion and verified mission handoff."""

import hashlib
from io import BytesIO
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Record
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).with_name('tools') / 'community_map_editor'))
from intake_contract import load_intake, load_intake_geometry
from interior_geometry import render_groups
from mesh_mappings import placements
from model_textures import load_texture_index
from t2_dts_reader import FAN, INDEXED, STRIP, decode_bytes
from t2_reader import convert, unpack_intake


def sample_shape():
    root = Record(parent_index=-1)
    leaf = Record(parent_index=0)
    identity = Record(normalized_floats=lambda: (0, 0, 0, 1))
    strip = Record(start=0, num_elements=4, mat_index=INDEXED | STRIP)
    fan = Record(start=0, num_elements=4, mat_index=FAN | 1)
    mesh = Record(verts=[(0, 0, 0), (2, 0, 0), (0, 1, 0), (0, 0, 1)], verts_per_frame=4,
                  indices=[0, 1, 2, 3], tverts=[(0, 0), (1, 0), (0, 1), (1, 1)], norms=[],
                  primitives=[strip, fan], flags=0, mesh_type=0, num_frames=1, num_mat_frames=1)
    return Record(source_version=23, sequences=[], nodes=[root, leaf], default_rotations=[identity, identity],
                  default_translations=[(3, 0, 0), (0, 4, 0)],
                  materials=[Record(name='Metal'), Record(name='Panel')], meshes=[mesh, None],
                  sub_shape_first_object=[0], sub_shape_num_objects=[1],
                  objects=[Record(num_meshes=2, node_index=1, start_mesh_index=0)],
                  details=[Record(size=8, sub_shape_num=0, object_detail_num=1),
                           Record(size=64, sub_shape_num=0, object_detail_num=0)])


RAW = bytes([23]) + bytes(15)


class T2DTSGeometryTest(unittest.TestCase):
    def test_highest_detail_uses_the_node_chain_and_material_primitives(self):
        data = decode_bytes(RAW, 'fixture.dts', (lambda _: sample_shape(), '0'*64))
        self.assertEqual(data['vertices_m'], [[3, 4, 0], [5, 4, 0], [3, 5, 0], [3, 4, 1]])
        self.assertEqual(data['source']['lod'], 0)
        self.assertEqual(data['source']['sha256'], hashlib.sha256(RAW).hexdigest())
        self.assertEqual([row[:4] for row in data['triangles']],
                         [[0, 0, 1, 2], [0, 2, 1, 3], [1, 0, 1, 2], [1, 0, 2, 3]])
        groups = render_groups(data)
        self.assertEqual([group[1] for group in groups], ['Metal', 'Panel'])
        self.assertEqual(groups[0][2][0], (300, -400, 0))
        self.assertEqual(groups[0][3][:3], [(0, 0), (1, 0), (0, 1)])

    def test_rejects_bad_indices_before_emitting_geometry(self):
        shape = sample_shape()
        shape.meshes[0].indices[0] = 50
        with self.assertRaisesRegex(ValueError, 'missing vertex'):
            decode_bytes(RAW, 'fixture.dts', (lambda _: shape, '0'*64))

    def test_vl2_import_keeps_missing_models_in_review_and_verifies_the_zip(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder/'Test.vl2'
            mission = '''new SimGroup(MissionGroup){
              new TerrainBlock(){terrainFile="Test.ter";position="0 0 0";squareSize="8";};
              new TSStatic(Prop){shapeName="Shapes/Fixture.dts";position="1 2 3";};
              new TSStatic(Missing){shapeName="Shapes/Missing.dts";position="0 0 0";};};'''
            terrain = b"\2" + struct.pack("<H", 32)*65536 + bytes(65536) + bytes(8)
            with ZipFile(source,'w') as archive:
                archive.writestr('missions/Test.mis', mission)
                archive.writestr('missions/Test.ter', terrain)
                archive.writestr('Shapes/Fixture.dts', RAW)
            with patch('t2_dts_reader.load_decoder', return_value=(lambda _: sample_shape(), '0'*64)):
                manifest = convert(source, folder/'intakes', 'missions/Test.mis', dts_decoder=folder)
            header, terrain_data, rows = load_intake(manifest)
            name = header['shapes']['tsstatic:shapes/fixture.dts']
            self.assertEqual(header['format'], 5)
            self.assertIn('not found', header['shape_review']['tsstatic:shapes/missing.dts'])
            model, _ = load_intake_geometry(manifest.parent, name, header['files'][name])
            self.assertEqual(model['format'], 2)
            plan = placements(rows, terrain_data, {'tsstatic:shapes/fixture.dts': '/Game/Community/Fixture'})
            self.assertEqual(plan[0][2], (105, -210, 315))
            bundle = folder/'intake.zip'
            with ZipFile(bundle,'w') as archive:
                for item in manifest.parent.iterdir():
                    archive.write(item, 'intake/' + item.name)
            self.assertEqual(load_intake(unpack_intake(bundle, folder/'reopened'))[0], header)

    def test_textured_model_prefers_its_own_sibling_and_carries_verified_colors(self):
        from PIL import Image

        def picture(color):
            raw = BytesIO()
            Image.new('RGBA', (4, 4), color).save(raw, format='PNG')
            return raw.getvalue()

        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'Mission.vl2'
            red, blue = picture((255,0,0,255)), picture((0,0,255,230))
            with ZipFile(source,'w') as archive:
                archive.writestr('missions/Test.mis', '''new SimGroup(MissionGroup){
                     new MissionArea(PlayArea){area="0 0 32 32";};
                     new TerrainBlock(){terrainFile="Test.ter";position="0 0 0";squareSize="8";};
                     new TSStatic(Prop){shapeName="Props/A/Fixture.dts";position="0 0 0";};};''')
                archive.writestr('missions/Test.ter', b"\2" + struct.pack("<H",32)*65536 + bytes(65536) + bytes(8))
                archive.writestr('Props/A/Fixture.dts', RAW)
                archive.writestr('props/a/METAL.PNG', blue)
                archive.writestr('Metal.png', red)
                archive.writestr('textures/a/Panel.png', red)
                archive.writestr('textures/b/Panel.png', blue)
            with patch('t2_dts_reader.load_decoder', return_value=(lambda _: sample_shape(), '0'*64)):
                manifest=convert(source, root/'intakes', 'missions/Test.mis', dts_decoder=root)
            header, _, _ = load_intake(manifest)
            self.assertEqual(header['format'], 6)
            self.assertIn('minimap.png', header['files'])
            index = load_texture_index(manifest.parent, header['files'])
            model = header['shapes']['tsstatic:props/a/fixture.dts']
            record = index[model]['0']
            self.assertEqual(record['source_sha256'], hashlib.sha256(blue).hexdigest())
            self.assertIn('Multiple PNG files',index[model]['1']['reason'])
            with Image.open(manifest.parent/record['file']) as image:
                self.assertEqual(image.getpixel((0,0)), (0,0,255,230))
            portable = root/'portable.zip'
            with ZipFile(portable,'w') as archive:
                for item in manifest.parent.iterdir():
                    archive.write(item, item.name)
            self.assertEqual(load_intake(unpack_intake(portable,root/'reopened'))[0],header)
            (manifest.parent/record['file']).write_bytes(red)
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                load_intake(manifest)
