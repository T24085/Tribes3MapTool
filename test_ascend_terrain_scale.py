"""Ascend terrain with no saved DrawScale3D uses the UE3 Terrain default (Katabatic)."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
import unreal_native_terrain as terrain  # noqa: E402

SCENE = {"coordinate_frame": {"source_units_per_m": 52.0833}}


def heightfield(keys, scale=(1.0, 1.0, 1.0)):
    actor = {"transform": {"position_m": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1], "scale": list(scale)}}
    node = {"entries": [{"key": key} for key in keys]}
    with mock.patch.object(terrain, "dimensions", return_value=(3, 3)), \
            mock.patch.object(terrain, "tile_dimensions"), \
            mock.patch.object(terrain, "terrain_arrays", return_value=(b"", b"", {})):
        return terrain.ascend_heightfield(actor, node, SCENE, None, "<")[4]


class TerrainScaleTests(unittest.TestCase):
    def test_a_saved_scale_is_used(self):
        # Tartarus saves (256, 256, 160).
        self.assertEqual(heightfield(["Location", "DrawScale3D"], (256, 256, 160)),
                         (256, 256, 160 / terrain.HEIGHT_UNITS))

    def test_an_unsaved_scale_is_the_ue3_default(self):
        # Katabatic saves only its Location.
        self.assertEqual(heightfield(["Location"]), (256, 256, 256 / terrain.HEIGHT_UNITS))

    def test_an_unread_scale_form_is_refused(self):
        with self.assertRaises(ValueError):
            heightfield(["Location", "DrawScale"])


if __name__ == "__main__":
    unittest.main()
