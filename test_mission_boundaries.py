"""Boundaries presets from Tribes 1 and Tribes 2 mission areas (#15)."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from intake_contract import BOUNDARY_RECIPE, PLAYABLE_BOUNDS, load_intake
from mission_boundaries import soft_boundary_recipe
from perimeter import read_boundary_recipe
from t1_reader import convert_legacy
from test_editor_t1_intake import legacy_fixture

TERRAIN = {"height_m": [10.0, 50.0], "origin_cm": [0, 0, 200], "cm_per_m": 100}


def mission_rows(flag_x=0.0):
    area = dict(status="boundary", source_class="MissionArea", name="", reason="",
                properties={"area": "-100 -100 200 200", "flightCeiling": "300", "flightCeilingRange": "20",
                            PLAYABLE_BOUNDS: [[-10_000, -10_000], [10_000, 10_000]]})
    flags = [dict(status="marker", kind="flag", name=f"Flag {team}", team=team, position_cm=[x, 0, 1000])
             for team, x in ((1, -5_000), (2, flag_x))]
    return [area, *flags]


class SoftBoundaryTests(unittest.TestCase):
    def test_tribes_2_preset_warns_and_drops_the_flag_without_walls(self):
        rows = mission_rows()
        recipe = soft_boundary_recipe(rows, TERRAIN, game="Tribes 2", cm_per_source_m=105)
        self.assertEqual(recipe["options"], {"perimeter": False, "flag_inset_m": 1, "warn_players": True})
        self.assertEqual(recipe["elevation_cm"], [1200.0, 5200.0])
        self.assertIs(rows[0]["properties"][BOUNDARY_RECIPE], recipe)
        reason = rows[0]["reason"]
        self.assertIn("drop a carried flag at the line", reason)
        self.assertIn("flight ceiling 315 m (soft over 21 m)", reason)
        self.assertIn("grace-time damage", reason)

    def test_flag_on_the_line_keeps_the_warning_only(self):
        rows = mission_rows(flag_x=9_950)  # half a metre from the edge
        recipe = soft_boundary_recipe(rows, TERRAIN, game="Tribes 2", cm_per_source_m=105)
        self.assertIsNone(recipe["options"]["flag_inset_m"])
        self.assertTrue(recipe["options"]["warn_players"])
        self.assertIn("Flag-only limit left off", rows[0]["reason"])

    def test_no_boundary_row_means_no_preset(self):
        self.assertIsNone(soft_boundary_recipe(mission_rows()[1:], TERRAIN, game="Tribes 2"))

    def test_tribes_1_intake_carries_a_valid_preset(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = convert_legacy(legacy_fixture(root), root / "intakes")
            _, _, rows = load_intake(manifest)  # validates the recipe with plan_boundaries
            recipe = read_boundary_recipe(rows)
            self.assertEqual(recipe["options"]["perimeter"], False)
            self.assertTrue(recipe["options"]["warn_players"])
            boundary = next(row for row in rows if row["status"] == "boundary")
            self.assertIn("Tribes 1 MissionCenterPos", boundary["reason"])
            self.assertEqual(recipe["options"]["flag_return_delay_s"], 5)
            self.assertIn("return a carried flag home after 5 s outside", boundary["reason"])


if __name__ == "__main__":
    unittest.main()
