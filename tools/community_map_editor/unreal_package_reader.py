"""Inventory native Vengeance and Ascend packages without loading the game."""

import argparse
from bisect import bisect_right
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import json
import mmap
import os
from pathlib import Path
import struct
import zlib


PACKAGE_TAG = 0x9E2A83C1
MAX_RECORDS = 250_000
MAX_CHUNK_BYTES = 64 * 1024 * 1024
MAX_NAMES = 1024
MAX_PATH_DEPTH = 64
MAX_PATH_CHARACTERS = 32 * 1024 * 1024
GAMES = {".tvm": "vengeance", ".pkg": "vengeance", ".fmap": "ascend", ".udk": "ascend"}


class Cursor:
    """Read a bounded interval from the logical package byte stream."""

    def __init__(self, stream, offset=0, limit=None, endian="<"):
        self.stream = stream
        self.offset = offset
        self.limit = len(stream) if limit is None else limit
        self.endian = endian

    def take(self, size):
        end = self.offset + size
        if size < 0 or self.offset < 0 or end > self.limit:
            raise ValueError("Package record runs beyond its containing section.")
        data = self.stream[self.offset:end]
        if len(data) != size:
            raise ValueError("Truncated package record.")
        self.offset = end
        return data

    def fields(self, form):
        layout = self.endian + form
        return struct.unpack(layout, self.take(struct.calcsize(layout)))

    def count(self, maximum=MAX_RECORDS):
        count, = self.fields("i")
        if not 0 <= count <= maximum:
            raise ValueError("Invalid package table or array count.")
        return count

    def index(self, modern):
        if modern:
            return self.fields("i")[0]
        first = self.take(1)[0]
        value, more = first & 0x3f, first & 0x40
        for shift in (6, 13, 20, 27):
            if not more:
                break
            byte = self.take(1)[0]
            value |= (byte & 0x7f) << shift
            more = byte & 0x80
        if more or value > 0x7fffffff:
            raise ValueError("Invalid compact package index.")
        return -value if first & 0x80 else value

    def string(self, modern):
        count = self.index(modern)
        if abs(count) > MAX_NAMES:
            raise ValueError("Oversized package string.")
        if not count:
            return ""
        if count > 0:
            data = self.take(count)
            terminator, encoding = b"\0", "cp1252"
        else:
            data = self.take(-count * 2)
            terminator = b"\0\0"
            encoding = "utf-16-le" if self.endian == "<" else "utf-16-be"
        if not data.endswith(terminator):
            raise ValueError("Unterminated package string.")
        name = data[:-len(terminator)].decode(encoding, errors="replace")
        if "\0" in name or (name and not name.isprintable()):
            raise ValueError("Invalid character in package string.")
        return name


class CompressedPackage:
    """Translate logical UE3 offsets while retaining only one inflated chunk."""

    def __init__(self, raw, chunks, header_end, endian):
        self.raw, self.endian = raw, endian
        self.chunks = sorted(chunks)
        self.starts = [row[0] for row in self.chunks]
        self.prefix = self.starts[0]
        self.end = self.chunks[-1][0] + self.chunks[-1][1]
        previous = self.prefix
        if self.prefix < header_end or self.end > 0x7fffffff:
            raise ValueError("Invalid logical compressed package extent.")
        compressed_end = self.prefix
        for offset, size, start, stored in self.chunks:
            if (offset != previous or not 0 < size <= MAX_CHUNK_BYTES or not 16 <= stored <= MAX_CHUNK_BYTES
                    or start < compressed_end or start + stored > len(raw)):
                raise ValueError("Invalid or overlapping compressed package chunks.")
            previous, compressed_end = offset + size, start + stored
        self.cached_offset = None
        self.cached = b""

    def __len__(self):
        return self.end

    def __getitem__(self, region):
        start, stop = region.start, region.stop
        if start >= stop:
            return b""
        parts = []
        if start < self.prefix:
            end = min(stop, self.prefix)
            parts.append(self.raw[start:end])
            start = end
        while start < stop:
            offset, size, physical, stored = self.chunks[bisect_right(self.starts, start) - 1]
            if offset != self.cached_offset:
                self.cached = self.inflate(physical, stored, size)
                self.cached_offset = offset
            end = min(stop, offset + size)
            parts.append(self.cached[start - offset:end - offset])
            start = end
        return b"".join(parts)

    def inflate(self, offset, stored, expected):
        cursor = Cursor(self.raw, offset, offset + stored, self.endian)
        tag, block_size, packed, size = cursor.fields("Iiii")
        if tag != PACKAGE_TAG or not 0 < block_size <= MAX_CHUNK_BYTES or size != expected or packed <= 0:
            raise ValueError("Invalid Unreal zlib chunk header.")
        blocks = (size + block_size - 1) // block_size
        if blocks > 4096:
            raise ValueError("Too many blocks in an Unreal zlib chunk.")
        pairs = [cursor.fields("ii") for _ in range(blocks)]
        if packed != sum(count for count, _ in pairs):
            raise ValueError("Invalid Unreal zlib block inventory.")
        if cursor.offset + packed != cursor.limit:
            raise ValueError("Unreal chunk bytes do not match the block inventory.")
        decoded = bytearray()
        for count, wanted in pairs:
            if count <= 0 or wanted != min(block_size, size - len(decoded)):
                raise ValueError("Invalid Unreal zlib block dimensions.")
            stream = zlib.decompressobj()
            try:
                block = stream.decompress(cursor.take(count), wanted + 1)
            except zlib.error as error:
                raise ValueError("Could not decode an Unreal zlib block.") from error
            if len(block) != wanted or not stream.eof or stream.unused_data:
                raise ValueError("Unexpected Unreal zlib output size.")
            decoded.extend(block)
        if len(decoded) != expected or cursor.offset != cursor.limit:
            raise ValueError("Unreal zlib chunk did not match its recorded length.")
        return decoded


def title_layout(version, licensee):
    if version == 805 and licensee in (0, 2):
        return "ascend"
    if ((version in (129, 130) and 0x17 <= licensee <= 0x1b)
            or (version == 123 and 3 <= licensee <= 0x0f)
            or (version == 126 and 0x12 <= licensee <= 0x17)):
        return "vengeance"
    raise ValueError(f"No supported Vengeance or Ascend package layout for version {version}/{licensee}.")


def package_summary(raw, game):
    endian = ">" if raw[:4] == struct.pack(">I", PACKAGE_TAG) else "<"
    cursor = Cursor(raw, endian=endian)
    tag, version_word = cursor.fields("II")
    if tag != PACKAGE_TAG:
        raise ValueError("This is not a recognized native Unreal package header.")
    if version_word in (PACKAGE_TAG, 0x20000):
        raise ValueError("An entirely compressed package needs an unwrapped package copy.")
    version, licensee = version_word & 0xffff, version_word >> 16
    layout = title_layout(version, licensee)
    if game != layout:
        raise ValueError("The selected game does not match the package version.")
    modern = game == "ascend"
    header_size = cursor.fields("i")[0] if modern else None
    group = cursor.string(True) if modern else ""
    flags, = cursor.fields("I")
    tables = {}
    for key in ("names", "exports", "imports"):
        tables[key] = cursor.count(), cursor.fields("i")[0]
    if sum(count for count, _ in tables.values()) > MAX_RECORDS:
        raise ValueError("Package has too many metadata records.")
    if modern:
        cursor.take(20)  # Dependencies and import/export GUID table descriptors.
    guid = cursor.fields("4I")
    generation_count = cursor.count(4096)
    cursor.take(generation_count * (12 if modern else 8))
    codec = 0
    chunks = []
    if modern:
        cursor.take(8)  # Engine and cooker build numbers.
        codec, = cursor.fields("I")
        chunks = [cursor.fields("4i") for _ in range(cursor.count(8192))]
        cursor.take(4)  # Package source.
    if chunks and codec != 1:
        raise ValueError(f"Package chunks use unsupported compression flags {codec:#x}.")
    stream = CompressedPackage(raw, chunks, cursor.offset, endian) if chunks else raw
    if header_size is not None and not cursor.offset <= header_size <= len(stream):
        raise ValueError("Invalid UE3 header size.")
    first = cursor.offset
    last = header_size if header_size is not None else len(stream)
    active = [offset for count, offset in tables.values() if count]
    if (len(active) != len(set(active)) or any(not first <= offset < last for offset in active)
            or not tables["names"][0]
            or any(offset < 0 or offset > len(stream) for count, offset in tables.values() if not count)):
        raise ValueError("Invalid or overlapping package table starts.")
    sections = {}
    for key, (count, offset) in tables.items():
        end = min((position for position in active if position > offset), default=last)
        sections[key] = count, Cursor(stream, offset, end, endian)
    record = dict(game=game, version=version, licensee_version=licensee, endian=endian,
                  flags=flags, group=group, guid="".join(f"{part:08x}" for part in guid),
                  summary_bytes=first,
                  logical_bytes=len(stream), compression="zlib" if chunks else "none",
                  compressed_chunks=len(chunks),
                  tables={kind: dict(count=count, offset=offset) for kind, (count, offset) in tables.items()})
    return record, sections, stream


def table_name(cursor, names, modern):
    index = cursor.index(modern)
    number = cursor.fields("i")[0] if modern else 0
    if not 0 <= index < len(names) or not 0 <= number <= 0x7fffffff:
        raise ValueError("Object table references an invalid package name.")
    name = names[index]
    return name if not number else f"{name}_{number - 1}"


def read_tables(summary, sections):
    modern = summary["game"] == "ascend"
    count, cursor = sections["names"]
    names = []
    for _ in range(count):
        names.append(cursor.string(modern))
        cursor.take(8 if modern else 4)
    if any(not name for name in names):
        raise ValueError("Package name table contains an empty name.")
    imports = []
    count, cursor = sections["imports"]
    for index in range(1, count + 1):
        package = table_name(cursor, names, modern)
        kind = table_name(cursor, names, modern)
        outer, = cursor.fields("i")
        object_name = table_name(cursor, names, modern)
        imports.append(dict(index=-index, name=object_name, class_name=kind, class_package=package, outer=outer))
    exports = []
    count, cursor = sections["exports"]
    for index in range(1, count + 1):
        class_index, super_index = cursor.index(modern), cursor.index(modern)
        outer, = cursor.fields("i")
        object_name = table_name(cursor, names, modern)
        archetype = cursor.index(True) if modern else 0
        flags, = cursor.fields("I")
        if modern:
            flags = (flags << 32) | cursor.fields("I")[0]  # UE3 stores the high word first.
        size = cursor.index(modern)
        offset = cursor.index(modern) if modern or size else 0
        export_flags = 0
        if modern:
            export_flags, = cursor.fields("I")
            cursor.take(cursor.count(4096) * 4 + 20)  # Net generations, GUID and package flags.
        if size < 0 or offset < 0 or offset + size > summary["logical_bytes"]:
            raise ValueError("Export payload lies outside the logical package.")
        exports.append(dict(index=index, name=object_name, class_index=class_index, super_index=super_index,
                            outer=outer, archetype=archetype, flags=flags, export_flags=export_flags,
                            serial_bytes=size, serial_offset=offset))
    intervals = [(summary["tables"][key]["offset"], cursor.offset)
                 for key, (count, cursor) in sections.items() if count]
    for export in exports:
        if not export["serial_bytes"]:
            continue
        offset = export["serial_offset"]
        end = offset + export["serial_bytes"]
        if offset < summary["summary_bytes"] or any(offset < used and end > start for start, used in intervals):
            raise ValueError("Export payload overlaps the package header or an object table.")
    return names, imports, exports


def object_paths(imports, exports):
    objects = {row["index"]: row for row in imports + exports}
    for item in objects.values():
        for key in ("outer", "class_index", "super_index", "archetype"):
            value = item.get(key, 0)
            if value and value not in objects:
                raise ValueError("Package object references an invalid import or export index.")
    paths = {0: ()}

    def path(index):
        chain, visited = [], set()
        current = index
        while current not in paths:
            if current in visited or len(chain) >= MAX_PATH_DEPTH:
                raise ValueError("Package objects contain a cycle or excessive nesting.")
            visited.add(current)
            chain.append(current)
            current = objects[current]["outer"]
        while chain:
            child = chain.pop()
            paths[child] = (*paths[current], objects[child]["name"])
            if len(paths[child]) > MAX_PATH_DEPTH:
                raise ValueError("Package object path is too deep.")
            current = child
        return ".".join(paths[index])

    total_characters = 0
    for item in objects.values():
        object_path = path(item["index"])
        total_characters += len(object_path)
        if total_characters > MAX_PATH_CHARACTERS:
            raise ValueError("Package object paths exceed the report size limit.")
        item["object_path"] = object_path
    for item in exports:
        cls = item["class_index"]
        item["class_name"] = objects[cls]["name"] if cls else "Class"
        item["class_path"] = objects[cls]["object_path"] if cls else "Class"


@contextmanager
def open_package(path, game=None):
    path = Path(path)
    game = game if game is not None else GAMES.get(path.suffix.casefold())
    if game not in GAMES.values():
        raise ValueError("Select a native package and specify its Vengeance or Ascend profile.")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        size = before.st_size
        if not 8 <= size <= 0x7fffffff:
            raise ValueError("Native package size is outside the supported range.")
        try:
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as raw:
                summary, sections, stream = package_summary(raw, game)
                names, imports, exports = read_tables(summary, sections)
                object_paths(imports, exports)
                digest = sha256(raw).hexdigest()
                yield dict(format="legacy-unreal-package-catalog", version=1,
                           source=dict(name=path.name, bytes=size, sha256=digest), summary=summary, names=names,
                           imports=imports, exports=exports,
                           classes=dict(Counter(row["class_name"] for row in exports))), stream
        except struct.error as error:
            raise ValueError("Truncated or malformed native package metadata.") from error
        after = os.fstat(handle.fileno())
        if (before.st_mtime_ns, size) != (after.st_mtime_ns, after.st_size):
            raise ValueError("The package changed while reading its inventory.")


def inspect_package(path, game=None):
    with open_package(path, game) as (report, _):
        return report


def write_report(report, path):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--game", choices=sorted(set(GAMES.values())))
    args = parser.parse_args()
    try:
        report = inspect_package(args.source, args.game)
        write_report(report, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
