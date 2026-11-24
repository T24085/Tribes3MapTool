"""Exercise native actor discovery, payload transforms and the shared draft import."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile
import zlib

from test_unreal_package_reader import build_package, compact
from test_unreal_legacy_intake import unreal_draft_fixture

from intake_contract import UNREAL_SCENE, load_intake, load_intake_geometry
from interior_geometry import render_sections
from minimap import grayscale_png
from model_textures import TEXTURE_INDEX, load_texture_index
from native_terrain_paint import NativeTerrainPaint, decode_mask
from public_layout import export_public_layout
from t2_reader import unpack_intake
from terrain_grid import TiledTerrain
from terrain_art import SOURCE_ART, available_art
from terrain_layers import FALLBACK, PALETTE_SURFACES, layer_choices
from unreal_binary_scene import read_binary_scene
from unreal_legacy_intake import convert_unreal
from unreal_package_reader import inspect_package
from vengeance_normal_maps import BC5, CXV8U8, DXT5N, normal_png


KINDS = ("Package", "Level", "MPFlag", "PlayerStart", "StaticMeshActor", "StaticMeshComponent",
         "StaticMesh", "ExtraTrigger")
STRINGS = ("None", "Core", "Class", "Fixture", *KINDS, "PersistentLevel", "BlueSpawn", "RedSpawn", "BlueFlag",
           "RedFlag", "Deck", "DeckMesh", "OriginalMesh", "Unmapped", "Location", "Rotation", "DrawScale3D",
           "StructProperty", "ObjectProperty", "FloatProperty", "BoolProperty", "IntProperty", "Vector", "Rotator",
           "Damage", "bAbsoluteRotation", "Base", "TrCTFBase_DiamondSword", "TrCTFBase_BloodEagle", "Terrain",
           "Terrain_1", "NumVerticesX", "NumVerticesY", "NumPatchesX", "NumPatchesY", "TerrainInfo", "TerrainSector",
           "Texture", "HeightTexture", "TerrainMap", "QuadVisibilityBitmap", "EdgeTurnBitmap", "Format", "USize", "VSize",
           "Model", "WorldModel", "FixtureStone", "FixtureGlass", "MaterialInstanceConstant",
           "FixtureTerrain", "HeightLayers", "WrongLayer", "Layers", "TerrainLayer", "AlphaMap",
           "PaintBase", "PaintUpper", "GrassSurface", "RockSurface", "UScale", "FixtureSurface", "ColorLayers",
           "TerrainMatrix", "Matrix", "Plane", "XPlane", "YPlane", "ZPlane", "WPlane", "X", "Y", "Z", "W",
           "Shader", "Diffuse", "OutputBlending", "TwoSided", "BspColor", "GlassColor", "Material", "Materials",
           "EnableCollision", "bBlockHavok", "bCollideActors", "bHidden", "MeshColor", "MeshStone", "MeshGlass",
           "Opacity", "GlowMapOverride", "GlowMaskOverride", "GlowBrightness", "TexPanner", "OpacityPanner",
           "PanRate", "PanDirection", "ShaderMask", "NormalMap", "SpecularityMask", "SurfaceNormal", "ReflectanceMask",
           "Engine", "Mover", "Brush", "BrushModel", "PrePivot", "KeyPos", "KeyRot", "NumKeys", "KeyNum", "MoveTime",
           "StayOpenTime", "InitialState", "TriggerOpenTimed", "ConstantLoop", "bOscillatingLoop", "MoverEncroachType",
           "Movement", "ElevatorVolume", "Active", "Gravity", "ZoneVelocity", "TerminalVelocity", "Priority",
           "bMoveProjectiles", "RenderMaterial", "AssociatedActor", "BumpOpenTimed", "BumpType", "DelayTime",
           "Event", "SecondElevator", "Trigger", "LiftSwitch", "CollisionRadius", "CollisionHeight",
           "TriggerType", "bTriggerOnceOnly", "ReTriggerDelay", "RepeatTriggerTime", "LiftFastSwitch")


def ascend_mesh_lod(materials):
    positions = [(0, 0, 40), (0, 40, 40), (100, 0, 40),
                 (0, 65, 70), (0, 105, 70), (100, 65, 70)]
    data = bytes(56) + struct.pack("<4i4H2i", 6, 0, 8, 1, 0, 1, 2, 0, 18, 1)
    data += bytes(16) + struct.pack("<i", len(materials))
    for slot, material in enumerate(materials):
        first = slot * 3
        data += struct.pack("<10iB", material, 1 - slot, 1 - slot, 1, first, 1, first, first + 2, slot, 0, 0)
    data += struct.pack("<4i", 12, len(positions), 12, len(positions))
    data += b"".join(struct.pack("<3f", *point) for point in positions)
    data += struct.pack("<6i", 1, 16, len(positions), 1, 16, len(positions))
    data += b"".join(struct.pack("<8x2f", *uv) for uv in ((0, 0), (0, 1), (1, 0)) * 2)
    return data + struct.pack("<5i6H2i", 0, 0, len(positions), 2, 6, *range(6), 2, 0)


def vengeance_mesh_buffers():
    positions = [(0, 0, 40), (0, 40, 40), (100, 0, 40), (0, 65, 70), (0, 105, 70), (100, 65, 70)]
    data = bytes(41) + struct.pack("<3i", 3, 1, 3) + compact(2)
    data += struct.pack("<I5HI5H", 0, 0, 0, 2, 1, 1, 0, 3, 3, 5, 1, 1)
    data += bytes(25) + struct.pack("<3i", 3, 0, 1) + compact(len(positions))
    data += b"".join(struct.pack("<6f", *point, 0, 0, 1) for point in positions)
    data += bytes(4) + compact(0)  # Vertex stream revision and empty tangent stream.
    data += (compact(0) + bytes(4)) * 2
    data += compact(1) + compact(len(positions))
    data += b"".join(struct.pack("<2f", *uv) for uv in ((0, 0), (0, 1), (1, 0)) * 2) + bytes(8)
    data += compact(6) + struct.pack("<6H", *range(6)) + bytes(4) + compact(0) + bytes(4)
    data += compact(0) + compact(1) + struct.pack("<6f4H", 0, 0, 40, 100, 40, 40, 1, 0, 0, 0)
    return data + compact(1) + struct.pack("<4H", 0, 1, 2, 0)


def binary_draft_fixture(root, game, *, corrupt=False, attached=False, native_terrain=False, bad_terrain_flag=False,
                         bsp_world=False, static_mesh=None, external_height=False, painted=False, source_artwork=False,
                         bsp_artwork=False, mesh_artwork=False, shader_inputs=False, surface_detail=False, brush_mover=False,
                         ascend_shader=None):
    exported, settings = unreal_draft_fixture(root, game)
    modern = game == "ascend"
    source = exported.with_suffix(".fmap" if modern else ".tvm")
    legacy_ground = native_terrain and not modern
    sector_names = [f"Sector{index}" for index in range(32 * 16)] if legacy_ground else []
    switch_once = brush_mover == "door-switch-once"
    chain_doors = brush_mover in ("door-chain", "door-chain-cycle")
    switch_door = switch_once or chain_doors or brush_mover in ("door-switch", "door-switch-incomplete")
    door_names = ("Tag", "TwinDoors", "SecondDoor", "DoorSwitch") if switch_door else ()
    if switch_once:
        door_names += ("DoorAuxSwitch",)
    if chain_doors:
        door_names += ("bInitiallyActive", "OtherTriggerToggles", "OtherTriggerTurnsOn", "OtherTriggerTurnsOff",
                       "RelayToggle", "RelayOn", "RelayOff", "DoorRelayOn", "DoorRelayOff", "DoorEnable", "DoorDisable",
                       "DoorToggleSpare")
    strings = (*STRINGS, *sector_names, *door_names)
    if modern and mesh_artwork:
        artwork_names = ("Texture2D", "MeshColor", "AscendColor", "ByteProperty", "EPixelFormat", "PF_A8R8G8B8",
                         "TextureParameterValues", "ParameterName", "ParameterValue", "NameProperty", "ArrayProperty",
                         "Base_Color", "Normal")
        strings = tuple(dict.fromkeys((*strings, *artwork_names)))
        if ascend_shader:
            strings += ("MaterialExpressionTextureSampleParameter2D", "ShaderParent", "ColorParent", "Parent",
                        "ColorNode", "RoofPaint", "DiffuseColor", "ColorMaterialInput", "Expression",
                        "Mask", "MaskR", "MaskG", "MaskB", "MaskA", "UseConstant")

    def pack(form, *values):
        return struct.pack("<" + form, *values)

    def name(value):
        index = strings.index(value)
        return pack("ii", index, 0) if modern else compact(index)

    def prop(key, kind, data, structure=None, slot=0):
        if modern:
            extension = name(structure or "None") if kind == "ByteProperty" else name(structure) if structure else b""
            if kind == "BoolProperty":
                return name(key) + name(kind) + pack("iiB", 0, 0, data[0])
            return name(key) + name(kind) + pack("ii", len(data), slot) + extension + data
        if kind == "BoolProperty":
            return name(key) + bytes([0x83 if data[0] else 0x03])
        old_type = {"StructProperty": 10, "ObjectProperty": 5, "IntProperty": 2, "ByteProperty": 1,
                    "ArrayProperty": 9, "FloatProperty": 4, "NameProperty": 6}[kind]
        size_code = 0x50 if len(data) <= 255 else 0x70
        return (name(key) + bytes([size_code | old_type | (0x80 if slot else 0)])
                + (name(structure) if structure else b"") + pack("B" if size_code == 0x50 else "i", len(data))
                + (bytes([slot]) if slot else b"") + data)

    def vector(key, *values, structure="Vector"):
        return prop(key, "StructProperty", pack("3f" if structure == "Vector" else "3i", *values), structure)

    def reference(key, index):
        data = pack("i", index) if modern else compact(index)
        return prop(key, "ObjectProperty", data)

    def object_bytes(*fields, component=False, stateful=0, truncated=False):
        if modern:
            state = pack("iiIHii", -4, -4, 0, 0, 0, -1) if stateful else b""
            prefix = state + (pack("i", -5) if component else b"") + pack("i", -1)
        else:
            header = pack("3i", 3, 0, 1)
            state = (header * stateful + bytes(24) + b"".join(map(compact, (0, -4, -4, 1)))
                     + b"\0" * 5 + b"\x81" + b"\0" * 4) if stateful else b""
            prefix = header + state
        return prefix + b"".join(fields) + (b"" if truncated else name("None"))

    def patch_texture_mip(path, index, start, length):
        payload = inspect_package(path)["exports"][index - 1]["serial_offset"]
        patched = bytearray(path.read_bytes())
        struct.pack_into("<i", patched, payload + start + 13, payload + start + 18)
        struct.pack_into("<i", patched, payload + start + 18, payload + length - 10)
        path.write_bytes(patched)

    def texture_bytes(pixel_format, width, height, pixels):
        props = object_bytes(prop("Format", "ByteProperty", bytes([pixel_format])),
                             prop("USize", "IntProperty", pack("i", width)),
                             prop("VSize", "IntProperty", pack("i", height)))
        tail = compact(1) + pack("3i", 3, 0, 1) + pack("i", 0) + compact(0) + pack("i", 0)
        tail += compact(len(pixels)) + pixels + pack("iiBB", width, height, width.bit_length() - 1, height.bit_length() - 1)
        return props + tail, len(props)

    def terrain_matrix():
        rows = ((.0078125, .001953125, 0, 0), (0, .015625, 0, 0), (0, .00390625, 1, 0), (.25, -.125, 0, 1))
        planes = []
        for axis, coordinates in zip("XYZW", rows):
            fields = b"".join(prop(key, "FloatProperty", pack("f", value)) for key, value in zip("XYZW", coordinates))
            planes.append(prop(axis + "Plane", "StructProperty", fields + name("None"), "Plane"))
        return prop("TerrainMatrix", "StructProperty", b"".join(planes) + name("None"), "Matrix")

    imports = [("Core", "Package", 0, "Fixture")]
    imports.extend(("Core", "Class", -1, kind) for kind in KINDS[1:])
    blue_class = red_class = -3
    if modern:
        imports.append(("Core", "Class", -1, "TrCTFBase_DiamondSword"))
        blue_class = -len(imports)
        imports.append(("Core", "Class", -1, "TrCTFBase_BloodEagle"))
        red_class = -len(imports)
    imports.append(("Fixture", "StaticMesh", -1, "OriginalMesh"))
    mesh_index = -len(imports)
    blue = [vector("Location", -100, 0, 10)]
    if attached:
        blue.append(reference("Base", 6))
    decks = [vector("Location", 30, 80, 200), vector("Rotation", 0, 16384, 0, structure="Rotator"),
             vector("DrawScale3D", 2, 1, 2), prop("Damage", "IntProperty", pack("i", 125))]
    outer = int(modern)
    paint_payloads = []
    if modern and static_mesh == "embedded":
        mesh_index = 10 if native_terrain else 9
    roster = (3, 2, 4, 5, 0, 6, 8, 9) if native_terrain else (3, 2, 4, 5, 0, 6, 8)
    level = object_bytes()
    if modern:
        level += pack("2i", 1, len(roster)) + pack(f"{len(roster)}i", *roster)
    else:
        level += pack("2i", len(roster), len(roster)) + b"".join(map(compact, roster))
    exports = [
        (-2, 0, "PersistentLevel", 0, level, 3),
        (-4, outer, "BlueSpawn", 0, object_bytes(vector("Location", -100, 200, 150),
                                                vector("Rotation", 0, 16384, 0, structure="Rotator"), stateful=1),
         0x02000003),
        (-4, outer, "RedSpawn", 0, object_bytes(vector("Location", 300, 200, 150), stateful=2), 0x02000003),
        (blue_class, outer, "BlueFlag", 0, object_bytes(*blue, truncated=corrupt), 3),
        (red_class, outer, "RedFlag", 0, object_bytes(vector("Location", 300, 0, 10)), 3),
        (-5, outer, "Deck", 0, object_bytes(*decks), 3),
        (-6, 6, "DeckMesh", 0, object_bytes(prop("bAbsoluteRotation", "BoolProperty", b"\0"),
                                           reference("StaticMesh", mesh_index), component=True), 3),
        (-8, outer, "Unmapped", 0, object_bytes(prop("Damage", "IntProperty", pack("i", 125))), 3),
    ]
    if native_terrain and modern:
        width, height = 259, 257
        count = width * height
        heights = pack(f"{count}H", *(32500 + 3 * col + 2 * row for row in range(height) for col in range(width)))
        holes = bytearray(count)
        holes[width * 10 + 255] = 2 if bad_terrain_flag else 1
        holes[width * 3 + 257] = 1
        dims = [("NumVerticesX", width), ("NumVerticesY", height),
                ("NumPatchesX", width - 1), ("NumPatchesY", height - 1)]
        props = [prop(key, "IntProperty", pack("i", number)) for key, number in dims]
        data = object_bytes(vector("Location", -1000, -1000, -300), vector("DrawScale3D", 8, 8, 64), *props)
        data += pack("i", count) + heights + pack("i", count) + holes
        imports.append(("Core", "Class", -1, "Terrain"))
        exports.append((-len(imports), 1, "Terrain_1", 0, data, 3))
    if legacy_ground:
        width, height = 512, 256
        count = width * height
        samples = [31967 + 3 * col + 2 * row for row in range(height) for col in range(width)]
        samples[33 * width + 257] += 80
        heights = pack(f"{count}H", *samples)
        visibility = bytearray(b"\xff" * (count // 8))
        for col, row in ((255, 10), (510, 3)):
            index = row * width + col
            visibility[index // 8] &= ~(1 << (index % 8))
        turns = bytearray(count // 8)
        turns[(32 * width + 256) // 8] = 1
        for kind in ("TerrainInfo", "Texture", "TerrainSector"):
            imports.append(("Core", "Class", -1, kind))
        terrain_class, texture_class, sector_class = (-len(imports) + 2, -len(imports) + 1, -len(imports))
        height_reference = 10
        if external_height:
            imports.append(("Core", "Package", 0, "FixtureTerrain"))
            imports.append(("Core", "Package", -len(imports), "HeightLayers"))
            imports.append(("Fixture", "Texture", -len(imports), "HeightTexture"))
            height_reference = -len(imports)
        bitmap = compact(count // 32)
        source_fields = [reference("TerrainMap", height_reference),
                         prop("QuadVisibilityBitmap", "ArrayProperty", bitmap + visibility),
                         prop("EdgeTurnBitmap", "ArrayProperty", bitmap + turns),
                         vector("Rotation", 0, -4096, 0, structure="Rotator")]
        if painted:
            if source_artwork:
                imports.append(("Core", "Package", 0, "FixtureSurface"))
                imports.append(("Core", "Package", -len(imports), "ColorLayers"))
                imports.append(("Fixture", "Texture", -len(imports), "RockSurface"))
                upper_surface = -len(imports)
                surface_pkg = source.with_name("FixtureSurface.pkg")
                classes = [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "Package"),
                           ("Core", "Class", -1, "Texture")]
                surface_data, surface_start = texture_bytes(3, 4, 4, pack("HHI", 31, 0, 0))
                nodes = [(-2, 0, "ColorLayers", 0, b"", 3), (-3, 1, "RockSurface", 0, surface_data, 3)]
                surface_pkg.write_bytes(build_package(game, strings, classes, nodes, version=130, licensee=27))
                patch_texture_mip(surface_pkg, 2, surface_start, len(surface_data))
                settings.setdefault("asset_packages", []).append(surface_pkg.name)
            fixtures = ((0, "PaintBase", "GrassSurface", bytes([255]) * 8),
                        (4, "PaintUpper", "RockSurface", bytes([0, 64, 192, 255, 0, 128, 128, 255])))
            for slot, mask_name, surface, alpha in fixtures:
                index = 11 + len(sector_names) + len(paint_payloads)
                if source_artwork:
                    material = upper_surface if slot else index + 1
                    matrix = terrain_matrix()
                else:
                    imports.append(("Fixture", "Texture", -1, surface))
                    material, matrix = -len(imports), b""
                layer = (reference("Texture", material) + reference("AlphaMap", index)
                         + prop("UScale", "FloatProperty", pack("f", 3 if slot else 2)) + matrix + name("None"))
                source_fields.append(prop("Layers", "StructProperty", layer, "TerrainLayer", slot=slot))
                rgba = b"".join(bytes((255, 255, 255, value)) for value in alpha)
                payload, start = texture_bytes(5, 4, 2, rgba)
                paint_payloads.append((index, (texture_class, 0, mask_name, 0, payload, 3), start))
                if source_artwork and not slot:
                    color, start = texture_bytes(5, 4, 4, bytes((48, 120, 46, 255)) * 16)
                    paint_payloads.append((material, (texture_class, 0, surface, 0, color, 3), start))
            unsupported = reference("Texture", material) + name("None")
            source_fields.append(prop("Layers", "StructProperty", unsupported, "TerrainLayer", slot=6))
        data = object_bytes(*source_fields) + pack("3i", 3, 4, 3) + compact(len(sector_names))
        data += b"".join(compact(index) for index in range(11, 11 + len(sector_names)))
        data += pack("2i12f", 32, 16, 125, 125, 32767, 8, 0, 0, 0, 8, 0, 0, 0, 0.5)
        texture, native_start = texture_bytes(10, width, height, heights)
        exports.extend([(terrain_class, 0, "Terrain_1", 0, data, 3),
                        (texture_class, 0, "HeightTexture", 0, b"" if external_height else texture, 3)])
        exports.extend((sector_class, 0, name, 0, b"", 3) for name in sector_names)
        exports.extend(row for _, row, _ in paint_payloads)
        if external_height:
            package = source.with_name("FixtureTerrain.pkg")
            classes = [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "Package"),
                       ("Core", "Class", -1, "Texture")]
            nodes = [(-2, 0, "WrongLayer", 0, b"", 3), (-3, 1, "HeightTexture", 0, b"", 3),
                     (-2, 0, "HeightLayers", 0, b"", 3), (-3, 3, "HeightTexture", 0, texture, 3)]
            package.write_bytes(build_package(game, strings, classes, nodes, version=130, licensee=27))
            patch_texture_mip(package, 4, native_start, len(texture))
            settings.setdefault("asset_packages", []).append(package.name)
    if native_terrain:
        for key in ("heightmap", "height_range_m", "origin_m", "step_m", "raster_y"):
            del settings[key]
        settings["terrain_actor"] = "Terrain_1" if legacy_ground else "PersistentLevel.Terrain_1"
        source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
        (root / "Ground.png").unlink()
    if bsp_world:
        level_model = len(exports) + 1
        level += bytes(5) + pack("6i", 7777, 1, 3, 8, 6, 63446) + compact(level_model)
        exports[0] = (*exports[0][:4], level, 3)
        imports.extend(("Fixture", "Texture", -1, name) for name in ("FixtureStone", "FixtureGlass"))
        imports.append(("Core", "Class", -1, "Model"))
        model_class = -len(imports)
        material_indices = (model_class + 2, model_class + 1)
        if bsp_artwork:
            imports.append(("Core", "Class", -1, "Texture"))
            texture_class = -len(imports)
            imports.append(("Core", "Class", -1, "Shader"))
            shader_class = -len(imports)
            imports.append(("Core", "Package", 0, "BspColor"))
            imports.append(("Fixture", "Texture", -len(imports), "GlassColor"))
            glass = -len(imports)
            material_indices = (level_model + 1, level_model + 2)
            source_png = b"".join(bytes((255, 0, 0, 255 if x < 32 else 0)) for _ in range(64) for x in range(64))
            payload, start = texture_bytes(5, 64, 64, source_png)
            package = source.with_name("BspColor.pkg")
            classes = [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "Texture")]
            package.write_bytes(build_package(game, strings, classes,
                [(-2, 0, "GlassColor", 0, payload, 3)], version=130, licensee=27))
            patch_texture_mip(package, 1, start, len(payload))
            settings.setdefault("asset_packages", []).append(package.name)
            source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
        model = object_bytes() + bytes(41) + pack("3i", 3, 0, 5)
        model += compact(3) + pack("9f", 0, 0, 1, 0.5, 0, 0, 0, 0.25, 0)
        points, nodes, surfaces, pool = [], [], [], []
        for face, (x, z, flags) in enumerate(((-100, 350, 0), (250, 385, 0x8), (500, 405, 0x1), (800, 430, 0x4000009))):
            first = len(points)
            points.extend(((x, 460, z), (x + 128, 460, z), (x + 128, 524, z), (x, 524, z)))
            nodes.append(pack("3i4fQB", 3, 2, 2, 0, 0, 1, z, 0, 0) + compact(first) + compact(face)
                         + compact(-1) * 5 + bytes(34) + bytes([4]) + bytes(21))
            material = material_indices[int(face == 1)]
            surfaces.append(pack("3i", 3, 1, 1) + compact(material) + pack("I", flags)
                            + b"".join(map(compact, (first, 0, 1, 2, 0, 0))) + pack("5f", 0, 0, 1, z, 32))
            pool.extend(compact(index) + compact(-1) for index in range(first, first + 4))
        model += compact(len(points)) + b"".join(pack("3f", *point) for point in points)
        for records in (nodes, surfaces, pool):
            model += compact(len(records)) + b"".join(records)
        exports.append((model_class, 0, "WorldModel", 0, model, 3))
        if bsp_artwork:
            stone, start = texture_bytes(5, 64, 32, bytes((40, 225, 60, 255)) * (64 * 32))
            exports.append((texture_class, 0, "FixtureStone", 0, stone, 3))
            paint_payloads.append((material_indices[0], exports[-1], start))
            shader = object_bytes(reference("Diffuse", glass), prop("OutputBlending", "ByteProperty", b"\x01"),
                                  prop("TwoSided", "BoolProperty", b"\x01"))
            exports.append((shader_class, 0, "FixtureGlass", 0, shader, 3))
        if brush_mover:
            lift = brush_mover in ("lift", "lift-linked", "lift-switch", "lift-repeat")
            elevator = lift or brush_mover == "elevator"
            bump_door = brush_mover in ("door", "door-once")
            state = "ConstantLoop" if brush_mover == "loop" else "BumpOpenTimed" if bump_door else "TriggerOpenTimed"
            package, type_name = ("Movement", "ElevatorVolume") if elevator else ("Engine", "Mover")
            imports.extend((("Core", "Package", 0, package), ("Core", "Class", -len(imports) - 1, type_name)))
            brush_class = -len(imports)
            brush_model = model
            if lift:
                points = [(-50, -50, -65), (50, -50, -65), (50, 50, -65), (-50, 50, -65),
                          (-50, -50, 65), (50, -50, 65), (50, 50, 65), (-50, 50, 65)]
                faces = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                         (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
                normals = ((0, 0, -1), (0, 0, 1), (0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0))
                nodes, surfaces, pool = [], [], []
                for face, indices in enumerate(faces):
                    normal = normals[face]
                    plane = sum(n * axis for n, axis in zip(normal, points[indices[0]]))
                    nodes.append(pack("3i4fQB", 3, 2, 2, *normal, plane, 0, 0) + compact(len(pool))
                                 + compact(face) + compact(-1) * 5 + bytes(34) + bytes([4]) + bytes(21))
                    surfaces.append(pack("3i", 3, 1, 1) + compact(material_indices[0]) + pack("I", 0)
                                    + b"".join(map(compact, (indices[0], face, 6, 7, 0, 0)))
                                    + pack("5f", *normal, plane, 32))
                    pool.extend(compact(index) + compact(-1) for index in indices)
                base_vectors = (*normals, (0.5, 0, 0), (0, 0.25, 0.25))
                brush_model = (object_bytes() + bytes(41) + pack("3i", 3, 0, 5)
                               + compact(len(base_vectors)) + b"".join(pack("3f", *vector3) for vector3 in base_vectors)
                               + compact(len(points)) + b"".join(pack("3f", *point) for point in points)
                               + b"".join(compact(len(records)) + b"".join(records) for records in (nodes, surfaces, pool)))
            exports.append((model_class, 0, "BrushModel", 0, brush_model, 3))
            fields = [reference("Brush", len(exports)), vector("Location", 40, -160, 200),
                      vector("Rotation", 0, 16384, 0, structure="Rotator"), vector("PrePivot", 20, 10, 0),
                      prop("NumKeys", "ByteProperty", b"\x03"),
                      prop("KeyNum", "ByteProperty", bytes([0 if switch_door or bump_door or brush_mover == "loop" else 1])),
                      prop("MoveTime", "FloatProperty", pack("f", 1.5)), prop("StayOpenTime", "FloatProperty", pack("f", 2)),
                      prop("InitialState", "NameProperty", name(state)),
                      prop("KeyPos", "StructProperty", pack("3f", 0, 0, 25), "Vector", slot=1),
                      prop("KeyPos", "StructProperty", pack("3f", 100, 50, 25), "Vector", slot=2),
                      prop("KeyRot", "StructProperty", pack("3i", 0, 16384, 0), "Rotator", slot=2)]
            if brush_mover == "loop":
                fields.extend((prop("bOscillatingLoop", "BoolProperty", b"\x01"),
                               prop("MoverEncroachType", "ByteProperty", b"\x03")))
            if bump_door:
                fields.extend((prop("BumpType", "ByteProperty", b"\x00"),
                               prop("MoverEncroachType", "ByteProperty", b"\x03"),
                               prop("DelayTime", "FloatProperty", pack("f", .25))))
                if brush_mover == "door-once":
                    fields.append(prop("bTriggerOnceOnly", "BoolProperty", b"\x01"))
            if switch_door:
                fields.extend((prop("Tag", "NameProperty", name("TwinDoors")),
                               prop("MoverEncroachType", "ByteProperty", b"\x03"),
                               prop("DelayTime", "FloatProperty", pack("f", .25))))
                side = [field for i, field in enumerate(fields) if i != 1]
                if switch_once:
                    side.append(prop("bTriggerOnceOnly", "BoolProperty", b"\x01"))
                exports.append((brush_class, 0, "SecondDoor", 0, object_bytes(vector("Location", 120, -160, 200), *side), 3))
                expanded = (*roster, len(exports))
                imports.extend((("Core", "Package", 0, "Engine"), ("Core", "Class", -len(imports) - 1, "Trigger")))
                receiver_settings = (prop("CollisionRadius", "FloatProperty", pack("f", 36)),
                                     prop("CollisionHeight", "FloatProperty", pack("f", 70)),
                                     prop("TriggerType", "ByteProperty", b"\x01"))
                trigger_event = prop("Event", "NameProperty", name("TwinDoors"))
                switch_fields = (prop("RepeatTriggerTime", "FloatProperty", pack("f", .625)),)
                if chain_doors:
                    switch_fields = (prop("Tag", "NameProperty", name("RelayToggle")),
                                     prop("InitialState", "NameProperty", name("OtherTriggerToggles")),
                                     prop("bInitiallyActive", "BoolProperty", b"\x00"),
                                     prop("bTriggerOnceOnly", "BoolProperty", b"\x01"))
                switch = object_bytes(vector("Location", 280, -200, 200), *receiver_settings,
                                      trigger_event, *switch_fields)
                trigger_class = -len(imports)
                exports.append((trigger_class, 0, "DoorSwitch", 0, switch, 3))
                expanded = (*expanded, len(exports))
                if switch_once:
                    auxiliary = object_bytes(vector("Location", 500, -200, 200), *receiver_settings, trigger_event)
                    exports.append((trigger_class, 0, "DoorAuxSwitch", 0, auxiliary, 3))
                    expanded = (*expanded, len(exports))
                if chain_doors:
                    relay_tag = "TwinDoors" if brush_mover == "door-chain-cycle" else "RelayOn"
                    for actor_name, x, tag_name, event_name, initial_state, active in (
                        ("DoorRelayOn", 370, relay_tag, "RelayToggle", "OtherTriggerTurnsOn", False),
                        ("DoorEnable", 500, None, relay_tag, None, True),
                        ("DoorRelayOff", 625, "RelayOff", "TwinDoors", "OtherTriggerTurnsOff", True),
                        ("DoorDisable", 750, None, "RelayOff", None, True),
                        ("DoorToggleSpare", 900, "RelayToggle", "TwinDoors", "OtherTriggerToggles", False),
                    ):
                        options = () if tag_name is None else (prop("Tag", "NameProperty", name(tag_name)),
                            prop("InitialState", "NameProperty", name(initial_state)),
                            prop("bInitiallyActive", "BoolProperty", bytes([active])))
                        body = object_bytes(vector("Location", x, -200, 200), *receiver_settings, *options,
                                            prop("Event", "NameProperty", name(event_name)))
                        exports.append((trigger_class, 0, actor_name, 0, body, 3))
                        expanded = (*expanded, len(exports))
                level = (object_bytes() + pack("2i", len(expanded), len(expanded)) + b"".join(map(compact, expanded))
                         + bytes(5) + pack("6i", 7777, 1, 3, 8, 6, 63446) + compact(level_model))
                exports[0] = (*exports[0][:4], level, 3)
                if brush_mover == "door-switch-incomplete":
                    decks.append(prop("Tag", "NameProperty", name("TwinDoors")))
                    exports[5] = (*exports[5][:4], object_bytes(*decks), 3)
            if elevator:
                fields = [*fields[:4], prop("Active", "BoolProperty", b"\x00"),
                          vector("Gravity", 0, 0, 100), vector("ZoneVelocity", 50, 0, 250),
                          prop("TerminalVelocity", "FloatProperty", pack("f", 600)),
                          prop("Priority", "IntProperty", pack("i", 42)),
                          prop("bMoveProjectiles", "BoolProperty", b"\x01"), reference("AssociatedActor", 4)]
            if lift:
                fields = [fields[0], vector("Location", 0, -200, 200)]
                exports.append((brush_class, 0, "SecondElevator", 0, object_bytes(fields[0],
                                vector("Location", 75, -200, 200)), 3))
                expanded = (*roster, len(exports))
                if brush_mover in ("lift-switch", "lift-repeat"):
                    imports.extend((("Core", "Package", 0, "Engine"), ("Core", "Class", -len(imports) - 1, "Trigger")))
                    shared = (prop("Event", "NameProperty", name("ElevatorVolume")),
                              prop("CollisionRadius", "FloatProperty", pack("f", 36)),
                              prop("CollisionHeight", "FloatProperty", pack("f", 70)),
                              prop("TriggerType", "ByteProperty", b"\x01"))
                    repeat = brush_mover == "lift-repeat"
                    behavior = prop("RepeatTriggerTime", "FloatProperty", pack("f", .375)) if repeat else prop(
                        "bTriggerOnceOnly", "BoolProperty", b"\x01")
                    switch = object_bytes(vector("Location", 280, -200, 200), *shared, behavior,
                                          prop("ReTriggerDelay", "FloatProperty", pack("f", .625 if repeat else .75)))
                    trigger_class = -len(imports)
                    exports.append((trigger_class, 0, "LiftSwitch", 0, switch, 3))
                    expanded = (*expanded, len(exports))
                    if repeat:
                        fast = object_bytes(vector("Location", 500, -200, 200), *shared,
                                            prop("RepeatTriggerTime", "FloatProperty", pack("f", .5)))
                        exports.append((trigger_class, 0, "LiftFastSwitch", 0, fast, 3))
                        expanded = (*expanded, len(exports))
                    fields.append(prop("Active", "BoolProperty", b"\x00"))
                level = (object_bytes() + pack("2i", len(expanded), len(expanded)) + b"".join(map(compact, expanded))
                         + bytes(5) + pack("6i", 7777, 1, 3, 8, 6, 63446) + compact(level_model))
                exports[0] = (*exports[0][:4], level, 3)
                if brush_mover == "lift-linked":
                    decks.append(prop("Event", "NameProperty", name("ElevatorVolume")))
                    exports[5] = (*exports[5][:4], object_bytes(*decks), 3)
            exports[7] = (brush_class, 0, "Unmapped", 0, object_bytes(*fields), 3)
    if static_mesh and modern:
        materials = [("Fixture", "MaterialInstanceConstant", -1, name) for name in ("FixtureStone", "FixtureGlass")]
        if mesh_artwork:
            # The map already points at Fixture. Its two slots are separate material exports.
            assert static_mesh == "companion"
            color = source.with_name("MeshColor.upk")
            color_types = [("Core", "Package", 0, "MeshColor"), ("Core", "Class", -1, "Texture2D")]
            texture = object_bytes(prop("Format", "ByteProperty", name("PF_A8R8G8B8"), "EPixelFormat"))
            mip_header = len(texture) + 16 + 4 + 24
            # A large cache-backed mip followed by a small, valid inline mip.
            texture += bytes(16) + pack("i", 2) + pack("6i", 1, 256, 256, 4096, 8, 8)
            texture += pack("4i", 0, 16, 16, 0) + bytes((70, 110, 205, 255)) * 4 + pack("2i", 2, 2)
            images = [(-2, 0, "AscendColor", 0, texture, 3)]
            if ascend_shader:
                default = texture.replace(bytes((70, 110, 205, 255)) * 4, bytes((9, 140, 36, 255)) * 4)
                images.append((-2, 0, "FixtureStone", 0, default, 3))
            color.write_bytes(build_package(game, strings, color_types, images))
            data = bytearray(color.read_bytes())
            for image in inspect_package(color, "ascend")["exports"]:
                offset = image["serial_offset"] + mip_header
                struct.pack_into("<i", data, offset + 12, offset + 16)
            color.write_bytes(data)

            types = [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "StaticMesh"),
                     ("Core", "Class", -1, "MaterialInstanceConstant"), ("Core", "Package", 0, "MeshColor"),
                     ("MeshColor", "Texture2D", -4, "AscendColor")]

            def parameters(key):
                entry = (prop("ParameterName", "NameProperty", name(key)) + reference("ParameterValue", -5)
                         + name("None"))
                return prop("TextureParameterValues", "ArrayProperty", pack("i", 1) + entry)

            if ascend_shader:
                types.extend((("Core", "Class", -1, "Material"),
                              ("Core", "Class", -1, "MaterialExpressionTextureSampleParameter2D")))
                if ascend_shader == "cycle":
                    upstream = object_bytes(reference("Parent", 1))
                else:
                    upstream = object_bytes(reference("Parent", 4), parameters("RoofPaint"))
                # First slot inherits the named override through an intermediate instance.
                # The second slot uses the same graph directly, so it uses the sample's default.
                child = object_bytes(reference("Parent", 3), parameters("Normal"))
                diffuse = (reference("Expression", 5) + b"".join(
                    prop(channel, "IntProperty", pack("i", 1)) for channel in ("Mask", "MaskR", "MaskG", "MaskB")))
                if ascend_shader == "masked-output":
                    diffuse += prop("MaskA", "IntProperty", pack("i", 1))
                graph = object_bytes(prop("DiffuseColor", "StructProperty", diffuse + name("None"), "ColorMaterialInput"))
                # Use a second package export for the sample default, with a different color cache lookup.
                types.append(("MeshColor", "Texture2D", -4, "FixtureStone"))
                node = object_bytes(prop("ParameterName", "NameProperty", name("RoofPaint")), reference("Texture", -8))
                mesh = object_bytes() + ascend_mesh_lod((1, 4))
                art_exports = [(-3, 0, "FixtureStone", 0, child, 3),
                               (-3, 0, "FixtureGlass", 0, object_bytes(), 3),
                               (-3, 0, "ShaderParent", 0, upstream, 3),
                               (-6, 0, "ColorParent", 0, graph, 3),
                               (-7, 4, "ColorNode", 0, node, 3)]
            else:
                mesh = object_bytes() + ascend_mesh_lod((1, 2))
                art_exports = [(-3, 0, "FixtureStone", 0, object_bytes(parameters("Base_Color")), 3),
                               (-3, 0, "FixtureGlass", 0, object_bytes(parameters("Normal")), 3)]
            companion = source.with_name("Fixture.upk")
            art_exports.append((-2, 0, "OriginalMesh", 0, mesh, 3))
            companion.write_bytes(build_package(game, strings, types, art_exports))
            settings["asset_packages"] = [companion.name, color.name]
            source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
        elif static_mesh == "embedded":
            imports.extend(materials)
            mesh = object_bytes() + ascend_mesh_lod((-len(imports) + 1, -len(imports)))
            exports.append((-7, 0, "OriginalMesh", 0, mesh, 3))
        else:
            types = [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "StaticMesh")]
            mesh = object_bytes() + ascend_mesh_lod((-3, -4))
            companion = source.with_name("Fixture.upk")
            companion.write_bytes(build_package(game, strings, [*types, *materials],
                                                 [(-2, 0, "OriginalMesh", 0, mesh, 3)]))
            settings["asset_packages"] = [companion.name]
            source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
    if static_mesh and not modern:
        slots = [("Fixture", "Shader", -1, name) for name in ("FixtureStone", "FixtureGlass")]
        local = static_mesh == "embedded"
        mesh_imports = imports if local else [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "StaticMesh")]
        mesh_exports = exports if local else []
        mesh_class = -7 if local else -2
        if mesh_artwork:
            mesh_imports.extend((("Core", "Class", -1, "Texture"), ("Core", "Class", -1, "Shader"),
                                 ("Core", "Package", 0, "MeshColor")))
            texture_class, shader_class, color_pkg = (-len(mesh_imports) + 2, -len(mesh_imports) + 1, -len(mesh_imports))
            mesh_imports.append(("Fixture", "Texture", color_pkg, "GlassColor"))
            material_indices = (len(mesh_exports) + 1, len(mesh_exports) + 2)
            stone, stone_start = texture_bytes(5, 64, 32, bytes((40, 225, 60, 255)) * (64 * 32))
            stone_export = (texture_class, 0, "MeshStone", 0, stone, 3)
            glass_ref = -len(mesh_imports)
            properties = [reference("Diffuse", glass_ref), prop("TwoSided", "BoolProperty", b"\x01")]
            if shader_inputs:
                mesh_imports.extend((("Core", "Class", -1, "TexPanner"), ("Fixture", "Texture", color_pkg, "ShaderMask")))
                panner_class = -len(mesh_imports) + 1
                mask_texture = -len(mesh_imports)
                properties.extend((reference("Opacity", material_indices[1] + 1),
                                   reference("GlowMapOverride", material_indices[0]),
                                   reference("GlowMaskOverride", mask_texture), prop("GlowBrightness", "ByteProperty", b"\x80")))
            else:
                properties.append(prop("OutputBlending", "ByteProperty", b"\x01"))
            if surface_detail:
                mesh_imports.extend((("Fixture", "Texture", color_pkg, "SurfaceNormal"),
                                     ("Fixture", "Texture", color_pkg, "ReflectanceMask")))
                properties.extend((reference("NormalMap", -len(mesh_imports) + 1),
                                   reference("SpecularityMask", -len(mesh_imports))))
            mesh_exports.extend((stone_export, (shader_class, 0, "MeshGlass", 0, object_bytes(*properties), 3)))
            if shader_inputs:
                pan = object_bytes(reference("Material", mask_texture), prop("PanRate", "FloatProperty", pack("f", .75)),
                                   vector("PanDirection", 0, 16384, 0, structure="Rotator"))
                mesh_exports.append((panner_class, 0, "OpacityPanner", 0, pan, 3))
            alpha = b"".join(bytes((255, 0, 0, 255 if shader_inputs or x < 16 else 0)) for _ in range(16) for x in range(32))
            pixels, start = texture_bytes(5, 32, 16, alpha)
            color = source.with_name("MeshColor.pkg")
            texture_imports = [("Core", "Package", 0, "Fixture"), ("Core", "Class", -1, "Texture")]
            texture_exports = [(-2, 0, "GlassColor", 0, pixels, 3)]
            mip_patches = [(1, start, len(pixels))]
            if shader_inputs:
                pattern = b"".join(bytes((240, 240, 240, 255 if y < 8 else 0)) for y in range(16) for _ in range(32))
                mask, mask_start = texture_bytes(5, 32, 16, pattern)
                texture_exports.append((-2, 0, "ShaderMask", 0, mask, 3))
                mip_patches.append((len(texture_exports), mask_start, len(mask)))
            if surface_detail:
                green = bytes((128, 128)) + bytes(6)
                blocks = b"".join((bytes((value, value)) + bytes(6) + green) * 8 for value in (224, 224, 32, 32))
                normal, normal_start = texture_bytes(BC5, 32, 16, blocks)
                texture_exports.append((-2, 0, "SurfaceNormal", 0, normal, 3))
                mip_patches.append((len(texture_exports), normal_start, len(normal)))
                pattern = b"".join(bytes((0, 0, 0, 60 if y < 8 else 220)) for y in range(16) for _ in range(32))
                reflectance, reflectance_start = texture_bytes(5, 32, 16, pattern)
                texture_exports.append((-2, 0, "ReflectanceMask", 0, reflectance, 3))
                mip_patches.append((len(texture_exports), reflectance_start, len(reflectance)))
            color.write_bytes(build_package(game, strings, texture_imports, texture_exports, version=130, licensee=27))
            for patch in mip_patches:
                patch_texture_mip(color, *patch)
            settings.setdefault("asset_packages", []).append(color.name)
        else:
            mesh_imports.extend(slots)
            material_indices = (-len(mesh_imports) + 1, -len(mesh_imports))
        material_data = compact(2)
        for index, solid in zip(material_indices, (True, False)):
            material_data += (reference("Material", index) + prop("EnableCollision", "BoolProperty", bytes([solid]))
                              + name("None"))
        mesh = object_bytes(prop("Materials", "ArrayProperty", material_data)) + vengeance_mesh_buffers()
        mesh_exports.append((mesh_class, 0, "OriginalMesh", 0, mesh, 3))
        if local:
            mesh_index = len(exports)
            if mesh_artwork:
                paint_payloads.append((material_indices[0], stone_export, stone_start))
        else:
            companion = source.with_name("Fixture.pkg")
            companion.write_bytes(build_package(game, strings, mesh_imports, mesh_exports, version=130, licensee=27))
            if mesh_artwork:
                patch_texture_mip(companion, material_indices[0], stone_start, len(stone))
            settings.setdefault("asset_packages", []).append(companion.name)
        if settings.get("asset_packages"):
            source.with_suffix(".import.json").write_text(json.dumps(settings), encoding="utf-8")
        exports[5] = (-5, 0, "Deck", 0, object_bytes(*decks, reference("StaticMesh", mesh_index)), 3)
        exports[6] = (-6, 6, "DeckMesh", 0, object_bytes(), 3)
        decorative = object_bytes(vector("Location", 30, 360, 200), reference("StaticMesh", mesh_index),
                                  prop("bBlockHavok", "BoolProperty", b"\0"),
                                  prop("bCollideActors", "BoolProperty", b"\0"),
                                  prop("bHidden", "BoolProperty", b"\x01"))
        exports[7] = (-5, 0, "Unmapped", 0, decorative, 3)
    package = build_package(game, strings, imports, exports, compressed=modern, licensee=27, version=130)
    source.write_bytes(package)
    if legacy_ground and not external_height:
        patch_texture_mip(source, 10, native_start, len(texture))
    for index, row, start in paint_payloads:
        patch_texture_mip(source, index, start, len(row[4]))
    return source


def write_ascend_rtc(folder):
    pixels = bytes((12, 80, 241, 255)) * 64
    payload = zlib.compress(pixels)
    prefix = b"unrelated-cache-bytes"
    (folder / "texture.cache.data.rtc").write_bytes(prefix + payload)

    def entry(name, offset):
        encoded = name.encode("ascii")
        return (struct.pack("<i", len(encoded)) + encoded + struct.pack("<2B2hi", 2, 2, 8, 8, 1)
                + struct.pack("<BI2i", 1, offset, len(payload), len(pixels)))

    # The same leaf in another package is not a candidate for this mesh.
    (folder / "texture.cache.hdr.rtc").write_bytes(
        struct.pack("<i", 2) + entry("OtherPackage.AscendColor", 0)
        + entry("meshcolor.AscendColor", len(prefix))
    )


class UnrealBinarySceneTests(unittest.TestCase):
    def test_vengeance_mover_keeps_brush_pivot_and_indexed_motion(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, bsp_artwork=True, brush_mover=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 32)
            mover, = [row for row in rows if row["source_class"] == "Engine.Mover"]
            reference = mover["model_reference"]
            self.assertTrue(reference.startswith("unrealmesh:brush."))
            self.assertEqual(mover["properties"]["Authored keys"], "3")
            self.assertEqual(mover["properties"]["Mover state"], "TriggerOpenTimed")
            for observed, expected in zip(mover["transform"]["position_cm"], (100, -360, 400)):
                self.assertAlmostEqual(observed, expected)
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))
            brush, = scene["native_brushes"].values()
            motion = brush["motion"]
            self.assertEqual(motion["current_key"], 1)
            self.assertEqual(motion["timing_seconds"], {"movetime": 1.5, "stayopentime": 2.0, "delaytime": 0.0})
            self.assertEqual(motion["keys"][0]["position_offset_m"], [0, 0, 0])
            self.assertEqual(motion["keys"][1]["position_offset_m"], [0, 0, .5])
            self.assertEqual(motion["keys"][2], {"position_offset_m": [2, 1, .5], "rotation_offset_units": [0, 16384, 0]})
            self.assertEqual(brush["behavior"]["pivot_m"], [.4, .2, 0])
            self.assertEqual(brush["behavior"]["saved_rotation_units"], [0, 16384, 0])
            model = header["interiors"][reference]
            geometry, _ = load_intake_geometry(manifest.parent, model, header["files"][model])
            self.assertEqual(len(geometry["triangles"]), 4)
            self.assertIn(model, load_texture_index(manifest.parent, header["files"]))
            with ZipFile(export_public_layout(manifest, root / "Public.zip")) as archive:
                self.assertFalse({UNREAL_SCENE, model} & set(archive.namelist()))
            motion["current_key"] = 3
            changed = json.dumps(scene).encode()
            manifest.with_name(UNREAL_SCENE).write_bytes(changed)
            header["files"][UNREAL_SCENE] = sha256(changed).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid Vengeance mover timing or base pose"):
                load_intake(manifest)

    def test_vengeance_loop_track_keeps_its_key_order_and_rotated_pivot(self):
        from vengeance_mover_plan import loop_track

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="loop")
            manifest = convert_unreal(source, root / "Intakes")
            load_intake(manifest)
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))
            brush, = scene["native_brushes"].values()
            self.assertTrue(brush["behavior"]["oscillating"])
            track = dict(loop_track(brush))
            for tick, expected in zip((0, 1500, 3000, 4500, 6000),
                                      ((100, -360, 400), (100, -360, 450), (320, -200, 450),
                                       (100, -360, 450), (100, -360, 400))):
                for value, target in zip(track[tick], (*expected, 0, 0, 180 if tick == 3000 else 90, 1, 1, 1)):
                    self.assertAlmostEqual(value, target)
            self.assertAlmostEqual(track[375][2], 407.8125)
            brush["behavior"]["encroach_type"] = 1
            self.assertIsNone(loop_track(brush))

    def test_vengeance_elevator_keeps_explicit_physics(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="elevator")
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 34)
            elevator, = [row for row in rows if row["source_class"] == "Movement.ElevatorVolume"]
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))
            brush = scene["native_brushes"][elevator["properties"]["Brush export"]]
            self.assertIsNone(brush["motion"])
            behavior = brush["behavior"]
            self.assertIs(behavior["active"], False)
            self.assertIs(behavior["moves_projectiles"], True)
            self.assertEqual(behavior["priority"], 42)
            self.assertEqual(behavior["gravity_m_s2"], [0, 0, 2])
            self.assertEqual(behavior["zone_velocity_m_s"], [1, 0, 5])
            self.assertEqual(behavior["terminal_speed_m_s"], 12)
            self.assertNotEqual(behavior["associated_actor"], "None")

    def test_vengeance_zero_gravity_hulls_keep_both_placements_and_detect_events(self):
        from vengeance_elevators import zero_gravity_hull

        for variant in ("lift", "lift-linked"):
            with self.subTest(variant=variant), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover=variant)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, rows = load_intake(manifest)
                linked = variant == "lift-linked"
                self.assertEqual(header["format"], 35 if linked else 34)
                lifts = [row for row in rows if row["source_class"] == "Movement.ElevatorVolume"]
                self.assertEqual(sorted(row["transform"]["position_cm"][0] for row in lifts), [0, 150])
                scene = json.loads(manifest.with_name(UNREAL_SCENE).read_bytes())
                for row in lifts:
                    record = scene["native_brushes"][row["properties"]["Brush export"]]
                    name = header["interiors"][row["model_reference"]]
                    shape, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
                    self.assertIs(record["behavior"]["incoming_trigger"], linked)
                    hull = zero_gravity_hull(record, shape, 50)
                    if linked:
                        self.assertIsNone(hull)
                        sender, = record["behavior"]["trigger_senders"]
                        self.assertEqual((sender["source_export"], sender["source_name"]), (6, "Deck"))
                        self.assertIsNone(sender["proximity"])
                        self.assertIn("does not use the standard proximity trigger", sender["review"])
                    else:
                        self.assertEqual({tuple(point) for point in hull},
                                         {(x, y, z) for x in (-100, 100) for y in (-100, 100) for z in (-130, 130)})
                        shape["collision"]["triangles"].pop()
                        self.assertIsNone(zero_gravity_hull(record, shape, 50))

    def test_vengeance_elevator_saves_proximity_sender_settings_and_fanout(self):
        from dock_review import object_review_document
        from object_catalog import load_catalog
        from vengeance_elevators import linked_elevator_senders, zero_gravity_hull

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="lift-switch")
            manifest = convert_unreal(source, root / "Intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["format"], 35)
            source_scene = manifest.with_name(UNREAL_SCENE)
            scene = json.loads(source_scene.read_bytes())
            behaviors = {key: value["behavior"] for key, value in scene["native_brushes"].items()}
            self.assertEqual(len(behaviors), 2)
            self.assertEqual({item["active"] for item in behaviors.values()}, {True, False})
            first, second = (item["trigger_senders"][0] for item in behaviors.values())
            self.assertEqual(first, second)
            self.assertEqual((first["source_name"], first["source_class"], first["event"]),
                             ("LiftSwitch", "Engine.Trigger", "elevatorvolume"))
            self.assertEqual(first["proximity"], dict(
                position_cm=[560, -400, 400], radius_cm=72, half_height_cm=140, activation=1,
                active=True, once=True, state="NormalTrigger", repeat_seconds=0, delay_seconds=.75,
                tag="Trigger", collision=True, blocking=False, touch=True, receives_events=False))
            for record in scene["native_brushes"].values():
                self.assertEqual(linked_elevator_senders(record["behavior"]), (first,))
                name = header["interiors"][record["reference"]]
                shape, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
                self.assertEqual(len(zero_gravity_hull(record, shape, 50, linked=True)), 8)
                record["behavior"]["trigger_senders"][0]["proximity"]["receives_events"] = True
                self.assertIsNone(zero_gravity_hull(record, shape, 50, linked=True))
                record["behavior"]["trigger_senders"][0]["proximity"]["receives_events"] = False
            row, = (row for row in rows if row["name"] == "LiftSwitch")
            self.assertEqual(set(row["properties"]["Elevator brush exports"].split(", ")), behaviors.keys())
            self.assertEqual(row["properties"]["Trigger reach (cm)"], "72 radius, 140 half-height")
            self.assertEqual(row["properties"]["Trigger activation"], "Pawn proximity")
            catalog = load_catalog(source_profile=header["profile"])
            review = object_review_document("DraftTV001", header, terrain, rows, catalog, {})
            lift_review = [item for item in review["objects"] if item["type"] == "Movement.ElevatorVolume"]
            self.assertEqual(len(lift_review), 2)
            self.assertTrue(all(item["status"] == "Linked field review" and item["review"] for item in lift_review))
            trigger_review, = (item for item in review["objects"] if item["object"] == "LiftSwitch")
            self.assertEqual((trigger_review["status"], trigger_review["feature"]), ("Source only", "Trigger links"))
            self.assertFalse(trigger_review["blocked"])
            with ZipFile(export_public_layout(manifest, root / "Public.zip")) as archive:
                self.assertNotIn(UNREAL_SCENE, archive.namelist())
                self.assertNotIn(b"LiftSwitch", archive.read("objects.json"))
            first["event"] = "other_destination"
            payload = json.dumps(scene).encode()
            source_scene.write_bytes(payload)
            header["files"][UNREAL_SCENE] = sha256(payload).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sender does not match"):
                load_intake(manifest)

    def test_vengeance_repeating_switches_keep_each_interval_and_cooldown(self):
        from vengeance_elevators import linked_elevator_senders

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="lift-repeat")
            manifest = convert_unreal(source, root / "Intakes")
            load_intake(manifest)
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_bytes())
            self.assertEqual(len(scene["native_brushes"]), 2)
            for brush in scene["native_brushes"].values():
                senders = linked_elevator_senders(brush["behavior"])
                self.assertEqual({sender["source_name"]: (sender["proximity"]["repeat_seconds"],
                                                          sender["proximity"]["delay_seconds"])
                                  for sender in senders}, {"LiftSwitch": (.375, .625), "LiftFastSwitch": (.5, 0)})

    def test_vengeance_bump_door_keeps_delay_and_open_time(self):
        from vengeance_mover_plan import bump_track

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="door")
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 33)
            door, = [row for row in rows if row["source_class"] == "Engine.Mover"]
            self.assertEqual(door["properties"]["Bump type"], "0")
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_bytes())
            brush = scene["native_brushes"][door["properties"]["Brush export"]]
            keyframes = bump_track(brush)
            self.assertEqual(sorted(set(frame for frame, _ in keyframes)), [frame for frame, _ in keyframes])
            track = dict(keyframes)
            self.assertEqual(track[0], track[250])
            self.assertEqual(track[3250], track[5250])
            for start, end in zip(track[0], track[8250]):
                self.assertAlmostEqual(start, end)
            self.assertNotEqual(track[0], track[3250])
            brush["behavior"]["bump_type"] = 1
            self.assertIsNone(bump_track(brush))

    def test_vengeance_triggered_doors_share_one_saved_switch_and_keep_their_full_cycle(self):
        from vengeance_mover_plan import trigger_track

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="door-switch")
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 36)
            source_scene = manifest.with_name(UNREAL_SCENE)
            scene = json.loads(source_scene.read_bytes())
            doors = scene["native_brushes"]
            switch, = [row for row in rows if row["name"] == "DoorSwitch"]
            self.assertEqual(set(switch["properties"]["Door brush exports"].split(", ")), doors.keys())
            self.assertEqual(switch["properties"]["Repeat seconds"], "0.625")
            saved_senders = []
            for door in doors.values():
                track, senders = trigger_track(door)
                self.assertTrue(track)
                self.assertEqual(dict(track)[3250], dict(track)[5250])
                for start, end in zip(track[0][1], track[-1][1]):
                    self.assertAlmostEqual(start, end)
                self.assertEqual(track[-1][0], 8250)
                saved_senders.append(senders)
                door["behavior"]["linked"] = True
                self.assertEqual(trigger_track(door), (None, ()))
            self.assertEqual(saved_senders[0], saved_senders[1])
            saved_senders[0][0]["event"] = "unrelated"
            data = json.dumps(scene).encode()
            source_scene.write_bytes(data)
            header["files"][UNREAL_SCENE] = sha256(data).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match its source actor or event"):
                load_intake(manifest)

    def test_vengeance_triggered_doors_stay_in_review_when_another_actor_shares_the_tag(self):
        from vengeance_mover_plan import trigger_track

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover="door-switch-incomplete")
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 36)
            switch, = [row for row in rows if row["name"] == "DoorSwitch"]
            self.assertIn("Additional actors share this event tag", switch["reason"])
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_bytes())
            self.assertTrue(all(trigger_track(door) == (None, ()) for door in scene["native_brushes"].values()))

    def test_vengeance_one_time_door_preserves_the_final_open_key_and_its_switches(self):
        from vengeance_mover_plan import bump_track, trigger_track

        for fixture in ("door-once", "door-switch-once"):
            with self.subTest(fixture=fixture), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover=fixture)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, rows = load_intake(manifest)
                self.assertEqual(header["format"], 37)
                scene_path = manifest.with_name(UNREAL_SCENE)
                scene = json.loads(scene_path.read_bytes())
                index, = [index for index, door in scene["native_brushes"].items() if door["behavior"].get("opens_once")]
                self.assertTrue(any(row["properties"].get("Brush export") == index
                                    and row["properties"].get("Stays open after activation") == "True" for row in rows))
                opened = scene["native_brushes"][index]
                if fixture == "door-once":
                    track = bump_track(opened)
                else:
                    track, senders = trigger_track(opened)
                    self.assertEqual({sender["source_name"] for sender in senders}, {"DoorSwitch", "DoorAuxSwitch"})
                    closing, = (door for key, door in scene["native_brushes"].items() if key != index)
                    self.assertEqual(trigger_track(closing)[0][-1][0], 8250)
                self.assertEqual(track[-1][0], 5250)
                self.assertEqual(dict(track)[3250], track[-1][1])
                self.assertNotEqual(track[0][1], track[-1][1])
                opened["behavior"]["opens_once"] = False
                data = json.dumps(scene).encode()
                scene_path.write_bytes(data)
                header["files"][UNREAL_SCENE] = sha256(data).hexdigest()
                manifest.write_text(json.dumps(header), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid Vengeance mover behavior"):
                    load_intake(manifest)

    def test_vengeance_chained_switches_keep_their_source_edges_and_refuse_partial_or_cyclic_groups(self):
        from vengeance_mover_plan import plan_proximity_chains, trigger_track

        for variant in ("door-chain", "door-chain-cycle"):
            with self.subTest(variant=variant), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "vengeance", bsp_world=True, brush_mover=variant)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, rows = load_intake(manifest)
                self.assertEqual(header["format"], 38)
                scene_path = manifest.with_name(UNREAL_SCENE)
                scene = json.loads(scene_path.read_bytes())
                brushes, chains = scene["native_brushes"], scene["trigger_chains"]
                by_export = {str(actor["line"]): actor["name"] for actor in scene["actors"]}
                self.assertEqual({by_export[key] for key in chains},
                                 {"DoorSwitch", "DoorRelayOn", "DoorRelayOff", "DoorToggleSpare"})
                row, = [item for item in rows if item["name"] == "DoorRelayOn"]
                self.assertEqual(row["properties"]["Trigger state"], "OtherTriggerTurnsOn")
                self.assertIn("DoorEnable", row["properties"]["Saved event senders"])
                self.assertTrue(row["properties"]["Controlled trigger exports"])
                chain_ids = set(map(int, chains))
                available = {int(key) for key, brush in brushes.items() if trigger_track(brush, chain_ids)[0]}
                self.assertEqual(available, set(map(int, brushes)))
                allowed, refused = plan_proximity_chains(chains, brushes, available)
                if variant == "door-chain-cycle":
                    self.assertEqual((allowed, refused), ({}, available))
                else:
                    self.assertEqual((allowed.keys(), refused), (chain_ids, set()))
                    self.assertEqual(plan_proximity_chains(chains, brushes, {min(available)}), ({}, available))
                upstream = next(senders[0] for index, senders in chains.items() if by_export[index] == "DoorRelayOff")
                upstream["event"] = "unrelated"
                data = json.dumps(scene).encode()
                scene_path.write_bytes(data)
                header["files"][UNREAL_SCENE] = sha256(data).hexdigest()
                manifest.write_text(json.dumps(header), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "does not match its source actor or event"):
                    load_intake(manifest)

    def test_vengeance_normal_channel_decoding(self):
        from PIL import Image

        uncompressed = bytes((128, 128, 192, 96)) * 8
        green565 = struct.pack("<2H4x", 32 << 5, 32 << 5)
        block_bc5 = bytes((200, 200)) + bytes(6) + bytes((80, 80)) + bytes(6)
        fixtures = ((CXV8U8, uncompressed, (128, 128), (192, 96)),
                    (DXT5N, bytes((200, 200)) + bytes(6) + green565, (200, 130), (200, 130)),
                    (BC5, block_bc5, (200, 80), (200, 80)))
        for encoding, data, first, second in fixtures:
            with self.subTest(encoding=encoding), Image.open(BytesIO(normal_png(encoding, 4, 4, data))) as image:
                for x, expected in enumerate((first, second)):
                    r, g, b, a = image.getpixel((x, 0))
                    self.assertEqual((r, g, a), (*expected, 255))
                    self.assertAlmostEqual(sum((v / 127.5 - 1) ** 2 for v in (r, g, b)), 1, delta=.01)

    def test_vengeance_surface_detail_binds_companion_normals_and_specular_alpha(self):
        from PIL import Image

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", static_mesh="companion", mesh_artwork=True, surface_detail=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 30)
            mesh = header["interiors"]["unrealmesh:fixture.originalmesh"]
            index = load_texture_index(manifest.parent, header["files"])
            record = index[mesh]["1"]
            self.assertEqual(record["mode"], "masked")
            self.assertFalse(any(row["source_class"] == "Material" for row in rows))
            for channel, values in (("normal", ((224, 128), (32, 128))), ("specular_mask", (60, 220))):
                binding = record[channel]
                self.assertEqual(binding["source_sha256"], sha256((root / "MeshColor.pkg").read_bytes()).hexdigest())
                with Image.open(manifest.with_name(binding["file"])) as image:
                    pixels = [image.getpixel((3, y)) for y in (3, 13)]
                    observed = tuple(pixel[:2] if channel == "normal" else pixel[3] for pixel in pixels)
                    self.assertEqual(observed, values)
            with ZipFile(export_public_layout(manifest, root / "Public.zip")) as archive:
                self.assertFalse({TEXTURE_INDEX, record["normal"]["file"], record["specular_mask"]["file"]} & set(archive.namelist()))
            header["format"] = 29
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Incomplete model texture provenance"):
                load_intake(manifest)

    def test_vengeance_shader_inputs_keep_panned_alpha_glow_and_source_receipts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", static_mesh="companion", mesh_artwork=True, shader_inputs=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, _ = load_intake(manifest)
            self.assertEqual(header["format"], 29)
            mesh = header["interiors"]["unrealmesh:fixture.originalmesh"]
            bindings = load_texture_index(manifest.parent, header["files"])
            binding = bindings[mesh]["1"]
            self.assertEqual(binding["mode"], "translucent")
            self.assertEqual(binding["opacity"]["pan"], [0, .75])
            self.assertEqual(binding["opacity"]["source"], "MeshColor.pkg")
            self.assertEqual(binding["glow"]["color"]["file"], bindings[mesh]["0"]["file"])
            self.assertEqual(binding["glow"]["mask"]["file"], binding["opacity"]["file"])
            self.assertEqual(binding["glow"]["brightness"], 128)
            self.assertNotEqual(binding["file"], binding["opacity"]["file"])
            expected = {mesh, TEXTURE_INDEX, binding["glow"]["mask"]["file"], binding["glow"]["color"]["file"], binding["file"]}
            with ZipFile(export_public_layout(manifest, root / "Public.zip")) as archive:
                self.assertFalse(expected & set(archive.namelist()))
            binding["opacity"]["pan"] = [0, 5000]
            content = json.dumps(bindings).encode()
            manifest.with_name(TEXTURE_INDEX).write_bytes(content)
            header["files"][TEXTURE_INDEX] = sha256(content).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "opacity panning speed"):
                load_intake(manifest)

    def test_vengeance_mesh_artwork_keeps_normalized_uvs_and_private_sources(self):
        for mode, model_path in (("embedded", "originalmesh"), ("companion", "fixture.originalmesh")):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "vengeance", static_mesh=mode, mesh_artwork=True, bsp_artwork=True,
                                              bsp_world=True, native_terrain=True)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, _ = load_intake(manifest)
                self.assertEqual(header["format"], 28)
                mesh = header["interiors"]["unrealmesh:" + model_path]
                bindings = load_texture_index(manifest.parent, header["files"])
                self.assertEqual(set(bindings), set(header["interiors"].values()))
                opaque, masked = (bindings[mesh][slot] for slot in ("0", "1"))
                self.assertEqual((opaque["size"], opaque["mode"], opaque["two_sided"]), ([64, 32], "opaque", False))
                self.assertEqual(opaque["source"], source.name if mode == "embedded" else "Fixture.pkg")
                self.assertEqual((masked["size"], masked["mode"], masked["two_sided"]), ([32, 16], "masked", True))
                self.assertEqual(masked["source_sha256"], sha256((root / "MeshColor.pkg").read_bytes()).hexdigest())
                geometry, _ = load_intake_geometry(manifest.parent, mesh, header["files"][mesh])
                self.assertEqual(render_sections(geometry)[0][3], [(0, 0), (0, 1), (1, 0)])
                with ZipFile(export_public_layout(manifest, root / "Public.zip")) as archive:
                    self.assertFalse({mesh, TEXTURE_INDEX, opaque["file"], masked["file"]} & set(archive.namelist()))
                metadata = source.with_suffix(".import.json")
                settings = json.loads(metadata.read_text(encoding="utf-8"))
                settings["asset_packages"].remove("MeshColor.pkg")
                metadata.write_text(json.dumps(settings), encoding="utf-8")
                staged = convert_unreal(source, root / "MissingTexture")
                fallback, _, review = load_intake(staged)
                records = load_texture_index(staged.parent, fallback["files"])
                self.assertEqual(fallback["format"], 28)
                self.assertEqual(records[mesh]["0"]["file"], opaque["file"])
                self.assertIn("MeshColor.pkg in asset_packages", records[mesh]["1"]["reason"])
                self.assertTrue(any("MeshColor.pkg in asset_packages" in row["reason"] for row in review))

    def test_vengeance_placed_meshes_keep_sections_uvs_and_collision(self):
        for mode, reference in (("embedded", "unrealmesh:originalmesh"), ("companion", "unrealmesh:fixture.originalmesh")):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "vengeance", static_mesh=mode)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, rows = load_intake(manifest)
                self.assertEqual(header["format"], 27)
                name = header["interiors"][reference]
                geometry, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
                self.assertEqual(geometry["materials"], ["Fixture.FixtureStone", "Fixture.FixtureGlass"])
                self.assertEqual(geometry["triangles"], [[0, 0, 1, 2, 0, 0, 0, 1, 1, 0],
                                                        [1, 3, 4, 5, 0, 0, 0, 1, 1, 0]])
                self.assertEqual(geometry["collision"]["triangles"], [[0, 1, 2]])
                deck, decorative = [row for row in rows if row.get("model_reference") == reference]
                self.assertEqual(deck["transform"]["position_cm"], [60, 160, 400])
                self.assertEqual(decorative["properties"]["bBlockHavok"], "False")
                self.assertEqual(decorative["properties"]["bCollideActors"], "False")
                self.assertEqual(decorative["properties"]["bHidden"], "True")
                public = export_public_layout(manifest, root / "Share.zip")
                with ZipFile(public) as archive:
                    self.assertNotIn(name, archive.namelist())
                if mode == "companion":
                    self.assertEqual(geometry["source"]["sha256"], sha256((root / "Fixture.pkg").read_bytes()).hexdigest())
                    manifest.write_text(json.dumps(dict(header, sources=header["sources"][:-1])), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "package source"):
                        load_intake(manifest)
                    settings = source.with_suffix(".import.json")
                    data = json.loads(settings.read_text(encoding="utf-8"))
                    del data["asset_packages"]
                    settings.write_text(json.dumps(data), encoding="utf-8")
                    _, _, review = load_intake(convert_unreal(source, root / "Missing"))
                    self.assertTrue(any("Fixture.pkg in asset_packages" in row["reason"] for row in review))

    def test_vengeance_bsp_materials_bind_textures_and_keep_collision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", native_terrain=True, bsp_world=True, bsp_artwork=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, _ = load_intake(manifest)
            self.assertEqual(header["format"], 26)
            model, = header["interiors"].values()
            geometry, _ = load_intake_geometry(manifest.parent, model, header["files"][model])
            self.assertEqual(geometry["materials"], ["FixtureStone", "FixtureGlass"])
            index = load_texture_index(manifest.parent, header["files"])[model]
            self.assertEqual([(row["mode"], row["size"], row["two_sided"]) for row in index.values()],
                             [("opaque", [64, 32], False), ("masked", [64, 64], True)])
            self.assertEqual(index["1"]["source_sha256"], sha256(source.with_name("BspColor.pkg").read_bytes()).hexdigest())
            groups = render_sections(geometry, texture_sizes={int(slot): row["size"] for slot, row in index.items()})
            self.assertEqual(groups[0][3][:3], [(0, 0), (1, .5), (1, 0)])
            self.assertEqual(groups[1][3][:3], [(0, 0), (1, .25), (1, 0)])
            self.assertEqual(len(geometry["collision"]["triangles"]), 4)
            public, _, _ = load_intake(unpack_intake(export_public_layout(manifest, root / "Share.zip"), root / "Public"))
            self.assertNotIn(TEXTURE_INDEX, public["files"])
            self.assertFalse({row["file"] for row in index.values()} & public["files"].keys())

    def test_vengeance_terrain_artwork_uses_embedded_and_external_mips(self):
        from PIL import Image

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", native_terrain=True, painted=True, source_artwork=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, terrain, _ = load_intake(manifest)
            self.assertEqual(header["format"], 25)
            artwork = available_art(header)
            self.assertEqual(header["terrain_uv"]["4"],
                             [[.00390625, 0, 0, .25], [.0009765625, .0078125, .001953125, -.125]])
            self.assertEqual(header["terrain_art"]["4"]["source_sha256"],
                             sha256(source.with_name("FixtureSurface.pkg").read_bytes()).hexdigest())
            self.assertEqual([entry[2] for entry in layer_choices(terrain, artwork=artwork)], [SOURCE_ART, SOURCE_ART])
            for slot, expected in ((0, (46, 120, 48, 255)), (4, (0, 0, 255, 255))):
                with Image.open(BytesIO(manifest.with_name(artwork[slot]).read_bytes())) as image:
                    self.assertEqual(image.getpixel((0, 0)), expected)
            share = export_public_layout(manifest, root / "Share.zip")
            public, _, _ = load_intake(unpack_intake(share, root / "Public"))
            self.assertFalse(set(public["files"]) & set(artwork.values()))
            self.assertNotIn("terrain_uv", public)

    def test_vengeance_paints_ordered_masks_across_tile_seams(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", native_terrain=True, painted=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["format"], 24)
            self.assertEqual(terrain["material_layers"], [[0, "Fixture.GrassSurface"], [4, "Fixture.RockSurface"]])
            name = header["terrain_paint"]["masks"]["4"]
            self.assertEqual(decode_mask(manifest.with_name(name).read_bytes()),
                             (4, 2, bytes([0, 64, 192, 255, 0, 128, 128, 255])))
            paint = NativeTerrainPaint(manifest.parent, header, layer_choices(terrain), PALETTE_SURFACES, FALLBACK)
            first, second = paint.tile_weights(0, 0), paint.tile_weights(1, 0)
            self.assertEqual(first[0], (1.0, 0.0, 0.0))
            self.assertEqual(second[255], (0.0, 1.0, 0.0))
            self.assertEqual(first[256::257], second[::257])
            self.assertAlmostEqual(first[256][1], (64 + 128 * (256 * 3 / 511 - 1)) / 255)
            self.assertTrue(any(row["name"] == "Terrain layer 6" and row["status"] == "review" for row in rows))
            private_archive = root / "Private.zip"
            with ZipFile(private_archive, "w") as archive:
                for member in ("manifest.json", *header["files"]):
                    archive.write(manifest.with_name(member), member)
            self.assertEqual(load_intake(unpack_intake(private_archive, root / "Private"))[0], header)
            share = export_public_layout(manifest, root / "Share.zip")
            public, public_terrain, _ = load_intake(unpack_intake(share, root / "Public"))
            self.assertEqual(public["format"], 22)
            self.assertEqual(public_terrain["material_layers"], [])
            self.assertNotIn(name, public["files"])
            different_mask = grayscale_png(bytes(8), 8, 4, 2)
            manifest.with_name(name).write_bytes(different_mask)
            header["files"][name] = sha256(different_mask).hexdigest()
            manifest.write_text(json.dumps(header), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "alpha pixels do not match"):
                load_intake(manifest)

    def test_vengeance_resolves_external_heightmap_by_package_and_group(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", native_terrain=True, external_height=True)
            embedded = binary_draft_fixture(root / "Embedded", "vengeance", native_terrain=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, terrain, _ = load_intake(manifest)
            comparison = convert_unreal(embedded, root / "EmbeddedIntakes")
            self.assertEqual(terrain, load_intake(comparison)[1])
            companion = root / "FixtureTerrain.pkg"
            scene = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))
            ground = scene["native_terrain"]
            self.assertEqual(ground["height_texture"], "FixtureTerrain.HeightLayers.HeightTexture")
            self.assertEqual(ground["height_package"], dict(name=companion.name, sha256=sha256(companion.read_bytes()).hexdigest()))
            self.assertIn(dict(ground["height_package"], origin=companion.name), header["sources"])
            manifest.write_text(json.dumps(dict(header, sources=header["sources"][:-1])), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match the intake provenance"):
                load_intake(manifest)
            metadata = source.with_suffix(".import.json")
            settings = json.loads(metadata.read_text(encoding="utf-8"))
            del settings["asset_packages"]
            metadata.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"FixtureTerrain\.pkg in asset_packages"):
                convert_unreal(source, root / "MissingIntakes")

    def test_ascend_placed_meshes_keep_material_uvs_and_independent_collision(self):
        for mode, reference in (("embedded", "unrealmesh:originalmesh"), ("companion", "unrealmesh:fixture.originalmesh")):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "ascend", static_mesh=mode)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, rows = load_intake(manifest)
                self.assertEqual(header["format"], 23)
                self.assertEqual(list(header["interiors"]), [reference])
                name = header["interiors"][reference]
                geometry, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
                self.assertEqual(geometry["materials"], ["Fixture.FixtureStone", "Fixture.FixtureGlass"])
                self.assertEqual(geometry["triangles"], [[0, 0, 1, 2, 0, 0, 0, 1, 1, 0],
                                                        [1, 3, 4, 5, 0, 0, 0, 1, 1, 0]])
                self.assertEqual(geometry["collision"]["triangles"], [[0, 1, 2]])
                deck, = (row for row in rows if row.get("model_reference") == reference)
                self.assertEqual(deck["transform"]["position_cm"], [60, 160, 400])
                share = export_public_layout(manifest, root / "Share.zip")
                public, _, _ = load_intake(unpack_intake(share, root / "Public"))
                self.assertEqual(public["format"], 22)
                self.assertNotIn("interiors", public)
                self.assertNotIn(name, public["files"])
                manifest.write_text(json.dumps(dict(header, format=22)), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid intake interiors"):
                    load_intake(manifest)

    def test_ascend_mesh_artwork_binds_exact_companion_and_reports_unmapped_slots(self):
        from PIL import Image

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "ascend", native_terrain=True, static_mesh="companion", mesh_artwork=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            self.assertEqual(header["format"], 39)
            model, = header["interiors"].values()
            texture_index = json.loads(manifest.with_name("model-textures.json").read_bytes())
            binding = texture_index[model]["0"]
            self.assertEqual((binding["source"], binding["size"], binding["mode"]), ("MeshColor.upk", [2, 2], "opaque"))
            self.assertIn("Larger mips were unavailable", binding["warning"])
            self.assertIn("no unambiguous base-color", texture_index[model]["1"]["reason"])
            self.assertTrue(any("base-color" in row["reason"] and row["source_class"] == "Material" for row in rows))
            with Image.open(manifest.with_name(binding["file"])) as image:
                self.assertEqual(image.getpixel((0, 0)), (205, 110, 70, 255))
            self.assertEqual(header["sources"][-1]["sha256"], sha256(source.with_name("MeshColor.upk").read_bytes()).hexdigest())

            public, _, _ = load_intake(unpack_intake(export_public_layout(manifest, root / "Share.zip"), root / "Public"))
            self.assertEqual(public["format"], 22)
            self.assertNotIn(binding["file"], public["files"])
            self.assertNotIn("model-textures.json", public["files"])

            settings_file = source.with_suffix(".import.json")
            settings = json.loads(settings_file.read_bytes())
            settings["asset_packages"].remove("MeshColor.upk")
            settings_file.write_text(json.dumps(settings), encoding="utf-8")
            plain, _, pending = load_intake(convert_unreal(source, root / "Intakes"))
            self.assertEqual(plain["format"], 23)
            self.assertEqual(plain["interiors"], header["interiors"])
            self.assertTrue(any("List the Ascend artwork package MeshColor.upk" in row["reason"] for row in pending))

    def test_ascend_rtc_uses_exact_cached_mip_and_falls_back_on_bad_data(self):
        from PIL import Image

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "ascend", static_mesh="companion", mesh_artwork=True)
            write_ascend_rtc(root)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, _ = load_intake(manifest)
            model, = header["interiors"].values()
            binding = json.loads(manifest.with_name(TEXTURE_INDEX).read_bytes())[model]["0"]
            self.assertEqual((binding["source"], binding["size"]), ("texture.cache.data.rtc", [8, 8]))
            with Image.open(manifest.with_name(binding["file"])) as image:
                self.assertEqual(image.getpixel((0, 0)), (241, 80, 12, 255))
            sources = {row["name"]: row["sha256"] for row in header["sources"]}
            for name in ("texture.cache.hdr.rtc", "texture.cache.data.rtc"):
                self.assertEqual(sources[name], sha256((root / name).read_bytes()).hexdigest())
            shared = export_public_layout(manifest, root / "Share.zip")
            with ZipFile(shared) as archive:
                self.assertNotIn(binding["file"], archive.namelist())
                self.assertNotIn(b"texture.cache", archive.read("manifest.json"))

            cache = root / "texture.cache.data.rtc"
            raw = cache.read_bytes()
            # Keep the file size and range valid while spoiling the stream bytes.
            cache.write_bytes(raw[:-1] + bytes((raw[-1] ^ 1,)))
            manifest = convert_unreal(source, root / "Intakes")
            fallback, _, rows = load_intake(manifest)
            record = json.loads(manifest.with_name(TEXTURE_INDEX).read_bytes())[model]["0"]
            self.assertEqual((record["source"], record["size"]), ("MeshColor.upk", [2, 2]))
            self.assertTrue(any("RTC cache could not be used" in row["reason"] for row in rows))
            self.assertFalse(any(row["name"].endswith(".rtc") for row in fallback["sources"]))

    def test_ascend_direct_diffuse_uses_parent_parameter_and_base_default(self):
        from PIL import Image

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "ascend", static_mesh="companion", mesh_artwork=True, ascend_shader="direct")
            write_ascend_rtc(root)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, _ = load_intake(manifest)
            model, = header["interiors"].values()
            index = json.loads(manifest.with_name(TEXTURE_INDEX).read_bytes())[model]
            self.assertEqual((index["0"]["source"], index["0"]["size"]), ("texture.cache.data.rtc", [8, 8]))
            self.assertEqual((index["1"]["source"], index["1"]["size"]), ("MeshColor.upk", [2, 2]))
            for slot, pixel in (("0", (241, 80, 12, 255)), ("1", (36, 140, 9, 255))):
                with Image.open(manifest.with_name(index[slot]["file"])) as image:
                    self.assertEqual(image.getpixel((0, 0)), pixel)

    def test_ascend_reports_cycles_and_nondirect_diffuse(self):
        for mode, reason in (("cycle", "parent chain contains a cycle"), ("masked-output", "not a direct RGB texture sample")):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "ascend", static_mesh="companion", mesh_artwork=True, ascend_shader=mode)
                manifest = convert_unreal(source, root / "Intakes")
                _, _, rows = load_intake(manifest)
                self.assertTrue(any(reason in row["reason"] for row in rows))

    def test_world_bsp_exports_distinct_visual_and_collision_sections(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", native_terrain=True, bsp_world=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, _, rows = load_intake(manifest)
            reference, = header["interiors"]
            name = header["interiors"][reference]
            geometry, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
            self.assertEqual(reference, f"unrealmesh:bsp.{geometry['source']['export']}")
            self.assertEqual(geometry["materials"], ["Fixture.FixtureStone", "Fixture.FixtureGlass"])
            visible = {geometry["vertices_m"][i][2] for row in geometry["triangles"] for i in row[1:4]}
            solid = {geometry["vertices_m"][i][2] for row in geometry["collision"]["triangles"] for i in row}
            self.assertEqual(visible, {7, 7.7})
            self.assertEqual(solid, {7, 8.1})
            self.assertEqual(geometry["triangles"][0][4:], [0, 0, 64, 16, 64, 0])
            self.assertTrue(any(row.get("model_reference") == reference for row in rows))
            share = export_public_layout(manifest, root / "Share.zip")
            public, _, _ = load_intake(unpack_intake(share, root / "Public"))
            self.assertNotIn("interiors", public)
            self.assertNotIn(name, public["files"])

    def test_vengeance_uses_cached_coordinates_and_preserves_deliberate_edge_turns(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "vengeance", native_terrain=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(header["minimap"]["image_bounds"], [[-2000, -2000], [6176, 2080]])
            self.assertEqual(terrain["origin_cm"], [-2000, -2000, 0])
            self.assertEqual(terrain["tile_grid"], [[0, 1]])
            self.assertEqual([r["name"] for r in rows if r["status"] == "terrain"], ["Terrain_1"])
            self.assertEqual(len([r for r in rows if r["status"] == "marker"]), 4)
            tiled = TiledTerrain(terrain)
            self.assertAlmostEqual(tiled.sample(256, 32), 0.32)
            self.assertAlmostEqual(tiled.sample(257, 33), 1.17)
            self.assertEqual(tiled.mesh_block(0, 0)["turned_quads"], [])
            self.assertEqual(tiled.mesh_block(1, 0)["turned_quads"], [32 * 256])
            self.assertIn(255 + 10 * 256, tiled.blocks[0]["holes"])
            self.assertIn(254 + 3 * 256, tiled.blocks[1]["holes"])
            self.assertIn(255 + 3 * 256, tiled.blocks[1]["holes"])
            provenance = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))["native_terrain"]
            self.assertEqual(provenance["height_texture"], "HeightTexture")
            self.assertEqual(provenance["height_midpoint"], 32767)

    def test_native_ascend_ground_preserves_seam_extents_holes_and_public_layout(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "ascend", native_terrain=True)
            manifest = convert_unreal(source, root / "Intakes")
            header, terrain, rows = load_intake(manifest)
            self.assertEqual(len(header["sources"]), 2)
            self.assertEqual(header["minimap"]["image_bounds"], [[-2000, -2000], [2128, 2096]])
            self.assertEqual(terrain["origin_cm"], [-2000, -2000, -600])
            self.assertEqual(terrain["step_cm"], 16)
            self.assertEqual(terrain["y_sign"], 1)
            self.assertEqual(terrain["tile_grid"], [[0, 1]])
            self.assertEqual([row["name"] for row in rows if row["status"] == "terrain"], ["PersistentLevel.Terrain_1"])
            tiled = TiledTerrain(terrain)
            self.assertAlmostEqual(tiled.sample(256, 32), 5.64)
            self.assertAlmostEqual(tiled.sample(258, 256), 10.18)
            self.assertEqual(tiled.sample(258, 256), tiled.sample(512, 256))
            self.assertIn(10 * 256 + 255, tiled.blocks[0]["holes"])
            self.assertIn(3 * 256 + 1, tiled.blocks[1]["holes"])
            self.assertNotIn(3 * 256, tiled.blocks[1]["holes"])
            self.assertIn(3 * 256 + 2, tiled.blocks[1]["holes"])
            provenance = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))["native_terrain"]
            self.assertEqual(provenance["vertices"], [259, 257])
            self.assertAlmostEqual(provenance["metres_per_height_unit"], 0.01)
            public = export_public_layout(manifest, root / "Public.zip")
            with ZipFile(public) as archive:
                self.assertNotIn(UNREAL_SCENE, archive.namelist())
            _, flat, _ = load_intake(unpack_intake(public, root / "Public"))
            self.assertEqual(flat["tile_grid"], [[0, 0]])
            self.assertEqual(set(flat["terrain_blocks"][0]["height_m"]), {0})

    def test_unrecognised_terrain_patch_flags_require_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = binary_draft_fixture(root, "ascend", native_terrain=True, bad_terrain_flag=True)
            with self.assertRaisesRegex(ValueError, "cannot interpret"):
                convert_unreal(source, root / "Intakes")

    def test_both_profiles_build_editable_intakes_with_object_provenance(self):
        for game in ("vengeance", "ascend"):
            with self.subTest(game=game), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, game)
                manifest = convert_unreal(source, root / "Intakes")
                header, _, rows = load_intake(manifest)
                scene = json.loads(manifest.with_name(UNREAL_SCENE).read_text(encoding="utf-8"))
                self.assertEqual(scene["reader"], "legacy-unreal-package-v1")
                self.assertEqual(scene["coverage"]["roles"]["flag"], 2)
                self.assertEqual(header["sources"][0]["sha256"], sha256(source.read_bytes()).hexdigest())
                markers = [item for item in rows if item["status"] == "marker"]
                self.assertEqual(len(markers), 4)
                spawn = next(row for row in markers if row["name"] == "BlueSpawn")
                self.assertEqual(spawn["position_cm"], [-200, 400, 300])
                self.assertAlmostEqual(spawn["yaw"], 90)
                deck = next(row for row in rows if row["name"] == "Deck")
                self.assertEqual(deck["model_reference"], "unrealmesh:fixture.originalmesh")
                self.assertEqual(deck["transform"]["scale"], [2, 1, 2])
                self.assertEqual(deck["transform"]["position_cm"], [60, 160, 400])
                nodes = scene["scene"][0]["entries"][0]["entries"]
                deck_node = next(node for node in nodes if node["export_index"] == 6)
                self.assertEqual(deck_node["object_path"], "Deck" if game == "vengeance" else "PersistentLevel.Deck")
                self.assertEqual(deck_node["tags"][-1]["name"], "Damage")
                self.assertEqual(deck_node["entries"][-1]["property_issues"], [])
                review = next(row for row in rows if row["name"] == "Unmapped")
                self.assertEqual(review["status"], "review")

    def test_truncated_and_attached_flag_exports_cannot_become_ctf_markers(self):
        for option in ("corrupt", "attached"):
            with self.subTest(option=option), TemporaryDirectory() as directory:
                root = Path(directory)
                source = binary_draft_fixture(root, "ascend", **{option: True})
                scene = read_binary_scene(source, "ascend", 50)
                flag = next(actor for actor in scene["actors"] if actor["name"] == "BlueFlag")
                if option == "corrupt":
                    self.assertIsNone(flag["transform"])
                    self.assertTrue(any("beyond" in issue for issue in flag["review"]))
                else:
                    self.assertEqual(flag["references"]["base"], "PersistentLevel.Deck")
                    self.assertEqual(flag["links"]["base"], 6)
                with self.assertRaisesRegex(ValueError, "independent actor transform"):
                    convert_unreal(source, root / "Intakes")
