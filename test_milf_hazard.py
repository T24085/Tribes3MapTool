"""MILF hazard component (T3BackEndServer#150) and the Ascend kill-zone converter."""
from pathlib import Path
import copy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from hazards_ascend import hull_xy, zone  # noqa: E402
from milf_hazard import validate  # noqa: E402
from hazards_ue5 import CONTACT_CM, KILL_DAMAGE_PER_SEC, MOLTEN_SEEN, WALL_OVERLAP_CM, emissive, plan  # noqa: E402

PIT = {
    "format": 1,
    "zones": [{
        "id": "utkillzvolume_1", "kind": "lava", "damage": {"mode": "kill", "cause": "lava"}, "surface_m": 26.5,
        "look": {"preset": "ascend-lava", "glow": {"color": [0.8, 0.78, 0.4], "extinction_per_m": 0.208,
                                                   "spheres": [{"center_m": [-220.4, -480.0, 26.5], "radius_m": 27.8}]}},
        "shape": {"type": "prism", "outline_m": [[-241, -504], [-192, -504], [-192, -455], [-241, -455]],
                  "floor_m": -1.3, "top_m": 68.1},
    }],
    "provenance": {"game": "ascend", "rules": "ascend-hazard-1"},
}


class HazardComponentTests(unittest.TestCase):
    def test_katabatic_pit_is_valid(self):
        component = copy.deepcopy(PIT)
        self.assertIs(validate(component), component)

    def test_rejections(self):
        cases = {
            "clockwise outline": lambda z: z["shape"].update(outline_m=z["shape"]["outline_m"][::-1]),
            "surface outside the zone": lambda z: z.update(surface_m=90),
            "kill with a rate": lambda z: z["damage"].update(per_second=5),
            "unknown kind": lambda z: z.update(kind="acid"),
            "bad preset id": lambda z: z["look"].update(preset="Lava Look!"),
            "glow colour out of range": lambda z: z["look"]["glow"].update(color=[2, 0, 0]),
            "extra field": lambda z: z.update(spin=1),
        }
        for name, change in cases.items():
            with self.subTest(name):
                component = copy.deepcopy(PIT)
                change(component["zones"][0])
                with self.assertRaises(ValueError):
                    validate(component)

    def test_duplicate_ids_are_rejected(self):
        component = copy.deepcopy(PIT)
        component["zones"].append(copy.deepcopy(component["zones"][0]))
        with self.assertRaises(ValueError):
            validate(component)

    def test_any_map_preset_id_is_accepted(self):
        component = copy.deepcopy(PIT)
        component["zones"][0]["look"]["preset"] = "katabatic-molten-2"
        validate(component)


class AscendConverterTests(unittest.TestCase):
    VOLUME = dict(name="UTKillZVolume_1", outline=hull_xy([(-241, -504), (-192, -504), (-192, -455), (-241, -455)]),
                  floor=63.2, top=68.1, per_second=10000.0)

    def test_hull_is_counter_clockwise(self):
        self.assertEqual(hull_xy([(0, 0), (1, 1), (1, 0), (0, 1), (0.5, 0.5)]), [(0, 0), (1, 0), (1, 1), (0, 1)])

    def test_glow_below_a_kill_slab_makes_a_lava_pit(self):
        sphere = dict(center=[-220.4, -480.0, 26.5], radius=27.8, extinction=0.208, color=[0.8, 0.78, 0.4])
        high = dict(center=[-215.6, -479.0, 112.6], radius=27.8, extinction=0.052, color=[0.8, 0.78, 0.4])
        result = zone(self.VOLUME, [sphere, high])
        self.assertEqual((result["kind"], result["damage"]), ("lava", {"mode": "kill", "cause": "lava"}))
        self.assertEqual(result["surface_m"], 26.5)
        self.assertEqual(result["shape"]["floor_m"], round(26.5 - 27.8, 3))
        self.assertEqual(len(result["look"]["glow"]["spheres"]), 1)  # The glow far above the pit isn't its lava.
        self.assertEqual(result["look"]["preset"], "ascend-lava")
        validate({"format": 1, "zones": [result]})

    def test_a_kill_slab_with_no_glow_is_a_fall_death(self):
        result = zone(self.VOLUME, [])
        self.assertEqual((result["kind"], result["damage"]["cause"], result["look"]), ("void", "fall", {"preset": "none"}))
        self.assertNotIn("surface_m", result)
        validate({"format": 1, "zones": [result]})

    def test_weak_damage_stays_per_second(self):
        result = zone(dict(self.VOLUME, per_second=40.0), [])
        self.assertEqual(result["damage"], {"mode": "per_second", "per_second": 40.0, "cause": "fall"})


class UnrealAdapterPlanTests(unittest.TestCase):
    def test_lava_pit_plan_in_unreal_centimetres(self):
        item, = plan(copy.deepcopy(PIT))
        self.assertEqual(item["damage_per_sec"], KILL_DAMAGE_PER_SEC)
        volume = item["volume"]
        # MILF Y -455..-504 m flips to Unreal +45500..+50400 cm.
        self.assertAlmostEqual(volume["center"][1], (45500 + 50400) / 2)
        self.assertAlmostEqual(volume["center"][2] - volume["half"][2], -130)
        # Visible lava kills on contact: the volume ends just above the surface, not at the kill slab.
        self.assertAlmostEqual(volume["center"][2] + volume["half"][2], 2650 + CONTACT_CM)
        under = item["under"]
        self.assertAlmostEqual(under["center"][2] + under["half"][2], 2650)  # The under-lava view ends at the surface.
        self.assertEqual(item["surface"]["center"][2], 2650)
        self.assertEqual(len(item["glow"]), 1)
        self.assertEqual(item["glow"][0]["center"], [-22040.0, 48000.0, 2650.0])

    def test_lava_rises_until_every_wall_column_is_closed(self):
        # Katabatic: side walls start at 37.63 m, corner pieces at 47.6 m (with more stacked above),
        # the catwalk far above the zone; the glow centre is 26.5 m.
        walls = [(-23560, 47920, 3763.0), (-23560, 47920, 4746.0), (-21660, 46020, 3763.0),
                 (-22670, 45960, 4760.0), (-22670, 45960, 6730.0), (-20670, 49890, 4760.0),
                 (-21660, 47920, 13590.0)]
        item, = plan(copy.deepcopy(PIT), {"utkillzvolume_1": walls})
        surface = 4760.0 + WALL_OVERLAP_CM
        self.assertEqual(item["surface"]["center"][2], surface)
        self.assertAlmostEqual(item["under"]["center"][2] + item["under"]["half"][2], surface)
        self.assertAlmostEqual(item["volume"]["center"][2] + item["volume"]["half"][2], surface + CONTACT_CM)

    def test_no_walls_above_keeps_the_source_surface(self):
        item, = plan(copy.deepcopy(PIT), {"utkillzvolume_1": [(0, 0, 1000.0)]})
        self.assertEqual(item["surface"]["center"][2], 2650)

    def test_emissive_lands_below_white_after_exposure_and_grade(self):
        view = {"exposure": {"bias": -3.32}, "grade": {"gain": [1.02, 1.08, 1.3]}}
        lava = emissive(MOLTEN_SEEN, view)
        shown = [value * 2 ** -3.32 * gain for value, gain in zip(lava, view["grade"]["gain"])]
        self.assertEqual([round(value, 6) for value in shown], list(MOLTEN_SEEN))
        self.assertLess(max(shown), 1)
        self.assertGreater(lava[0], lava[1] * 3)  # Still orange-red before the grade.

    def test_an_invisible_zone_keeps_its_source_height(self):
        component = copy.deepcopy(PIT)
        component["zones"][0]["look"] = {"preset": "none"}
        item, = plan(component)
        self.assertAlmostEqual(item["volume"]["center"][2] + item["volume"]["half"][2], 6810)

    def test_void_zone_places_only_the_damage_volume(self):
        component = copy.deepcopy(PIT)
        zone = component["zones"][0]
        zone.update(kind="void", damage={"mode": "per_second", "per_second": 40, "cause": "fall"}, look={"preset": "none"})
        del zone["surface_m"]
        item, = plan(component)
        self.assertEqual((item["damage_per_sec"], "under" in item, "surface" in item, "glow" in item), (40.0, False, False, False))


if __name__ == "__main__":
    unittest.main()
