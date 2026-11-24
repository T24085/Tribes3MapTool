"""Exercise cubemap orientation, seam detection, and approved DDS export."""

import hashlib
import importlib
import json
from pathlib import Path
import shutil
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

try:
    from PIL import Image
except ImportError:
    Image = None

sys.path.insert(0, str(Path(__file__).with_name('tools') / 'community_map_editor'))
from texture_batches import create_batch, inspect, load_batch, save_json
from sky_contract import FACES, collect_faces, verify_sky_build

if Image is not None:
    from sky_cube import build_cube, direction, infer_faces, load_plan, seam_report


@unittest.skipIf(Image is None, 'Texture review uses Pillow')
class SkyCubeTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.library = self.folder / 'drafts'
        self.draft = 'DraftT2001'
        entries = []
        for face in FACES:
            name = 'T_Horizon_' + face.replace('+', 'P').replace('-', 'N')
            source = self.folder / f'{name}.png'
            self.fixture_face(face, 4).save(source)
            entries.append((f'/Game/Community/Sky/{name}.{name}', 'sky', 'Match adjoining edges', source))
        batch = create_batch(self.library, self.draft, entries, shutil.copyfile)
        self.batch, self.items = load_batch(self.library, self.draft, batch.name)
        self.plan = infer_faces(self.items)
        self.assertEqual(tuple(self.plan), FACES)
        for face in FACES:
            self.fixture_face(face, 8).save(self.replacement(face))

    def fixture_face(self, face, side):
        image = Image.new('RGBA', (side, side))
        for x in range(side):
            for y in range(side):
                ray = direction(face, x * 2 / (side - 1) - 1, y * 2 / (side - 1) - 1)
                image.putpixel((x, y), (*[round((position + 1) * 127.5) for position in ray], 255))
        return image

    def replacement(self, face):
        return self.batch / 'replacements' / (self.plan[face]['id'] + '.png')

    def approve(self):
        reviews = {row['id']: {'status': 'approved', 'sha256': inspect(self.batch, row)[2]['sha256']}
                   for row in self.items.values()}
        save_json(self.batch / 'review.json', reviews)

    def test_joins_all_twelve_edges_and_recovers_a_quarter_turn(self):
        faces = collect_faces(self.batch, self.items, self.plan)
        edges = seam_report(faces, replacement=True)
        self.assertEqual(len(edges), 12)
        self.assertTrue(all(edge['peak'] == 0 for edge in edges))

        self.fixture_face('+Y', 8).transpose(Image.Transpose.ROTATE_90).save(self.replacement('+Y'))
        broken = seam_report(collect_faces(self.batch, self.items, self.plan), replacement=True)
        flagged = [edge for edge in broken if edge['review']]
        self.assertTrue(flagged)
        self.assertTrue(all('+Y' in edge['first'] or '+Y' in edge['second'] for edge in flagged))

        self.plan['+Y']['rotation'] = 90
        save_json(self.batch / 'sky-faces.json', self.plan)
        saved = load_plan(self.batch, self.items)
        fixed = seam_report(collect_faces(self.batch, self.items, saved), replacement=True)
        self.assertTrue(all(edge['peak'] == 0 for edge in fixed))

    def test_build_requires_all_approvals_and_verifies_its_previous_dds(self):
        with self.assertRaisesRegex(ValueError, 'Approve T_001'):
            build_cube(self.batch, self.items, self.plan)
        self.approve()
        output = build_cube(self.batch, self.items, self.plan)
        self.assertEqual(build_cube(self.batch, self.items, self.plan), output)

        file = output / 'SkyCube.dds'
        data = file.read_bytes()
        self.assertEqual(len(data), 128 + 6 * 8 * 8 * 4)
        self.assertEqual(data[:4], b'DDS ')
        header = struct.unpack('<31I', data[4:128])
        self.assertEqual(header[:7], (124, 0x100F, 8, 8, 32, 0, 0))
        self.assertEqual(header[27], 0xFE00)
        start = 128 + 4 * 8 * 8 * 4
        self.assertEqual(data[start:start + 4], bytes((255, 255, 0, 255)))
        receipt = json.loads((output / 'sky-report.json').read_bytes())
        self.assertEqual(receipt['dds_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual([row['face'] for row in receipt['faces']], list(FACES))
        contact = output / 'SkyFaces.png'
        preview = contact.read_bytes()
        self.assertEqual(receipt['contact_sheet_sha256'], hashlib.sha256(preview).hexdigest())
        with Image.open(contact) as sheet:
            self.assertEqual(sheet.size, (1268, 530))
            self.assertEqual(sheet.getpixel((116, 159))[0], 255)
            self.assertEqual(sheet.getpixel((320, 159))[0], 255)
            self.assertEqual(sheet.getpixel((532, 159))[0], 0)
        contact.write_bytes(preview + b'extra')
        with self.assertRaisesRegex(ValueError, 'could not be verified'):
            verify_sky_build(self.batch, self.items, output.name)
        contact.write_bytes(preview)

        file.write_bytes(data[:-1] + b'\0')
        with self.assertRaisesRegex(ValueError, 'could not be verified'):
            build_cube(self.batch, self.items, self.plan)

    def test_explicit_original_mode_preserves_sources_and_can_build_without_edits(self):
        for face in FACES:
            self.replacement(face).unlink()
        build = build_cube(self.batch, self.items, self.plan, use_originals=True)
        dds, _ = verify_sky_build(self.batch, self.items, build.name)
        report = json.loads((build / 'sky-report.json').read_bytes())
        self.assertTrue(report['uses_originals'])
        self.assertEqual(report['size'], 4)
        self.assertEqual(dds.stat().st_size, 128 + 24 * 4 * 4)
        original = self.batch / 'originals' / (self.plan['+Y']['id'] + '.png')
        original.write_bytes(original.read_bytes() + b'extra')
        with self.assertRaisesRegex(ValueError, 'original PNG changed'):
            verify_sky_build(self.batch, self.items, build.name)

    def test_import_handoff_rechecks_header_and_current_approvals(self):
        self.approve()
        build = build_cube(self.batch, self.items, self.plan)
        dds, digest = verify_sky_build(self.batch, self.items, build.name)
        raw = dds.read_bytes()
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError, 'owned by this texture batch'):
            verify_sky_build(self.batch, self.items, '../' + build.name)

        report_path = build / 'sky-report.json'
        report = json.loads(report_path.read_bytes())
        malformed = raw[:112] + bytes(4) + raw[116:]
        self.assertNotEqual(malformed, raw)
        dds.write_bytes(malformed)
        save_json(report_path, dict(report, dds_sha256=hashlib.sha256(malformed).hexdigest()))
        with self.assertRaisesRegex(ValueError, 'could not be verified'):
            verify_sky_build(self.batch, self.items, build.name)

        dds.write_bytes(raw)
        save_json(report_path, report)
        save_json(self.batch / 'review.json', {})
        with self.assertRaisesRegex(ValueError, 'Approve T_001'):
            verify_sky_build(self.batch, self.items, build.name)

    def test_editor_import_requires_texture_cube_and_reuses_verified_revision(self):
        self.approve()
        build = build_cube(self.batch, self.items, self.plan)
        path = build / 'SkyCube.dds'
        destination = (f'/Game/Community/{self.draft}/TextureRevisions/Sky/'
                       f'B_{self.batch.name[6:]}/R_{build.name[4:]}')
        object_path = destination + '/SkyCube.SkyCube'

        class TextureCube:
            def get_path_name(self):
                return object_path

            def get_editor_property(self, name):
                return {'asset_import_data': Mock(get_first_filename=Mock(return_value=str(path)))}[name]

        cube = TextureCube()
        unreal = Mock(TextureCube=TextureCube)
        assets = unreal.EditorAssetLibrary
        assets.does_asset_exist.return_value = False
        task = unreal.AssetImportTask.return_value
        task.get_objects.return_value = [object()]
        options = {'T3_TEXTURE_LIBRARY': str(self.library), 'T3_CONVERTER_PYTHON': sys.executable,
                   'T3_CONVERTER_TOOL': str(Path(__file__).parent)}
        selection = json.dumps({'batch': self.batch.name, 'sky': build.name})
        with patch.dict(sys.modules, {'unreal': unreal}), patch.dict('os.environ', options):
            editor = importlib.import_module('texture_batches_unreal')
            with patch.object(editor.subprocess, 'run', return_value=Mock(returncode=0, stdout=selection)):
                with self.assertRaisesRegex(ValueError, 'expected TextureCube'):
                    editor.review_and_import(self.draft, None)
                assets.save_loaded_asset.assert_not_called()
                task.get_objects.return_value = [cube]
                self.assertEqual(editor.review_and_import(self.draft, None), [cube])
                self.assertEqual(dict(call.args for call in task.set_editor_property.call_args_list), {
                    'automated': True, 'destination_path': destination, 'filename': str(path),
                    'replace_existing': False, 'save': False, 'factory': unreal.TextureFactory.return_value,
                })
                receipt = json.loads((build / 'sky-imported.json').read_bytes())
                self.assertEqual(receipt['asset'], object_path)
                assets.set_metadata_tag.assert_called_once_with(cube, editor.SKY_DDS_TAG, receipt['dds_sha256'])
                assets.sync_browser_to_objects.assert_called_once_with([object_path])

                assets.does_asset_exist.return_value = True
                assets.load_asset.return_value = cube
                assets.get_metadata_tag.return_value = 'different checksum'
                with self.assertRaisesRegex(ValueError, 'cubemap path is already in use'):
                    editor.review_and_import(self.draft, None)
                assets.get_metadata_tag.return_value = receipt['dds_sha256']
                assets.sync_browser_to_objects.reset_mock()
                self.assertIs(editor.import_sky_cube(self.draft, self.batch, self.items, build.name), cube)
                assets.sync_browser_to_objects.assert_not_called()
                self.assertEqual(editor.review_and_import(self.draft, None), [cube])
                self.assertEqual(unreal.AssetImportTask.call_count, 2)

    def test_rejects_duplicate_assignments_and_changed_approved_faces(self):
        self.approve()
        duplicate = dict(self.plan, **{'-X': self.plan['+X']})
        with self.assertRaisesRegex(ValueError, 'different texture'):
            collect_faces(self.batch, self.items, duplicate)
        Image.new('RGBA', (8, 8), (1, 10, 5, 255)).save(self.replacement('-Z'))
        with self.assertRaisesRegex(ValueError, 'Approve T_006'):
            build_cube(self.batch, self.items, self.plan)


if __name__ == '__main__':
    unittest.main()
