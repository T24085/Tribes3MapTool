"""Fixture coverage for resumable texture reviews and their exact import inputs."""

from pathlib import Path
import shutil
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).with_name('tools') / 'community_map_editor'))
from texture_batches import (approved_replacements, create_batch, inspect, load_batch, make_color_revision,
                             retry_batch, save_json)


def png(width, height, color):
    def chunk(kind, value):
        payload = kind + value
        return struct.pack('>I', len(value)) + payload + struct.pack('>I', zlib.crc32(payload))

    header = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
    rows = (b'\0' + bytes(color) * width) * height
    return b'\x89PNG\r\n\x1a\n' + header + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


class TextureBatchTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name).resolve()
        self.library = folder / 'batches'
        self.draft = 'DraftT2001'
        source = folder / 'TextureSource.png'
        source.write_bytes(png(4, 2, (22, 40, 60, 80)))
        self.batch = create_batch(self.library, self.draft, [
            ('/Game/Community/Surfaces/TerrainColor.TerrainColor', 'color', 'Preserve tiling', source),
            ('/Game/Community/Surfaces/TerrainNormal.TerrainNormal', 'normal', 'Preserve normals', source),
            ('/Game/Community/Surfaces/TerrainMasks.TerrainMasks', 'masks', 'Preserve mask channels', source),
        ], shutil.copyfile)
        self.root, self.items = load_batch(self.library, self.draft, self.batch.name)

    def replacement(self, ident, width=8, rgba=(56, 80, 120, 200)):
        target = self.root / 'replacements' / (ident + '.png')
        target.write_bytes(png(width, 4, rgba))
        return target

    def test_checks_dimensions_and_reopens_partial_reviews(self):
        first = self.items['T_001']
        self.assertEqual(first['target'], [8, 4])
        with self.assertRaises(FileNotFoundError):
            inspect(self.root, first)
        self.replacement('T_001', width=4)
        with self.assertRaisesRegex(ValueError, 'Expected 8×4'):
            inspect(self.root, first)
        file = self.replacement('T_001')
        self.replacement('T_002')
        digest = inspect(self.root, first)[2]['sha256']
        rejected = inspect(self.root, self.items['T_002'])[2]['sha256']
        save_json(self.root / 'review.json', {'T_001': {'status': 'approved', 'sha256': digest},
                                               'T_002': {'status': 'rejected', 'sha256': rejected}})
        self.assertEqual(list(approved_replacements(self.root, self.items)), [(first, file, digest)])

        retry = retry_batch(self.library, self.draft, self.root, self.items)
        _, rows = load_batch(self.library, self.draft, retry.name)
        self.assertEqual([row['asset'] for row in rows.values()], [
            self.items['T_002']['asset'], self.items['T_003']['asset'],
        ])
        self.assertTrue(all(row['source'] == first['source'] for row in rows.values()))
        self.assertEqual(list((retry / 'replacements').iterdir()), [])

        self.replacement('T_001', rgba=(1, 2, 3, 60))
        with self.assertRaisesRegex(ValueError, 'changed after approval'):
            list(approved_replacements(self.root, self.items))

    def test_refuses_corrupted_original_and_non_local_batch(self):
        ident = 'T_001'
        self.replacement(ident)
        original = self.root / 'originals' / (ident + '.png')
        original.write_bytes(png(4, 2, (100, 40, 60, 80)))
        with self.assertRaisesRegex(ValueError, 'original PNG changed'):
            inspect(self.root, self.items[ident])
        with self.assertRaisesRegex(ValueError, 'owned by this draft'):
            load_batch(self.library, self.draft, '../' + self.batch.name)

    def test_retries_when_windows_temporarily_locks_the_staged_texture_directory(self):
        rename = Path.rename
        attempts = []

        def rename_after_scan(source, target):
            self.assertTrue((source / 'manifest.json').is_file())
            self.assertFalse(target.exists())
            attempts.append(target)
            if len(attempts) == 1:
                error = PermissionError('Directory is temporarily in use')
                error.winerror = 5
                raise error
            return rename(source, target)

        asset = self.items['T_001']['asset']
        file = self.root / 'originals' / 'T_001.png'
        with patch.object(Path, 'rename', rename_after_scan):
            batch = create_batch(self.library, self.draft, [(asset, 'color', '', file)], shutil.copyfile)
        self.assertEqual(attempts, [batch, batch])
        _, items = load_batch(self.library, self.draft, batch.name)
        self.assertEqual(items['T_001']['source'], self.items['T_001']['source'])

    def test_vengeance_and_ascend_drafts_can_open_and_retry_texture_batches(self):
        asset = self.items['T_001']['asset']
        file = self.root / 'originals' / 'T_001.png'
        for draft in ('DraftTV001', 'DraftTA001'):
            with self.subTest(draft=draft):
                batch = create_batch(self.library, draft, [(asset, 'color', '', file)], shutil.copyfile)
                root, items = load_batch(self.library, draft, batch.name)
                self.assertEqual(items['T_001']['source'], self.items['T_001']['source'])
                retry = retry_batch(self.library, draft, root, items)
                self.assertEqual(retry.parent, self.library / draft)
                self.assertEqual(load_batch(self.library, draft, retry.name)[1], items)

    def test_color_revision_tints_the_resized_original_without_overwriting_the_batch(self):
        from PIL import Image

        row = self.items['T_001']
        candidate = make_color_revision(self.library, self.draft, self.root, row, tint=(102, 80, 20), strength=50)
        root, rows = load_batch(self.library, self.draft, candidate.name)
        self.assertNotEqual(root, self.root)
        self.assertEqual(rows['T_001'], row)
        self.assertEqual(list(self.root.joinpath('replacements').iterdir()), [])
        _, edited, _ = inspect(root, rows['T_001'])
        with Image.open(edited) as image:
            self.assertEqual(image.size, (8, 4))
            self.assertEqual(image.getchannel('A').getextrema(), (80, 80))
            color = image.getpixel((0, 0))
            self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(color[:3], (62, 60, 40), strict=True)))
        self.assertEqual(list(approved_replacements(root, rows)), [])

    def test_color_reference_uses_visible_pixels_and_keeps_an_approved_replacement(self):
        from PIL import Image

        row = self.items['T_001']
        existing = self.replacement('T_001')
        digest = inspect(self.root, row)[2]['sha256']
        save_json(self.root / 'review.json', {'T_001': {'status': 'approved', 'sha256': digest}})
        reference = self.root / 'reference.png'
        image = Image.new('RGBA', (2, 1), (255, 255, 255, 0))
        image.putpixel((0, 0), (120, 70, 20, 255))
        image.save(reference)
        candidate = make_color_revision(self.library, self.draft, self.root, row, reference=reference, strength=100)
        root, rows = load_batch(self.library, self.draft, candidate.name)
        _, edited, _ = inspect(root, rows['T_001'])
        with Image.open(edited) as output:
            self.assertEqual(output.getpixel((0, 0)), (120, 70, 20, 200))
        self.assertEqual(list(approved_replacements(self.root, self.items)), [(row, existing, digest)])
        with self.assertRaisesRegex(ValueError, 'RGB or RGBA color texture'):
            make_color_revision(self.library, self.draft, self.root, self.items['T_002'], reference=reference, strength=100)


if __name__ == '__main__':
    unittest.main()
