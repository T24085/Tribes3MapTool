"""Ascend CTF markers resting on the world are usable (Katabatic's flag stands)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from unreal_legacy_intake import usable_marker, world_lines  # noqa: E402

TRANSFORM = {"position_m": [1, 2, 3], "rotation_xyzw": [0, 0, 0, 1], "scale": [1, 1, 1], "authored": True}


def actor(line, source_class, **links):
    return {"line": line, "source_class": source_class, "transform": dict(TRANSFORM), "components": [],
            "review": [], "references": {key: "ref" for key in links}, "links": links}


class WorldBasedMarkerTests(unittest.TestCase):
    def test_a_flag_based_on_the_world_info_is_usable(self):
        # UE3 saves Base = WorldInfo for an actor resting on world geometry.
        actors = [actor(10000671, "Engine.WorldInfo"),
                  actor(10000700, "TribesGame.TrCTFBase_BloodEagle", base=10000671)]
        self.assertTrue(usable_marker(actors[1], "flag", None, world_lines(actors)))

    def test_a_flag_attached_to_something_else_is_not(self):
        actors = [actor(10000671, "Engine.WorldInfo"), actor(10000680, "Engine.InterpActor"),
                  actor(10000700, "TribesGame.TrCTFBase_BloodEagle", base=10000680)]
        self.assertFalse(usable_marker(actors[2], "flag", None, world_lines(actors)))
        moving = actor(10000701, "TribesGame.TrCTFBase_DiamondSword", base=10000671, attachparent=10000680)
        self.assertFalse(usable_marker(moving, "flag", None, world_lines(actors)))

    def test_unattached_markers_still_work(self):
        self.assertTrue(usable_marker(actor(5, "TribesGame.TrCTFBase_BloodEagle"), "flag"))


if __name__ == "__main__":
    unittest.main()
