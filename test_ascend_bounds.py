"""Ascend source out-of-bounds box (#12): wall selection, ceiling, recipe and review notes."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from ascend_bounds import boundary_recipe, derive_play_box, describe
from unreal_legacy_intake import mark_boundary_sources

# Tartarus's edge volumes and ceiling, in metres at 39.68 source units per metre.
U = 39.6825


def box(name, lower, upper):
    return name, [v / U for v in lower], [v / U for v in upper]


TARTARUS = [
    box("BlockingVolume_0", (-51200, -43008, 73728), (51200, 43008, 77824)),
    box("BlockingVolume_1", (-51200, -43008, -8192), (-49152, 43008, 73728)),
    box("BlockingVolume_2", (49152, -43008, -8192), (51200, 43008, 73728)),
    box("BlockingVolume_3", (-49152, -43008, -8192), (49152, -40960, 73728)),
    box("BlockingVolume_4", (-49152, 40960, -8192), (49152, 43008, 73728)),
    box("BlockingVolume_9", (27712, 3312, -896), (28560, 4016, -608)),  # a small in-map blocker
]
FLAGS = [[22748 / U, 7538 / U, -262 / U], [-22747 / U, 7538 / U, -262 / U]]


class PlayBoxTests(unittest.TestCase):
    def test_tartarus_walls_and_ceiling(self):
        play = derive_play_box(TARTARUS, FLAGS)
        self.assertAlmostEqual(play["lower_m"][0], -49152 / U)
        self.assertAlmostEqual(play["upper_m"][1], 40960 / U)
        self.assertAlmostEqual(play["ceiling_m"], 73728 / U)
        self.assertEqual(play["volumes"], [f"BlockingVolume_{n}" for n in range(5)])

    def test_open_side_or_no_markers_gives_no_box(self):
        self.assertIsNone(derive_play_box([entry for entry in TARTARUS if entry[0] != "BlockingVolume_2"], FLAGS))
        self.assertIsNone(derive_play_box(TARTARUS, []))

    def test_inner_walls_win_and_short_walls_are_ignored(self):
        inner = box("Inner", (-45000, -43008, -8192), (-44000, 43008, 73728))
        short = box("Short", (40000, 0, -8192), (41000, 2000, 73728))  # does not span the flags
        play = derive_play_box([*TARTARUS, inner, short], FLAGS)
        self.assertAlmostEqual(play["lower_m"][0], -44000 / U)
        self.assertAlmostEqual(play["upper_m"][0], 49152 / U)

    def test_recipe_and_description(self):
        play = derive_play_box(TARTARUS, FLAGS)
        recipe = boundary_recipe(play, [-5000.0, 19000.0])
        self.assertEqual(recipe["options"]["perimeter"], True)
        self.assertAlmostEqual(recipe["options"]["wall_limits"]["ceiling_m"], round(73728 / U - 190, 2))
        self.assertIsNone(recipe["options"]["wall_limits"]["floor_m"])
        text = describe(play, {"killz_m": -206.4, "stallz_m": 327.6})
        self.assertIn("5 blocking volumes", text)
        self.assertIn("kill floor at KillZ (-206 m)", text)
        self.assertIn("Not applied: flight ceiling", text)
        with_kill = boundary_recipe(play, [-5000.0, 19000.0], {"killz_m": -206.4})
        self.assertEqual(with_kill["options"]["kill_floor_m"], 156.4)  # KillZ is 156.4 m below the lowest terrain
        self.assertNotIn("kill_floor_m", boundary_recipe(play, [-5000.0, 19000.0], {"killz_m": 10})["options"])
        self.assertLessEqual(len(text), 240)

    def test_review_marks_used_volumes_and_edge_meshes(self):
        play = derive_play_box(TARTARUS, FLAGS)
        rows = [dict(name="BlockingVolume_1", source_class="Engine.BlockingVolume", reason="r"),
                dict(name="BlockingVolume_9", source_class="Engine.BlockingVolume", reason="r"),
                dict(name="Panel", source_class="Engine.StaticMeshActor", reason="r", model_reference="m",
                     transform=dict(position_cm=[-49152 / U * 100, 40960 / U * 100, 0])),
                dict(name="Rock", source_class="Engine.StaticMeshActor", reason="r", model_reference="m",
                     transform=dict(position_cm=[0, 0, 0]))]
        mark_boundary_sources(rows, play)
        self.assertIn("out-of-bounds box", rows[0]["reason"])
        self.assertEqual(rows[1]["reason"], "r")
        self.assertIn("boundary line", rows[2]["reason"])
        self.assertEqual(rows[3]["reason"], "r")


if __name__ == "__main__":
    unittest.main()
