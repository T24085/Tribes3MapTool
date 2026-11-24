"""Verify legacy Unreal exports through the shared editor intake and review pipeline."""

from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from intake_contract import UNREAL_SCENE, load_intake
from map_pipeline import pipeline_config
from mesh_mappings import placements
from minimap import grayscale_png
from object_catalog import build_rows, classify, load_catalog
from public_layout import export_public_layout
from source_profiles import UNREAL_PROFILES
from t2_reader import unpack_intake
from unreal_legacy_intake import convert_unreal


def unreal_draft_fixture(root, game):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "Original.t3d"
    source.write_text("""Begin Map
 Begin Level Name=PersistentLevel
  Begin Actor Class=Gameplay.PlayerStart Name=BlueSpawn
   Location=(X=-100,Y=200,Z=150)
   Rotation=(Yaw=16384)
  End Actor
  Begin Actor Class=Gameplay.PlayerStart Name=RedSpawn
   Location=(X=300,Y=200,Z=150)
  End Actor
  Begin Actor Class=Gameplay.MPFlag Name=BlueFlag
   Location=(X=-100,Y=0,Z=10)
  End Actor
  Begin Actor Class=Gameplay.MPFlag Name=RedFlag
   Location=(X=300,Y=0,Z=10)
  End Actor
  Begin Actor Class=Engine.StaticMeshActor Name=Deck
   Location=(X=30,Y=80,Z=200)
   Rotation=(Yaw=16384)
   DrawScale3D=(X=2,Y=1,Z=2)
   Begin Object Class=Engine.StaticMeshComponent Name=DeckMesh
    StaticMesh=StaticMesh'PrivatePackage.PrivateDeck'
   End Object
  End Actor
  Begin Actor Class=Fixture.ExtraTrigger Name=Unmapped
   Metadata="C:\\Private\\Original"
  End Actor
 End Level
End Map
""", encoding="utf-8")
    points = bytearray(257 * 257 * 2)
    struct.pack_into(">H", points, len(points) - 2, 65535)
    (root / "Ground.png").write_bytes(grayscale_png(points, 16, 257, 257))
    settings = dict(format=1, game=game, units_per_metre=50, heightmap="Ground.png", raster_y=-1,
                    origin_m=[-32, 40, 0], step_m=0.25, height_range_m=[0, 1],
                    teams=[dict(flag="PersistentLevel.BlueFlag", spawns=["BlueSpawn"]),
                           dict(flag="RedFlag", spawns=["RedSpawn"])])
    source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
    return source, settings


class UnrealLegacyIntakeTests(unittest.TestCase):
    def test_both_profiles_project_heightfield_objectives_and_mapped_objects(self):
        for game, profile in UNREAL_PROFILES.items():
            with self.subTest(game=game), TemporaryDirectory() as scratch:
                root = Path(scratch)
                source, _ = unreal_draft_fixture(root, game)
                manifest = convert_unreal(source, root / "Intakes")
                header, terrain, rows = load_intake(manifest)
                self.assertEqual((header["format"], header["profile"]), (22, profile))
                self.assertEqual(terrain["origin_cm"], [-3200, 4000, 0])
                self.assertEqual(terrain["height_m"][-1], 1)
                self.assertEqual(header["minimap"]["image_bounds"], [[-3200, -2400], [3200, 4000]])
                self.assertEqual(header["files"][UNREAL_SCENE], sha256(manifest.with_name(UNREAL_SCENE).read_bytes()).hexdigest())
                self.assertEqual(pipeline_config(source_profile=profile)["profile"], profile)
                catalog = load_catalog(source_profile=profile)
                report = classify(rows, terrain, catalog)
                active = build_rows(rows, report)
                self.assertEqual(len([row for row in active if row["status"] == "marker"]), 4)
                spawn = next(row for row in active if row["name"] == "BlueSpawn")
                self.assertEqual(spawn["position_cm"], [-200, 400, 300])
                self.assertAlmostEqual(spawn["yaw"], 90.0)
                unmapped = next(row for row in rows if row["name"] == "Unmapped")
                self.assertTrue(report[unmapped["id"]]["blocked"])
                model = "unrealmesh:privatepackage.privatedeck"
                row, mesh, location, rotation, scale = placements(active, terrain, {model: "/Game/Community/Deck"})[0]
                self.assertEqual((row["name"], location, scale), ("Deck", (60, 160, 400), (2, 1, 2)))
                self.assertAlmostEqual(rotation[2], 0.7071067811865475)

                public = export_public_layout(manifest, root / "Share.zip")
                with ZipFile(public) as archive:
                    self.assertNotIn(UNREAL_SCENE, archive.namelist())
                    self.assertNotIn(b"Private", archive.read("objects.json"))
                opened = unpack_intake(public, root / "Shared")
                shared, ground, shared_rows = load_intake(opened)
                self.assertEqual(shared["profile"], profile)
                self.assertEqual(set(ground["height_m"]), {0})
                self.assertEqual(next(row for row in shared_rows if "model_reference" in row)["model_reference"],
                                 "unrealmesh:model-00001")

    def test_marker_bindings_require_unique_existing_safe_placements(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            source, settings = unreal_draft_fixture(root, "ascend")
            settings["teams"][1]["flag"] = "BlueSpawn"
            source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot be assigned twice"):
                convert_unreal(source, root / "Intakes")
            settings["teams"][1]["flag"] = "RedFlag"
            metadata = source.with_suffix(".import.json")
            metadata.write_text(json.dumps(settings), encoding="utf-8")
            source.write_text(source.read_text().replace("Location=(X=300,Y=0,Z=10)", "Location=(X=9900,Y=0,Z=10)"))
            with self.assertRaisesRegex(ValueError, "within the playable bounds"):
                convert_unreal(source, root / "Intakes")

    def test_rehashed_intake_rejects_an_invalid_quaternion(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            source, _ = unreal_draft_fixture(root, "vengeance")
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            placement = next(row["transform"] for row in rows if row["name"] == "Deck")
            placement["rotation_xyzw"] = [0, 0, 0, 0]
            modified = json.dumps(rows).encode("utf-8")
            manifest.with_name("objects.json").write_bytes(modified)
            header["files"]["objects.json"] = sha256(modified).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid Unreal actor rotation"):
                load_intake(manifest)
