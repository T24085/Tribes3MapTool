"""Original UE2 and UE3 fixtures for native map package metadata."""

from hashlib import sha256
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
import zlib

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from unreal_package_reader import inspect_package

TAG = 0x9e2a83c1
STRINGS = ("None", "Core", "Package", "Class", "Gameplay", "MPFlag", "PersistentLevel", "Flag", "Café")


def compact(value):
    first = (abs(value) & 63) | (128 if value < 0 else 0)
    remaining = abs(value) >> 6
    output = bytearray([first | (64 if remaining else 0)])
    while remaining:
        byte = remaining & 127
        remaining >>= 7
        output.append(byte | (128 if remaining else 0))
    return output


def native_fixture(game, *, compressed=False, orphan=False, cycle=False, bad_version=False, wide=False):
    return build_package(game, STRINGS,
                         [("Core", "Package", 0, "Gameplay"), ("Core", "Class", -1, "MPFlag")],
                         [(0, 2 if cycle else 0, "PersistentLevel", 0, b"", 3),
                          (-9 if orphan else -2, 1, "Flag", 2, b"data", 3)],
                         compressed=compressed, bad_version=bad_version, wide=wide)


def build_package(game, strings, imported_objects, exported_objects, *, compressed=False, bad_version=False,
                  wide=False, licensee=6, version=123, ascend_licensee=2):
    modern = game == "ascend"
    endian = ">" if wide else "<"

    def pack(form, *values):
        return struct.pack(endian + form, *values)

    def token(name, number=0):
        value = strings.index(name)
        return pack("ii", value, number) if modern else compact(value)

    def string(value):
        if value.isascii():
            data = value.encode("ascii") + b"\0"
            length = len(data)
        else:
            data = value.encode("utf-16-be" if wide else "utf-16-le") + b"\0\0"
            length = -len(data) // 2
        return (pack("i", length) if modern else compact(length)) + data

    def imported(package, cls, outer, name):
        return token(package) + token(cls) + pack("i", outer) + token(name)

    def exported(cls, outer, name, number, payload, flags, offset):
        size = len(payload)
        if modern:
            return (pack("iii", cls, 0, outer) + token(name, number)
                    + pack("iIIiiIi", -2 if cls else 0, 2, flags & ~2, size, offset, 0, 1)
                    + pack("6i", 0, 0, 0, 0, 0, 0))
        return (compact(cls) + compact(0) + pack("i", outer) + token(name)
                + pack("I", flags) + compact(size) + (compact(offset) if size else b""))

    prefix = 256
    names = b"".join(string(name) + b"\0" * (8 if modern else 4) for name in strings)
    imports = b"".join(imported(*row) for row in imported_objects)
    export_start = prefix + len(names) + len(imports)
    serial_start = export_start
    while True:
        table, cursor = bytearray(), serial_start
        for entry in exported_objects:
            size = len(entry[4])
            table.extend(exported(*entry, cursor if size else 0))
            cursor += size
        if serial_start == export_start + len(table):
            break
        serial_start = export_start + len(table)
    table_bytes = names + imports + table + b"".join(entry[4] for entry in exported_objects)
    version_word = (version | (licensee << 16)) if not modern else ((806 if bad_version else 805) | ascend_licensee << 16)
    header = pack("II", TAG, version_word)
    if modern:
        header += pack("i", serial_start) + string("FixtureMap")
    header += pack("I6i", 0, len(strings), prefix, len(exported_objects), export_start,
                   len(imported_objects), prefix + len(names))
    if modern:
        header += pack("5i", 0, 0, 0, 0, 0)
    header += pack("4Ii", 1, 2, 3, 4, 1)
    header += pack("2i", len(exported_objects), len(strings)) + (pack("i", 0) if modern else b"")
    if modern:
        header += pack("2i", 42, 3)
        if compressed:
            block_size = 256 if len(table_bytes) > 256 * 1024 else 64
            blocks = [table_bytes[i:i + block_size] for i in range(0, len(table_bytes), block_size)]
            payloads = [zlib.compress(block) for block in blocks]
            packed = (pack("I3i", TAG, block_size, sum(map(len, payloads)), len(table_bytes))
                      + b"".join(pack("ii", len(data), len(block)) for block, data in zip(blocks, payloads))
                      + b"".join(payloads))
            header += pack("2I4i", 1, 1, prefix, len(table_bytes), prefix, len(packed))
            table_bytes = packed
        else:
            header += pack("2I", 0, 0)
        header += pack("I", 0)
    return header.ljust(prefix, b"\0") + table_bytes


class UnrealPackageReaderTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def write(self, game, data):
        output = self.root / ("Model.pkg" if game == "vengeance" else "Level.fmap")
        output.write_bytes(data)
        return output

    def test_vengeance_resolves_compact_class_links_and_export_payloads(self):
        data = native_fixture("vengeance")
        report = inspect_package(self.write("vengeance", data))
        self.assertEqual(report["source"]["sha256"], sha256(data).hexdigest())
        self.assertEqual(report["summary"]["version"], 123)
        self.assertEqual(report["names"][-1], "Café")
        level, flag = report["exports"]
        self.assertEqual(level["class_name"], "Class")
        self.assertEqual(flag["object_path"], "PersistentLevel.Flag")
        self.assertEqual(flag["class_path"], "Gameplay.MPFlag")
        self.assertEqual(data[flag["serial_offset"]:flag["serial_offset"] + flag["serial_bytes"]], b"data")

    def test_ascend_zlib_offsets_and_numbered_names_optionally_swap_byte_order(self):
        for wide in (False, True):
            with self.subTest(wide=wide):
                raw = native_fixture("ascend", compressed=True, wide=wide)
                result = inspect_package(self.write("ascend", raw))
                flag = result["exports"][1]
                self.assertEqual(result["summary"]["compression"], "zlib")
                self.assertEqual(result["summary"]["group"], "FixtureMap")
                self.assertEqual(result["names"][-1], "Café")
                self.assertEqual(flag["object_path"], "PersistentLevel.Flag_1")
                self.assertEqual(flag["flags"], (2 << 32) + 1)
                self.assertEqual(flag["archetype"], -2)
                self.assertEqual(result["classes"], {"Class": 1, "MPFlag": 1})
        raw = native_fixture("ascend")
        vanilla = inspect_package(self.write("ascend", raw))
        self.assertEqual(vanilla["summary"]["compression"], "none")

    def test_rejects_wrong_versions_references_cycles_and_truncated_compression(self):
        with self.assertRaisesRegex(ValueError, "Invalid|invalid"):
            inspect_package(self.write("ascend", native_fixture("ascend", orphan=True)))
        with self.assertRaisesRegex(ValueError, "cycle"):
            inspect_package(self.write("vengeance", native_fixture("vengeance", cycle=True)))
        with self.assertRaisesRegex(ValueError, "version 806/2"):
            inspect_package(self.write("ascend", native_fixture("ascend", bad_version=True)))
        raw = native_fixture("ascend", compressed=True)
        with self.assertRaisesRegex(ValueError, "compressed package chunks"):
            inspect_package(self.write("ascend", raw[:-5]))
        with self.assertRaisesRegex(ValueError, "recognized native Unreal package"):
            inspect_package(self.write("ascend", bytes(40)))
