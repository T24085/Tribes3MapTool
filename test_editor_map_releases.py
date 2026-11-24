"""Check staged history and comparison against a fingerprinted host catalog."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.community_map_editor.map_releases import HostCatalog, capture_export, release_history, verify_release


class MapReleaseTests(unittest.TestCase):
    def test_release_requires_verified_local_bytes_and_exact_current_manifest(self):
        with TemporaryDirectory() as tmp:
            export = Path(tmp).resolve() / "export"
            library = export.parent / "library"
            map_id = "DraftT2001"
            runtime = export / "payload" / "lua" / map_id
            runtime.mkdir(parents=True)
            label = "Release fixture (T2 2026)"
            definition = {"label": label, "asset_group": map_id, "level": f"Arena_{map_id}_P"}
            (runtime / "map.json").write_text(json.dumps(dict(definition, id=map_id, format=1)), encoding="utf-8")
            objective = runtime / "objectives.lua"
            objective.write_bytes(b"return {}")
            row = {"path": f"lua/{map_id}/objectives.lua", "bytes": objective.stat().st_size,
                   "sha256": hashlib.sha256(objective.read_bytes()).hexdigest()}
            (runtime / "manifest.json").write_text(json.dumps({"format": 1, "files": [row]}), encoding="utf-8")
            receipt = {"format": 1, "map": map_id, "base_inventory_sha256": "1" * 64, "new_shared_files": []}
            (export / "export-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

            release = capture_export(library, export)
            self.assertEqual(release, capture_export(library, export))
            self.assertEqual(release_history(library), [release])
            verify_release(release)

            peer = HostCatalog("https://test.invalid:18443")
            document = {"format": 3, "id": map_id, "contentHash": "2" * 64, "files": [],
                        "objectives": [row], "definition": definition}
            index_row = {"id": map_id, "label": label, "contentHash": "2" * 64, "manifestHash": "3" * 64}
            index = {"format": 3, "coreHash": "4" * 64, "maps": [index_row]}
            with patch.object(peer, "request", side_effect=[(index, "f" * 64), (document, "3" * 64),
                    (dict(document, objectives=[dict(row, sha256="5" * 64)]), "3" * 64), (document, "0" * 64)]):
                retrieved = peer.index()[map_id.casefold()]
                self.assertTrue(peer.compare(retrieved, release))
                self.assertFalse(peer.compare(retrieved, release))
                with self.assertRaisesRegex(ValueError, "catalog changed"):
                    peer.compare(retrieved, release)

            objective.write_bytes(b"return []")
            with self.assertRaisesRegex(ValueError, "failed verification"):
                verify_release(release)


if __name__ == "__main__":
    unittest.main()
