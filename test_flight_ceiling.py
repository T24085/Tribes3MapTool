"""Flight ceiling Boundaries option and its T3MissionFlightCeiling markers (#22)."""

from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from perimeter import BOUNDARY_OPTIONS, FLIGHT_CEILING, FLIGHT_FADE, flight_ceiling_heights

TERRAIN = {"height_m": [10.0, 50.0], "origin_cm": [0, 0, 200], "cm_per_m": 100}  # lowest terrain at 1,200 cm


def rows(flag_z=1500):
    return [dict(status="boundary", properties={}, name="", source_class="PlayArea"),
            dict(status="marker", kind="flag", name="Flag 1", position_cm=[0, 0, flag_z])]


class FlightCeilingTests(unittest.TestCase):
    def test_heights_are_above_the_lowest_terrain(self):
        self.assertEqual(flight_ceiling_heights({}, TERRAIN, rows(), {"height_m": 300}), (31200, None))
        self.assertEqual(flight_ceiling_heights({}, TERRAIN, rows(), {"height_m": 300, "fade_m": 21}), (31200, 29100))

    def test_bad_values_and_markers_above_the_ceiling_are_allowed(self):
        for value in ({}, {"fade_m": 5}, {"height_m": 0}, {"height_m": 50, "fade_m": 50},
                      {"height_m": 50, "extra": 1}, {"height_m": float("nan")}, 300):
            with self.subTest(value=value), self.assertRaises(ValueError):
                flight_ceiling_heights({}, TERRAIN, rows(), value)
        # The ceiling only stops climbing, so a marker above it is allowed (Cluster_Stonehenge has a spawn there).
        self.assertEqual(flight_ceiling_heights({}, TERRAIN, rows(flag_z=40000), {"height_m": 300}), (31200, None))

    def test_option_is_known_and_reaches_the_draft(self):
        self.assertIn("flight_ceiling", BOUNDARY_OPTIONS)
        self.assertEqual((FLIGHT_CEILING, FLIGHT_FADE), ("T3MissionFlightCeiling", "T3MissionFlightCeilingFade"))
        source = (TOOL / "import_intake.py").read_text(encoding="utf-8")
        selection = source[source.index('required = {"manifest"'):source.index("or not isinstance(selection")]
        self.assertIn('"flight_ceiling"', selection)
        self.assertIn("place_flight_ceiling(editor.ACTORS", source)
        window = (TOOL / "conversion_window.py").read_text(encoding="utf-8")
        self.assertIn("Flight ceiling, m above lowest terrain", window)


if __name__ == "__main__":
    unittest.main()
