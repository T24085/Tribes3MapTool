"""Sound packs: game pack and map override layout, using original fixtures only (issue #7)."""

import json
import os
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from test_unreal_package_reader import build_package

import sound_packs
from sound_packs import asset_path, build_map_override, build_pack, category, find_decoder, relative_file
from unreal_package_reader import inspect_package

STRINGS = ("None", "Core", "Package", "Class", "SoundNodeWave", "SoundCue", "FloatProperty", "ObjectProperty",
           "Duration", "FirstNode", "AUD_ENV_Swamp", "Chirp", "Croak", "A_Cue_Chirp")
FAKE_OGG = {"Chirp": b"OggS" + b"chirp" * 8, "Croak": b"OggS" + b"croak" * 8}


def pack(form, *values):
    return struct.pack("<" + form, *values)


def name(value):
    return pack("ii", STRINGS.index(value), 0)


def bulk(data=b"", offset=0):
    return pack("I3i", 0, len(data), len(data), offset) + data


def sound_package(waves, cue=True):
    """A UE3 Ascend package with AUD_ENV_Swamp.<wave> SoundNodeWaves and an optional cue."""

    def wave_payload(ogg, start):
        head = pack("i", -1) + name("Duration") + name("FloatProperty") + pack("ii", 4, 0) + pack("f", 1.5)
        head += name("None") + bulk(offset=start)
        data_at = start + len(head) + 16
        return head + bulk(ogg, data_at) + bulk(offset=data_at + len(ogg)) + bulk(offset=data_at + len(ogg))

    imports = [("Core", "Package", 0, "Core"), ("Core", "Class", -1, "SoundNodeWave"),
               ("Core", "Class", -1, "SoundCue")]

    def exports(starts):
        rows = [(0, 0, "AUD_ENV_Swamp", 0, b"", 3)]
        for index, wave in enumerate(waves):
            rows.append((-2, 1, wave, 0, wave_payload(FAKE_OGG[wave], starts.get(index + 2, 0)), 3))
        if cue:
            first = pack("i", -1) + name("FirstNode") + name("ObjectProperty") + pack("ii", 4, 0) + pack("i", 2)
            rows.append((-3, 1, "A_Cue_Chirp", 0, first + name("None"), 3))
        return rows

    def build(starts):
        return build_package("ascend", STRINGS, imports, exports(starts))

    return build, imports


def write_package(path, waves, cue=True):
    build, _ = sound_package(waves, cue)
    path.write_bytes(build({}))
    starts = {row["index"]: row["serial_offset"] for row in inspect_package(path, "ascend")["exports"]}
    path.write_bytes(build(starts))
    return path


class CopyDecoder:
    name = "copy"

    def receipt(self):
        return dict(name=self.name, version="test")

    def to_wav(self, ogg, wav):
        wav.write_bytes(b"RIFF" + ogg.read_bytes())


class SoundPackTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.cooked = self.root / "CookedPC"
        (self.cooked / "Maps" / "Swamp").mkdir(parents=True)

    def test_layout_names_and_categories(self):
        self.assertEqual(category("AUD_ENV_Tatarus.Birds.A_CUE_Bird"), "env")
        self.assertEqual(category("TribesGame.Effects.Sound"), "misc")
        self.assertEqual(asset_path("/Game/Community/Liquids2026/TA", "AUD_WEP.Spinfusor.Fire"),
                         "/Game/Community/Liquids2026/TA/AUD_WEP/Spinfusor/SW_Fire")
        self.assertEqual(relative_file("AUD_WEP.Spin fusor.Fire", ".wav"), Path("AUD_WEP", "Spin_fusor", "Fire.wav"))
        with self.assertRaisesRegex(ValueError, "Unusable"):
            relative_file("..", ".wav")

    def test_game_pack_reads_shared_packages_only(self):
        write_package(self.cooked / "Shared.upk", ["Chirp"])
        write_package(self.cooked / "Maps" / "Swamp" / "Swamp_Sound.fmap", ["Croak"])
        manifest = build_pack(self.cooked, self.root / "Out", "TA", decoder=CopyDecoder())
        result = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(result["kind"], "pack")
        self.assertEqual([row["object_path"] for row in result["waves"]], ["AUD_ENV_Swamp.Chirp"])
        wave, = result["waves"]
        self.assertEqual(wave["asset"], "/Game/Community/Liquids2026/TA/AUD_ENV_Swamp/SW_Chirp")
        self.assertEqual((manifest.parent / wave["ogg"]).read_bytes(), FAKE_OGG["Chirp"])
        self.assertEqual(wave["properties"]["Duration"], 1.5)
        event, = result["events"]
        self.assertEqual((event["id"], event["category"]), ("AUD_ENV_Swamp.A_Cue_Chirp", "env"))
        self.assertEqual(event["graph"]["asset"], wave["asset"])
        self.assertEqual(result["coverage"], dict(waves=1, decoded=1, events=1, issues=0))
        sums = (manifest.parent / "SHA256SUMS.txt").read_text(encoding="utf-8")
        self.assertIn("AUD_ENV_Swamp/Chirp.wav", sums)
        self.assertIn("pack.json", sums)

    def test_map_override_keeps_only_sounds_the_pack_lacks(self):
        write_package(self.cooked / "Shared.upk", ["Chirp"])
        level = write_package(self.cooked / "Maps" / "Swamp" / "Swamp_Sound.fmap", ["Chirp", "Croak"])
        manifest = build_pack(self.cooked, self.root / "Out", "TA", decoder=CopyDecoder())
        override = build_map_override([level], manifest, self.root / "Out", "SwampTA2026",
                                      units_per_metre=50, decoder=CopyDecoder())
        result = json.loads(override.read_text(encoding="utf-8"))
        self.assertEqual(result["kind"], "map-override")
        self.assertEqual([row["object_path"] for row in result["waves"]], ["AUD_ENV_Swamp.Croak"])
        self.assertEqual(result["waves"][0]["asset"], "/Game/Community/SwampTA2026/Sounds/AUD_ENV_Swamp/SW_Croak")
        self.assertEqual(result["events"], [])  # The cue already exists in the game pack.
        self.assertEqual(result["scale"]["cm_per_source_unit"], 2)
        self.assertEqual(override.parent, self.root / "Out" / "maps" / "SwampTA2026")
        with self.assertRaisesRegex(ValueError, "map id"):
            build_map_override([level], manifest, self.root / "Out", "../Escape", decoder=CopyDecoder())

    def test_decoder_selection_prefers_configured_ffmpeg_and_explains_missing_tools(self):
        with mock.patch.dict(os.environ, {"T3_MAP_TOOL_FFMPEG": str(self.root / "missing.exe")}):
            with self.assertRaisesRegex(ValueError, "ffmpeg was not found"):
                find_decoder()
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(sound_packs, "SoundfileDecoder", side_effect=ImportError), \
                mock.patch.object(sound_packs.shutil, "which", return_value=None):
            os.environ.pop("T3_MAP_TOOL_FFMPEG", None)
            with self.assertRaisesRegex(ValueError, "optional soundfile package"):
                find_decoder()


if __name__ == "__main__":
    unittest.main()
