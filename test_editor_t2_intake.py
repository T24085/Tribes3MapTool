"""Regression coverage for the map editor's T2 intake and archive handoff."""

import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Record
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from conversion_window import coverage_status
from batch_rebuild import compare_drafts, create_batch, load_batch, save_batch
from conversion_rules import checksum, create_snapshot, draft_rules_path, edit_scope, history, profile_config, selectors, write
from intake_contract import load_intake, load_intake_geometry
from interior_geometry import render_sections
from interior_lighting import LIGHTING_INDEX, lighting_summary, load_lighting_index
from map_pipeline import PipelineRun, pipeline_config, validate_pipeline
from mesh_mappings import placements, source_reference, source_transform
from mission_weather import cloud_layers, precipitation_emitters
from object_catalog import build_rows, classify, load_catalog, source_fields
from minimap import export_host_minimap, framing, terrain_pixels, view_transform
from perimeter import (PolygonWalls, check_inside_perimeter, check_marker_clearance, flag_bounds, mission_extents,
                       plan_boundaries, plan_walls, triangulate_polygon, turn, verified_extents, wall_appearance,
                       wall_boxes, wall_surface)
from perimeter_collision import probe_profiles, validate_channels
from public_layout import export_public_layout
from source_library import catalog_rows, record_draft, record_source
from t2_reader import convert, parse_mission, unpack_intake
from terrain_layers import layer_choices, load_weights, weight_png


class IntakeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.mission = self.folder / "Test.mis"
        self.mission.write_text("""new SimGroup(MissionGroup){
          new TerrainBlock(){terrainFile="Test.ter";position="-8 -16 1";squareSize="8";emptySquares="65537";};
          new SimGroup(Team2){new SpawnSphere(S){position="1 2 3";rotation="0 0 1 90";};
          new Item(F){dataBlock="Flag";position="4 5 6";};};
          new InteriorInstance(I){interiorFile="base.dif";note="look; // still quoted";};
          new Item(Rabbit){dataBlock="Flag";missionTypesList="Rabbit";position="0 0 0";};};""")
        self.terrain = self.folder / "Test.ter"
        self.terrain.write_bytes(
            b"\2" + struct.pack("<H", 32) * 65536 + bytes(65536) + bytes(8)
        )
        self.library = self.folder / "library"

    def test_public_layout_reopens_with_ctf_and_power_but_no_private_payload(self):
        private = ("SecretAuthor", "192.168.23.17", "PrivateTerrain", "InternalNotes", "PrivateClass")
        self.mission.rename(self.mission.with_name(private[0] + ".mis"))
        self.mission = self.mission.with_name(private[0] + ".mis")
        self.terrain.rename(self.terrain.with_name(private[2] + ".ter"))
        self.terrain = self.terrain.with_name(private[2] + ".ter")
        self.mission.write_text('''new SimGroup(MissionGroup) {
          new TerrainBlock(){terrainFile="PrivateTerrain.ter";position="-8 -16 0";squareSize="8";};
          new MissionArea(SecretAuthor){area="-8 -16 32 32";};
          new Sun(S){direction="0 3 -4";color="1 .75 .5";};
          new SimGroup(Team1){new SpawnSphere(S){position="1 2 3";};
          new Item(F){datablock="Flag";position="4 5 6";};
          new SimGroup(SecretAuthorPowered){
          new StaticShape(InternalNotes){datablock="GeneratorLarge";position="7 8 9";};};};
          new SimGroup(Team2){new SpawnSphere(S){position="11 12 3";};
          new Item(F){datablock="Flag";position="14 15 6";};};
          new InteriorInstance(A){interiorFile="C:/SecretAuthor/model.dif";position="0 1 0";};
          new InteriorInstance(B){interiorFile="C:/SecretAuthor/model.dif";position="0 2 0";};
          new PrivateClass(SecretAuthor){position="192.168.23.17";InternalNotes="192.168.23.17";};};''',
                                encoding="utf-8")
        source = convert(self.mission, self.library)
        source_header, source_terrain, source_rows = load_intake(source)
        result = export_public_layout(source, self.folder / "share.zip")
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(set(archive.namelist()), {
                "manifest.json", "objects.json", "terrain.json", "height.png", "minimap.png", "equipment.json",
            })
            for file in archive.namelist():
                data = archive.read(file).lower()
                for value in (*private, "C:/", source_header["sources"][0]["sha256"]):
                    self.assertNotIn(value.lower().encode(), data)
        restored = unpack_intake(result, self.folder / "shared")
        header, terrain, rows = load_intake(restored)
        self.assertEqual(header["minimap"], source_header["minimap"])
        self.assertEqual(header["environment"], source_header["environment"])
        self.assertEqual(terrain["origin_cm"], source_terrain["origin_cm"])
        self.assertEqual(set(terrain["height_m"]), {0})
        self.assertNotEqual(header["files"]["height.png"], source_header["files"]["height.png"])
        objectives = lambda entries: sorted((r["kind"], r["team"], r["position_cm"]) for r in entries if r["status"] == "marker")
        self.assertEqual(objectives(rows), objectives(source_rows))
        refs = [source_reference(row) for row in rows if row["source_class"] == "interiorinstance"]
        self.assertEqual(refs, ["interiorinstance:model-00001.dif"] * 2)
        projected = json.loads((restored.parent / "equipment.json").read_bytes())
        self.assertEqual([(item["role"], item["team"]) for item in projected["equipment"]], [("generator", 1)])
        self.assertEqual(projected["equipment"][0]["power_group"], projected["power_groups"][0]["source_id"])
        self.assertEqual(len([row for row in build_rows(rows, classify(rows, terrain, load_catalog()))
                              if row["status"] == "marker"]), 4)

    def test_source_library_tracks_drafts_and_preserves_portable_provenance(self):
        manifest = convert(self.mission, self.library)
        original_bytes = manifest.read_bytes()
        self.assertEqual(catalog_rows(self.library, [])[0]["status"], "Intake only")

        record_source(self.library, manifest, self.mission, None)
        record_draft(self.library, "DraftT2001", manifest)
        drafts = ["DraftT2001", "DraftCTF001"]
        rows = {row["draft"]: row for row in catalog_rows(self.library, drafts)}
        self.assertEqual(rows["DraftCTF001"]["status"], "No source link")
        self.assertEqual(rows["DraftT2001"]["status"], "Source present")
        self.assertEqual(rows["DraftT2001"]["origin"], str(self.mission.resolve()))
        self.assertEqual(manifest.read_bytes(), original_bytes)

        self.terrain.unlink()
        rows = {row["draft"]: row for row in catalog_rows(self.library, drafts)}
        self.assertEqual(rows["DraftT2001"]["status"], "Original source missing")
        self.assertEqual(rows["DraftT2001"]["manifest"], manifest)
        manifest.write_bytes(original_bytes + b" ")
        rows = {row["draft"]: row for row in catalog_rows(self.library, drafts)}
        self.assertEqual(rows["DraftT2001"]["status"], "Intake unavailable")

    def test_conversion_rule_precedence_history_and_replay(self):
        text = self.mission.read_text()
        self.mission.write_text(text[:-2] + 'new Sky(S){fogDistance="100";visibleDistance="400";};};')
        manifest = convert(self.mission, self.library)
        header, _, rows = load_intake(manifest)
        profile = profile_config()
        scopes = selectors(profile, header, rows)
        other = {**header, "sources": [{**header["sources"][0], "sha256": "1"*64}, *header["sources"][1:]]}
        self.assertNotEqual(selectors(profile, other, rows)["objects"], scopes["objects"])
        fog_scope = scopes["objects"]["fog"]
        rule = "fog.visible_cm"
        edit_scope(self.library, scopes["game"], profile, {rule: 65000}, None)
        edit_scope(self.library, scopes["map"], profile, {rule: 70000}, None)
        with self.assertRaisesRegex(ValueError, "allowed range"):
            edit_scope(self.library, fog_scope, profile, {"terrain.tile_cm": 450}, None, group="fog")
        first, = edit_scope(self.library, fog_scope, profile, {rule: 50000}, None, group="fog")
        options = {"perimeter": True}
        frozen = create_snapshot(self.library, header, rows, manifest, options)
        self.assertEqual(frozen["results"][rule], {"value": 50000, "layer": "object"})
        self.assertAlmostEqual(frozen["results"]["fog.density"]["value"], 800 / 39500)
        second = edit_scope(self.library, fog_scope, profile, {}, first["sha256"], group="fog")[-1]
        self.assertEqual(second["previous"], first["sha256"])
        self.assertEqual(len(history(self.library, fog_scope)), 2)
        with self.assertRaisesRegex(ValueError, "changed while"):
            edit_scope(self.library, fog_scope, profile, {rule: 60000}, first["sha256"], group="fog")
        updated = create_snapshot(self.library, header, rows, manifest, options)
        self.assertEqual(updated["results"][rule], {"value": 70000, "layer": "map"})
        self.assertNotEqual(updated["sha256"], frozen["sha256"])
        self.assertEqual(create_snapshot(self.library, header, rows, manifest, options, replay=frozen), frozen)
        with self.assertRaisesRegex(ValueError, "does not match"):
            create_snapshot(self.library, header, rows, manifest, {"perimeter": False}, replay=frozen)

    def test_batch_plan_pins_rule_changes_and_allows_releasing_pending_work(self):
        manifest = convert(self.mission, self.library)
        header, _, rows = load_intake(manifest)
        draft = "DraftT2001"
        record_draft(self.library, draft, manifest)
        original = create_snapshot(self.library, header, rows, manifest, {})
        write(draft_rules_path(self.library, draft), original)
        profile = profile_config()
        scope = selectors(profile, header, rows)["game"]
        first, = edit_scope(self.library, scope, profile, {"terrain.tile_cm": 2400}, None)
        selected, _, _ = compare_drafts(self.library, [draft])
        self.assertEqual(selected[0]["status"], "Affected")
        ident = create_batch(self.library, selected)
        edit_scope(self.library, scope, profile, {"terrain.tile_cm": 4800}, first["sha256"])
        plan = load_batch(self.library, ident)
        item, = plan["items"]
        self.assertEqual(item["rules"]["results"]["terrain.tile_cm"]["value"], 2400)
        self.assertEqual(compare_drafts(self.library, [draft])[0][0]["status"], "In batch")
        item["status"] = "released"
        save_batch(self.library, plan)
        self.assertEqual(compare_drafts(self.library, [draft])[0][0]["status"], "Affected")
        item["base"] = "c" * 64
        save_batch(self.library, plan)
        with self.assertRaisesRegex(ValueError, "selection has changed"):
            load_batch(self.library, ident)

    def test_pipeline_checks_record_failure_and_keep_pinned_legacy_rules(self):
        manifest = convert(self.mission, self.library)
        header, _, rows = load_intake(manifest)
        initial = create_snapshot(self.library, header, rows, manifest, {})
        payload = {key: value for key, value in initial.items() if key not in {"sha256", "pipeline", "catalog"}}
        payload["format"] = 1
        legacy = {**payload, "sha256": checksum(payload)}
        pipeline = pipeline_config()
        steps = pipeline["draft"]
        steps.insert(1, {"id": "require-sun", "operation": "check-source-classes", "label": "Require sun",
                         "options": {"classes": ["Sun"]}})
        steps[:] = [step for step in steps if step["operation"] != "weather"]
        validate_pipeline(pipeline)
        write(self.library/"tribes2_pipeline.json", pipeline)
        self.assertEqual(create_snapshot(self.library, header, rows, manifest, {}, replay=legacy), legacy)
        self.assertEqual(create_snapshot(self.library, header, rows, manifest, {})["pipeline"], pipeline)
        report_path = self.folder/"pipeline.json"
        run = PipelineRun(pipeline, "draft", path=report_path)

        def missing_class(options):
            self.assertEqual(options, {"classes": ["Sun"]})
            raise ValueError("No sun in test intake.")

        with self.assertRaisesRegex(ValueError, "No sun"):
            run.execute({"verify": lambda _: None, "check-source-classes": missing_class})
        report = json.loads(report_path.read_bytes())
        self.assertEqual([item["status"] for item in report["steps"][:3]], ["passed", "failed", "pending"])
        misplaced = steps.pop()
        steps.insert(0, misplaced)
        with self.assertRaisesRegex(ValueError, "dependency order"):
            validate_pipeline(pipeline)

    def test_object_profile_keeps_invalid_markers_and_withholds_invalid_placements(self):
        text = self.mission.read_text().replace('position="1 2 3"', 'position="invalid 2 3"')
        self.mission.write_text(text[:-2] + """
            new TSStatic(Good) { shapeName="shape.dts"; position="1 2 3"; };
            new TSStatic(Bad) { shapeName="shape.dts"; position="1 2 3"; scale="0 1 1"; };
            new SomeEffect(Unknown) { width="4"; };
        };""")
        manifest = convert(self.mission, self.library)
        header, terrain, rows = load_intake(manifest)
        by_name = {row["name"]: row for row in rows}
        catalog = load_catalog()
        report = classify(rows, terrain, catalog)
        self.assertEqual(by_name["S"]["status"], "review")
        self.assertTrue(all(report[by_name[name]["id"]]["blocked"] for name in ("S", "Bad", "Unknown")))
        self.assertFalse(report[by_name["Good"]["id"]]["blocked"])
        active = {row["name"]: row for row in build_rows(rows, report)}
        self.assertEqual(active["Bad"]["status"], "excluded")
        self.assertEqual(by_name["Bad"]["properties"]["scale"], "0 1 1")
        document = catalog.document
        document["fields"]["windsafe"] = {"label": "Wind safe", "type": "boolean", "default": "1"}
        document["classes"]["world-object"]["fields"].append("windsafe")
        write(self.library / "tribes2_catalog.json", document)
        profile = load_catalog(self.library)
        fields = {item["key"]: item for item in source_fields(by_name["Good"], profile)}
        self.assertEqual((fields["windsafe"]["status"], fields["windsafe"]["value"]), ("Default", "Yes"))
        snapshot = create_snapshot(self.library, header, rows, manifest, {})
        self.assertEqual(snapshot["catalog"], document)

    def test_cloud_slots_keep_layer_positions_and_use_authored_drift(self):
        sky = {"source_id": 4, "class": "sky", "values": {
            "wind_target_axes": [0, -2, 0],
            "cloud_height_fractions": {"0": 0.2, "2": 0.45},
            "cloud_speeds_source": {"cloudspeed1": [0.01], "cloudspeed3": [0.004, 0.006]},
        }}
        skybox = [{"reference": f"Sky{i}.png", "file": "provided"} for i in range(6)]
        cloud_art = [
            {"reference": "LayerOne.png", "file": "provided"},
            {"reference": "LayerTwo.png", "reason": "missing"},
            {"reference": "LayerThree.png", "file": "provided"},
        ]
        artwork = [{"source_id": 4, "slots": skybox + [{"reference": "Env.png", "reason": "missing"}] + cloud_art}]
        layers = cloud_layers({"objects": [sky]}, artwork, -1)
        self.assertEqual([(layer["slot"], layer["layer"], layer["height"]) for layer in layers],
                         [(8, 1, 0.2), (10, 3, 0.45)])
        self.assertEqual(layers[0]["uv_per_second"], (0, -0.6))
        self.assertEqual(layers[1]["uv_per_second"], (0.24, -0.36))

        artwork[0]["slots"] = skybox + [{"reference": "CloudLayer.png", "file": "provided"}]
        self.assertEqual(cloud_layers({"objects": [sky]}, artwork, -1)[0]["slot"], 7)
        self.assertEqual(cloud_layers({"objects": [sky, {**sky, "source_id": 8}]}, artwork, -1), [])

    def test_weather_intake_preserves_authored_settings_and_rejects_detached_projections(self):
        mission = self.mission.read_text()
        self.mission.write_text(mission[:mission.rindex("};")] + """
            new Sky(Sky) { materialList="skies/ice.dml"; useSkyTextures="1";
                cloudHeightPer[0]="0.35"; cloudSpeed0="0.01 0.02"; cloudSpeed1="0.003";
                windVelocity="1 2 0"; windEffectPrecipitation="0"; fogDistance="400"; };
            new Precipitation(Snowfall) { dataBlock="Snow"; percentage="0.75"; maxNumDrops="2000";
                maxRadius="125"; minVelocity="0.25"; maxVelocity="1.5"; color2="-1 0 0 1"; };
            new Precipitation(Unmapped) { dataBlock="CustomStorm"; percentage="1.2"; maxNumDrops="1.5";
                minVelocity="4"; maxVelocity="1"; };
            new Precipitation(Rabbit) { dataBlock="Rain"; missionTypesList="Rabbit"; };
        };""")
        manifest = convert(self.mission, self.library)
        header, _, rows = load_intake(manifest)
        self.assertEqual(header["format"], 8)
        self.assertNotIn("equipment.json", header["files"])
        self.assertEqual(header["environment"]["fog"]["start_cm"], 42000)
        path = manifest.parent / "weather.json"
        weather = json.loads(path.read_bytes())
        sky, snow, custom = weather["objects"]
        self.assertEqual(sky["values"], {
            "material_list": "skies/ice.dml", "uses_sky_textures": True,
            "wind_source_xyz": [1, 2, 0], "wind_target_axes": [1, -2, 0],
            "wind_affects_precipitation": False,
            "cloud_height_fractions": {"0": 0.35}, "cloud_speeds_source": {"cloudspeed0": [.01, .02], "cloudspeed1": [.003]},
        })
        self.assertEqual(snow["values"], {
            "kind": "snow", "datablock": "Snow", "fraction": .75, "max_drops": 2000, "radius_cm": 13125,
            "minvelocity_source": .25, "maxvelocity_source": 1.5, "colors_source": {"2": [-1, 0, 0, 1]},
        })
        self.assertEqual(custom["values"], {"kind": "unmapped", "datablock": "CustomStorm"})
        self.assertEqual(custom["invalid_source_fields"], ["maxnumdrops", "maxvelocity", "minvelocity", "percentage"])
        self.assertEqual({row["id"] for row in rows if row["name"] in {"Sky", "Snowfall", "Unmapped"}},
                         {item["source_id"] for item in weather["objects"]})
        self.assertEqual(next(row["status"] for row in rows if row["name"] == "Rabbit"
                              and row["source_class"] == "Precipitation"), "excluded")

        archive = self.folder / "intake.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for item in manifest.parent.iterdir():
                output.write(item, item.name)
        self.assertEqual(load_intake(unpack_intake(archive, self.library))[0], header)
        snow["values"]["radius_cm"] = 25
        path.write_text(json.dumps(weather), encoding="utf-8")
        header["files"]["weather.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(header), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "weather records do not match"):
            load_intake(manifest)

    def test_precipitation_projection_respects_density_wind_and_unknown_types(self):
        self.add_mission_area("""
            new Sky(Sky) { windVelocity="1 2 -3"; windEffectPrecipitation="1"; };
            new Precipitation(Snowfall) { dataBlock="Snow"; percentage="0.25"; maxNumDrops="12";
                maxRadius="10"; minVelocity="0.25"; maxVelocity="1.5"; offsetSpeed="0.5";
                color1="-1 0 0 1"; color2="0.1 0.2 0.3 0.4"; };
            new Precipitation(Shower) { dataBlock="Rain"; color1="-1 0 0 1"; };
            new Precipitation(Off) { dataBlock="Snow"; percentage="0"; };
            new Precipitation(Unmapped) { dataBlock="CustomStorm"; };
        """)
        manifest = convert(self.mission, self.library)
        _, terrain, _ = load_intake(manifest)
        weather = json.loads((manifest.parent / "weather.json").read_bytes())
        snow, rain = precipitation_emitters(weather, terrain["cm_per_m"])
        self.assertEqual(snow, {
            "source_id": next(row["source_id"] for row in weather["objects"] if row["values"].get("kind") == "snow"),
            "kind": "snow", "drops": 3, "radius_cm": 1050,
            "fall_cm_per_second": (26.25, 157.5), "drift_cm_per_second": 52.5,
            "wind_cm_per_second": (105, -210, -315), "colors": [[0.1, 0.2, 0.3, 0.4]],
        })
        self.assertEqual(rain["kind"], "rain")
        self.assertEqual(rain["fall_cm_per_second"], (840, 1260))
        self.assertEqual(rain["colors"], [[0.64, 0.78, 0.93, 0.55]])
        sky = next(row for row in weather["objects"] if row["class"] == "sky")
        sky["values"]["wind_affects_precipitation"] = False
        self.assertEqual([row["wind_cm_per_second"] for row in precipitation_emitters(weather, terrain["cm_per_m"])],
                         [(0, 0, 0), (0, 0, 0)])

    def test_sky_artwork_resolves_archive_neighbors_and_keeps_missing_slots(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Image intake uses Pillow")
        from mission_sky_assets import material_names

        self.assertEqual(material_names(b"\x01\x02\0\0\0\x04test\x05other"), ["test", "other"])
        with self.assertRaisesRegex(ValueError, "truncated"):
            material_names(b"\x01\x01\0\0\0\x08short")
        with self.assertRaisesRegex(ValueError, "texture paths"):
            material_names(b"../outside.png\n")

        self.add_mission_area('new Sky(ReviewSky) { materialList="Review.dml"; };')
        content = self.folder / "Skies.vl2"
        with io.BytesIO() as pixels, Image.new("RGBA", (12, 8), (12, 30, 51, 255)) as image:
            image.save(pixels, format="PNG")
            png = pixels.getvalue()
        dml = b"Face_PX.png\r\nFace_PX.png\r\nCloudMissing.png\r\n\r\n"
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("Environments/Twilight/Review.DML", dml)
            archive.writestr("Environments/Twilight/FACE_PX.PNG", png)
            archive.writestr("Other/Face_PX.png", b"wrong asset")

        manifest = convert(self.mission, self.library, sky_assets=content)
        header, _, rows = load_intake(manifest)
        self.assertEqual(header["format"], 9)
        index = manifest.parent / "sky-artwork.json"
        artwork = json.loads(index.read_bytes())
        sky = artwork["skies"][0]
        self.assertEqual(sky["source_id"], next(row["id"] for row in rows if row["name"] == "ReviewSky"))
        self.assertEqual(sky["source_sha256"], hashlib.sha256(dml).hexdigest())
        self.assertEqual(sky["slots"][0], sky["slots"][1])
        self.assertEqual(sky["slots"][0]["source_sha256"], hashlib.sha256(png).hexdigest())
        self.assertEqual(sky["slots"][2], {"reference": "CloudMissing.png", "reason": "missing"})
        self.assertIn(sky["slots"][0]["file"], header["files"])

        loose = self.folder / "LocalSkyContent" / "skies" / "Twilight"
        loose.mkdir(parents=True)
        (loose / "Review.dml").write_bytes(dml)
        (loose / "Face_PX.png").write_bytes(png)
        self.assertEqual(convert(self.mission, self.library, sky_assets=loose.parents[1]), manifest)

        intact = self.folder / "intake.zip"
        with zipfile.ZipFile(intact, "w") as archive:
            for file in manifest.parent.iterdir():
                archive.write(file, file.name)
        self.assertEqual(load_intake(unpack_intake(intact, self.library))[0], header)

        sky["source_id"] = 123456
        index.write_text(json.dumps(artwork), encoding="utf-8")
        header["files"][index.name] = hashlib.sha256(index.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(header), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match an authored mission sky"):
            load_intake(manifest)

    def test_import_review_keeps_missing_mapped_assets_and_incomplete_textures_visible(self):
        defaults = dict(equipment=None, target=None, mapped=False, decoded=False, texture_issue=None)
        cases = (
            ("marker", {}, ("marker", False)),
            ("review", {}, ("review", True)),
            ("ignored", {"target": "/Game/Missing"}, ("Missing mesh", True)),
            ("review", {"target": "/Game/Replaced", "mapped": True}, ("Mapped", False)),
            ("review", {"decoded": True}, ("Decoded", False)),
            ("review", {"decoded": True, "texture_issue": "Missing BaseColor"}, ("Texture review", True)),
            ("ignored", {"equipment": {"role": "sensor"}}, ("Equipment review", True)),
        )
        for source, values, expected in cases:
            with self.subTest(source=source, **values):
                self.assertEqual(coverage_status({"status": source}, **(defaults | values)), expected)

    def test_terrain_layer_weights_follow_the_source_and_saved_palette_assignments(self):
        base_slots = bytearray([7]) * 65536
        base_slots[1] = 1
        names = [b"lushGrass", b"desert", b"forestFloor", b"", b"", b"", b"", b""]
        table = b"".join(bytes([len(name)]) + name for name in names)
        masks = bytearray(3 * 65536)
        masks[0] = 128
        masks[65536] = 64
        masks[2 * 65536 + 2] = 255
        self.terrain.write_bytes(b"\2" + struct.pack("<H", 32) * 65536 + base_slots + table + masks)
        manifest = convert(self.mission, self.library)
        header, terrain, _ = load_intake(manifest)
        choices = layer_choices(terrain, {"1": "Forest"})
        self.assertEqual(choices, [(0, "lushGrass", "Grass"), (1, "desert", "Forest"),
                                   (2, "forestFloor", "Forest")])
        weights, used = load_weights(manifest.parent, header["files"], choices)
        self.assertEqual(weights[:4], [(2/3, 0, 1/3), (0, 0, 1), (0, 0, 1), (0, 1, 0)])
        self.assertEqual(used, (0, 1, 2))

        automatic = layer_choices(terrain)
        self.assertIsNone(automatic[1][2])
        default, _ = load_weights(manifest.parent, header["files"], automatic)
        self.assertEqual(default[:2], [(2/3, 1/3, 0), (0, 1, 0)])
        with self.assertRaisesRegex(ValueError, "assignments"):
            layer_choices(terrain, {"7": "Rock"})
        corrupt = bytearray((manifest.parent / "material-0.png").read_bytes())
        corrupt[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            weight_png(corrupt)

    def test_real_units_holes_and_coverage(self):
        manifest = convert(self.mission, self.library)
        header, t, rows = load_intake(manifest)
        self.assertEqual(
            header["coverage"],
            {"group": 2, "terrain": 1, "marker": 2, "review": 1, "excluded": 1},
        )
        self.assertEqual(t["height_m"][0], 1)
        self.assertEqual(t["origin_cm"], [-840, 1680, 105])
        self.assertEqual(t["holes"], [1])
        self.assertEqual(rows[3]["team"], 2)
        self.assertEqual(rows[3]["position_cm"], [105, -210, 315])
        self.assertAlmostEqual(rows[3]["yaw"], 90)
        self.assertEqual(rows[5]["properties"]["note"], "look; // still quoted")
        self.assertEqual(convert(self.mission, self.library), manifest)
        (manifest.parent / "height.png").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "checksum"):
            convert(self.mission, self.library)

    def test_equipment_uses_authored_power_groups_and_survives_a_zip(self):
        self.add_mission_area('''
          new MissionArea(PlayArea) { area="-6 -10 8 12"; };
          new SimGroup(Team1) {
            new SimGroup(BasePowered) {
              new StaticShape(GenA) { dataBlock="GeneratorLarge"; position="1 2 3"; };
              new SimGroup(Defense) {
                new Turret(Roof) { dataBlock="TurretBaseLarge"; position="4 5 6"; initialBarrel="AABarrelLarge"; };
                new Turret(OtherTeam) { team="2"; dataBlock="TurretBaseLarge"; position="7 8 9"; };
                new Turret(Neutral) { team="0"; dataBlock="TurretBaseLarge"; position="9 8 7"; };
                new Turret(Rabbit) { missionTypesList="Rabbit"; dataBlock="TurretBaseLarge"; position="0 0 0"; };
              };
            };
            new SimGroup(Tower) {
              providesPower="1";
              new StaticShape(Radar) { dataBlock="SensorLargePulse"; position="4 6 8"; };
              new ForceFieldBare(Shield) { dataBlock="defaultForceFieldBare"; position="1 1 1"; };
            };
            new SimGroup(BaseUnpowered) {
              new StaticShape(Station) { dataBlock="StationInventory"; position="invalid"; };
            };
          };
        ''')
        manifest = convert(self.mission, self.library)
        header, _, objects = load_intake(manifest)
        self.assertEqual(header['format'], 7)
        self.assertEqual(header['coverage']['boundary'], 1)
        by_id = {row['id']: row for row in objects}
        path = manifest.parent/'equipment.json'
        data = json.loads(path.read_bytes())
        records = {by_id[row['source_id']]['name']: row for row in data['equipment']}
        groups = {row['name']: row for row in data['power_groups']}
        self.assertEqual(set(records), {'GenA', 'Roof', 'OtherTeam', 'Neutral', 'Radar', 'Shield', 'Station'})
        self.assertEqual(records['GenA']['transform']['position_cm'], [105, -210, 315])
        self.assertEqual(groups['BasePowered']['generators'], [records['GenA']['source_id']])
        self.assertIsNone(groups['BasePowered']['declared_power'])
        self.assertEqual(records['Roof']['power_group'], groups['BasePowered']['source_id'])
        self.assertEqual(by_id[records['Roof']['source_id']]['properties']['initialBarrel'], 'AABarrelLarge')
        self.assertIsNone(records['OtherTeam']['power_group'])
        self.assertEqual(records['Neutral']['team'], 0)
        self.assertEqual(records['Neutral']['power_group'], groups['BasePowered']['source_id'])
        self.assertIs(groups['Tower']['declared_power'], True)
        self.assertEqual(groups['Tower']['generators'], [])
        self.assertEqual(records['Shield']['power_group'], records['Radar']['power_group'])
        self.assertIsNone(records['Station']['transform'])
        self.assertIsNone(records['Station']['power_group'])
        self.assertNotIn('BaseUnpowered', groups)

        portable = self.folder/'equipment.zip'
        with zipfile.ZipFile(portable, 'w') as archive:
            for member in manifest.parent.iterdir():
                archive.write(member, member.name)
        self.assertEqual(load_intake(unpack_intake(portable, self.folder/'opened'))[0], header)

        records['Radar']['power_group'] = groups['BasePowered']['source_id']
        raw = json.dumps(data).encode()
        path.write_bytes(raw)
        header['files'][path.name] = hashlib.sha256(raw).hexdigest()
        manifest.write_text(json.dumps(header), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'does not match the source objects'):
            load_intake(manifest)

    def test_v3_terrain_keeps_masks_and_length_prefixed_authoring_rules(self):
        terrain_core = (
            struct.pack("<H", 321) * 65536 + bytes(65536)
            + b"\x04Rock" + bytes(7) + bytes([37]) * 65536
        )
        self.terrain.write_bytes(b"\x02" + terrain_core)
        v2 = convert(self.mission, self.library)
        base, terrain_v2, _ = load_intake(v2)
        self.assertNotIn("authoring", terrain_v2)
        texture = b"Rock\\t1000\nPlace by Slope\\tstep\xe9"
        terraformer = b"General\\tTab_general\\tgeneral_scale\\t125"
        rules = struct.pack("<I", len(texture)) + texture + struct.pack("<I", len(terraformer)) + terraformer
        self.terrain.write_bytes(b"\x03" + terrain_core + rules)
        manifest = convert(self.mission, self.library)
        header, terrain_v3, _ = load_intake(manifest)
        actual = terrain_v3.pop("authoring")
        self.assertEqual(actual, {
            "source_version": 3,
            "texture_rules": texture.decode("latin-1"),
            "terraformer_rules": terraformer.decode("latin-1"),
        })
        self.assertEqual(terrain_v3, terrain_v2)
        for name in ("height.png", "material-index.png", "material-0.png"):
            self.assertEqual(header["files"][name], base["files"][name])

        for tail, error in (
            (b"\0\0\0", "authoring length"),
            (rules[:-1], "authoring data"),
            (rules + b"\0", "after T2 terrain authoring"),
        ):
            with self.subTest(error=error):
                self.terrain.write_bytes(b"\x03" + terrain_core + tail)
                with self.assertRaisesRegex(ValueError, error):
                    convert(self.mission, self.library)

    def test_vl2_and_portable_intake(self):
        archive = self.folder / "Maps.vl2"
        with zipfile.ZipFile(archive, "w") as z:
            z.write(self.mission, "missions/Test.mis")
            z.write(self.terrain, "terrains/Test.ter")
            z.writestr("missions/Second.mis", "")
        with self.assertRaisesRegex(ValueError, "Select one mission"):
            convert(archive, self.library)
        manifest = convert(archive, self.library, "missions/Test.mis")
        bundle = self.folder / "portable.zip"
        with zipfile.ZipFile(bundle, "w") as z:
            for member in manifest.parent.iterdir():
                z.write(member, "Test/" + member.name)
        self.assertEqual(
            load_intake(manifest), load_intake(unpack_intake(bundle, self.library))
        )
        with zipfile.ZipFile(bundle, "a") as z:
            z.writestr("../outside", "no")
        with self.assertRaisesRegex(ValueError, "members"):
            unpack_intake(bundle, self.library)
        self.assertFalse((self.folder / "outside").exists())

    def test_unknown_or_malformed_data_is_reported(self):
        with self.assertRaises(ValueError):
            parse_mission(b"new SimGroup(G){")
        self.terrain.write_bytes(b"\1test")
        with self.assertRaisesRegex(ValueError, "terrain"):
            convert(self.mission, self.library)
        self.assertFalse(self.library.exists())

    def test_mission_decodes_referenced_interior_once_and_keeps_portable_geometry(self):
        self.mission.write_text(self.mission.read_text().replace(
            'interiorFile="base.dif";', 'interiorFile="interiors/Base.dif";position="1 2 3";\n'
            'rotation="0 0 1 90";').replace('new Item(Rabbit)', '''
            new InteriorInstance(I2){interiorFile="interiors/BASE.DIF";position="4 5 6";};
            new InteriorInstance(Missing){interiorFile="interiors/other.dif";position="7 8 9";};
            new Item(Rabbit)'''))
        mission_pack = self.folder / "Mission.vl2"
        assets_pack = self.folder / "Content.vl2"
        with zipfile.ZipFile(mission_pack, "w") as archive:
            archive.write(self.mission, "missions/Test.mis")
            archive.write(self.terrain, "terrains/Test.ter")
        with zipfile.ZipFile(assets_pack, "w") as archive:
            archive.writestr("interiors/Base.dif", b"fixture")

        def decode(raw, name, decoder, *, include_lighting):
            self.assertEqual(raw, b"fixture")
            self.assertEqual(decoder, "fixture reader")
            self.assertTrue(include_lighting)
            return {
                "format": 1, "source": {"name": name, "sha256": "a" * 64, "decoder_sha256": "b" * 64,
                                        "dif_version": 44, "lod": 0},
                "materials": ["floor"], "vertices_m": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "triangles": [[0, 0, 1, 2, 0, 0, 1, 0, 0, 1]],
            }, None, {}

        with patch("mission_interiors.load_decoder", return_value="fixture reader"), \
                patch("mission_interiors.decode_bytes", side_effect=decode) as reader:
            manifest = convert(mission_pack, self.library, dif_decoder=self.folder / "decoder.py",
                               dif_assets=assets_pack)
        reader.assert_called_once()
        header, terrain, rows = load_intake(manifest)
        self.assertEqual(header["format"], 4)
        self.assertEqual(header["interiors"], {"interiorinstance:interiors/base.dif": "interior-0001.json"})
        self.assertIn("not found", header["interior_review"]["interiorinstance:interiors/other.dif"])
        plan = placements(rows, terrain, {"interiorinstance:interiors/base.dif": "/Game/Community/Fixture/SM_Base"})
        self.assertEqual([item[2] for item in plan], [(105, -210, 315), (420, -525, 630)])

        portable = self.folder / "portable.zip"
        with zipfile.ZipFile(portable, "w") as archive:
            for member in manifest.parent.iterdir():
                archive.write(member, "intake/" + member.name)
        self.assertEqual(load_intake(unpack_intake(portable, self.library)), (header, terrain, rows))
        (manifest.parent / "interior-0001.json").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            load_intake(manifest)

    def test_interior_lighting_round_trip_preserves_faces_and_both_states(self):
        from PIL import Image

        def atlas(color):
            png = io.BytesIO()
            with Image.new("RGB", (4, 4), color) as image:
                image.save(png, format="PNG")
            return png.getvalue()

        first = Record(windingStart=0, windingCount=4, textureIndex=0, texGenIndex=0, planeIndex=0,
                       planeFlipped=False, lightMapFinalWord=(1 << 13) | (2 << 6) | 3,
                       lightMapTexGenXD=.25, lightMapTexGenYD=.5)
        second = Record(**{**vars(first), "windingStart": 4, "windingCount": 3})
        normal, alarm, direction = atlas("white"), atlas("red"), atlas("blue")
        interior = Record(
            points=[Record(x=x, y=y, z=0) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))],
            materialList=["floor"], surfaces=[first, second], windings=[0, 1, 2, 3, 0, 1, 2],
            texGenEQs=[Record(planeX=Record(x=1, y=0, z=0, d=0), planeY=Record(x=0, y=1, z=0, d=0))],
            planes=[Record(normalIndex=0)], normals=[Record(x=0, y=0, z=1)],
            hasAlarmState=1, normalLMapIndices=[0, 255], alarmLMapIndices=[1, 8],
            lightMaps=[Record(lightmap=normal, lightdirmap=direction), Record(lightmap=alarm)],
            animatedLights=[Record(nameIndex=0, stateIndex=0, stateCount=1, flags=2, duration=750)],
            lightStates=[Record(red=255, green=80, blue=40, activeTime=500, dataIndex=0, dataCount=1)],
            stateDatas=[Record(surfaceIndex=0, mapIndex=0, lightStateIndex=0)],
            nameBuffer=b"alarm\0", stateDataBuffers=bytes((1, 2, 3)),
        )
        (self.folder / "base.dif").write_bytes(b"\x2c\0\0\0fixture")
        self.add_mission_area('new Sky(Sky) { cloudSpeed0="0.01"; };')
        decoder = (lambda _raw: Record(difVersion=44, interiors=[interior]), "b" * 64)
        with patch("mission_interiors.load_decoder", return_value=decoder):
            manifest = convert(self.mission, self.library, dif_decoder=self.folder / "decoder.py")
        header, terrain, rows = load_intake(manifest)
        self.assertEqual(header["format"], 10)
        self.assertIn("weather.json", header["files"])
        index = load_lighting_index(manifest.parent, header["files"])
        record = index["interior-0001.json"]
        self.assertEqual(record["surfaces"], [[0, 0, 2, 0, 1, [0, 2, .25, .125, .25, .5]]])
        self.assertEqual(record["atlases"][1]["color"]["source_sha256"], hashlib.sha256(alarm).hexdigest())
        self.assertEqual(lighting_summary(record)["source_animated_lights"], 1)
        self.assertEqual(lighting_summary(record)["direction_atlases"], 1)
        self.assertEqual(record["animation"], {
            "tracks": [[0, 0, 1, 2, 750]], "states": [[255, 80, 40, 500, 0, 1]],
            "surface_data": [[0, 0, 0]], "names_hex": "616c61726d00", "state_masks_hex": "010203",
        })
        self.assertEqual(record["review"], ["Surface 1 refers to a missing alarm atlas."])
        model, _ = load_intake_geometry(manifest.parent, "interior-0001.json", header["files"]["interior-0001.json"])
        unlit, lit = render_sections(model, record)
        self.assertEqual(unlit[-3:], ([], -1, -1))
        self.assertEqual(lit[-3:], ([(.25, .5), (.5, .5), (.25, .5), (.5, .5), (.5, .5), (.25, .5)], 0, 1))

        archive = self.folder / "lighting.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for item in manifest.parent.iterdir():
                output.write(item, item.name)
        self.assertEqual(load_intake(unpack_intake(archive, self.library)), (header, terrain, rows))
        record["surfaces"][0][2] = 500
        data = json.dumps(index).encode()
        (manifest.parent / LIGHTING_INDEX).write_bytes(data)
        header["files"][LIGHTING_INDEX] = hashlib.sha256(data).hexdigest()
        manifest.write_text(json.dumps(header), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid interior lightmap surface binding"):
            load_intake(manifest)

    def test_duplicate_archive_interior_stays_in_review(self):
        source = self.folder / "Assets.vl2"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("Base.dif", b"one")
            archive.writestr("BASE.DIF", b"two")
        with patch("mission_interiors.load_decoder"), patch("mission_interiors.decode_bytes") as reader:
            manifest = convert(self.mission, self.library, dif_decoder=self.folder / "decoder.py", dif_assets=source)
        reader.assert_not_called()
        header, _, _ = load_intake(manifest)
        self.assertEqual(header["interiors"], {})
        self.assertIn("Multiple DIF files", header["interior_review"]["interiorinstance:base.dif"])

    def test_static_mesh_mapping_uses_all_matching_instances_and_keeps_unmapped_objects(self):
        source = self.mission.read_text().replace('interiorFile="base.dif";',
            'interiorFile="./Interior/Base.dif"; position="10 20 5"; '
            'rotation="0 0 2 90"; scale="1 2 3";')
        source = source.replace('new Item(Rabbit)', '''
            new InteriorInstance(I2){INTERIORFILE="interior/base.DIF";position="-10 12 6";};
            new TSStatic(Rock){shapeName="Interior/Base.dif";position="0 0 0";};
            new InteriorInstance(NotCTF){interiorFile="Interior/Base.dif";missionTypesList="Rabbit";};
            new Item(Rabbit)''')
        self.mission.write_text(source)
        _, terrain, rows = load_intake(convert(self.mission, self.library))
        a, b, rock, excluded, _ = rows[5:]
        self.assertEqual(source_reference(a), source_reference(b))
        self.assertNotEqual(source_reference(a), source_reference(rock))
        self.assertIsNone(source_reference(excluded))
        path = "/Game/Community/Fixtures/SM_TestBase"
        mapped = placements(rows, terrain, {source_reference(a): path})
        self.assertEqual([item[0]["name"] for item in mapped], ["I", "I2"])
        self.assertEqual(mapped[0][1:3], (path, (1050, -2100, 525)))
        self.assertAlmostEqual(mapped[0][3][2], math.sqrt(.5))
        self.assertAlmostEqual(mapped[0][3][3], math.sqrt(.5))
        self.assertEqual(mapped[0][4], (1, 2, 3))
        self.assertEqual(mapped[1][2:], ((-1050, -1260, 630), (0, 0, 0, 1), (1, 1, 1)))

    def test_mesh_placement_reports_invalid_references_and_transforms(self):
        _, terrain, rows = load_intake(convert(self.mission, self.library))
        with self.assertRaisesRegex(ValueError, "unknown source"):
            placements(rows, terrain, {"tsstatic:other.dts": "/Game/Community/SM_Test"})
        with self.assertRaisesRegex(ValueError, "project asset"):
            placements(rows, terrain, {"interiorinstance:base.dif": "/Game/Other/SM_Test"})
        with self.assertRaisesRegex(ValueError, "I has an invalid source transform"):
            placements(rows, terrain, {"interiorinstance:base.dif": "/Game/Community/SM_Test"})
        for raw, reason in (
                ({"rotation": "0 0 0 30"}, "nonzero axis"),
                ({"rotation": "0 0 Inf 30"}, "Invalid rotation"),
                ({"scale": "1 1 0"}, "scale cannot be zero"),
                ({"position": "1 1 2000000"}, "outside")):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, reason):
                source_transform({"position": "0 0 0", **raw}, terrain)
        _, reflected, _ = source_transform({"position": "0 0 0", "rotation": "1 2 3 180"}, terrain)
        _, unreflected, _ = source_transform(
            {"position": "0 0 0", "rotation": "1 2 3 180"}, {**terrain, "y_sign": 1}
        )
        factor = 1 / math.sqrt(14)
        self.assertEqual(reflected[:3], (factor, -2 * factor, 3 * factor))
        self.assertEqual(unreflected[:3], (-factor, -2 * factor, -3 * factor))

    def add_mission_area(self, text):
        text = self.mission.read_text().replace("new TerrainBlock()", text + " new TerrainBlock()")
        self.mission.write_text(text)

    def test_minimap_uses_asymmetric_mission_area_and_exports_host_fragments(self):
        self.add_mission_area('new MissionArea(PlayArea) { Area = "-6 -10 8 12"; };')
        manifest = convert(self.mission, self.library)
        header, terrain, rows = load_intake(manifest)
        self.assertEqual(header["format"], 2)
        self.assertEqual(header["coverage"]["boundary"], 1)
        self.assertEqual(rows[1]["properties"]["Area"], "-6 -10 8 12")
        settings = header["minimap"]
        self.assertEqual(settings["image_bounds"], [[-630, -210], [210, 1050]])
        self.assertEqual(settings["center"], [-210, 420])
        self.assertNotEqual(settings["center"], terrain["origin_cm"][:2])
        self.assertAlmostEqual(settings["extent"][0], 924)
        self.assertAlmostEqual(settings["extent"][1], 1386)
        image = (manifest.parent / "minimap.png").read_bytes()
        self.assertEqual(struct.unpack_from(">II", image, 16), (256, 384))
        bundle = self.folder / "minimap-intake.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            for member in manifest.parent.iterdir():
                archive.write(member, member.name)
        self.assertEqual(load_intake(unpack_intake(bundle, self.library))[0], header)

        rotated = framing(settings["image_bounds"], rotation=90, low=0.5, default=1.25, high=3)
        project, world = view_transform(rotated, 400)
        self.assertEqual(project(*settings["center"]), (200, 200))
        self.assertGreater(project(-110, 420)[1], 200)
        self.assertAlmostEqual(world(*project(210, 1050))[0], 210)
        self.assertAlmostEqual(world(*project(210, 1050))[1], 1050)
        out = export_host_minimap(manifest, rotated, "PlayAreaT22026", self.folder)
        self.assertEqual(json.loads((out / "map-minimap.json").read_text()), {"minimap": rotated})
        expected = {"path": "lua/PlayAreaT22026/minimap.png", "bytes": len(image),
                    "sha256": hashlib.sha256(image).hexdigest()}
        self.assertEqual(json.loads((out / "manifest-minimap.json").read_text())["files"], [expected])
        with self.assertRaises(FileExistsError):
            export_host_minimap(manifest, settings, "PlayAreaT22026", self.folder)
        (manifest.parent / "minimap.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            load_intake(manifest)
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            export_host_minimap(manifest, settings, "OtherMap", self.folder)

    def test_ambiguous_and_invalid_areas_remain_in_review(self):
        self.add_mission_area('new MissionArea(First) { area = "0 0 8 0.1"; };')
        header, _, rows = load_intake(convert(self.mission, self.library))
        self.assertNotIn("minimap", header)
        self.assertIn("invalid", rows[1]["reason"])
        self.add_mission_area('new MissionArea(Second) { area = "0 0 8 8"; };')
        header, _, rows = load_intake(convert(self.mission, self.library))
        self.assertNotIn("minimap", header)
        self.assertTrue(all(row["status"] == "review" and "Multiple" in row["reason"] for row in rows[1:3]))

    def test_older_manifest_still_loads(self):
        manifest = convert(self.mission, self.library)
        header = json.loads(manifest.read_bytes())
        header.update(format=1, reader="t2-mission-v1.0")
        manifest.write_text(json.dumps(header), encoding="utf-8")
        self.assertEqual(load_intake(manifest)[0], header)

    def test_v3_converts_sun_color_and_fog_to_neutral_world_units(self):
        self.add_mission_area('''
            new MissionArea(PlayArea) { area="-6 -10 8 12"; };
            new Sun(Global) { DiRecTion="0 3 -4"; color="1 .75 .5"; ambient=".1 .2 .3 1"; };
            new Sky(GlobalSky) { fogColor=".4 .5 .6 1"; fogDistance="350"; visibleDistance="780"; };
        ''')
        manifest = convert(self.mission, self.library)
        header, _, rows = load_intake(manifest)
        self.assertEqual(header["format"], 3)
        self.assertIn("minimap", header)
        settings = header["environment"]
        self.assertEqual(settings["sun"], {"direction": [0, -.6, -.8],
                                          "color": [1, .75, .5], "ambient": [.1, .2, .3]})
        self.assertEqual(settings["fog"], {"start_cm": 36750, "visible_cm": 81900, "color": [.4, .5, .6]})
        sky = next(row for row in rows if row["name"] == "GlobalSky")
        self.assertEqual(sky["properties"]["visibleDistance"], "780")
        self.assertIn("mapped", sky["reason"])
        bundle = self.folder / "daylight-intake.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            for member in manifest.parent.iterdir():
                archive.write(member, member.name)
        self.assertEqual(load_intake(unpack_intake(bundle, self.library))[0], header)
        header["environment"]["sun"]["direction"] = [0, 0, 0]
        manifest.write_text(json.dumps(header), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid intake sun"):
            load_intake(manifest)

    def test_ambiguous_suns_and_bad_fog_fields_stay_in_review(self):
        self.add_mission_area('''
            new Sun(One) { direction="0 1 -1"; };
            new Sun(Two) { direction="0 1 -1"; };
            new Sky(Sky) { fogDistance="600"; fogColor="3 0 1"; visibleDistance="500"; };
        ''')
        header, _, rows = load_intake(convert(self.mission, self.library))
        self.assertEqual(header["environment"], {"fog": {"start_cm": 63000, "visible_cm": 52500}})
        self.assertTrue(all("Multiple sun" in row["reason"] for row in rows
                            if row["source_class"] == "Sun"))
        self.assertIn("fogcolor", next(row for row in rows if row["source_class"] == "Sky")["reason"])

    def test_draft_places_the_neutral_environment_and_preserves_old_defaults(self):
        fake = Mock()
        fake.Vector.side_effect = lambda *values: values
        fake.Rotator.side_effect = lambda **values: values
        fake.LinearColor.side_effect = lambda *values: values
        path = Path(__file__).with_name("tools") / "community_map_editor" / "import_intake.py"
        spec = importlib.util.spec_from_file_location("intake_editor_fixture", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"unreal": fake}):
            spec.loader.exec_module(module)
        sun, sky, atmosphere, fog = (Mock() for _ in range(4))
        actors = Mock()
        actors.spawn_actor_from_class.side_effect = (sun, sky, atmosphere, fog)
        module.place_environment(actors, {"sun": {"direction": [0, -.6, -.8],
                                                  "color": [1, .75, .5], "ambient": [.1, .2, .3]},
                                           "fog": {"start_cm": 10000, "visible_cm": 50000,
                                                   "color": [.4, .5, .6]}})
        lighting = actors.spawn_actor_from_class.call_args_list[0].args
        self.assertIs(lighting[0], fake.DirectionalLight)
        self.assertAlmostEqual(lighting[2]["pitch"], -53.13010235)
        self.assertAlmostEqual(lighting[2]["yaw"], -90)
        sun.light_component.set_editor_property.assert_called_once_with("atmosphere_sun_light", True)
        sun.set_light_color.assert_called_once_with((1, .75, .5, 1))
        sky.get_component_by_class.return_value.set_light_color.assert_called_once_with((.1, .2, .3, 1))
        component = fog.get_component_by_class.return_value
        component.set_start_distance.assert_called_once_with(10000)
        component.set_fog_density.assert_called_once_with(.02)
        component.set_fog_inscattering_color.assert_called_once_with((.4, .5, .6, 1))
        for settings in ({}, {"fog": {"start_cm": 10000, "visible_cm": 10000}}):
            actors = Mock()
            module.place_environment(actors, settings)
            self.assertEqual(actors.spawn_actor_from_class.call_count, 3)
            self.assertEqual(actors.spawn_actor_from_class.call_args_list[0].args[2],
                             {"pitch": -48, "yaw": 120})

    def test_relief_uses_world_y_and_skips_holes(self):
        terrain = {
            "height_m": [0] * 65536, "origin_cm": [0, 2, 0],
            "holes": [256], "step_cm": 1, "y_sign": -1,
        }
        terrain["height_m"][257] = 1
        pixels = terrain_pixels(terrain, 2, 2, lambda x, y: (x, y))
        self.assertEqual(pixels[0], 24)
        self.assertGreater(pixels[1], pixels[3])

    def test_mission_perimeter_keeps_original_xy_and_closes_at_the_ceiling(self):
        self.add_mission_area('new MissionArea(PlayArea) { Area = "-6 -10 8 12"; };')
        manifest = convert(self.mission, self.library)
        header, terrain, _ = load_intake(manifest)
        lower, upper = mission_extents(header["minimap"]["image_bounds"], terrain)
        self.assertEqual(lower, [-630, -210, -49790])
        self.assertEqual(upper, [210, 1050, 200210])
        actual = wall_boxes(lower, upper)
        self.assertEqual(verified_extents(actual), (lower, upper))
        for side, (axis, rim, direction) in {
            "West": (0, lower[0], 1), "East": (0, upper[0], -1),
            "South": (1, lower[1], 1), "North": (1, upper[1], -1),
            "Ceiling": (2, upper[2], -1),
        }.items():
            center, size = actual[side]
            self.assertEqual(center[axis] + direction * size[axis] / 2, rim)
        self.assertTrue(all(center[2] + size[2] / 2 == upper[2] + 1000
                            for center, size in actual.values()))

        check_marker_clearance(lower, upper, [("Red Flag", (0, 0, 315))])
        with self.assertRaisesRegex(ValueError, "Red Flag"):
            check_marker_clearance(lower, upper, [("Red Flag", (150, 0, 315))])
        actual["West"][0][1] += 75
        with self.assertRaisesRegex(ValueError, "misaligned west"):
            verified_extents(actual)
        del actual["North"]
        with self.assertRaisesRegex(ValueError, "four walls"):
            verified_extents(actual)

    def test_configurable_mission_caps_keep_wall_edges_and_are_verified(self):
        terrain = {"origin_cm": [0, 0, 500], "height_m": [-2, 5], "cm_per_m": 100}
        area = [[-18_000, -25_000], [24_000, 15_000]]
        markers = [("Blue Flag", [0, 0, 650])]
        boxes = plan_walls(area, terrain, markers, {"ceiling_m": 350, "floor_m": 50})
        self.assertEqual(verified_extents(boxes, floor=True), ([-18_000, -25_000, -4700], [24_000, 15_000, 36_000]))
        del boxes["Floor"]
        with self.assertRaisesRegex(ValueError, "configured ceiling and floor"):
            verified_extents(boxes, floor=True)

        open_top = plan_walls(area, terrain, markers, {"ceiling_m": None, "floor_m": 50})
        self.assertNotIn("Ceiling", open_top)
        self.assertEqual(verified_extents(open_top, ceiling=False, floor=True)[1], [24_000, 15_000, 201_000])
        with self.assertRaisesRegex(ValueError, "configured ceiling and floor"):
            verified_extents(open_top, floor=True)

        for invalid in (-1, True, "350", float("inf"), float("nan")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Ceiling distance"):
                plan_walls(area, terrain, markers, {"ceiling_m": invalid, "floor_m": None})
        with self.assertRaisesRegex(ValueError, "Blue Flag"):
            plan_walls(area, terrain, [("Blue Flag", [0, 0, 1100])], {"ceiling_m": 0, "floor_m": None})

    def test_flag_only_limit_is_inset_from_the_same_asymmetric_area(self):
        area = [[-18000, -25000], [24000, 15000]]
        stands = [("Blue Flag", (1000, -10000, 500)), ("Red Flag", (19000, 5000, 700))]
        self.assertEqual(flag_bounds(area, 40, stands), ([-14000, -21000], [20000, 11000]))
        with self.assertRaisesRegex(ValueError, "Red Flag"):
            flag_bounds(area, 50, stands)
        with self.assertRaisesRegex(ValueError, "too little room"):
            flag_bounds(area, 200, stands)
        for invalid in (0, -1, float("nan"), float("inf"), True, "40"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "positive"):
                flag_bounds(area, invalid, stands)

    def test_flag_polygon_respects_the_mission_area_and_the_two_flag_stands(self):
        header = {"minimap": {"image_bounds": [[-2000, -2000], [2000, 2000]]}}
        rows = [
            {"status": "boundary"},
            {"status": "marker", "kind": "flag", "name": "Blue Flag", "position_cm": [-700, 0, 0]},
            {"status": "marker", "kind": "flag", "name": "Red Flag", "position_cm": [700, 0, 0]},
        ]
        points = [[-14, -12], [14, -12], [14, 12], [6, 12], [0, 2], [-6, 12], [-14, 12]]
        options = dict(perimeter=False, flag_inset_m=None, flag_polygon_m=points)
        boxes, saved = plan_boundaries(header, {}, rows, **options)
        self.assertIsNone(boxes)
        self.assertEqual(saved, tuple((x * 100, y * 100) for x, y in points))
        with self.assertRaisesRegex(ValueError, "either"):
            plan_boundaries(header, {}, rows, **dict(options, flag_inset_m=3))
        with self.assertRaisesRegex(ValueError, "edges cannot cross"):
            plan_boundaries(header, {}, rows, **dict(options, flag_polygon_m=[[-14, -12], [14, 12], [14, -12], [-14, 12]]))
        with self.assertRaisesRegex(ValueError, "inside the playable boundary"):
            plan_boundaries(header, {}, rows, **dict(options, flag_polygon_m=[[-20, -10], [14, -12], [14, 12]]))
        rows[-1]["position_cm"] = [0, 700, 0]
        with self.assertRaisesRegex(ValueError, "Red Flag"):
            plan_boundaries(header, {}, rows, **options)

    def test_solid_polygon_caps_cover_the_exact_concave_shape_in_either_order(self):
        outer = ((-1700, -1700), (1700, -1700), (1700, 1700), (600, 1700),
                 (0, 200), (-600, 1700), (-1700, 1700))
        terrain = {"origin_cm": [0, 0, 100], "cm_per_m": 100, "height_m": [0, 5]}
        rect = ((-2000, -2000), (2000, 2000))
        for points in (outer, tuple(reversed(outer))):
            with self.subTest(points=points):
                walls = plan_walls(rect, terrain, [("Spawn", (-800, 400, 300))],
                                   {"ceiling_m": 100, "floor_m": 20}, [[x/100, y/100] for x, y in points])
                self.assertIsInstance(walls, PolygonWalls)
                self.assertEqual((walls.z_min, walls.z_max), (-1900, 10_600))
                faces = triangulate_polygon(walls.vertices)
                self.assertEqual(len(faces), len(points) - 2)
                areas = [turn(points[a], points[b], points[c]) for a, b, c in faces]
                self.assertTrue(all(area > 0 for area in areas))
                self.assertEqual(sum(areas), 2 * 3400**2 - 1200 * 1500)

        # Every corner is legal on its own. The top edge crosses the open notch.
        with self.assertRaisesRegex(ValueError, "whole flag-only limit"):
            check_inside_perimeter(outer, ((-1000, -1000), (1000, -1000), (1000, 800), (-1000, 800)))
        check_inside_perimeter(outer, ((-1000, -1000), (1000, -1000), (1000, 0), (-1000, 0)))

    def test_vehicle_only_walls_allow_ctf_markers_outside_the_wall_polygon(self):
        header = {"minimap": {"image_bounds": [[-2000, -2000], [2000, 2000]]}}
        terrain = {"origin_cm": [0, 0, 0], "cm_per_m": 100, "height_m": [0, 5]}
        rows = [{"status": "boundary"},
                {"status": "marker", "kind": "flag", "name": "Blue Flag", "position_cm": [1500, 0, 200]}]
        options = dict(perimeter=True, flag_inset_m=3, wall_polygon_m=[[-10, -10], [10, -10], [10, 10], [-10, 10]])
        walls, soft_limit = plan_boundaries(header, terrain, rows, **options, wall_block_channels=["vehicle"])
        self.assertIsInstance(walls, PolygonWalls)
        self.assertEqual(soft_limit, ([-1700, -1700], [1700, 1700]))
        with self.assertRaisesRegex(ValueError, "Blue Flag"):
            plan_boundaries(header, terrain, rows, **options, wall_block_channels=["pawn"])
        for value in ([], "pawn", ["pawn", "pawn"], ["projectile"], [{"pawn": True}]):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Choose Pawn"):
                validate_channels(value)
        self.assertEqual(probe_profiles(validate_channels(["vehicle", "pawn"])),
                         (("Pawn", True), ("Vehicle", True), ("BlockAllDynamic", False)))

    def test_boundary_visual_uses_inner_faces_and_preserves_optional_caps(self):
        lower, upper = [-2000, -2500, -750], [4000, 5000, 8000]
        for ceiling, floor in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(ceiling=ceiling, floor=floor):
                surface = wall_surface(wall_boxes(lower, upper, ceiling=ceiling, floor=floor))
                self.assertEqual(surface, PolygonWalls(
                    ((-2000, -2500), (4000, -2500), (4000, 5000), (-2000, 5000)),
                    -750, 8000, ceiling, floor,
                ))
                self.assertIs(wall_surface(surface), surface)
        self.assertEqual(wall_appearance({"style": "warning", "distance_m": 80}),
                         {"style": "warning", "distance_m": 80.0})
        for style, distance in (("invalid", 20), ([], 20), ("energy", 0), ("energy", float("nan")),
                                ("energy", float("inf")), ("warning", True), ("energy", 10_001)):
            with self.subTest(style=style, distance=distance), self.assertRaises(ValueError):
                wall_appearance({"style": style, "distance_m": distance})
        with self.assertRaisesRegex(ValueError, "Enable solid side walls"):
            plan_boundaries({}, {}, [], perimeter=False, flag_inset_m=None,
                            wall_visual={"style": "energy", "distance_m": 20})
