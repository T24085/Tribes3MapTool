"""Tribes: Ascend base equipment placed as retail TRIBES 3 objects (Tribes3MapTool#29)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools/community_map_editor"))
sys.path.insert(0, str(ROOT / "tools"))
from attach_t2_equipment import native_layout  # noqa: E402
from mission_equipment import ASCEND_GROUP_BASE, mission_equipment  # noqa: E402
from source_profiles import inventory_station_package  # noqa: E402

SUPPORT = dict(actor="StaticMeshActor_1", asset="/Game/Community/DraftTA002/Terrain.Terrain", kind="terrain")


def row(ident, cls, x, *, status="review", **extra):
    return dict(id=ident, parent=None, source_class="TribesGame." + cls, name=f"{cls}_{ident}", status=status,
                reason="", properties={}, **extra)


def placed(ident, cls, x):
    return row(ident, cls, x, transform=dict(position_cm=[x, 0.0, 0.0], rotation_xyzw=[0, 0, 0, 1], scale=[1, 1, 1]))


def flag(ident, suffix, team):
    return row(ident, "TrCTFBase_" + suffix, 0, status="marker", kind="flag", team=team)


def tartarus(blood_eagle=1, diamond_sword=2):
    """Each Tartarus base: a generator, a radar, two turrets, two stations and a vehicle station."""
    rows = [flag(1, "BloodEagle", blood_eagle), flag(2, "DiamondSword", diamond_sword)]
    ident = 10
    for suffix, side in (("BloodEagle", 1), ("DiamondSword", -1)):
        for cls in ("TrPowerGenerator", "TrRadarStation", "TrBaseTurret", "TrBaseTurret",
                    "TrInventoryStation", "TrInventoryStation", "TrVehicleStation"):
            rows.append(placed(ident, f"{cls}_{suffix}", side * 1000.0 * ident))
            ident += 1
    rows.append(row(99, "TrVehiclePad", 0))
    return rows


class AscendEquipmentTests(unittest.TestCase):
    def test_classes_map_to_roles_teams_and_base_power(self):
        equipment = mission_equipment(tartarus(), None)
        records = {row["source_id"]: row for row in equipment["equipment"]}
        self.assertEqual([records[i]["role"] for i in range(10, 17)],
                         ["generator", "sensor", "base_turret", "base_turret", "inventory_station",
                          "inventory_station", "vehicle_station"])
        self.assertNotIn(99, records)  # the pad itself has no team or retail role
        self.assertEqual({records[i]["team"] for i in range(10, 17)}, {1})
        self.assertEqual({records[i]["team"] for i in range(17, 24)}, {2})
        self.assertEqual(records[10]["datablock"], "TrPowerGenerator_BloodEagle")
        self.assertTrue(all(not row["review"] for row in records.values()))
        self.assertEqual(equipment["power_groups"], [
            dict(source_id=ASCEND_GROUP_BASE + 1, name="Team 1 base power", team=1, declared_power=None, generators=[10]),
            dict(source_id=ASCEND_GROUP_BASE + 2, name="Team 2 base power", team=2, declared_power=None, generators=[17]),
        ])

    def test_teams_follow_the_assigned_flags(self):
        equipment = mission_equipment(tartarus(blood_eagle=2, diamond_sword=1), None)
        records = {row["source_id"]: row for row in equipment["equipment"]}
        self.assertEqual(records[10]["team"], 2)
        self.assertEqual(records[17]["team"], 1)
        self.assertEqual(records[10]["power_group"], ASCEND_GROUP_BASE + 2)

    def test_unmatched_teams_and_missing_transforms_stay_in_review(self):
        rows = [item for item in tartarus() if item["id"] not in (2, 10)]
        rows.append(flag(2, "BloodEagle", 2))  # both flags claim Blood Eagle
        rows.append(row(10, "TrPowerGenerator_BloodEagle", 0))  # saved before transforms were recorded
        records = {item["source_id"]: item for item in mission_equipment(rows, None)["equipment"]}
        self.assertEqual(len(records), 14)
        self.assertEqual(records[10]["team"], 0)
        self.assertIsNone(records[10]["power_group"])
        self.assertIn("Re-import this map to record the equipment transform.", records[10]["review"])
        self.assertIn("Source team does not match an assigned CTF flag.", records[17]["review"])

    def test_a_base_without_a_generator_stays_powered(self):
        rows = [row for row in tartarus() if not row["source_class"].startswith("TribesGame.TrPowerGenerator")]
        groups = mission_equipment(rows, None)["power_groups"]
        self.assertTrue(all(group["declared_power"] is True and not group["generators"] for group in groups))

    def test_the_host_adapter_accepts_an_approved_ascend_base(self):
        equipment = mission_equipment(tartarus(), None)
        placements = []
        for record in equipment["equipment"]:
            if record["role"] == "vehicle_station":
                continue  # the host has no retail vehicle station yet
            placement = dict(source_id=record["source_id"], anchor_position_cm=record["transform"]["position_cm"],
                             yaw_degrees=0.0, support=SUPPORT)
            if record["role"] == "inventory_station":
                placement["station_floor_z_cm"] = -10.0
            placements.append(placement)
        result = native_layout(equipment, dict(map="DraftTA002", placements=placements))
        layout = result["layout"]
        self.assertEqual((layout["generators_per_team"], layout["radars_per_team"], layout["turrets_per_team"]), (1, 1, 2))
        self.assertEqual(len(layout["inventory_stations"]), 4)
        self.assertEqual(layout["power_groups"], {f"T2Group{ASCEND_GROUP_BASE + 1}": dict(team=1, mode="generators"),
                                                  f"T2Group{ASCEND_GROUP_BASE + 2}": dict(team=2, mode="generators")})
        self.assertEqual(result["package"], "/Game/Maps/Community/DraftTA002/Arena_DraftTA002_P")

    def test_station_blueprints_cover_tribes_2_and_ascend_drafts(self):
        self.assertEqual(inventory_station_package("DraftTA002"),
                         "/Game/Community/DraftTA002/Equipment/BP_CommunityInventoryStation")
        inventory_station_package("DraftT2001")
        for other in ("DraftTV001", "DraftT1001", "DraftCTF001"):
            with self.assertRaises(ValueError):
                inventory_station_package(other)


if __name__ == "__main__":
    unittest.main()
