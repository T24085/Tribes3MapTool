"""Vengeance BoundaryVolume play area and Boundaries preset (#17)."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from perimeter import parse_polygon, check_polygon_outline
from vengeance_bounds import boundary_recipe, convex_hull, describe, play_area

TERRAIN_CM = [[-200_000, -200_000], [200_000, 200_000]]
# Beggar's Run's six-corner volume, in metres.
BEGGARS = dict(name="BoundaryVolume0", z_m=[-522, 430],
               outline_m=[(-317.44, -646.4), (-245.76, -655.36), (901.12, -655.36), (993.28, -646.4),
                          (993.28, 664.32), (-317.44, 664.32)])
FLAGS = [[0, 0, 0], [600, 0, 0]]


class VengeanceBoundsTests(unittest.TestCase):
    def test_hull_drops_inner_and_collinear_points(self):
        hull = convex_hull([(0, 0), (4, 0), (2, 0), (4, 4), (0, 4), (2, 2)])
        self.assertEqual(sorted(hull), [(0, 0), (0, 4), (4, 0), (4, 4)])

    def test_polygon_volume_gets_polygon_walls_inside_a_margin(self):
        bounds, polygon, note = play_area(BEGGARS, TERRAIN_CM, FLAGS)
        self.assertIsNone(note)
        self.assertEqual(len(polygon), 6)
        self.assertAlmostEqual(bounds[0][0], (-317.44 - 1) * 100)
        # The walls must pass the Boundaries outline check against those bounds.
        check_polygon_outline(parse_polygon(polygon, "Solid perimeter"), "Solid perimeter", bounds)

    def test_rectangle_volume_uses_plain_walls(self):
        box = dict(BEGGARS, outline_m=[(-500, -500), (500, -500), (500, 500), (-500, 500)])
        bounds, polygon, note = play_area(box, TERRAIN_CM, FLAGS[:1])
        self.assertIsNone(polygon)
        self.assertEqual(bounds, [[-50_000, -50_000], [50_000, 50_000]])
        self.assertIsNone(note)

    def test_volume_past_the_terrain_is_clipped_to_a_rectangle(self):
        small = [[-50_000, -50_000], [50_000, 50_000]]
        bounds, polygon, note = play_area(BEGGARS, small, [[0, 0, 0]])
        self.assertIsNone(polygon)
        self.assertEqual(bounds[1], [500.0 * 100, 500.0 * 100])
        self.assertIn("reaches past the terrain", note)

    def test_markers_outside_the_volume_keep_the_terrain_framing(self):
        self.assertIsNone(play_area(BEGGARS, TERRAIN_CM, [[2000, 0, 0]]))

    def test_recipe_and_review_text(self):
        _, polygon, note = play_area(BEGGARS, TERRAIN_CM, FLAGS)
        recipe = boundary_recipe(BEGGARS, polygon, [-24_575.25, 0.0])
        options = recipe["options"]
        self.assertTrue(options["perimeter"])
        self.assertEqual(options["wall_limits"], {"ceiling_m": 430.0, "floor_m": None})
        self.assertEqual(options["wall_polygon_m"], polygon)
        text = describe(BEGGARS, polygon, note, recipe)
        self.assertIn("6-corner outline", text)
        self.assertIn("kills players and destroys vehicles", text)
        self.assertEqual(options["kill_floor_m"], round(-24_575.25 / 100 + 522, 2))  # volume bottom -522 m
        self.assertIn("Kill floor at the volume's bottom", text)
        self.assertNotIn("kill_floor_m", boundary_recipe(dict(BEGGARS, z_m=[0, 430]), None, [-100, 0])["options"])
        low_top = dict(BEGGARS, z_m=[-522, -10])
        self.assertIsNone(boundary_recipe(low_top, None, [0, 5000])["options"]["wall_limits"]["ceiling_m"])


if __name__ == "__main__":
    unittest.main()
