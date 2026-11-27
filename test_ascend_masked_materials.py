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


if __name__ == "__main__":
    unittest.main()
