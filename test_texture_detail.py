"""Exercise the surface detail merge through the texture revision contract."""

import importlib.util
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent / "tools/community_map_editor"))
from texture_batches import approved_replacements, create_batch, inspect, load_batch
from texture_detail import merge_revision, reorient, tangent_xy, tile_detail, tile_edges


class TextureDetailTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.library = self.root / "Batches"
        self.draft = "DraftT2001"

    def image(self, name, mode, color):
        path = self.root / (name + ".png")
        with Image.new(mode, (4, 2), color) as image:
            image.save(path)
        return path

    def batch(self, role, mode, color):
        path = self.image("Original", mode, color)
        batch = create_batch(self.library, self.draft, [
            ("/Game/Community/TestTexture.TestTexture", role, "", path),
        ], shutil.copyfile)
        root, items = load_batch(self.library, self.draft, batch.name)
        return root, items["T_001"]

    def texture(self, candidate):
        root, rows = load_batch(self.library, self.draft, candidate.name)
        _, file, info = inspect(root, rows["T_001"])
        self.assertEqual(info["size"], [8, 4])
        self.assertEqual(list(approved_replacements(root, rows)), [])
        return Image.open(file)

    def test_reorients_tangent_normals_with_flat_identity(self):
        self.assertEqual(reorient(128, 128, 128, 128, 1), (128, 128, 255))
        self.assertEqual(reorient(190, 128, 128, 128, 1), reorient(128, 128, 190, 128, 1))
        result = reorient(190, 128, 128, 190, 1)
        x, y, z = tangent_xy(*result[:2])
        self.assertGreater(x, .38)
        self.assertGreater(y, .45)
        self.assertGreater(z, .68)
        self.assertLess(z, .83)
        self.assertEqual(reorient(190, 128, 128, 190, 0), reorient(190, 128, 128, 128, 1))

    def test_normal_merge_saves_unapproved_revision_with_original_alpha(self):
        root, row = self.batch("normal", "RGBA", (128, 128, 255, 72))
        detail = self.image("DetailNormal", "RGB", (190, 128, 220))
        revision = merge_revision(self.library, self.draft, root, row, detail,
                                  operation="normal", strength=100, repeats=2)
        with self.texture(revision) as image:
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getpixel((7, 3))[:2], (190, 128))
            self.assertEqual(image.getchannel("A").getextrema(), (72, 72))
        self.assertFalse(any((root / "replacements").iterdir()))

    def test_roughness_and_occlusion_touch_only_the_selected_packed_channel(self):
        root, row = self.batch("masks", "RGBA", (80, 100, 146, 255))
        detail = self.image("PackedDetail", "RGBA", (10, 95, 224, 0))
        roughness = merge_revision(self.library, self.draft, root, row, detail,
                                   operation="roughness", strength=100, repeats=1,
                                   source_channel="B", target_channel="G")
        with self.texture(roughness) as image:
            self.assertEqual(image.getpixel((0, 0)), (80, 196, 146, 255))

        occlusion = merge_revision(self.library, self.draft, root, row, detail,
                                   operation="occlusion", strength=50, repeats=1,
                                   source_channel="A", target_channel="A")
        with self.texture(occlusion) as image:
            self.assertEqual(image.getpixel((0, 0)), (80, 100, 146, 128))
        with self.assertRaisesRegex(ValueError, "source channel"):
            merge_revision(self.library, self.draft, root, row, detail, operation="roughness",
                           strength=100, repeats=1, target_channel="G")

    def test_grayscale_detail_and_non_divisible_tiles(self):
        root, row = self.batch("masks", "LA", (160, 100))
        detail = self.image("Gray", "L", 128)
        revision = merge_revision(self.library, self.draft, root, row, detail,
                                  operation="occlusion", strength=100, repeats=3, target_channel="L")
        with self.texture(revision) as image:
            self.assertEqual(image.getpixel((7, 3)), (80, 100))
        with Image.new("L", (3, 4), 93) as image:
            with tile_detail(image, (7, 8), 3) as tiled:
                self.assertEqual(tiled.getextrema(), (93, 93))

    def test_tile_check_reports_each_axis_independently(self):
        with Image.new("L", (3, 2)) as image:
            image.putdata([0, 0, 40, 20, 0, 40])
            horizontal, vertical = tile_edges(image)
            self.assertEqual(horizontal, (30.0, 40))
            self.assertAlmostEqual(vertical[0], 20 / 3)
            self.assertEqual(vertical[1], 20)

    def test_material_apply_checks_the_assigned_value_after_an_unreal_false_result(self):
        class Instance:
            pass

        revision = Instance()
        material = Mock()
        texture = Mock()
        texture.get_path_name.return_value = "/Game/Community/Revision.Revision"
        api = Mock()
        api.set_material_instance_texture_parameter_value.return_value = False
        api.get_material_instance_texture_parameter_value.return_value = texture
        unreal = Mock(MaterialInstanceConstant=Instance, MaterialEditingLibrary=api)
        tools = unreal.AssetToolsHelpers.get_asset_tools.return_value
        tools.create_asset.return_value = revision
        module = importlib.util.spec_from_file_location(
            "surface_detail_material_test", Path(__file__).parent / "tools/community_map_editor/draft_materials.py"
        )
        native = importlib.util.module_from_spec(module)
        with patch.dict(sys.modules, {"unreal": unreal}):
            module.loader.exec_module(native)
        target = "/Game/Community/DraftT2001/TextureRevisions/Materials"
        self.assertIs(native.make_revision(material, {"Surface": texture}, target), revision)
        api.set_material_instance_texture_parameter_value.assert_called_once_with(revision, "Surface", texture)
        unreal.EditorAssetLibrary.save_loaded_asset.assert_called_once_with(revision, only_if_is_dirty=False)
        api.get_material_instance_texture_parameter_value.return_value = None
        with self.assertRaisesRegex(ValueError, "Could not verify"):
            native.make_revision(material, {"Surface": texture}, target)


if __name__ == "__main__":
    unittest.main()
