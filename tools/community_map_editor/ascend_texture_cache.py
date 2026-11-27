"""Read Ascend texture caches: the RTC catalog pair and the shared .tfc texture file caches."""

from functools import cached_property
from hashlib import sha256
import os
from pathlib import Path
import zlib

from model_textures import MAX_SIDE
from unreal_package_reader import Cursor, decode_chunk
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


TFC_SEARCH_DEPTH = 3  # The map's folder, then up to CookedPC (Maps\<Map>\ -> Maps\ -> CookedPC\).
MAX_TFC_PAYLOAD = MAX_PACKED_BYTES


class AscendTextureCache:
    def __init__(self, directory):
        self.directory = directory
        self.available = any((directory / name).exists() for name in (CATALOG, DATA))
        self.data_receipt = None
        self.tfc = {}  # Cache name (casefolded) -> (path, file identity, receipt).

    def texture_file_cache(self, name):
        """Locate <name>.tfc beside the map or in a parent folder up to the cooked content root."""
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("Invalid Ascend texture file cache name.")
        key = name.casefold()
        if key in self.tfc:
            return self.tfc[key]
        folder = self.directory
        for _ in range(TFC_SEARCH_DEPTH + 1):
            matches = [path for path in folder.glob("*.tfc") if path.stem.casefold() == key]
            if matches:
                path = matches[0]
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"{path.name} must be a regular file.")
                with path.open("rb") as file:
                    original = identity(os.fstat(file.fileno()))
                    digest = sha256()
                    for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
                        digest.update(chunk)
                    if identity(os.fstat(file.fileno())) != original:
                        raise ValueError(f"{path.name} changed during import.")
                entry = path, original, dict(name=path.name, sha256=digest.hexdigest())
                self.tfc[key] = entry
                return entry
            if folder.parent == folder:
                break
            folder = folder.parent
        raise ValueError(f"Place {name}.tfc beside the Ascend map or in its cooked content folder.")

    def cache_receipt(self, name):
        return self.texture_file_cache(name)[2]

    def read_external(self, name, offset, size, codec, count):
        """Read one mip stored in a texture file cache, decoding zlib or LZO chunks."""
        path, original, _ = self.texture_file_cache(name)
        if not 0 < size <= MAX_TFC_PAYLOAD or not 0 < count <= MAX_TFC_PAYLOAD or offset < 0:
            raise ValueError("Invalid Ascend texture file cache mip size.")
        with path.open("rb") as file:
            if identity(os.fstat(file.fileno())) != original:
                raise ValueError(f"{path.name} changed during import.")
            if offset + size > original[2]:
                raise ValueError(f"The Ascend mip lies outside {path.name}.")
            file.seek(offset)
            packed = file.read(size)
        if len(packed) != size:
            raise ValueError(f"Truncated mip in {path.name}.")
        if not codec:
            if size != count:
                raise ValueError(f"Uncompressed mip size disagrees in {path.name}.")
            return packed
        return decode_chunk(packed, 0, size, count, codec)

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
