"""Terrain smoothing keeps every source height and matches tile edges."""

from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from terrain_smoothing import bilinear, catmull_rom, smooth_grid


class SmoothingTests(unittest.TestCase):
    def setUp(self):
        random.seed(7)
        self.size = 6
        self.grid = [[random.uniform(-5, 20) for _ in range(self.size + 1)] for _ in range(self.size + 1)]

    def height(self, x, y):
        return self.grid[y][x]

    def test_source_samples_stay_exact(self):
        out = smooth_grid(self.height, self.size, 2)
        width = self.size * 2 + 1
        self.assertEqual(len(out), width * width)
        for y in range(self.size + 1):
            for x in range(self.size + 1):
                self.assertAlmostEqual(out[(2 * y) * width + 2 * x], self.grid[y][x])

    def test_edge_points_use_only_edge_samples(self):
        # Changing everything off the bottom edge must not move any vertex on it: neighbouring tiles meet.
        out = smooth_grid(self.height, self.size, 2)
        for y in range(1, self.size + 1):
            self.grid[y] = [value + 100 for value in self.grid[y]]
        changed = smooth_grid(self.height, self.size, 2)
        width = self.size * 2 + 1
        self.assertEqual(out[:width], changed[:width])

    def test_straight_slope_stays_straight(self):
        out = smooth_grid(lambda x, y: 3 * x + 2 * y, 4, 2)
        width = 9
        for fy in range(width):
            for fx in range(width):
                self.assertAlmostEqual(out[fy * width + fx], 1.5 * fx + fy)

    def test_curve_and_factor_one(self):
        self.assertAlmostEqual(catmull_rom(0, 1, 2, 3, 0.5), 1.5)
        self.assertEqual(smooth_grid(self.height, self.size, 1)[8], self.grid[1][1])

    def test_bilinear_vertex_values(self):
        values = [[float(x), float(y)] for y in range(3) for x in range(3)]
        self.assertEqual(bilinear(values, 2, 2, 1, 3), [0.5, 1.5])
        self.assertEqual(bilinear(values, 2, 2, 4, 4), [2.0, 2.0])


if __name__ == "__main__":
    unittest.main()
