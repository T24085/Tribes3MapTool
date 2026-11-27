"""Ascend masked materials keep their cut-out (Tribes3MapTool#35)."""
from io import BytesIO
from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from ascend_materials import MASK_CHANNELS, with_alpha  # noqa: E402


def png(image):
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class MaskedMaterialTests(unittest.TestCase):
    def test_the_mask_channel_becomes_the_colour_alpha(self):
        color = Image.new("RGB", (4, 4), (10, 120, 30))
        # A separate mask whose green channel cuts out the left half, as Tartarus's tree leaves do.
        mask = Image.new("RGB", (4, 4), (255, 0, 255))
        for y in range(4):
            for x in range(2, 4):
                mask.putpixel((x, y), (0, 255, 0))
        result = Image.open(BytesIO(with_alpha(png(color), png(mask), MASK_CHANNELS.index("maskg"))))
        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.getpixel((0, 0)), (10, 120, 30, 0))
        self.assertEqual(result.getpixel((3, 3)), (10, 120, 30, 255))

    def test_a_smaller_mask_is_scaled_to_the_colour_texture(self):
        # Water plants read their mask from a normal map that can differ in size.
        color = Image.new("RGBA", (8, 8), (0, 0, 0, 255))
        mask = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        result = Image.open(BytesIO(with_alpha(png(color), png(mask), MASK_CHANNELS.index("maska"))))
        self.assertEqual(result.size, (8, 8))
        self.assertEqual(result.getchannel("A").getextrema(), (0, 0))

    def test_the_diffuse_alpha_is_kept_when_it_is_the_mask(self):
        # Tartarus's vines cut out with their own diffuse alpha.
        color = Image.new("RGBA", (2, 1), (50, 90, 40, 255))
        color.putpixel((0, 0), (50, 90, 40, 0))
        data = png(color)
        result = Image.open(BytesIO(with_alpha(data, data, MASK_CHANNELS.index("maska"))))
        self.assertEqual([result.getpixel((x, 0))[3] for x in range(2)], [0, 255])


class GlowChannelTests(unittest.TestCase):
    """Ascend glows that read one texture channel (Tartarus's ship light kit reads the diffuse alpha)."""

    def test_an_input_reading_one_channel_names_it(self):
        from ascend_materials import input_channel

        self.assertEqual(input_channel({"mask": 1, "maska": 1}), 3)
        self.assertIsNone(input_channel({"mask": 1, "maskr": 1, "maskg": 1, "maskb": 1}))
        self.assertIsNone(input_channel({}))

    def test_the_glow_mask_comes_from_that_channel(self):
        from ascend_materials import channel_png

        lamp = Image.new("RGBA", (2, 1), (200, 200, 200, 0))
        lamp.putpixel((1, 0), (200, 200, 200, 255))  # only the lamp strip is in the alpha
        data = channel_png(png(lamp), 3)
        from model_textures import validate_png

        validate_png(data)  # model textures must be 8-bit RGBA
        mask = Image.open(BytesIO(data))
        self.assertEqual([mask.getpixel((x, 0))[:3] for x in range(2)], [(0, 0, 0), (255, 255, 255)])


class GlowBindingTests(unittest.TestCase):
    """Ascend glow bindings (Tribes3MapTool#40): texture x linear tint, HDR allowed."""

    def binding(self, **glow):
        source = dict(file="texture_" + "b" * 32 + ".png", source="Pods.upk", source_sha256="c" * 64, size=[64, 64])
        return dict(file="texture_" + "a" * 32 + ".png", source="Pods.upk", source_sha256="c" * 64, size=[64, 64],
                    mode="opaque", two_sided=False, warning="w",
                    ascend_glow=dict(dict(color=source, tint=[0.1, 0.3, 5.25]), **glow))

    def validate(self, record):
        from model_textures import validate_bindings

        inventory = {name: name[8:40] + "0" * 32 for name in (record["file"], record["ascend_glow"]["color"]["file"])}
        validate_bindings({"m.json": {"0": record}}, {"m.json": {"0"}}, inventory,
                          legacy_sizes={name: (64, 64) for name in inventory},
                          receipts={("Pods.upk", "c" * 64)}, native_bsp=True,
                          ascend_uvs={"m.json": 1}, require_extended=False)

    def test_a_bright_coloured_glow_is_accepted_and_tracked(self):
        from model_textures import shader_signature, texture_records

        record = self.binding()
        record["ascend_base"] = {"vertex_rgb": True}
        self.validate(record)
        self.assertIn(record["ascend_glow"]["color"], list(texture_records(record)))
        inventory = {row["file"]: "x" for row in texture_records(record)}
        self.assertIn([0.1, 0.3, 5.25], shader_signature(record, inventory))

    def test_glows_that_share_textures_get_different_materials(self):
        # Tartarus's blue and orange pods use the same textures and no base-colour settings (#40).
        from model_textures import shader_signature, texture_records

        blue, orange = self.binding(), self.binding(tint=[2.5, 0.5, 0.0])
        inventory = {row["file"]: "x" for row in texture_records(blue)}
        self.assertNotEqual(shader_signature(blue, inventory), shader_signature(orange, inventory))

    def test_invalid_glow_tints_are_refused(self):
        for tint in ([1, 1], [1, 1, 99], [1, -1, 1], [1, 1, float("nan")]):
            record = self.binding(tint=tint)
            record["ascend_base"] = {"vertex_rgb": True}
            with self.assertRaises(ValueError):
                self.validate(record)

    def test_an_over_bright_glow_keeps_its_hue(self):
        # A deep blue pod (0.1, 0.3, 5.25) stays deep blue, as UE3's per-channel clip showed it.
        from environment_ascend import GLOW_CLIP, glow_colour

        capped = glow_colour([0.1, 0.3, 5.25])
        self.assertAlmostEqual(max(capped), GLOW_CLIP)
        self.assertAlmostEqual(capped[1] / capped[2], 0.3 / 5.25)
        self.assertEqual(glow_colour([0.2, 0.4, 0.6]), [0.2, 0.4, 0.6])

    def test_the_glow_gain_matches_the_ascend_exposure(self):
        from environment_ascend import EMISSIVE_GAIN, EV100
        from milf_environment import exposure_scale

        self.assertAlmostEqual(EMISSIVE_GAIN * exposure_scale(EV100), 1, places=3)


if __name__ == "__main__":
    unittest.main()
