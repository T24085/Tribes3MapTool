"""CTF drafts approve base turrets, generators and sensors by default; stations wait for host support."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from mission_equipment import auto_approved


class AutoApprovalTests(unittest.TestCase):
    def test_ctf_defaults(self):
        for role in ("base_turret", "generator", "sensor"):
            self.assertTrue(auto_approved(role, "ctf"), role)
        for role in ("inventory_station", "vehicle_station", "force_shield"):
            self.assertFalse(auto_approved(role, "ctf"), role)

    def test_other_modes_have_their_own_sets(self):
        self.assertFalse(auto_approved("base_turret", "arena"))


if __name__ == "__main__":
    unittest.main()
