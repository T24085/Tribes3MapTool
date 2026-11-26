"""LZO1X decoding and Ascend texture file cache (.tfc) reads, using original fixtures only."""

from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
import zlib

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from ascend_materials import bulk_range
from ascend_texture_cache import AscendTextureCache
from unreal_lzo import decompress
from unreal_package_reader import Cursor, decode_chunk

TAG = 0x9e2a83c1
END = b"\x11\x00\x00"


def chunk(data, codec, block_size=64):
    blocks = [data[i:i + block_size] for i in range(0, len(data), block_size)]
    if codec == 2:
        payloads = [bytes([17 + len(block)]) + block + END for block in blocks]
    else:
        payloads = [zlib.compress(block) for block in blocks]
    return (struct.pack("<I3i", TAG, block_size, sum(map(len, payloads)), len(data))
            + b"".join(struct.pack("<ii", len(p), len(b)) for b, p in zip(blocks, payloads)) + b"".join(payloads))


class LzoTests(unittest.TestCase):
    def test_literal_run_and_end_marker(self):
        self.assertEqual(decompress(bytes([20]) + b"abc" + END, 3), b"abc")

    def test_overlapping_m3_match_repeats_a_pattern(self):
        # "abc" literal, then an M3 match of 9 bytes at distance 3.
        stream = bytes([20]) + b"abc" + bytes([32 | 7, 2 << 2, 0]) + END
        self.assertEqual(decompress(stream, 12), b"abc" * 4)

    def test_m2_match_trailing_literal_and_short_m1_match(self):
        # "abcd", an M2 match (distance 4, length 4) carrying one literal "z",
        # then a two-byte M1 match at distance 2.
        stream = bytes([21]) + b"abcd" + bytes([0x6D, 0]) + b"z" + bytes([4, 0]) + END
        self.assertEqual(decompress(stream, 11), b"abcdabcdzdz")

    def test_rejects_corrupt_streams(self):
        cases = {
            "before the block": bytes([20]) + b"abc" + bytes([32 | 7, 40 << 2, 0]) + END,
            "Truncated": bytes([20]) + b"ab",
            "exceeds": bytes([20]) + b"abc" + END,
            "after the LZO end marker": bytes([20]) + b"abc" + END + b"x",
        }
        expected = {"exceeds": 2}
        for message, stream in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                decompress(stream, expected.get(message, 3))

    def test_package_chunks_decode_with_either_codec(self):
        data = bytes(range(256)) * 2
        for codec in (1, 2):
            with self.subTest(codec=codec):
                raw = chunk(data, codec)
                self.assertEqual(decode_chunk(raw, 0, len(raw), len(data), codec), data)


class TextureFileCacheTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.cooked = Path(scratch.name) / "CookedPC"
        self.map_folder = self.cooked / "Maps" / "Fixture"
        self.map_folder.mkdir(parents=True)

    def test_bulk_headers_classify_inline_cache_and_unused_mips(self):
        inline = struct.pack("<I3i", 0, 4, 4, 16) + b"DATA"
        cursor = Cursor(inline, 0)
        self.assertEqual(bulk_range(cursor), ("inline", 16, 4, 0, 4))
        cached = Cursor(struct.pack("<I3i", 0x11, 512, 90, 1234))
        self.assertEqual(bulk_range(cached), ("cache", 1234, 90, 2, 512))
        unused = Cursor(struct.pack("<I3i", 0x21, 0, -1, -1))
        self.assertIsNone(bulk_range(unused))

    def test_reads_lzo_and_zlib_mips_from_a_cache_in_the_cooked_folder(self):
        pixels = bytes(range(128)) * 4
        lzo, deflated = chunk(pixels, 2), chunk(pixels, 1)
        (self.cooked / "Textures.tfc").write_bytes(b"pad" + lzo + deflated)
        cache = AscendTextureCache(self.map_folder)
        self.assertEqual(cache.read_external("Textures", 3, len(lzo), 2, len(pixels)), pixels)
        self.assertEqual(cache.read_external("Textures", 3 + len(lzo), len(deflated), 1, len(pixels)), pixels)
        self.assertEqual(cache.cache_receipt("Textures")["name"], "Textures.tfc")

    def test_missing_cache_and_out_of_range_reads_are_reported(self):
        cache = AscendTextureCache(self.map_folder)
        with self.assertRaisesRegex(ValueError, "Place Textures.tfc"):
            cache.read_external("Textures", 0, 16, 2, 16)
        (self.cooked / "Textures.tfc").write_bytes(b"x" * 32)
        with self.assertRaisesRegex(ValueError, "outside Textures.tfc"):
            AscendTextureCache(self.map_folder).read_external("Textures", 24, 16, 0, 16)
        with self.assertRaisesRegex(ValueError, "Invalid Ascend texture file cache name"):
            cache.read_external("../Textures", 0, 16, 0, 16)


if __name__ == "__main__":
    unittest.main()
