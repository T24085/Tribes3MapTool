"""Remastered texture folders and Ascend base-color name matching (#9)."""

from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from ascend_materials import color_parameter
from remastered_textures import RemasteredTextures, remaster_name, validate_folder

try:
    from PIL import Image
except ImportError:  # Pillow is a Map Tool requirement; skip image cases without it.
    Image = None


def png(width, height, mode="RGB"):
    output = BytesIO()
    Image.new(mode, (width, height), (90, 120, 60)).save(output, "PNG")
    return output.getvalue()


class NameTests(unittest.TestCase):
    def test_remaster_file_names_follow_the_object_path(self):
        self.assertEqual(remaster_name("Arch_Drydock.Tex.T_DryDock_Crane_01_DIF"),
                         "Arch_Drydock__Tex__T_DryDock_Crane_01_DIF.png")

    def test_ascend_color_parameter_names(self):
        for name in ("Dif", "DIF1", "DIF02", "Diffuse", "Base_Color", "albedo"):
            self.assertTrue(color_parameter(name), name)
        for name in ("DetailDif", "DIF_Overlay2", "Mask", "MSK", "Normal", "Dif123"):
            self.assertFalse(color_parameter(name), name)

    def test_folder_setting_must_be_an_existing_absolute_folder(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(validate_folder(directory), Path(directory))
            for value in ("relative/folder", str(Path(directory) / "missing"), "", 7):
                with self.assertRaisesRegex(ValueError, "remastered texture folder"):
                    validate_folder(value)


@unittest.skipIf(Image is None, "Pillow is not installed")
class RemasterTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, object_path, data):
        (self.folder / remaster_name(object_path)).write_bytes(data)

    def test_uses_a_larger_same_shape_remaster_as_rgba(self):
        self.write("Pkg.Tex.T_Rock_DIF", png(64, 32))
        textures = RemasteredTextures(self.folder)
        (data, size, receipt), note = textures.load("Pkg.Tex.T_Rock_DIF", [32, 16])
        self.assertIsNone(note)
        self.assertEqual(size, [64, 32])
        self.assertEqual(data[24:26], b"\x08\x06")  # 8-bit RGBA, as intakes require
        self.assertEqual(receipt["name"], "Pkg__Tex__T_Rock_DIF.png")
        self.assertEqual(textures.load("pkg.tex.t_rock_dif", [32, 16])[0][0], data)  # cached, any case

    def test_keeps_the_original_and_says_why(self):
        self.write("Pkg.Tex.Wide", png(64, 64))
        self.write("Pkg.Tex.Small", png(16, 8))
        (self.folder / remaster_name("Pkg.Tex.Broken")).write_bytes(b"not a png")
        textures = RemasteredTextures(self.folder)
        self.assertEqual(textures.load("Pkg.Tex.Missing", [32, 16]), (None, None))
        for path, reason in (("Pkg.Tex.Wide", "shape"), ("Pkg.Tex.Small", "shape"), ("Pkg.Tex.Broken", "not used")):
            result, note = textures.load(path, [32, 16])
            self.assertIsNone(result)
            self.assertIn(reason, note)

    def test_budget_falls_back_to_originals(self):
        self.write("Pkg.Tex.A", png(64, 64))
        self.write("Pkg.Tex.B", png(64, 64))
        first = RemasteredTextures(self.folder).load("Pkg.Tex.A", [32, 32])[0][0]
        textures = RemasteredTextures(self.folder, budget=len(first) + 1)
        self.assertIsNotNone(textures.load("Pkg.Tex.A", [32, 32])[0])
        result, note = textures.load("Pkg.Tex.B", [32, 32])
        self.assertIsNone(result)
        self.assertIn("budget is full", note)


if __name__ == "__main__":
    unittest.main()
