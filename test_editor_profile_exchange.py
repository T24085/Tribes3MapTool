"""Verify portable profile privacy, application, and recorded draft replay."""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from tools.community_map_editor import launch
from tools.community_map_editor.conversion_rules import checksum, create_snapshot, edit_scope, write
from tools.community_map_editor.map_pipeline import pipeline_config
from tools.community_map_editor.object_catalog import load_catalog
from tools.community_map_editor.profile_exchange import active_profile, collect_bundle, install_bundle, validate_bundle
from tools.community_map_editor.source_profiles import NAMES, T2, VENGEANCE


def seal(document):
    document.pop("sha256", None)
    document["sha256"] = checksum(document)
    return document


class ProfileExchangeTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.library = self.root / "author"
        self.peer = self.root / "other-author"
        self.profile = self.root / "portable.json"

    def test_default_profiles_round_trip_in_a_different_workspace(self):
        for game in NAMES:
            with self.subTest(game=game):
                bundle = collect_bundle(self.library, game)
                write(self.profile, bundle)
                self.assertEqual(install_bundle(self.peer, self.profile), bundle)
                self.assertEqual(collect_bundle(self.peer, game), bundle)
        self.assertEqual(len(list((self.peer / "GameProfiles").glob("*.json"))), len(NAMES))

    def test_installed_editor_scripts_can_import_profile_support_in_isolation(self):
        project = self.root / "project" / "MapEditor.uproject"
        launch.install_editor_scripts(project, {}, include_seed=False)
        scripts = project.parent / "Content/Python"
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
             "from profile_exchange import collect_bundle; assert collect_bundle(None, 'tribes2-ctf')['conversion']",
             str(scripts)],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_share_uses_loose_catalog_configuration_and_retains_prior_imports(self):
        catalog = deepcopy(collect_bundle(self.library, T2)["catalog"])
        catalog["revision"] = 2
        catalog["features"]["ctf"]["label"] = "Capture the flag"
        write(self.library / "tribes2_catalog.json", catalog)
        first = collect_bundle(self.library, T2)
        write(self.profile, first)
        install_bundle(self.peer, self.profile)

        bundle = deepcopy(first)
        bundle["conversion"]["conversions"]["terrain.tile_cm"]["value"] = 1200
        bundle["pipeline"]["draft"][0]["label"] = "Verify source files"
        bundle["catalog"]["features"]["ctf"]["label"] = "Capture objectives"
        write(self.profile, seal(bundle))
        install_bundle(self.peer, self.profile)
        self.assertEqual(load_catalog(self.peer, source_profile=T2).features["ctf"]["label"], "Capture objectives")
        self.assertEqual(pipeline_config(self.peer, source_profile=T2)["draft"][0]["label"], "Verify source files")
        self.assertTrue((self.peer / "GameProfiles" / "History" / (first["sha256"] + ".json")).is_file())

    def test_new_drafts_pin_all_three_components_and_old_drafts_replay(self):
        manifest = self.root / "manifest.json"
        manifest.write_text("fixture", encoding="utf-8")
        header = {"profile": VENGEANCE, "sources": [{"sha256": "1" * 64}],
                  "files": {"objects.json": "2" * 64}}
        before = create_snapshot(self.peer, header, [], manifest, {})
        bundle = collect_bundle(self.library, VENGEANCE)
        bundle["conversion"]["conversions"]["terrain.tile_cm"]["value"] = 1200
        bundle["catalog"]["features"]["ctf"]["detail"] = "Shared objective definitions."
        bundle["pipeline"]["draft"][0]["label"] = "Check the source package"
        write(self.profile, seal(bundle))
        install_bundle(self.peer, self.profile)
        after = create_snapshot(self.peer, header, [], manifest, {})
        self.assertEqual(after["profile"], bundle["conversion"])
        self.assertEqual(after["results"]["terrain.tile_cm"], {"value": 1200, "layer": "profile"})
        self.assertEqual(after["catalog"], bundle["catalog"])
        self.assertEqual(after["pipeline"], bundle["pipeline"])
        self.assertEqual(create_snapshot(self.peer, header, [], manifest, {}, replay=before), before)

    def test_rejects_invalid_or_private_bundles_before_install(self):
        original = collect_bundle(self.library, T2)
        write(self.profile, original)
        install_bundle(self.peer, self.profile)
        for invalid_label in ("C:/Users/Mapper/asset.png", "/home/mapper/file", "192.168.0.8:8080",
                              "host at 2001:db8::1", "settings at name@example.test"):
            with self.subTest(label=invalid_label):
                bundle = deepcopy(original)
                bundle["pipeline"]["draft"][0]["label"] = invalid_label
                write(self.profile, seal(bundle))
                with self.assertRaisesRegex(ValueError, "connection details"):
                    install_bundle(self.peer, self.profile)
        bad = deepcopy(original)
        bad["pipeline"]["profile"] = VENGEANCE
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_bundle(bad)
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_bundle(seal(bad))
        self.assertEqual(active_profile(self.peer, T2), original)

    def test_install_checks_existing_game_overrides_before_changing_defaults(self):
        first = collect_bundle(self.library, T2)
        write(self.profile, first)
        install_bundle(self.peer, self.profile)
        edit_scope(self.peer, "game-" + T2, first["conversion"], {"terrain.tile_cm": 1200}, None)
        altered = deepcopy(first)
        rule = altered["conversion"]["conversions"]["terrain.tile_cm"]
        rule.update(value=700, range=[200, 900])
        write(self.profile, seal(altered))
        with self.assertRaisesRegex(ValueError, "outside the conversion rule"):
            install_bundle(self.peer, self.profile)
        self.assertEqual(active_profile(self.peer, T2), first)


if __name__ == "__main__":
    unittest.main()
