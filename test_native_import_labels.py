"""Flag dropdown labels in the native import dialog name the flag and its team (#8)."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from native_import_window import flag_labels, marker_label


def actor(path, source_class, role):
    return dict(name=path.rsplit(".", 1)[-1], object_path=path, source_class=source_class, role=role)


ASCEND = (actor("TrCTF-Tartarus.TheWorld.PersistentLevel.TrCTFBase_BloodEagle_0", "TribesGame.TrCTFBase_BloodEagle", "flag"),
          actor("TrCTF-Tartarus.TheWorld.PersistentLevel.TrCTFBase_DiamondSword_0", "TribesGame.TrCTFBase_DiamondSword",
                "flag"))


class FlagLabelTests(unittest.TestCase):
    def test_ascend_bases_read_as_team_flags(self):
        self.assertEqual([marker_label(flag) for flag in ASCEND],
                         ["FLAG · Blood Eagle · TrCTFBase_BloodEagle_0", "FLAG · Diamond Sword · TrCTFBase_DiamondSword_0"])

    def test_vengeance_stands_and_unknown_teams(self):
        stand = actor("Map.myLevel.CaptureStandPhoenix0", "Gameplay.CaptureStandPhoenix", "capture-point")
        self.assertEqual(marker_label(stand), "FLAG STAND · Phoenix · CaptureStandPhoenix0")
        self.assertEqual(marker_label(actor("Map.L.Flag0", "Game.Flag", "flag")), "FLAG · Flag0")
        self.assertEqual(marker_label(actor("Map.L.Rock3", "Engine.StaticMeshActor", None)), "Rock3 (StaticMeshActor)")

    def test_labels_map_back_to_object_paths_and_stay_unique(self):
        self.assertEqual(set(flag_labels(ASCEND).values()), {flag["object_path"] for flag in ASCEND})
        twins = (actor("A.PersistentLevel.TrCTFBase_BloodEagle_0", "TribesGame.TrCTFBase_BloodEagle", "flag"),
                 actor("B.PersistentLevel.TrCTFBase_BloodEagle_0", "TribesGame.TrCTFBase_BloodEagle", "flag"))
        labels = flag_labels(twins)
        self.assertEqual(len(labels), 2)
        self.assertEqual(set(labels.values()), {twin["object_path"] for twin in twins})


if __name__ == "__main__":
    unittest.main()
