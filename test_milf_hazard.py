"""MILF hazard component (T3BackEndServer#150) and the Ascend kill-zone converter."""
from pathlib import Path
import copy
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from hazards_ascend import hull_xy, zone  # noqa: E402
from milf_hazard import validate  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
