"""Exercise the legacy source profile with an original mission and heightfield."""

import json
from hashlib import sha256
from io import BytesIO
from itertools import groupby
import math
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from conversion_rules import create_snapshot
from intake_contract import load_intake, load_intake_geometry
from interior_geometry import render_sections
from interior_lighting import LIGHTING_INDEX, load_lighting_index
from mesh_mappings import placements, source_reference
from minimap import grayscale_png, terrain_pixels
from model_textures import TEXTURE_INDEX, load_texture_index
from object_catalog import build_rows, classify, load_catalog
from public_layout import export_public_layout
from source_profiles import T1
from t1_lzh import AdaptiveTree, LEAVES, ROOT
from t1_lzh import decode as decode_lzh
from t1_bitmap import artwork_png, palette_banks
from t1_interior import dis_index
from t1_lighting import Lightmaps
from t1_materials import material_names
from t1_reader import convert_legacy, legacy_mission
from t1_terrain import Volume, volume_missions
from t2_reader import unpack_intake
from terrain_art import available_art
from terrain_grid import ARTWORK_UV, TiledTerrain
from terrain_layers import ART_SLOT_OFFSET, PALETTE_SURFACES, layer_choices, painted_cells


def legacy_fixture(root):
    mission = root / "AuthorOnly.mis"
    mission.write_text('''//--- OBJECT WRITE BEGIN ---
instant SimGroup "MissionGroup" {
    instant SimGroup "Empty legacy group";
    instant MissionCenterPos "Play area" { x = "-400"; y = "-400"; w = "800"; h = "800"; };
    instant SimTerrain "Terrain" { hazeDistance = "100"; visibleDistance = "700"; position = "-1024 -1024 0"; };
    instant TeamGroup "team0" {
        instant Item "Flag0" { dataBlock = "Flag"; position = "-70 0 0"; rotation = "0 0 1.5707963267948966"; };
        instant SimGroup "DropPoints" { instant SimGroup "Start" {
            instant Marker "Spawn0" { dataBlock = "DropPointMarker"; position = "-70 20 5"; rotation = "0 0 0"; };
        }; };
        instant InteriorShape "Own bunker" { fileName = "models/AuthorOnly.dis"; position = "-70 30 0"; rotation = "0 0 1.5707963267948966"; };
    };
    instant TeamGroup "team1" {
        instant Item "Flag1" { dataBlock = "Flag"; position = "70 0 0"; };
        instant SimGroup "DropPoints" { instant Marker "Spawn1" { dataBlock = "DropPointMarker"; position = "70 20 5"; }; };
    };
    instant SimGroup "ObserverDropPoints" {
        instant Marker "Observer" { dataBlock = "DropPointMarker"; position = "0 0 50"; };
    };
};
//--- OBJECT WRITE END ---
$Game::missionType = "CTF";
exec("AuthorOnly.cs");
''', encoding="utf-8")
    heightmap = mission.with_suffix(".height.png")
    samples = bytearray(257 * 257 * 2)
    struct.pack_into(">H", samples, len(samples) - 2, 65535)
    heightmap.write_bytes(grayscale_png(samples, 16, 257, 257))
    heightmap.with_suffix(".json").write_text(json.dumps({"origin_m": [-1024, -1024, 0], "step_m": 8,
                                                       "height_range_m": [0, 128]}), encoding="utf-8")
    return mission


def compress_runs(data, *, initial_space_match=False):
    """Encode authored fixture bytes with literals and overlapping one-byte matches."""
    tree = AdaptiveTree()
    bits = []

    def symbol(value):
        node = tree.parent[LEAVES + value]
        path = []
        while node != ROOT:
            path.append(str(node % 2))
            node = tree.parent[node]
        bits.extend(reversed(path))
        tree.update(value)

    if initial_space_match:
        assert data.startswith(b"    ")
        symbol(4 + 253)
        bits.append("0" * 9)
        data = data[4:]

    for value, group in groupby(data):
        remaining = len(list(group)) - 1
        symbol(value)
        while remaining >= 3:
            size = min(remaining, 60)
            symbol(size + 253)
            bits.append("0" * 9)
            remaining -= size
        for _ in range(remaining):
            symbol(value)
    bitstream = ''.join(bits)
    return bytes(int(bitstream[i:i + 8].ljust(8, '0'), 2) for i in range(0, len(bitstream), 8)) + b'\0'


def make_volume(entries):
    data = bytearray(b"PVOL\0\0\0\0")
    names = bytearray()
    index = bytearray()

    def chunk(tag, content):
        data.extend(tag + struct.pack("<I", len(content)) + content + b"\0" * (len(content) % 2))

    for name, content, method in entries:
        index.extend(struct.pack("<IIIIB", 0, len(names), len(data), len(content), method))
        names.extend(name.encode("ascii") + b"\0")
        chunk(b"VBLK", compress_runs(content) if method == 3 else content)
    struct.pack_into("<I", data, 4, len(data))
    chunk(b"vols", names)
    chunk(b"voli", index)
    return bytes(data)


def author_materials(names):
    body = struct.pack("<H16sIII", 16, b"TS::MaterialList", 4, 1, len(names))
    body += b"".join(struct.pack("<16x32s16x", name) for name in names)
    return b"PERS" + struct.pack("<I", len(body)) + body


def binary_fixture(root, *, tiled=False, painted=False, oriented=False):
    mission = legacy_fixture(root)
    mission.with_suffix(".height.png").unlink()
    mission.with_suffix(".height.json").unlink()
    source = mission.read_text(encoding="utf-8").replace('position = "-1024 -1024 0";',
                'position = "-3072 -3072 0"; tedFileName = "AuthorOnly.dtf";')
    if tiled:
        source = (source.replace('w = "800"; h = "800"', 'w = "1800"; h = "1800"')
                        .replace('position = "70 0 0"', 'position = "1250 1200 0"')
                        .replace('position = "70 20 5"', 'position = "1250 1220 5"'))
    mission.write_text(source, encoding="utf-8")
    layout = [0, 0, 0, 0, 0, 1, 0, 1, 0] if tiled else [0] * 9
    block_ids = (0, 1) if tiled else (0,)
    index = (struct.pack("<2I", 1, 10) + b"author.dml" + struct.pack("<3I6f2i2f3I", 1, 9, 3,
             *([0] * 8), 0.0, 128.0, 3, 3, 0) + struct.pack("<9iI", *layout, len(block_ids))
             + b"".join(struct.pack("<2I", ident, 0) for ident in block_ids))
    materials = bytearray(b"\0\0" * (256**2 - 1) + b"\x06\x03")
    if painted:
        for y in range(95, 107):
            for x in range(95, 107):
                materials[2 * (256 * y + x) + 1] = 1
        materials[2 * (256 * 100 + 100)] = 0xcf
        materials[2 * (256 * 100 + 101)] = 0xc7
        materials[2 * (256 * 100 + 102) + 1] = 200
    if oriented:
        for flag in range(8):
            cell = 256 * 101 + 96 + flag
            materials[2 * cell:2 * cell + 2] = bytes((flag, 200))
    entries = [("AuthorOnly.dtf", b"GFIL" + struct.pack("<I", len(index)) + index, 0)]
    for ident in block_ids:
        heights = bytearray(4 * 257**2)
        samples = ((259, 13.25), (257**2 - 1, 128.0)) if ident == 0 else ((0, 40.0), (257 + 2, 92.0))
        for sample_index, metres in samples:
            struct.pack_into("<f", heights, sample_index * 4, metres)
        block = struct.pack("<I16s2I2f2I", 5, b"author block", 9, 0, 0, max(v for _, v in samples), 256, 256)
        for raw in (heights, materials):
            block += struct.pack("<I", len(raw)) + compress_runs(raw)
        entries.append((f"AuthorOnly#{ident}.dtb", b"GBLK" + struct.pack("<I", len(block)) + block, 0))
    if painted:
        names = [b""] * 201
        names[0], names[1], names[200] = b"rock_ridge.bmp", b"grass_meadow.bmp", b"woodland_moss.bmp"
        entries.append(("author.dml", author_materials(names), 0))
    mission.with_suffix(".ted").write_bytes(make_volume(entries))
    return mission, entries


def artwork_fixture(root):
    artwork = root / "Artwork"
    artwork.mkdir()
    size = 8
    pixels = bytes((1 if x < 4 else 2) if y < 4 else (3 if x < 4 else 4)
                   for y in range(size) for x in range(size))
    palette = b"\0\0\0\0" + bytes.fromhex("e02010 00 10d040 00 2060e0 00 f0c020 00") + bytes(251 * 4)
    key = 1277
    table = b"PL98" + struct.pack("<4I", 2, 0, 0, 0) + bytes(32)
    table += bytes(1024) + struct.pack("<2I", key + 1, 0) + palette + struct.pack("<2I", key, 0)
    (artwork / "original.ppl").write_bytes(table)

    def tag(name, body):
        return name + struct.pack("<I", len(body)) + body

    pbmp = (b"PBMP" + struct.pack("<I", len(pixels) + 32) + tag(b"head", struct.pack("<5I", 3, size, size, 8, 8))
            + tag(b"data", pixels) + tag(b"DETL", struct.pack("<I", 1)) + tag(b"PiDX", struct.pack("<I", key)))
    volume = artwork / "AuthoredPaint.vol"
    volume.write_bytes(make_volume([("woodland_moss.bmp", pbmp, 0)])[:-1])
    return artwork, pbmp, table


def interior_fixture(root):
    folder = root / "Interiors"
    folder.mkdir()
    pool = (b"Default\0FloorLow.dig\0FloorHigh.dig\0AuthorFloor.dml\0")
    dis = (struct.pack("<5I", 3, 1, 0, 0, 2) + struct.pack("<I", 2)
           + struct.pack("<8I", 10, pool.index(b"FloorLow"), 0, 0, 200, pool.index(b"FloorHigh"), 0, 0)
           + struct.pack("<3I", 0, 0, len(pool)) + pool + struct.pack("<IB", pool.index(b"AuthorFloor"), 0))
    surfaces = struct.pack("<6BH2I2B2x", 0x80, 0, 7, 3, 3, 5, 0, 0, 0, 4, 0)
    surfaces += struct.pack("<6BH2I2B2x", 0x81, 0, 7, 3, 0, 0, 0, 4, 0, 4, 0)
    points = ((0, 0, 4), (12, 0, 4), (12, 8, 4), (0, 8, 4), (24, 0, 4), (24, 8, 4))
    texels = ((0, 0), (1, 0), (1, 1), (0, 1))
    corners = ((0, 0), (3, 3), (2, 2), (1, 1), (1, 0), (4, 1), (5, 2), (2, 3))
    dig = struct.pack("<H11sxII7f9I", 11, b"ITRGeometry", 7, 123, 32, 0, 0, 4, 24, 8, 4,
                      2, 0, 0, 0, 0, len(corners), len(points), len(texels), 1)
    dig += surfaces + b"".join(struct.pack("<HH", *corner) for corner in corners)
    dig += b"".join(struct.pack("<3f", *point) for point in points)
    dig += b"".join(struct.pack("<2f", *sample) for sample in texels)
    dig += struct.pack("<4f2I", 0, 0, 1, -4, 1, 0)
    entries = [("models/AuthorOnly.dis", b"ITRs" + struct.pack("<I", len(dis)) + dis, 0),
               ("models/FloorHigh.dig", b"PERS" + struct.pack("<I", len(dig)) + dig, 0),
               ("models/AuthorFloor.dml", author_materials([b"authored_deck.bmp"]), 0)]
    (folder / "AuthoredBuildings.vol").write_bytes(make_volume(entries)[:-1])
    return folder, entries


def interior_art_fixture(root, *, masked=True):
    folder, entries = interior_fixture(root)
    _, _, palette = artwork_fixture(root)
    # Non-square and asymmetric so an accidental V flip or uniform repeat is visible.
    pixels = bytes([0, 1, 2, 3] * 4 + [2, 3, 4, 1] * 4)
    if not masked:
        pixels = pixels.replace(b"\0", b"\x04")

    def chunk(tag, body):
        return tag + struct.pack("<I", len(body)) + body

    chunks = chunk(b"head", struct.pack("<5I", 2, 4, 8, 8, int(masked) | 8))
    chunks += chunk(b"data", pixels) + chunk(b"PiDX", struct.pack("<I", 1277))
    bitmap = b"PBMP" + struct.pack("<I", len(chunks)) + chunks
    entries += [("models/authored_deck.bmp", bitmap, 0), ("palettes/interior.ppl", palette, 0)]
    (folder / "AuthoredBuildings.vol").write_bytes(make_volume(entries))
    return folder, entries


def interior_light_fixture(root, *, mission_overlay=False):
    folder, entries = interior_art_fixture(root)
    normal_ref = b"FloorNormal-000-2.dil" if mission_overlay else b"FloorNormal.dil"
    lighting = [(b"Normal", normal_ref), (b"Emergency", b"FloorAlarm.dil"),
                (b"Decorative", b"Decorative.dil")]
    pool = b"Default\0FloorLow.dig\0FloorHigh.dig\0AuthorFloor.dml\0"
    pool += b"\0".join(word for pair in lighting for word in pair) + b"\0"
    dis = struct.pack("<5I", 3, 1, 0, 0, 2) + struct.pack("<I", 2)
    dis += struct.pack("<8I", 10, pool.index(b"FloorLow"), 0, 0, 200, pool.index(b"FloorHigh"), 3, 0)
    dis += struct.pack("<I", 6) + b"".join(struct.pack("<I", pool.index(file)) for _, file in lighting * 2)
    dis += struct.pack("<I", 3) + b"".join(struct.pack("<I", pool.index(label)) for label, _ in lighting)
    dis += struct.pack("<I", len(pool)) + pool + struct.pack("<IB", pool.index(b"AuthorFloor"), 0)
    entries[0] = (entries[0][0], b"ITRs" + struct.pack("<I", len(dis)) + dis, 0)

    def dil(shift, dimensions, offsets, pixels, *, compressed=False):
        states = tracks = surface_lights = b""
        count = int(compressed)
        flags = 0x40000000 if compressed else 0
        area = math.prod(dimensions)
        names = b""
        tree = b""
        if compressed:
            states = struct.pack("<4Hfhh", 0, 0xf000, 0, 0, 0, 1, 0)
            surface_lights = struct.pack("<hhi", 0, 0, len(pixels))
            tracks = struct.pack("<iiiifI", 5, 0, 1, 0, 1.0, 1)
            pixels += b"\xff" + bytes(area - 1)
            names = b"Deck\0"
            tree = struct.pack("<2I2i2I", 1, 2, -2, -1, 0x0246, 0x09b0)
        body = struct.pack("<H11sx9I", 11, b"ITRLighting", 7, 123, shift, count, count, count, count, 2, len(pixels))
        body += states + surface_lights + tracks
        body += struct.pack("<Ihh4B", flags, count, 0, *dimensions, *offsets)
        body += struct.pack("<Ihh4B", 0x80000531, 0, 0, 2, 2, 0, 0)
        body += pixels + struct.pack("<I", len(names)) + names + bytes((count,)) + tree
        return b"PERS" + struct.pack("<I", len(body)) + body

    entries += [
        ("models/FloorNormal-000.dil" if mission_overlay else "models/FloorNormal.dil",
         dil(1, (9, 5), (0, 0), bytes((0xaa,)) * 6, compressed=True), 0),
        ("models/FloorAlarm.dil", dil(2, (6, 4), (2, 1), struct.pack("<H", 0x0b21) * 24), 0),
    ]
    if mission_overlay:
        words = struct.pack("<H", 0x0a42) * 45
        body = struct.pack("<H18s9I", 18, b"ITRMissionLighting", 7, 123, 1, 0, 0, 0, 0, 1, len(words))
        body += struct.pack("<Ihh4B", 0, 0, 0, 9, 5, 0, 0) + words
        body += struct.pack("<IBI", 0, 0, 1) + compress_runs(struct.pack("<II", 0, 0))
        entries.append(("models/FloorNormal-000-2.dil", b"PERS" + struct.pack("<I", len(body)) + body, 0))
    (folder / "AuthoredBuildings.vol").write_bytes(make_volume(entries))
    return folder, entries


class TribesOneIntakeTests(unittest.TestCase):
    def test_tiled_tribes_one_placement_uses_darkstar_euler_order(self):
        row = dict(status="review", source_class="InteriorShape", name="Tilted panel", properties={
            "fileName": "panel.dis", "position": "1 2 3", "rotation": ' '.join([str(math.pi / 2)] * 3)})
        terrain = dict(tile_grid=[[0]], cm_per_m=100, y_sign=-1)
        _, _, position, (qx, qy, qz, qw), _ = placements([row], terrain, {
            "interiorshape:panel.dis": "/Game/Community/TestPanel"})[0]
        self.assertEqual(position, (100, -200, 300))
        self.assertAlmostEqual(qx, 0)
        self.assertAlmostEqual(qw, 0)
        self.assertAlmostEqual(qy, math.sqrt(0.5))
        self.assertAlmostEqual(qz, -math.sqrt(0.5))

    def test_dis_bundle_maps_valid_geometry_and_retains_missing_models_for_review(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, terrain_entries = binary_fixture(root)
            folder, entries = interior_fixture(root)
            self.assertEqual(dis_index(entries[0][1]), (1, "FloorHigh.dig", "AuthorFloor.dml", []))
            mission.write_text(mission.read_text(encoding="utf-8").replace('instant TeamGroup "team1"',
                'instant InteriorShape "Missing" { fileName = "missing.dis"; position = "0 0 0"; };\n'
                '    instant TeamGroup "team1"'), encoding="utf-8")
            manifest = convert_legacy(mission, root / "intakes", interiors=folder)
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["format"], 16)
            reference = "interiorshape:models/authoronly.dis"
            self.assertEqual(header["interiors"], {reference: "interior-0001.json"})
            self.assertIn("is missing", header["interior_review"]["interiorshape:missing.dis"])
            mesh, _ = load_intake_geometry(manifest.parent, "interior-0001.json", header["files"]["interior-0001.json"])
            self.assertEqual(mesh["format"], 3)
            self.assertEqual(mesh["source"]["lod"], 1)
            self.assertEqual(mesh["source"]["geometry_sha256"], sha256(entries[1][1]).hexdigest())
            self.assertEqual(mesh["triangles"], [[0, 0, 2, 3, 3, 5, 19, 13, 3, 13],
                                                   [0, 0, 1, 2, 3, 5, 19, 5, 19, 13]])
            self.assertIn("is missing", load_texture_index(manifest.parent, header["files"])
                          ["interior-0001.json"]["0"]["reason"])
            mapping, = placements(rows, terrain, {reference: "/Game/Community/TestDeck"})
            self.assertEqual(mapping[2], (-7000, -3000, 0))
            self.assertAlmostEqual(mapping[3][2], -math.sqrt(0.5))

            mission_volume = root / "Embedded.vol"
            mission_volume.write_bytes(make_volume([(mission.name, mission.read_bytes(), 0), *terrain_entries, *entries]))
            embedded, _, _ = load_intake(convert_legacy(mission_volume, root / "intakes"))
            self.assertEqual(embedded["files"]["interior-0001.json"], header["files"]["interior-0001.json"])
            with ZipFile(root / "private-interior.zip", "w") as archive:
                for member in [manifest, *(manifest.with_name(name) for name in header["files"])]:
                    archive.write(member, member.name)
            private, _, _ = load_intake(unpack_intake(root / "private-interior.zip", root / "private"))
            self.assertEqual(private["interiors"], header["interiors"])
            shared = export_public_layout(manifest, root / "shared.zip")
            public, _, _ = load_intake(unpack_intake(shared, root / "shared"))
            self.assertNotIn("interiors", public)
            self.assertNotIn("interior-0001.json", public["files"])

            header["format"] = 15
            header["files"].pop(TEXTURE_INDEX)
            manifest.write_text(json.dumps(header), encoding="utf-8")
            self.assertEqual(load_intake(manifest)[0]["format"], 15)
            header["sources"] = [entry for entry in header["sources"] if entry["name"] != "FloorHigh.dig"]
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source receipt"):
                load_intake(manifest)

    def test_interior_can_be_combined_with_an_authored_png_heightfield(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission = legacy_fixture(root)
            folder, entries = interior_fixture(root)
            (folder / "AuthoredBuildings.vol").unlink()
            for name, raw, _ in entries:
                file = folder / name
                file.parent.mkdir(exist_ok=True)
                file.write_bytes(raw)
            manifest = convert_legacy(mission, root / "intakes", interiors=folder)
            header, terrain, _ = load_intake(manifest)
            self.assertEqual(header["format"], 16)
            self.assertEqual(set(header["interiors"]), {"interiorshape:models/authoronly.dis"})
            self.assertEqual(len(terrain["height_m"]), 257**2)

    def test_interior_art_binds_palette_pixels_and_checks_provenance(self):
        from PIL import Image

        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, terrain_files = binary_fixture(root, painted=True)
            folder, entries = interior_art_fixture(root)
            losing_file = folder / "authored_deck.bmp"
            losing_file.write_bytes(b"A same-name loose file must not override its companion.")
            manifest = convert_legacy(mission, root / "intakes", artwork=root / "Artwork", interiors=folder)
            header, _, _ = load_intake(manifest)
            self.assertEqual(set(available_art(header)), {200})
            filename, = header['interiors'].values()
            texture = load_texture_index(manifest.parent, header["files"])[filename]["0"]
            with Image.open(manifest.with_name(texture["file"])) as image:
                self.assertEqual(image.size, (4, 8))
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
                self.assertEqual(image.getpixel((1, 0)), (224, 32, 16, 255))
                self.assertEqual(image.getpixel((0, 7)), (16, 208, 64, 255))
            self.assertEqual(texture["mode"], "masked")
            self.assertEqual(texture["size"], [4, 8])
            self.assertEqual(texture["source_sha256"], sha256(entries[-2][1]).hexdigest())
            self.assertEqual(texture["palette_sha256"], sha256(entries[-1][1]).hexdigest())
            mesh, _ = load_intake_geometry(manifest.parent, filename, header["files"][filename])
            section, = render_sections(mesh, texture_sizes={0: texture["size"]})
            self.assertEqual(section[3][:3], [(0.75, 0.625), (4.75, 1.625), (0.75, 1.625)])

            image_zip = export_public_layout(manifest, root / "public.zip")
            with ZipFile(image_zip) as archive:
                self.assertNotIn(texture["file"], archive.namelist())
                self.assertNotIn(TEXTURE_INDEX, archive.namelist())

            mission_volume = root / "Embedded.vol"
            mission_volume.write_bytes(make_volume([(mission.name, mission.read_bytes(), 0), *terrain_files, *entries]))
            embedded, _, _ = load_intake(convert_legacy(mission_volume, root / "intakes"))
            self.assertEqual(embedded["files"][TEXTURE_INDEX], header["files"][TEXTURE_INDEX])

            rejected = dict(texture, size=[8, 4])
            raw = json.dumps({filename: {"0": rejected}}).encode()
            digest = header["files"][TEXTURE_INDEX]
            index = manifest.with_name(TEXTURE_INDEX)
            original = index.read_bytes()
            index.write_bytes(raw)
            header["files"][TEXTURE_INDEX] = sha256(raw).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "texture dimensions"):
                load_intake(manifest)
            index.write_bytes(original)
            header["files"][TEXTURE_INDEX] = digest
            header["sources"] = [entry for entry in header["sources"] if entry["name"] != "interior.ppl"]
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "palette receipt"):
                load_intake(manifest)

    def test_dil_preserves_distinct_states_and_checksums(self):
        from PIL import Image

        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            folder, entries = interior_light_fixture(root)
            manifest = convert_legacy(mission, root / "intakes", interiors=folder)
            header, _, _ = load_intake(manifest)
            self.assertEqual(header["format"], 17)
            fname, = header["interiors"].values()
            lighting = load_lighting_index(manifest.parent, header["files"])[fname]
            self.assertEqual([state["role"] for state in lighting["legacy_states"]], ["normal", "alarm", "review"])
            self.assertEqual(lighting["animated_lights"], 1)
            self.assertTrue(lighting["has_alarm"])
            self.assertIn("Timed light changes", lighting["review"][0])
            normal, alarm = lighting["atlases"]
            with Image.open(manifest.with_name(normal["color"]["file"])) as image:
                self.assertEqual(image.size, (16, 8))
                self.assertEqual(image.getpixel((1, 1)), (34, 255, 102, 255))
                self.assertEqual(image.getpixel((2, 1)), (153, 187, 0, 255))
                self.assertEqual(image.getpixel((0, 0)), image.getpixel((1, 1)))
            with Image.open(manifest.with_name(alarm["color"]["file"])) as image:
                self.assertEqual(image.size, (8, 8))
                self.assertEqual(image.getpixel((1, 1)), (187, 34, 17, 255))
            mesh, _ = load_intake_geometry(manifest.parent, fname, header["files"][fname])
            section, = render_sections(mesh, lighting, {0: [4, 8]})
            self.assertEqual(section[3][0], (0.75, 0.625))
            self.assertEqual(section[5][0], (1.5 / 16, 1.5 / 8))
            self.assertEqual(section[8][0], (2 / 8, 1.75 / 8))

            with self.assertRaisesRegex(ValueError, "DIG build"):
                Lightmaps(entries[-2][1], 122, 2)
            shared = export_public_layout(manifest, root / "shared.zip")
            with ZipFile(shared) as archive:
                self.assertFalse(any("lightmap" in name or name == LIGHTING_INDEX for name in archive.namelist()))
            header["sources"] = [source for source in header["sources"] if source["name"] != "FloorAlarm.dil"]
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "valid DIL provenance"):
                load_intake(manifest)

    def test_dil_overrides_use_the_mission_map_and_base_light_track(self):
        from PIL import Image

        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            folder, entries = interior_light_fixture(root, mission_overlay=True)
            manifest = convert_legacy(mission, root / "intakes", interiors=folder)
            header, _, _ = load_intake(manifest)
            filename, = header["interiors"].values()
            lighting = load_lighting_index(manifest.parent, header["files"])[filename]
            normal = lighting["legacy_states"][0]
            self.assertEqual(normal["source"], "FloorNormal-000-2.dil")
            self.assertEqual(normal["sha256"], sha256(entries[-1][1]).hexdigest())
            self.assertEqual(normal["base_source"], "FloorNormal-000.dil")
            self.assertEqual(normal["base_sha256"], sha256(entries[-3][1]).hexdigest())
            atlas = lighting["atlases"][0]["color"]["file"]
            with Image.open(manifest.with_name(atlas)) as image:
                self.assertEqual(image.getpixel((1, 1)), (170, 255, 34, 255))
                self.assertEqual(image.getpixel((2, 1)), (170, 68, 34, 255))
            header["sources"] = [source for source in header["sources"] if source["name"] != normal["base_source"]]
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "base DIL receipt"):
                load_intake(manifest)

    def test_legacy_binary_heightfield_imports_through_volume_or_loose_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, entries = binary_fixture(root)
            archive = mission.with_suffix(".ted")
            manifest = convert_legacy(mission, root / "intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["format"], 13)
            ground, = terrain["terrain_blocks"]
            self.assertEqual(terrain["origin_cm"], [-102400, 102400, 0])
            self.assertEqual(terrain["step_cm"], 800)
            self.assertEqual(ground["height_m"][259], 13.25)
            self.assertEqual(ground["height_m"][-1], 128.0)
            self.assertEqual(ground["legacy_grid"]["tile"], [1, 1])
            self.assertEqual(ground["legacy_grid"]["grid_size"], [3, 3])
            self.assertTrue(ground["legacy_grid"]["material_flags"].endswith("0006"))
            self.assertTrue(ground["legacy_grid"]["material_indices"].endswith("0003"))
            self.assertEqual(terrain["material_layers"], [[0, "Material slot 0"], [3, "Material slot 3"]])
            self.assertEqual(header["sources"][1]["sha256"], sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(header["sources"][2]["origin"], "AuthorOnly.ted:AuthorOnly.dtf")
            self.assertEqual(len([r for r in rows if r["status"] == "marker"]), 4)
            for name, data, _ in entries:
                (root / name).write_bytes(data)
            for name, _, _ in entries:
                _, loose, _ = load_intake(convert_legacy(mission, root / "intakes", root / name))
                self.assertEqual(terrain, loose)

            volume = root / "Missions.vol"
            volume.write_bytes(make_volume([("missions/AuthorOnly.mis", mission.read_bytes(), 3),
                                            ("missions/Alternate.mis", mission.read_bytes(), 0), *entries]))
            self.assertEqual(volume_missions(volume), ["missions/Alternate.mis", "missions/AuthorOnly.mis"])
            with self.assertRaisesRegex(ValueError, "Select a mission"):
                convert_legacy(volume, root / "intakes")
            from_archive = convert_legacy(volume, root / "intakes", mission_member="missions/AuthorOnly.mis")
            archive_header, volume_terrain, _ = load_intake(from_archive)
            self.assertEqual(terrain, volume_terrain)
            self.assertEqual(archive_header["sources"][1]["origin"], "Missions.vol:missions/AuthorOnly.mis")

    def test_multitile_source_preserves_blocks_and_uses_shared_seams(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root, tiled=True)
            manifest = convert_legacy(mission, root / "intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["format"], 13)
            self.assertEqual(terrain["tile_grid"], [[0, 1], [1, 0]])
            self.assertEqual([entry["name"] for entry in header["sources"][-2:]],
                             ["AuthorOnly#0.dtb", "AuthorOnly#1.dtb"])
            self.assertEqual(terrain["origin_cm"], [-102400, 102400, 0])
            tiled = TiledTerrain(terrain)
            left, right = tiled.mesh_block(0, 0), tiled.mesh_block(1, 0)
            below = tiled.mesh_block(0, 1)
            self.assertEqual(left["height_m"][256::257], right["height_m"][::257])
            self.assertEqual(left["height_m"][-257:], below["height_m"][:257])
            self.assertEqual((left["height_m"][256], below["height_m"][0]), (40.0, 40.0))
            self.assertEqual(terrain["terrain_blocks"][0]["height_m"][-1], 128.0)
            pixels = terrain_pixels(terrain, 2, 1, lambda x, y: (100000 + x * 3200, 100000))
            self.assertTrue(all(value > 24 for value in pixels))
            self.assertEqual(struct.unpack_from(">II", (manifest.parent / "height.png").read_bytes(), 16), (513, 513))
            self.assertIn([125000, -120000, 0], [r["position_cm"] for r in rows if r.get("kind") == "flag"])

            shared = export_public_layout(manifest, root / "shared.zip")
            public_header, public, _ = load_intake(unpack_intake(shared, root / "unpacked"))
            self.assertEqual(public_header["format"], 13)
            self.assertEqual(public["tile_grid"], [[0, 0], [0, 0]])
            self.assertEqual(len(public["terrain_blocks"]), 1)
            self.assertEqual(set(public["terrain_blocks"][0]["height_m"]), {0})
            self.assertNotIn("legacy_grid", public["terrain_blocks"][0])

            terrain["tile_grid"][0][0] = 3
            bad = json.dumps(terrain).encode()
            (manifest.parent / "terrain.json").write_bytes(bad)
            header["files"]["terrain.json"] = sha256(bad).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tile.*grid or block references"):
                load_intake(manifest)

    def test_source_material_names_and_cutouts_drive_the_palette_and_preview(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, entries = binary_fixture(root, painted=True)
            # Exercise the companion material volume path used by loose missions.
            mission.with_suffix(".ted").write_bytes(make_volume(entries[:-1]))
            (root / "authorTerrain.vol").write_bytes(make_volume(entries[-1:]))
            archive_name, raw, _ = entries[-1]
            self.assertEqual(material_names(raw)[200], "woodland_moss.bmp")
            with self.assertRaisesRegex(ValueError, "material list"):
                material_names(raw[:-4])
            manifest = convert_legacy(mission, root / "intakes")
            header, terrain, _ = load_intake(manifest)
            block, = terrain["terrain_blocks"]
            self.assertEqual((header["sources"][-1]["name"], header["sources"][-1]["origin"]),
                             (archive_name, "authorTerrain.vol:author.dml"))
            self.assertEqual(terrain["material_layers"], [[0, "rock_ridge.bmp"], [1, "grass_meadow.bmp"],
                                                         [3, "Material slot 3"], [200, "woodland_moss.bmp"]])
            holes = [256 * 100 + 100]
            self.assertEqual(block["holes"], holes)
            paint = painted_cells(block, layer_choices(terrain, {"1": "Rock"}))
            self.assertEqual(PALETTE_SURFACES[paint[holes[0] + 1]], "Rock")
            self.assertEqual(PALETTE_SURFACES[paint[holes[0] + 2]], "Forest")
            pixels = terrain_pixels(terrain, 2, 1, lambda x, y: (-22400 + 800 * x, 22000))
            self.assertEqual(pixels[0], 24)
            self.assertGreater(pixels[1], 24)
            block["holes"] = []
            bad = json.dumps(terrain).encode()
            (manifest.parent / "terrain.json").write_bytes(bad)
            header["files"]["terrain.json"] = sha256(bad).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "holes do not match"):
                load_intake(manifest)

    def test_keyed_artwork_is_bound_and_public_layout_strips_it(self):
        from PIL import Image

        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root, painted=True, oriented=True)
            artwork, raw, table = artwork_fixture(root)
            banks = palette_banks(table)
            self.assertEqual(set(banks), {1277, 1278})
            with self.assertRaisesRegex(ValueError, "Truncated"):
                artwork_png(raw[:-2], lambda key: (banks[key], None))
            manifest = convert_legacy(mission, root / "intakes", artwork=artwork)
            header, terrain, _ = load_intake(manifest)
            self.assertEqual(header["format"], 14)
            self.assertEqual(set(available_art(header)), {200})
            record = header["terrain_art"]["200"]
            self.assertEqual(record["palette_sha256"], sha256(table).hexdigest())
            self.assertEqual(record["source_sha256"], sha256(raw).hexdigest())
            self.assertIn("reason", header["terrain_art"]["0"])
            file = manifest.with_name(record["file"])
            with Image.open(BytesIO(file.read_bytes())) as image:
                self.assertEqual(image.size, (8, 8))
                self.assertEqual([image.getpixel(point) for point in ((0, 0), (7, 0), (7, 7), (0, 7))],
                                 [(224, 32, 16, 255), (16, 208, 64, 255), (240, 192, 32, 255), (32, 96, 224, 255)])
            block, = terrain["terrain_blocks"]
            paint = painted_cells(block, layer_choices(terrain, artwork=available_art(header)))
            self.assertEqual(paint[101 * 256 + 96:101 * 256 + 104], bytes((ART_SLOT_OFFSET + 200,)) * 8)
            override = layer_choices(terrain, {"200": "Forest"}, artwork=available_art(header))
            self.assertEqual(PALETTE_SURFACES[painted_cells(block, override)[101 * 256 + 96]], "Forest")

            with ZipFile(root / "private.zip", "w") as archive:
                for member in [manifest, *(manifest.with_name(name) for name in header["files"])]:
                    archive.write(member, member.name)
            private, _, _ = load_intake(unpack_intake(root / "private.zip", root / "private"))
            self.assertEqual(private["terrain_art"], header["terrain_art"])
            shared = export_public_layout(manifest, root / "shared.zip")
            public, _, _ = load_intake(unpack_intake(shared, root / "shared"))
            self.assertEqual(public["format"], 13)
            self.assertNotIn("terrain_art", public)
            self.assertNotIn(file.name, public["files"])

    def test_all_legacy_artwork_orientations_permute_the_corners(self):
        corners = ((0, 0), (1, 0), (1, 1), (0, 1))
        expected = ("0123", "3012", "1032", "2103", "3210", "0321", "2301", "1230")
        for flags, sequence in enumerate(expected):
            self.assertEqual(ARTWORK_UV[flags], tuple(corners[int(index)] for index in sequence))

    def test_binary_source_rejects_a_playable_area_outside_the_grid(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            mission.write_text(mission.read_text(encoding="utf-8").replace('w = "800"', 'w = "3800"'), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extends outside the Tribes terrain grid"):
                convert_legacy(mission, root / "intakes")
            self.assertFalse((root / "intakes").exists())

    def test_volume_rejects_duplicate_traversal_and_truncated_entries(self):
        seeded = b"    native dictionary"
        self.assertEqual(decode_lzh(compress_runs(seeded, initial_space_match=True), len(seeded))[0], seeded)
        for entries, error in (
            ([("Same.DTF", b"first", 0), ("same.dtf", b"second", 0)], "Duplicate"),
            ([("../outside.dtf", b"no", 0)], "Invalid Tribes terrain resource"),
        ):
            with self.assertRaisesRegex(ValueError, error):
                Volume(make_volume(entries))
        data = make_volume([("terrain.dtf", b"a", 0)])
        self.assertEqual(Volume(data[:-1]).read("terrain.dtf"), b"a")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            Volume(data[:-2])
        bad_size = bytearray(data)
        struct.pack_into("<I", bad_size, 12, 60)
        with self.assertRaisesRegex(ValueError, "Truncated|overlaps"):
            Volume(bytes(bad_size)).read("terrain.dtf")

    def test_spawn_budget_keeps_explicit_starts_before_other_drop_points(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission = legacy_fixture(root)
            original = mission.read_text(encoding="utf-8")
            extras = ''.join(f'instant Marker "Random {i}" {{ dataBlock = "DropPointMarker"; position = "0 0 5"; }};'
                             for i in range(25))
            mission.write_text(original.replace('instant SimGroup "Start"', extras + 'instant SimGroup "Start"'),
                               encoding="utf-8")
            _, _, rows = load_intake(convert_legacy(mission, root / "intakes"))
            blue = [r for r in rows if r["status"] == "marker" and r["kind"] == "spawn" and r["team"] == 1]
            self.assertEqual(len(blue), 24)
            self.assertIn("Spawn0", [r["name"] for r in blue])
            self.assertEqual(len([r for r in rows if r["reason"].startswith("Beyond the 24")]), 2)

    def test_legacy_profile_preserves_source_and_builds_neutral_layout(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission = legacy_fixture(root)
            self.assertTrue(legacy_mission(mission))
            manifest = convert_legacy(mission, root / "intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["profile"], T1)
            self.assertEqual(len(header["sources"]), 3)
            self.assertEqual((terrain["height_m"][0], terrain["height_m"][-1]), (0, 128))
            self.assertEqual(header["minimap"]["image_bounds"], [[-40000, -40000], [40000, 40000]])
            self.assertEqual(header["environment"]["fog"], {"start_cm": 10000, "visible_cm": 70000})
            catalog = load_catalog(source_profile=T1)
            buildable = build_rows(rows, classify(rows, terrain, catalog))
            markers = [r for r in buildable if r["status"] == "marker"]
            self.assertEqual([(r["team"], r["kind"]) for r in markers], [(1, "flag"), (1, "spawn"), (2, "flag"), (2, "spawn")])
            self.assertEqual(markers[0]["yaw"], -90)
            self.assertEqual(markers[1]["position_cm"], [-7000, -2000, 500])
            self.assertTrue(all(r["status"] == "excluded" for r in rows if r["source_class"] == "MissionStatement"))
            building = next(r for r in buildable if r["source_class"] == "InteriorShape")
            [(row, _, where, rotation, _)] = placements(buildable, terrain,
                {source_reference(building): "/Game/Community/Fixture/Bunker"})
            self.assertEqual(row["properties"]["fileName"], "models/AuthorOnly.dis")
            self.assertEqual(where, (-7000, -3000, 0))
            self.assertAlmostEqual(rotation[2], -math.sqrt(0.5))
            snapshot = create_snapshot(root, header, rows, manifest, {})
            self.assertEqual(snapshot["profile"]["id"], T1)
            self.assertEqual(snapshot["pipeline"]["profile"], T1)
            self.assertEqual(snapshot["catalog"]["profile"], T1)
            self.assertEqual(create_snapshot(root, header, rows, manifest, {}, replay=snapshot), snapshot)

            shared = export_public_layout(manifest, root / "shared.zip")
            with ZipFile(shared) as archive:
                self.assertNotIn(b"AuthorOnly", archive.read("objects.json"))
            public_data = unpack_intake(shared, root / "shared")
            public_header, public_terrain, public_rows = load_intake(public_data)
            self.assertEqual(public_header["profile"], T1)
            self.assertEqual(set(public_terrain["height_m"]), {0})
            self.assertEqual([r["position_cm"] for r in public_rows if r["status"] == "marker"],
                             [r["position_cm"] for r in markers])


if __name__ == "__main__":
    unittest.main()
