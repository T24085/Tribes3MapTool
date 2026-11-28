"""Remastering a map's textures 2x during intake (#9): routes, cache, sizes and settings."""

from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from remastered_textures import RemasteredTextures, remaster_name
from texture_remaster import AutoRemaster, method, pad_mode, remaster_png, validate_tool

try:
    from PIL import Image
except ImportError:  # Pillow is a Map Tool requirement; skip image cases without it.
    Image = None


def png(width, height, mode="RGBA", color=(90, 120, 60, 255)):
    output = BytesIO()
    Image.new(mode, (width, height), color[:len(mode)]).save(output, "PNG")
    return output.getvalue()


class RouteTests(unittest.TestCase):
    def test_routes_by_role_and_name(self):
        self.assertEqual(method("Pkg.Tex.T_Rock_DIF", "base"), "ai")
        self.assertEqual(method("Pkg.Tex.T_Sky_EMI", "sky"), "ai")  # Skies are remastered by the AI.
        self.assertEqual(method("Pkg.Tex.T_Panel_EMI", "glow"), "lanczos")
        self.assertEqual(method("Pkg.Tex.T_Panel_EMI", "base"), "lanczos")
        self.assertEqual(method("Pkg.Tex.Cubemap_Front", "base"), "lanczos")
        self.assertEqual(method("Pkg.Tex.Fall_ThumbnailTexture", "base"), "copy")
        self.assertEqual(pad_mode("Pkg.Tex.T_Rock_DIF", "base"), "wrap")
        self.assertEqual(pad_mode("Pkg.Tex.T_Clouds", "sky"), "edge")
        self.assertEqual(pad_mode("Pkg.Tex.T_Decal_01", "base"), "edge")

    def test_tool_folder_needs_the_executable_and_models(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            with self.assertRaisesRegex(ValueError, "Real-ESRGAN"):
                validate_tool(directory)
            (folder / "realesrgan-ncnn-vulkan.exe").write_bytes(b"")
            (folder / "models").mkdir()
            self.assertEqual(validate_tool(directory), folder)
            for value in ("relative", "", 3):
                with self.assertRaisesRegex(ValueError, "Real-ESRGAN"):
                    validate_tool(value)


@unittest.skipIf(Image is None, "Pillow is not installed")
class RemasterTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.cache = Path(self.directory.name) / "cache"
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def fake(self, source, object_path, role, tool):
        self.calls.append((object_path, role))
        with Image.open(BytesIO(source)) as image:
            return png(image.width * 2, image.height * 2), "lanczos"

    def test_lanczos_route_doubles_and_keeps_alpha(self):
        data, how = remaster_png(png(8, 4, color=(10, 200, 30, 100)), "Pkg.Tex.T_Panel_EMI", "glow", None)
        self.assertEqual(how, "lanczos")
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.size, (16, 8))
            self.assertEqual(image.getpixel((8, 4)), (10, 200, 30, 100))

    def test_ai_route_needs_the_tool(self):
        with self.assertRaisesRegex(ValueError, "not set up"):
            remaster_png(png(8, 8), "Pkg.Tex.T_Rock_DIF", "base", None)

    def test_remasters_once_then_reads_the_cache(self):
        auto = AutoRemaster(None, cache=self.cache, remaster=self.fake)
        (data, size, receipt), note = auto.load("Pkg.Tex.T_Rock_DIF", [16, 8], png(16, 8))
        self.assertIsNone(note)
        self.assertEqual(size, [32, 16])
        self.assertTrue(receipt["name"].startswith("Pkg__Tex__T_Rock_DIF.base."))
        again = AutoRemaster(None, cache=self.cache, remaster=self.fake)
        self.assertEqual(again.load("Pkg.Tex.T_Rock_DIF", [16, 8], png(16, 8))[0][2], receipt)
        self.assertEqual(self.calls, [("Pkg.Tex.T_Rock_DIF", "base")])
        self.assertEqual(again.methods["Pkg.Tex.T_Rock_DIF"], "cached")

    def test_redo_rebuilds_cached_results(self):
        AutoRemaster(None, cache=self.cache, remaster=self.fake).load("P.T.A", [8, 8], png(8, 8))
        AutoRemaster(None, cache=self.cache, remaster=self.fake, redo=True).load("P.T.A", [8, 8], png(8, 8))
        AutoRemaster(None, cache=self.cache, remaster=self.fake).load("P.T.A", [8, 8], png(8, 8))
        self.assertEqual(len(self.calls), 2)

    def test_changed_source_is_remastered_again(self):
        auto = AutoRemaster(None, cache=self.cache, remaster=self.fake)
        auto.load("Pkg.Tex.T_Rock_DIF", [16, 8], png(16, 8))
        auto.load("Pkg.Tex.T_Rock_DIF", [16, 8], png(16, 8, color=(1, 2, 3, 255)))
        self.assertEqual(len(self.calls), 2)

    def test_hand_made_remaster_wins(self):
        folder = Path(self.directory.name) / "manual"
        folder.mkdir()
        (folder / remaster_name("Pkg.Tex.T_Rock_DIF")).write_bytes(png(64, 32))
        auto = AutoRemaster(None, cache=self.cache, manual=RemasteredTextures(folder), remaster=self.fake)
        (data, size, receipt), note = auto.load("Pkg.Tex.T_Rock_DIF", [16, 8], png(16, 8))
        self.assertEqual(size, [64, 32])
        self.assertEqual(self.calls, [])
        # Hand-made folders hold base colours and skies only; glow maps go through the pipeline.
        auto.load("Pkg.Tex.T_Rock_DIF", [16, 8], png(16, 8), "glow")
        self.assertEqual(self.calls, [("Pkg.Tex.T_Rock_DIF", "glow")])

    def test_failures_and_full_budget_keep_the_original(self):
        def broken(*_args):
            raise ValueError("Real-ESRGAN failed (1): no GPU")

        found, note = AutoRemaster(None, cache=self.cache, remaster=broken).load("P.T.A", [8, 8], png(8, 8))
        self.assertIsNone(found)
        self.assertIn("no GPU", note)
        full = AutoRemaster(None, cache=self.cache, remaster=self.fake, budget=10)
        found, note = full.load("P.T.B", [8, 8], png(8, 8))
        self.assertIsNone(found)
        self.assertIn("budget", note)

    def test_no_source_pixels_means_no_remaster(self):
        auto = AutoRemaster(None, cache=self.cache, remaster=self.fake)
        self.assertEqual(auto.load("P.T.A", [8, 8]), (None, None))


if __name__ == "__main__":
    unittest.main()
