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
            base_hash, content_hash = "1" * 64, "2" * 64
            plan = json.dumps({"map": map_id, "central_before": base_hash, "after_catalog": {map_id: content_hash}}).encode()
            (export / "map-release.json").write_bytes(plan)
            receipt = {"format": 1, "map": map_id, "base_inventory_sha256": base_hash, "new_shared_files": [],
                       "release_plan_sha256": hashlib.sha256(plan).hexdigest()}
            (export / "export-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

            release = capture_export(library, export)
            self.assertEqual(release, capture_export(library, export))
            self.assertEqual(release_history(library), [release])
            self.assertEqual(release["content_hash"], content_hash)
            self.assertEqual(verify_release(release)["content_hash"], content_hash)
            with self.assertRaisesRegex(ValueError, "recorded map catalog fingerprint"):
                verify_release(dict(release, content_hash="0" * 64))

            peer = HostCatalog("https://test.invalid:18443")
            document = {"format": 3, "id": map_id, "contentHash": content_hash, "files": [],
                        "objectives": [row], "definition": definition, "sharedHash": "6" * 64}
            index_row = {"id": map_id, "label": label, "contentHash": content_hash, "manifestHash": "3" * 64}
            index = {"format": 3, "coreHash": "4" * 64, "maps": [index_row]}
            changed_shared = dict(document, contentHash="7" * 64, sharedHash="8" * 64)
            with patch.object(peer, "request", side_effect=[(index, "f" * 64), (document, "3" * 64),
                    (dict(document, objectives=[dict(row, sha256="5" * 64)]), "3" * 64), (document, "0" * 64),
                    (changed_shared, "3" * 64), (document, "3" * 64)]):
                retrieved = peer.index()[map_id.casefold()]
                self.assertTrue(peer.compare(retrieved, release))
                self.assertFalse(peer.compare(retrieved, release))
                with self.assertRaisesRegex(ValueError, "catalog changed"):
                    peer.compare(retrieved, release)
                self.assertFalse(peer.compare(dict(retrieved, contentHash=changed_shared["contentHash"]), release))
                with self.assertRaisesRegex(ValueError, "no catalog fingerprint"):
                    peer.compare(retrieved, dict(release, content_hash=None))

            objective.write_bytes(b"return []")
            with self.assertRaisesRegex(ValueError, "failed verification"):
                verify_release(release)


if __name__ == "__main__":
    unittest.main()
