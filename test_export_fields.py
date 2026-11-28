"""Build for Direct Host remembers the fields that never change for a map and finds the draft's intake."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.community_map_editor.export_window import draft_intake, remember_fields, remembered_fields


class ExportFieldTests(unittest.TestCase):
    def test_finds_the_intake_a_draft_was_built_from(self):
        with TemporaryDirectory() as directory:
            library = Path(directory)
            (library / "DraftRules").mkdir()
            (library / "DraftRules" / "DraftTA035.json").write_text(json.dumps({"intake_sha256": "8651c6a83301241d" + "0" * 48}))
            intake = library / "Katabatic_Ter-8651c6a83301241d"
            intake.mkdir()
            self.assertIsNone(draft_intake(library, "DraftTA035"))  # No manifest yet.
            (intake / "manifest.json").write_text("{}")
            self.assertEqual(draft_intake(library, "DraftTA035"), intake)
            self.assertIsNone(draft_intake(library, "DraftTA099"))

    def test_fields_round_trip_and_tolerate_a_missing_file(self):
        with TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            self.assertEqual(remembered_fields(), {})
            remember_fields({"host": "D:/host", "export_root": "D:/CWExports", "labels": {"Katabatic_Ter": "Katabatic"}})
            self.assertEqual(remembered_fields()["labels"]["Katabatic_Ter"], "Katabatic")


if __name__ == "__main__":
    unittest.main()
