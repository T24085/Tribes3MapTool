"""Qualify a locally authored Darkstar shape and its mission intake."""

from hashlib import sha256
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from test_editor_t1_intake import author_materials, binary_fixture, make_volume

from dts_preview import ClipSampler, Track, bake_times
from intake_contract import load_intake, load_intake_geometry
from interior_geometry import material_slots
from model_textures import load_texture_index
from public_layout import export_public_layout
from t1_dts import decode_dts
from t1_reader import convert_legacy
from t2_reader import unpack_intake


def pers(kind, body, *, align_four=False):
    payload = struct.pack("<H", len(kind)) + kind + b"\0" * (len(kind) % 2) + body
    flags = 0x80000000 if align_four else 0
    return struct.pack("<4sI", b"PERS", len(payload) | flags) + payload + b"\0" * (-len(payload) % (4 if flags else 2))


def authored_shape(version=8, mesh_version=3, *, collision=False, motion=False, animated_materials=False):
    names = [b"root", b"detail20", b"detail200", b"deck", b"always", b"collision" if collision else b"bounds",
             b"high", b"low", b"extra", b"hidden", b"testCycle"]
    node_format = "<5h" if version == 8 else "<5i"
    parents = [-1, 0, 0, 2, 0, 0]
    nodes = b"".join(struct.pack(node_format, index, parent, int(motion and index == 3), 0, index)
                     for index, parent in enumerate(parents))
    positions = [(1, 0, 0), (0, 0, 30), (2, 0, 0), (0, 1, 2), (10, 0, 1),
                 (2, -4, 2.5) if collision else (0, 0, 60)]
    poses = bytearray()
    for index, point in enumerate(positions):
        axis = (0, 0, 23170, 23170) if index == 3 else (0, 0, 0, 32767)
        if version < 7:
            poses += struct.pack("<7f", *(value / 32767 for value in axis), *point)
        else:
            poses += struct.pack("<4h3f", *axis, *point)
        if version < 8:
            poses += struct.pack("<3f", 1, 1, 1)

    def attach(name_index, flags, node, offset):
        count, first = (1, 1) if motion and name_index == 9 else (0, 0)
        if version == 8:
            return struct.pack("<HHih2x3f2h", name_index, flags, 0, node, *offset, count, first)
        return struct.pack("<HHiiI12f2i", name_index, flags, 0, node, 0,
                           1, 0, 0, 0, 1, 0, 0, 0, 1, *offset, count, first)

    attached = b"".join((attach(6, 0, 3, (1, 0, 1)), attach(7, 0, 1, (0, 0, 0)),
                         attach(8, 0, 4, (0, 0, 0)), attach(9, 1, 3, (0, 0, 40)),
                         attach(5, 0, 5, (0, 0, 0))))
    details = b"".join(struct.pack("<if", root, size) for root, size in ((1, 20), (2, 200), (5, -1)))
    sequence = struct.pack("<iifi4i", 10, 1, 1.5, 0, 0, int(motion), int(motion), 2 if motion else 0)
    subsequences = keys = transition = trigger = b""
    if motion:
        sub = "<3h" if version == 8 else "<3i"
        subsequences = b"".join(struct.pack(sub, 0, count, first)
                                for count, first in ((2, 0), (2, 2), (1 + animated_materials, 4)))
        key, bits = ("<f2H", 12) if version == 8 else ("<f2I", 28)
        keys = b"".join(struct.pack(key, phase, index, flags << bits | uv)
                        for phase, index, flags, uv in ((0, 3, 0, 0), (1, 2, 0, 0),
                             (0, 0, 5, 0), (.5, 1, 15 if mesh_version >= 2 else 13, int(mesh_version >= 2)),
                             (0, 0, 0, 0)))
        if animated_materials:
            keys += struct.pack(key, .5, 1, 0)
        transform_size = {8: 20, 7: 32}.get(version, 40)
        transition = struct.pack("<2i3f", 0, 0, .75, .25, .5) + poses[:transform_size]
        trigger = struct.pack("<fi", -.25, 31)
    body = struct.pack("<12I", version, 6, 1, 3 if motion else 0, 5 + animated_materials if motion else 0,
                       6, len(names), 5, 3, 1, int(motion), int(motion))
    body += bytes(40 if version == 8 else 16)
    body += nodes + sequence + subsequences + keys + poses + b"".join(struct.pack("<24s", name) for name in names)
    body += attached + details + transition + trigger + struct.pack("<I", 1)
    if version >= 6:
        body += struct.pack("<i", 4)
    encoded = [(0, 0, 0, 0), (4, 3, 0, 0), (0, 0, 0, 0),
               (4, 0, 0, 0), (4, 3, 0, 0), (0, 3, 0, 0)]
    mesh = struct.pack("<6I", mesh_version, 12, 6, 8 if motion and mesh_version >= 2 else 4, 2, 2)
    if mesh_version >= 2:
        mesh += struct.pack("<I", 4)
    if mesh_version < 3:
        mesh += struct.pack("<6f", 1, 1, 1, 0, 0, 0)
    mesh += struct.pack("<f", 5)
    mesh += b"".join(struct.pack("<4B", *point) for point in encoded)
    offset_z = 9 if mesh_version < 3 else 0
    mesh += b"".join(struct.pack("<4B", x, y, z + offset_z, n) for x, y, z, n in encoded)
    mesh += struct.pack("<8f", 0, 0, 1, 0, 1, 1, 0, 1)
    if motion and mesh_version >= 2:
        mesh += struct.pack("<8f", 0, 0, .5, 0, .5, .5, 0, .5)
    mesh += struct.pack("<14I", 2, 0, 4, 2, 3, 1, 0, 2, 0, 5, 3, 4, 2, 0)
    if mesh_version == 3:
        mesh += struct.pack("<I6f", 0, 1, 1, 1, 0, 0, 0) + struct.pack("<I6f", 6, 1, 1, 1, 0, 0, 15)
    else:
        mesh += struct.pack("<2I", 0, 6)
    body += pers(b"TS::CelAnimMesh", mesh, align_four=True)
    materials = [b"deck-paint.png", b"deck-alarm.png"] if animated_materials else [b"deck-paint.png"]
    body += struct.pack("<I", 1) + author_materials(materials)
    return pers(b"TS::Shape", body)


def shape_fixture(root, *, collision=False, motion=False, animated_materials=False):
    from PIL import Image
    from io import BytesIO

    folder = root / "Props"
    folder.mkdir()
    artwork = BytesIO()
    Image.new("RGB", (8, 4), (72, 175, 228)).save(artwork, format="PNG")
    entries = [("models/DeckProp.dts", authored_shape(collision=collision, motion=motion,
                                                     animated_materials=animated_materials), 0),
               ("models/deck-paint.png", artwork.getvalue(), 0),
               ("props.cs", b'StaticShapeData AuthorDeck { shapeFile = "models/DeckProp"; };', 0)]
    if animated_materials:
        alarm = BytesIO()
        Image.new("RGB", (4, 4), (235, 34, 7)).save(alarm, format="PNG")
        entries.append(("models/deck-alarm.png", alarm.getvalue(), 0))
    (folder / "AuthoredProps.vol").write_bytes(make_volume(entries))
    return folder, entries


def add_props(mission):
    source = mission.read_text(encoding="utf-8")
    props = '''
    instant StaticShape "Authored prop" {
        dataBlock = "AuthorDeck"; position = "60 -25 0"; rotation = "0 0 1.5707963267948966";
    };
    instant StaticShape "Missing prop" {
        shapeFile = "missing.dts"; position = "100 45 0";
    };
    '''
    mission.write_text(source.replace('    instant TeamGroup "team0"', props + '    instant TeamGroup "team0"'), encoding="utf-8")


class DarkstarShapes(unittest.TestCase):
    def test_highest_detail_default_pose_and_always_node(self):
        for version in (5, 6, 7, 8):
            with self.subTest(version=version):
                mesh_version = min(version - 4, 3)
                model = decode_dts("deck.dts", authored_shape(version, mesh_version))
                self.assertEqual(model["format"], 4)
                self.assertEqual(model["source"]["lod"], 1)
                self.assertEqual(model["source"]["sequences"], 1)
                self.assertEqual(model["source"]["max_visible_frames"], 2)
                self.assertEqual(len(model["triangles"]), 4 if version >= 6 else 2)
                self.assertEqual(model["triangles"][0], [0, 2, 3, 4, 0, 0, 1, 0, 1, 1])
                self.assertEqual([round(value, 4) for value in model["vertices_m"][3]], [3, -4, 3])
                if version >= 6:
                    self.assertEqual(model["vertices_m"][9], [15, 0, 1])
                with self.assertRaisesRegex(ValueError, "Truncated"):
                    decode_dts("deck.dts", authored_shape(version)[:-24])

    def test_private_shape_intake_resolves_datablock_and_strips_assets_on_share(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            add_props(mission)
            folder, _ = shape_fixture(root)
            manifest = convert_legacy(mission, root/"intakes", interiors=folder)
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 18)
            self.assertEqual(header["shapes"], {"staticshape:models/deckprop.dts": "shape-0001.json"})
            self.assertIn("is missing", header["shape_review"]["staticshape:missing.dts"])
            self.assertIn("props.cs", [item["name"] for item in header["sources"]])
            name, = header["shapes"].values()
            mesh, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
            self.assertEqual(mesh["materials"], ["deck-paint.png"])
            texture = load_texture_index(manifest.parent, header["files"])[name]["0"]
            self.assertEqual(texture["size"], [8, 4])
            self.assertEqual(texture["mode"], "opaque")
            with ZipFile(root/"private.zip", "w") as archive:
                for member in ["manifest.json", *header["files"]]:
                    archive.write(manifest.with_name(member), member)
            self.assertEqual(load_intake(unpack_intake(root/"private.zip", root/"private"))[0], header)
            shared = export_public_layout(manifest, root/"shared.zip")
            public, _, public_rows = load_intake(unpack_intake(shared, root/"public"))
            self.assertNotIn("shapes", public)
            self.assertNotIn(texture["file"], public["files"])
            self.assertNotIn("AuthorDeck", json.dumps(public_rows))
            self.assertIn("model-", json.dumps(public_rows))
            self.assertEqual(mesh["source"]["sha256"], sha256(authored_shape()).hexdigest())
            header["sources"] = [row for row in header["sources"] if row["name"] != "DeckProp.dts"]
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DTS source receipt"):
                load_intake(manifest)

    def test_collision_uses_named_detail_without_changing_visible_faces(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            add_props(mission)
            folder, _ = shape_fixture(root, collision=True)
            manifest = convert_legacy(mission, root/"intakes", interiors=folder)
            header, _, _ = load_intake(manifest)
            name, = header["shapes"].values()
            model, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
            baseline = decode_dts("deck.dts", authored_shape())
            self.assertEqual(header["format"], 19)
            self.assertEqual(model["format"], 5)
            self.assertEqual(model["triangles"], baseline["triangles"])
            self.assertEqual(model["vertices_m"], baseline["vertices_m"])
            self.assertEqual(model["collision"]["detail"], 2)
            self.assertEqual(model["collision"]["triangles"], [[2, 3, 4], [2, 4, 5]])
            self.assertEqual(model["collision"]["vertices_m"][2], [3, -4, 2.5])
            shared = export_public_layout(manifest, root/"shared.zip")
            public, _, _ = load_intake(unpack_intake(shared, root/"public"))
            self.assertNotIn(name, public["files"])
            header["format"] = 18
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source profile"):
                load_intake(manifest)

    def test_darkstar_motion_keeps_clips_mesh_frames_and_original_default_pose(self):
        for version in range(5, 9):
            with self.subTest(version=version):
                mesh_version = min(version - 4, 3)
                data = decode_dts("deck.dts", authored_shape(version, mesh_version, motion=True, collision=True))
                static = decode_dts("deck.dts", authored_shape(version, mesh_version, collision=True))
                self.assertEqual(data["format"], 6)
                self.assertEqual(data["vertices_m"], static["vertices_m"])
                self.assertEqual(data["collision"], static["collision"])
                animation = data["animation"]
                clip, = animation["clips"]
                mesh, = animation["meshes"]
                self.assertEqual((clip["name"], clip["duration_s"], clip["loop"]), ("testCycle", 1.5, True))
                node, pose_keys = clip["nodes"][0]
                self.assertEqual(node, 3)
                self.assertEqual(pose_keys[-1], [1, [0, 0, 0, 1, 2, 0, 0], None])
                attachment, cel_keys = clip["objects"][0]
                self.assertEqual(cel_keys[1], [.5, 1, True, 1 if mesh_version >= 2 else None])
                self.assertFalse(animation["objects"][attachment]["visible"])
                self.assertEqual(mesh["frames"][1][2][2], 15 if mesh_version == 3 else 9)
                self.assertEqual(clip["triggers"], [[.25, 31, False]])
                self.assertEqual(clip["material_swaps"], [[[0, 0, 0]]])
                self.assertEqual(animation["transitions"][0][:5], [0, 0, .75, .25, .5])
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            add_props(mission)
            folder, _ = shape_fixture(root, collision=True, motion=True)
            manifest = convert_legacy(mission, root/"intakes", interiors=folder)
            header, _, _ = load_intake(manifest)
            self.assertEqual(header["format"], 20)
            output = export_public_layout(manifest, root/"shared.zip")
            shared, _, _ = load_intake(unpack_intake(output, root/"public"))
            self.assertNotIn("shape-0001.json", shared["files"])

    def test_animated_material_is_imported_without_reordering_the_resting_mesh(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            mission, _ = binary_fixture(root)
            add_props(mission)
            folder, _ = shape_fixture(root, collision=True, motion=True, animated_materials=True)
            manifest = convert_legacy(mission, root/"intakes", interiors=folder)
            header, _, _ = load_intake(manifest)
            name, = header["shapes"].values()
            geometry, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
            self.assertEqual((header["format"], geometry["format"]), (21, 7))
            self.assertEqual(header["reader"], "t1-mission-v8")
            self.assertEqual({face[0] for face in geometry["triangles"]}, {0})
            self.assertEqual(material_slots(geometry), [0, 1])
            textures = load_texture_index(manifest.parent, header["files"])[name]
            self.assertEqual((textures["0"]["size"], textures["1"]["size"]), ([8, 4], [4, 4]))
            sampler = ClipSampler(geometry, 0)
            self.assertEqual({face[0] for face in sampler.sample(.49)["triangles"]}, {0})
            self.assertEqual({face[0] for face in sampler.sample(.5)["triangles"]}, {1})
            header["format"] = 20
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source profile"):
                load_intake(manifest)

    def test_preview_samples_pose_visibility_and_uv_at_authored_keys(self):
        model = decode_dts("deck.dts", authored_shape(motion=True, collision=True))
        sampler = ClipSampler(model, 0)
        first = sampler.sample(0)
        self.assertEqual(first["triangles"], model["triangles"])
        for rendered, static in zip(first["vertices_m"], model["vertices_m"]):
            self.assertEqual(list(rendered), static)
        before = sampler.sample(.49)
        key = sampler.sample(.5)
        self.assertEqual(len(before["triangles"]), 4)
        self.assertEqual(len(key["triangles"]), 6)
        self.assertNotEqual(before["vertices_m"][3], first["vertices_m"][3])
        self.assertEqual(key["vertices_m"][-1][2], 56)
        self.assertEqual(key["triangles"][-1][4:], [0, 0, .5, .5, 0, .5])
        track = Track([[.25, 1], [.75, 2]])
        self.assertEqual(track.step(0, True), [.75, 2])
        self.assertEqual(track.step(0, False), [.25, 1])

        times = bake_times(model["animation"]["clips"][0])
        self.assertEqual(times[0], (0, 0))
        self.assertEqual(times[-1], (1.5, 1))
        self.assertIn((.75, .5), times)
        self.assertEqual(len(times), 47)
