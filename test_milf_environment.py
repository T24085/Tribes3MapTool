"""MILF environment component, the Ascend converter and the UE5 adapter's numbers (T3BackEndServer#146)."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from environment_ascend import EV100, EXPOSURE_UNIT_LUX, LUX_PER_BRIGHTNESS, RULES, convert  # noqa: E402
from environment_ue5 import plan  # noqa: E402
from milf_environment import exposure_scale, validate  # noqa: E402

# Tartarus's intake environment and the projected conversion-profile values.
TARTARUS = {
    "fog": {"color": [0.366, 0.533, 0.188], "density": 0.018, "height_cm": 29641.38, "height_falloff": 0.08,
            "start_cm": 2880.0},
    "sun": {"ambient": [1.0, 0.888, 0.515], "ambient_scale": 0.5, "color": [1.0, 0.815, 0.381],
            "direction": [0.00064, 0.83486, -0.55046], "intensity_scale": 4.0},
}
VALUES = {"sun.intensity_scale": 4.0, "sun.ambient_scale": 0.5, "fog.start_cm": 2880.0, "fog.density": 0.018}


class AscendConverterTests(unittest.TestCase):
    def test_tartarus_becomes_a_milf_component(self):
        component = convert(TARTARUS, VALUES)
        self.assertEqual(component["provenance"], {"game": "ascend", "rules": RULES})
        self.assertAlmostEqual(component["sun"]["illuminance_lux"], 4.0 * LUX_PER_BRIGHTNESS, places=3)
        self.assertEqual(component["sun"]["direction"], [0.00064, -0.83486, -0.55046])  # MILF flips Y
        self.assertEqual(component["sky"], {"color": [1.0, 0.888, 0.515], "intensity": 0.5})
        self.assertEqual(component["fog"]["start_m"], 28.8)
        self.assertAlmostEqual(component["fog"]["height_m"], 296.4138)
        self.assertEqual(component["exposure"], {"mode": "fixed", "ev100": EV100})

    def test_per_map_tuning_flows_through_the_profile_values(self):
        component = convert(TARTARUS, dict(VALUES, **{"sun.intensity_scale": 2.0, "sun.ambient_scale": 1.5}))
        self.assertAlmostEqual(component["sun"]["illuminance_lux"], 2.0 * LUX_PER_BRIGHTNESS, places=3)
        self.assertEqual(component["sky"]["intensity"], 1.5)

    def test_the_default_exposure_shows_one_ue3_unit_at_its_ue3_brightness(self):
        self.assertAlmostEqual(exposure_scale(EV100) * EXPOSURE_UNIT_LUX, 1, places=3)
        self.assertAlmostEqual(LUX_PER_BRIGHTNESS, 31.4159, places=3)  # UE5 lux carry a 1/pi.


class AdapterPlanTests(unittest.TestCase):
    def test_exposure_is_fixed_and_fog_keeps_its_seen_colour(self):
        settings = plan(convert(TARTARUS, VALUES))
        scale = 2 ** settings["exposure"]["bias"]
        self.assertAlmostEqual(scale, exposure_scale(EV100))
        for seen, sent in zip(TARTARUS["fog"]["color"], settings["fog"]["inscattering"]):
            self.assertAlmostEqual(sent * scale, seen)
        self.assertAlmostEqual(settings["sun"]["lux"], 4.0 * LUX_PER_BRIGHTNESS, places=3)
        self.assertAlmostEqual(settings["fog"]["start_cm"], 2880.0)

    def test_the_sun_direction_returns_to_unreal_axes(self):
        settings = plan(convert(TARTARUS, VALUES))
        x, y, z = TARTARUS["sun"]["direction"]
        self.assertAlmostEqual(settings["sun"]["yaw"], math.degrees(math.atan2(y, x)))
        self.assertAlmostEqual(settings["sun"]["pitch"], math.degrees(math.atan2(z, math.hypot(x, y))))


GRADE = {"shadows": [0.01, 0.01, 0.01], "midtones": [1.3, 1.1, 1.1], "highlights": [0.9, 0.9, 0.9],
         "bloom": {"scale": 1.0, "threshold": 3.0, "tint": [1.0, 1.0, 1.0]}, "lut": "grade-lut.png"}


class GradeTests(unittest.TestCase):
    """Tartarus's post-process grade (Tribes3MapTool#38)."""

    def test_the_source_grade_becomes_the_milf_grade(self):
        grade = convert(dict(TARTARUS, grade=GRADE), VALUES)["grade"]
        self.assertEqual(grade["midtones"], [1.3, 1.1, 1.1])
        self.assertEqual(grade["bloom"], {"intensity": 1.0, "threshold": 3.0, "tint": [1.0, 1.0, 1.0]})
        self.assertEqual(grade["lut"], "grade-lut.png")

    def test_the_adapter_maps_tones_to_ue5_colour_grading(self):
        from environment_ue5 import UE5_BLOOM_INTENSITY

        settings = plan(convert(dict(TARTARUS, grade=GRADE), VALUES))["grade"]
        # UE3 mid-tones above 1 darken, so the UE5 gamma is their inverse; UE3 divides by the highlights.
        self.assertAlmostEqual(settings["gamma"][0], 1 / 1.3, delta=0.04)  # Shadows and highlights shift the fit slightly.
        # UE3's display-space curve pow((x - shadows) / highlights, mid-tones) is folded as a fit:
        # highlights 0.9 brighten (gain above 1), and there is no raw linear offset.
        self.assertTrue(all(gain > 1 for gain in settings["gain"]))
        self.assertEqual(settings["offset"], [0.0, 0.0, 0.0])
        self.assertEqual(settings["bloom_intensity"], UE5_BLOOM_INTENSITY)
        self.assertIsNone(settings["bloom_threshold"])  # UE5 keeps its own threshold

    def test_bad_grades_are_refused(self):
        component = convert(dict(TARTARUS, grade=GRADE), VALUES)
        for grade in (dict(component["grade"], lut="../escape.png"), dict(component["grade"], shadows=[2, 0, 0]),
                      dict(component["grade"], bloom={"intensity": 99})):
            with self.assertRaises(ValueError):
                validate(dict(component, grade=grade))

    def test_the_intake_accepts_only_a_well_formed_source_grade(self):
        from intake_contract import validate_environment

        validate_environment({"grade": GRADE}, unreal=True)
        for broken in (dict(GRADE, lut="other.png"), dict(GRADE, bloom={"scale": 1}), {k: v for k, v in GRADE.items() if k != "midtones"}):
            with self.assertRaises(ValueError):
                validate_environment({"grade": broken}, unreal=True)
        with self.assertRaises(ValueError):
            validate_environment({"grade": GRADE}, unreal=False)


class ValidationTests(unittest.TestCase):
    def test_invalid_components_are_refused(self):
        good = convert(TARTARUS, VALUES)
        for broken in (dict(good, format=2), dict(good, extra=1),
                       dict(good, sun=dict(good["sun"], direction=[1, 1, 1])),
                       dict(good, exposure={"mode": "auto", "ev100": 3}),
                       dict(good, fog=dict(good["fog"], density=9)),
                       dict(good, provenance={"game": "quake"})):
            with self.assertRaises(ValueError):
                validate(broken)


if __name__ == "__main__":
    unittest.main()


class ToneFitTests(unittest.TestCase):
    def test_midtones_alone_fit_exactly(self):
        from environment_ue5 import tone_fit

        scale, power = tone_fit(0, 1.3, 1)
        self.assertAlmostEqual(scale, 1.0, places=6)
        self.assertAlmostEqual(power, 1.3, places=6)

    def test_arx_novena_grade_stays_neutral_in_green(self):
        # Shadows (0.04, 0.12, 0.05), mid-tones (0.75, 0.7, 0.75), highlights (0.7, 0.7, 0.9).
        from environment_ue5 import plan

        grade = plan({"format": 1, "grade": {"shadows": [0.04, 0.12, 0.05], "midtones": [0.75, 0.7, 0.75],
                                               "highlights": [0.7, 0.7, 0.9]}})["grade"]
        red, green, blue = grade["gain"]
        self.assertGreater(green, 0.6 * red)  # No longer a green wipe-out (magenta).
        self.assertEqual(grade["offset"], [0.0, 0.0, 0.0])

