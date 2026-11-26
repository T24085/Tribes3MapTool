"""Kill floor Boundaries option and its T3MissionKillFloor marker (#20)."""

from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from perimeter import BOUNDARY_OPTIONS, KILL_FLOOR, kill_floor_height

TERRAIN = {"height_m": [10.0, 50.0], "origin_cm": [0, 0, 200], "cm_per_m": 100}  # 1,200 to 5,200 cm


def rows(flag_z=1500):
    return [dict(status="boundary", properties={}, name="", source_class="PlayArea"),
            dict(status="marker", kind="flag", name="Flag 1", position_cm=[0, 0, flag_z])]


class KillFloorTests(unittest.TestCase):
    def test_height_is_below_the_lowest_terrain(self):
        self.assertEqual(kill_floor_height({}, TERRAIN, rows(), 0), 1200)
        self.assertEqual(kill_floor_height({}, TERRAIN, rows(), 206.4), 1200 - 20640)

    def test_markers_must_stay_above_it_and_values_are_checked(self):
        with self.assertRaisesRegex(ValueError, "above the kill floor"):
            kill_floor_height({}, TERRAIN, rows(flag_z=1250), 0)
        for value in (-1, 10_001, float("nan"), "5", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "kill floor"):
                kill_floor_height({}, TERRAIN, rows(), value)

    def test_recipe_elevation_takes_priority(self):
        recipe_rows = rows()
        recipe_rows[0]["properties"]["draft_boundary_recipe"] = {
            "format": 1, "elevation_cm": [-5000.0, 9000.0], "options": {"perimeter": False, "flag_inset_m": None}}
        self.assertEqual(kill_floor_height({}, TERRAIN, recipe_rows, 10), -6000)

    def test_option_is_known_and_reaches_the_draft(self):
        self.assertIn("kill_floor_m", BOUNDARY_OPTIONS)
        self.assertEqual(KILL_FLOOR, "T3MissionKillFloor")  # read by the host's boundary_players.lua
        source = (TOOL / "import_intake.py").read_text(encoding="utf-8")
        selection = source[source.index('required = {"manifest"'):source.index("or not isinstance(selection")]
        self.assertIn('"kill_floor_m"', selection)  # accepted in the conversion window's selection
        self.assertIn("place_kill_floor(editor.ACTORS", source)
        window = (TOOL / "conversion_window.py").read_text(encoding="utf-8")
        self.assertIn("Kill floor, metres below lowest terrain", window)
        self.assertIn('options["kill_floor_m"]', window)


if __name__ == "__main__":
    unittest.main()
