"""T3 style boundary: the retail border planes fitted to a rectangular boundary."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from perimeter import (RETAIL_BORDER_HALF_CM, RETAIL_BORDER_PLANES, RETAIL_BORDER_Z_CM, PolygonWalls,
                       retail_border_pieces)


def walls(vertices, z_min=100.0, z_max=1100.0):
    return PolygonWalls(tuple(vertices), z_min, z_max, False, False)


class RetailBorderTests(unittest.TestCase):
    def planes(self, pieces):
        """Each piece's plane in world space: (start xy, end xy, bottom z, top z)."""
        out = []
        for name, (x, y, z), (sx, sy, sz) in pieces:
            (ax, ay), (bx, by) = RETAIL_BORDER_PLANES[name]
            out.append(((x + ax * sx, y + ay * sy), (x + bx * sx, y + by * sy),
                        z + RETAIL_BORDER_Z_CM[0] * sz, z + RETAIL_BORDER_Z_CM[1] * sz))
        return out

    def check_square(self, low, high, z_min=100.0, z_max=1100.0):
        pieces = retail_border_pieces(walls(((low[0], low[1]), (high[0], low[1]), (high[0], high[1]),
                                             (low[0], high[1])), z_min, z_max))
        self.assertEqual(len(pieces), 8)
        self.assertEqual({name for name, _, _ in pieces}, {"SM_Border_TopWall_A", "SM_Border_TopWall_D"})
        spans = {}
        for (a, b, bottom, top) in self.planes(pieces):
            self.assertAlmostEqual(bottom, z_min)
            self.assertAlmostEqual(top, z_max)
            if abs(a[0] - b[0]) < 1e-6:  # On an X wall.
                self.assertTrue(any(abs(a[0] - wall) < 1e-6 for wall in (low[0], high[0])))
                spans.setdefault(("x", round(a[0])), []).append(sorted((a[1], b[1])))
            else:
                self.assertAlmostEqual(a[1], b[1])
                self.assertTrue(any(abs(a[1] - wall) < 1e-6 for wall in (low[1], high[1])))
                spans.setdefault(("y", round(a[1])), []).append(sorted((a[0], b[0])))
        self.assertEqual(len(spans), 4)
        for (axis, _), halves in spans.items():
            halves.sort()
            lo, hi = (low[1], high[1]) if axis == "x" else (low[0], high[0])
            # Two halves meet at the centre and reach both corners: no gap, no overhang.
            self.assertAlmostEqual(halves[0][0], lo)
            self.assertAlmostEqual(halves[0][1], halves[1][0])
            self.assertAlmostEqual(halves[1][1], hi)

    def test_border_lies_on_the_walls_corner_to_corner(self):
        self.check_square((-70800, -78600), (70800, 94400))  # Katabatic: long, off centre in Y.
        self.check_square((-1000, -500), (3000, 1500))  # Smaller than the retail arena.

    def test_other_outlines_keep_the_grid_wall(self):
        self.assertIsNone(retail_border_pieces(walls(((0, 0), (10, 0), (12, 8), (0, 10)))))
        self.assertIsNone(retail_border_pieces(walls(((0, 0), (10, 0), (10, 10), (5, 12), (0, 10)))))
        self.assertIsNone(retail_border_pieces(walls(((0, 0), (10, 0), (10, 10), (0, 10)), 50.0, 50.0)))


if __name__ == "__main__":
    unittest.main()
