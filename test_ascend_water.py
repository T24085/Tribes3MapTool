"""TA standing water: the binding check, typed liquid rows and Quagmire's water kit values."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from hazards_ue5 import UNDER_WATER, WATER_VOLUME, liquid_row
from model_textures import valid_water

TARTARUS = {"tint": [1.5, 1.0, 0.15], "fill": [[0.2, 0.1, 0.05], [0.1, 0.15, 0.05]], "tiling": [6.0, 3.0]}


class WaterTests(unittest.TestCase):
    def test_tartarus_water_binding_is_valid(self):
        self.assertTrue(valid_water(TARTARUS))
        for broken in ({**TARTARUS, "tint": [1, 1]}, {**TARTARUS, "tiling": [0, 3]},
                       {**TARTARUS, "fill": [[0.2, 0.1, 0.05]]}, {**TARTARUS, "extra": 1}, [1]):
            self.assertFalse(valid_water(broken), broken)

    def test_liquid_rows_carry_their_type(self):
        self.assertEqual(liquid_row("water1", [0, 0, -100], [10, 10, 0], -1, "stagnant_water")["liquid_type"],
                         "stagnant_water")
        self.assertEqual(liquid_row("lava1", [0, 0, 0], [1, 1, 1], 0.5)["liquid_type"], "lava")

    def test_water_kit_matches_quagmire(self):
        self.assertEqual(WATER_VOLUME["fluid_friction"], 0.6)
        self.assertEqual(UNDER_WATER["priority"], 10.0)
        self.assertEqual(UNDER_WATER["below_surface_cm"], 50.0)


if __name__ == "__main__":
    unittest.main()
