"""Ascend maps cooked as separate gameplay, _Ter (world) and _Sound levels import as one map."""

import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from test_unreal_binary_scene import binary_draft_fixture

from intake_contract import UNREAL_SCENE, load_intake
from unreal_binary_scene import LEVEL_LINE_STRIDE, read_binary_scene
from unreal_legacy_intake import convert_unreal, split_level_set, validate_unreal_settings


class SplitLevelSetTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def touch(self, *names):
        for name in names:
            (self.root / name).write_bytes(b"")
        return [self.root / name for name in names]

    def test_gameplay_level_selects_its_world_and_sound_levels(self):
        gameplay, world, sound = self.touch("TrCTF-Fixture.fmap", "Fixture_Ter.fmap", "Fixture_Sound.fmap")
        self.assertEqual(split_level_set(gameplay), (world, [gameplay, sound]))

    def test_variant_levels_prefer_their_own_world_and_sound(self):
        gameplay, own_world, own_sound, _ = self.touch(
            "TrTeamRabbit-Fixture.fmap", "TrTeamRabbit-Fixture_Ter.fmap", "TrTeamRabbit-Fixture_Sound.fmap",
            "Fixture_Ter.fmap")
        self.assertEqual(split_level_set(gameplay), (own_world, [gameplay, own_sound]))

    def test_sound_level_is_optional_and_other_files_import_alone(self):
        gameplay, world = self.touch("TrCTF-Fixture.fmap", "Fixture_Ter.fmap")
        self.assertEqual(split_level_set(gameplay), (world, [gameplay]))
        lone, = self.touch("TrCTF-Lonely.fmap")
        self.assertEqual(split_level_set(lone), (lone, []))
        self.assertEqual(split_level_set(world), (world, []))

    def test_level_settings_are_validated(self):
        base = dict(format=1, game="ascend", units_per_metre=52, teams=[])
        self.assertEqual(validate_unreal_settings(dict(base, levels=["TrCTF-Fixture.fmap"]))["levels"],
                         ["TrCTF-Fixture.fmap"])
        for levels in (["Gameplay.upk"], ["A.fmap", "a.fmap"], ["../A.fmap"], [f"L{i}.fmap" for i in range(9)]):
            with self.subTest(levels=levels), self.assertRaisesRegex(ValueError, "Ascend .fmap levels"):
                validate_unreal_settings(dict(base, levels=levels))
        with self.assertRaisesRegex(ValueError, "Ascend .fmap levels"):
            validate_unreal_settings(dict(base, game="vengeance", levels=["A.fmap"]))


class MergedLevelImportTests(unittest.TestCase):
    def test_gameplay_level_actors_merge_into_the_world_import(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            world = binary_draft_fixture(root, "ascend")
            gameplay = root / "TrCTF-Gameplay.fmap"
            shutil.copyfile(world, gameplay)

            scene = read_binary_scene(world, "ascend", 50, [gameplay])
            merged = [actor for actor in scene["actors"] if actor.get("merged_level")]
            self.assertTrue(merged)
            self.assertTrue(all(actor["line"] >= LEVEL_LINE_STRIDE for actor in merged))
            self.assertTrue(all(actor["object_path"].startswith("TrCTF-Gameplay.") for actor in merged))
            self.assertEqual(scene["levels"][0]["label"], "TrCTF-Gameplay")

            metadata = world.with_suffix(".import.json")
            settings = json.loads(metadata.read_text(encoding="utf-8"))
            settings["levels"] = [gameplay.name]
            settings["teams"] = [dict(flag="TrCTF-Gameplay.BlueFlag", spawns=["TrCTF-Gameplay.BlueSpawn"]),
                                 dict(flag="TrCTF-Gameplay.RedFlag", spawns=["TrCTF-Gameplay.RedSpawn"])]
            metadata.write_text(json.dumps(settings), encoding="utf-8")
            manifest = convert_unreal(world, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertIn(gameplay.name, {source["name"] for source in header["sources"]})
            source_scene = json.loads((manifest.parent / UNREAL_SCENE).read_text(encoding="utf-8"))
            markers = [row for row in rows if row["status"] == "marker"]
            self.assertEqual(len(markers), 4)
            self.assertTrue(all(row["name"] in {"BlueFlag", "RedFlag", "BlueSpawn", "RedSpawn"} for row in markers))
            self.assertEqual(source_scene["levels"][0]["name"], gameplay.name)

            # The unqualified name now matches an actor in each level, so it is rejected as ambiguous.
            settings["teams"][0]["flag"] = "BlueFlag"
            metadata.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unambiguous"):
                convert_unreal(world, root / "Ambiguous")


if __name__ == "__main__":
    unittest.main()
