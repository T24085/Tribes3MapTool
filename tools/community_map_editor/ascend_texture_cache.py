"""Read the original Ascend RTC catalog and its zlib-compressed texture data."""

from functools import cached_property
from hashlib import sha256
import os
from pathlib import Path
import zlib

from model_textures import MAX_SIDE
from unreal_package_reader import Cursor
from vengeance_terrain_layers import COLOR_FORMATS


CATALOG = "texture.cache.hdr.rtc"
DATA = "texture.cache.data.rtc"
RTC_FORMAT = {2: 5, 5: 3, 6: 7, 7: 8}  # UE3 pixel formats to the shared color decoder.
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_PACKED_BYTES = MAX_SIDE * MAX_SIDE * 4 + 1024 * 1024


def parse_catalog(raw):
    entries = {}
    cursor = Cursor(raw)
    for _ in range(cursor.count()):
        count = cursor.count(512)
        try:
            name = cursor.take(count).decode("cp1252").casefold()
        except UnicodeError as error:
            raise ValueError("The Ascend RTC catalog contains an invalid object path.") from error
        if not name.isprintable():
            raise ValueError("The Ascend RTC catalog contains an invalid object path.")
        fmt, marker, width, height = cursor.fields("2B2h")
        mip_count = cursor.count(24)
        first = cursor.fields("BI2i") if mip_count else None
        cursor.take(max(0, mip_count - 1) * 13)
        if name in entries:
            entries[name] = None
        else:
            entries[name] = (fmt, width, height, first) if marker == 2 else None
    if cursor.offset != cursor.limit:
        raise ValueError("Unexpected trailing data in the Ascend RTC catalog.")
    return entries


def identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


class AscendTextureCache:
    def __init__(self, directory):
        self.directory = directory
        self.available = any((directory / name).exists() for name in (CATALOG, DATA))
        self.data_receipt = None

    def companion(self, name):
        path = self.directory / name
        if not path.is_file() or path.is_symlink() or path.resolve().parent != self.directory:
            raise ValueError(f"Place {name} beside the Ascend map to recover cached mips.")
        return path

    @cached_property
    def catalog(self):
        try:
            with self.companion(CATALOG).open("rb") as file:
                original = identity(os.fstat(file.fileno()))
                raw = file.read(MAX_CATALOG_BYTES + 1)
                if len(raw) > MAX_CATALOG_BYTES:
                    raise ValueError("Ascend RTC catalog exceeds the supported size.")
                if identity(os.fstat(file.fileno())) != original:
                    raise ValueError("Ascend RTC catalog changed during import.")
            return parse_catalog(raw), dict(name=CATALOG, sha256=sha256(raw).hexdigest())
        except (OSError, ValueError) as error:
            return None, str(error)

    def read_mip(self, asset, owner, kind, minimum_area):
        entries, header = self.catalog
        if entries is None:
            raise ValueError(header)
        path = asset["object_path"]
        if not asset["outer"]:
            path = f'{Path(owner["source"]["name"]).stem}.{path}'
        key = path.casefold()
        if key not in entries:
            return None
        entry = entries[key]
        if entry is None:
            raise ValueError("The Ascend RTC object path has duplicate entries or an unsupported record.")
        fmt, width, height, first = entry
        if RTC_FORMAT.get(fmt) != kind or not first or not all(1 <= axis <= MAX_SIDE for axis in (width, height)):
            raise ValueError("The cached Ascend texture has an unsupported format or size.")
        if width * height < minimum_area:
            return None
        marker, offset, packed_size, unpacked_size = first
        stride, block = COLOR_FORMATS[kind]
        expected = ((width + stride - 1) // stride) * ((height + stride - 1) // stride) * block
        if marker != 1 or not 0 < packed_size <= MAX_PACKED_BYTES or unpacked_size != expected:
            raise ValueError("The Ascend RTC mip dimensions disagree with its byte count.")
        with self.companion(DATA).open("rb") as file:
            current = identity(os.fstat(file.fileno()))
            if offset + packed_size > current[2]:
                raise ValueError("The Ascend RTC mip lies outside its data file.")
            if self.data_receipt and self.data_receipt[0] != current:
                raise ValueError("Ascend RTC data changed during import.")
            file.seek(offset)
            packed = file.read(packed_size)
            decoder = zlib.decompressobj()
            pixels = decoder.decompress(packed, expected + 1)
            if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError("Invalid Ascend RTC zlib mip.")
            if self.data_receipt is None:
                digest = sha256()
                file.seek(0)
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
                receipt = dict(name=DATA, sha256=digest.hexdigest())
            else:
                receipt = self.data_receipt[1]
            if identity(os.fstat(file.fileno())) != current:
                raise ValueError("Ascend RTC data changed during import.")
            self.data_receipt = current, receipt
        return width, height, pixels, (header, receipt)
