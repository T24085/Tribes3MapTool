"""Resolve and inventory the static interiors referenced by a Tribes 1 mission."""

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath

from intake_contract import MAX_INTAKE_INTERIOR_BYTES, MAX_INTAKE_INTERIORS, T1_SHAPE_INTAKE_VERSION, bounded
from interior_geometry import material_slots
from interior_lighting import LIGHTING_INDEX, MAX_LIGHTING_INDEX, MAX_LIGHTMAP_BYTES, MAX_LIGHTMAPS
from mesh_mappings import source_reference
from model_textures import MAX_BINDINGS, MAX_TEXTURES, MAX_TEXTURE_BYTES, TEXTURE_INDEX, validate_png
from t1_artwork import BITMAP_SUFFIXES, PALETTE_SUFFIXES
from t1_bitmap import UNKEYED, bitmap_png, palette_banks
from t1_dts import decode_dts
from t1_interior import decode_interior, dis_index
from t1_lighting import archive_lights
from t1_shape_bindings import bind_shape_rows
from t1_terrain import ARCHIVE_LIMIT, MEMBER_LIMIT, Volume, resource_name
from t2_dif_reader import geometry_bytes


INTERIOR_FILES = {".dis", ".dig", ".dml", ".dil"}
SOURCE_FILES = INTERIOR_FILES | {".dts", ".cs"} | set(BITMAP_SUFFIXES) | set(PALETTE_SUFFIXES)


@dataclass(frozen=True)
class Asset:
    container: str
    name: str
    origin: str
    archive: Volume | None
    path: Path | None

    def read(self):
        data = self.archive.read(self.name) if self.archive is not None else bounded(self.path, MEMBER_LIMIT)
        return self.name, self.origin, data


class InteriorAssets:
    def __init__(self, folder, mission_volume):
        self.members = defaultdict(list)
        self.palettes = []
        self.scripts = []
        self.palette_index = None
        self.primary = None
        if mission_volume is not None:
            self.primary, volume = mission_volume
            self._mount(self.primary, volume)
        if folder is None:
            return
        root = Path(folder).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Choose a folder containing the Tribes interior files or volumes.")
        paths = (path for path in root.rglob("*") if path.suffix.casefold() in SOURCE_FILES | {".vol"})
        for path in sorted(paths, key=lambda file: file.as_posix().casefold()):
            if not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            suffix = path.suffix.casefold()
            relative = path.relative_to(root).as_posix()
            if suffix in SOURCE_FILES:
                self.add(Asset("", relative, relative, None, path))
            elif suffix == ".vol" and path.name.casefold() != (self.primary or "").casefold():
                self._mount(relative, Volume(bounded(path, ARCHIVE_LIMIT)))

    def _mount(self, source, volume):
        for name, _, _, _ in volume.entries.values():
            member = PurePosixPath(name)
            if member.suffix.casefold() in SOURCE_FILES:
                self.add(Asset(source, name, f"{source}:{name}", volume, None))

    def add(self, asset):
        member = PurePosixPath(asset.name)
        if member.suffix.casefold() in PALETTE_SUFFIXES:
            self.palettes.append(asset)
        elif member.suffix.casefold() == ".cs":
            self.scripts.append(asset)
        else:
            self.members[member.name.casefold()].append(asset)

    def find(self, requested, *, companion=None):
        requested = resource_name(requested).casefold()
        matches = self.members[PurePosixPath(requested).name]
        if not matches:
            raise ValueError(f"Referenced interior resource {PurePosixPath(requested).name} is missing.")
        preferred = companion.container if companion is not None else self.primary
        if companion is not None and "/" not in requested:
            requested = str(PurePosixPath(companion.name.casefold()).with_name(requested))

        def priority(asset):
            return (asset.container != preferred, asset.name.casefold() != requested, asset.archive is not None)

        highest = min(map(priority, matches))
        best = [asset for asset in matches if priority(asset) == highest]
        if len(best) != 1:
            raise ValueError(f"Several interior resources match {PurePosixPath(requested).name}.")
        return best[0]

    def find_palette(self, key, bitmap):
        if self.palette_index is None:
            self.palette_index = defaultdict(list)
            for asset in self.palettes:
                try:
                    name, origin, raw = asset.read()
                    banks = palette_banks(raw)
                except (OSError, ValueError):
                    continue
                receipt = PurePosixPath(name).name, origin, sha256(raw).hexdigest()
                for bank, colors in banks.items():
                    self.palette_index[bank].append((asset.container, colors, receipt))
        candidates = self.palette_index.get(key, ())
        if not candidates and key == UNKEYED and len(self.palette_index) == 1:
            candidates = next(iter(self.palette_index.values()))
        if not candidates:
            raise ValueError(f"Interior palette bank {key:#x} is missing.")

        def rank(container):
            return container != bitmap.container, container != ""

        priority = min(rank(container) for container, _, _ in candidates)
        chosen = [(colors, receipt) for container, colors, receipt in candidates
                  if rank(container) == priority]
        colors, receipt = chosen[0]
        if any(other != colors for other, _ in chosen):
            raise ValueError(f"Interior palette bank {key:#x} is ambiguous.")
        return colors, receipt


def collect_textures(catalog, models, receipts):
    records, files, decoded = {}, {}, {}
    size = count = 0

    def read_bitmap(bitmap):
        nonlocal size
        try:
            _, origin, raw = bitmap.read()
            png, palette, mode = bitmap_png(raw, lambda key: catalog.find_palette(key, bitmap), allow_mask=True)
            dimensions = list(validate_png(png))
            source = PurePosixPath(bitmap.name).name
            digest = sha256(raw).hexdigest()
            target = "texture_" + sha256(png).hexdigest()[:32] + ".png"
            if target not in files:
                if (len(files) >= MAX_TEXTURES or len(png) > 16 * 1024 * 1024
                        or size + len(png) > MAX_TEXTURE_BYTES):
                    raise ValueError("The intake interior texture size limit was reached.")
                files[target] = png
                size += len(png)
            receipts[origin] = dict(name=source, origin=origin, sha256=digest)
            record = dict(file=target, source=source, source_sha256=digest, size=dimensions, mode=mode)
            if palette:
                pal_name, pal_origin, pal_hash = palette
                receipts[pal_origin] = dict(name=pal_name, origin=pal_origin, sha256=pal_hash)
                record.update(palette=pal_name, palette_sha256=pal_hash)
            return record
        except (ImportError, OSError, ValueError) as error:
            return {"reason": (str(error) or "Source bitmap could not be decoded.")[:240]}

    for filename, materials, material in models:
        slots = {}
        for slot, name in materials.items():
            count += 1
            if count > MAX_BINDINGS:
                raise ValueError("This mission has too many interior texture bindings.")
            try:
                if PurePosixPath(name).suffix.casefold() not in BITMAP_SUFFIXES:
                    raise ValueError("The material does not name a supported bitmap.")
                bitmap = catalog.find(name, companion=material)
            except ValueError as error:
                slots[str(slot)] = {"reason": str(error)[:240]}
                continue
            if bitmap.origin not in decoded:
                decoded[bitmap.origin] = read_bitmap(bitmap)
            slots[str(slot)] = decoded[bitmap.origin]
        records[filename] = slots
    if records:
        files[TEXTURE_INDEX] = json.dumps(records, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return files


def collect_models(rows, folder=None, mission_volume=None):
    references = sorted({ref for row in rows if (ref := source_reference(row)) and ref.startswith("interiorshape:")})
    if len(references) > MAX_INTAKE_INTERIORS:
        raise ValueError("This mission has too many unique interior references for one intake.")
    catalog = InteriorAssets(folder, mission_volume)
    found, review, shapes, shape_review, files, receipts = {}, {}, {}, {}, {}, {}
    bind_shape_rows(rows, catalog.scripts, catalog.primary, receipts)
    props = sorted({ref for row in rows if (ref := source_reference(row))
                    and ref.startswith(("staticshape:", "tsstatic:")) and ref.endswith(".dts")})
    if len(props) > MAX_INTAKE_INTERIORS:
        raise ValueError("This mission has too many unique DTS references for one intake.")
    models = []
    total = light_bytes = lightmap_count = lighting_bytes = 0
    illumination = {}
    for reference in references:
        try:
            name = reference.split(":", 1)[1]
            if not name.endswith(".dis"):
                raise ValueError("Expected an InteriorShape with a DIS filename.")
            asset = catalog.find(name)
            dis = asset.read()
            lod, geography, material, light_sources = dis_index(dis[2])
            dig = catalog.find(geography, companion=asset).read()
            material = catalog.find(material, companion=asset)
            dml = material.read()
            mesh, lighting_geometry = decode_interior(dis, dig, dml, lod)
            payload = geometry_bytes(mesh)
            if total + len(payload) > MAX_INTAKE_INTERIOR_BYTES:
                raise ValueError("The intake has reached the decoded model size limit.")
        except (OSError, ValueError) as error:
            review[reference] = str(error)[:240]
            continue
        filename = f"interior-{len(found) + 1:04d}.json"
        found[reference] = filename
        files[filename] = payload
        models.append((filename, {slot: mesh["materials"][slot] for slot in material_slots(mesh)}, material))
        total += len(payload)
        for name, origin, raw in (dis, dig, dml):
            receipts[origin] = dict(name=PurePosixPath(name).name, origin=origin, sha256=sha256(raw).hexdigest())
        if light_sources:
            lighting, images, sources = archive_lights(
                light_sources, lighting_geometry, mesh["source"]["sha256"],
                lambda filename: catalog.find(filename, companion=asset).read())
            additional = {path: raw for path, raw in images.items() if path not in files}
            image_bytes = sum(map(len, additional.values()))
            encoded = json.dumps(lighting, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if (lightmap_count + len(additional) > MAX_LIGHTMAPS or light_bytes + image_bytes > MAX_LIGHTMAP_BYTES
                    or lighting_bytes + len(encoded) + 48 > MAX_LIGHTING_INDEX):
                raise ValueError("The mission reached the intake's baked interior lighting limits.")
            illumination[filename] = lighting
            lightmap_count += len(additional)
            light_bytes += image_bytes
            lighting_bytes += len(encoded) + 48
            files.update(additional)
            receipts.update((source["origin"], source) for source in sources)
    if illumination:
        files[LIGHTING_INDEX] = json.dumps(
            illumination, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    shape_version = 18
    for reference in props:
        try:
            name = reference.split(":", 1)[1]
            asset = catalog.find(name)
            member, origin, raw = asset.read()
            mesh = decode_dts(member, raw)
            payload = geometry_bytes(mesh)
            if total + len(payload) > MAX_INTAKE_INTERIOR_BYTES:
                raise ValueError("The intake has reached the decoded model size limit.")
        except (OSError, ValueError) as error:
            shape_review[reference] = str(error)[:240]
            continue
        filename = f"shape-{len(shapes) + 1:04d}.json"
        shapes[reference] = filename
        shape_version = max(shape_version, T1_SHAPE_INTAKE_VERSION[mesh["format"]])
        files[filename] = payload
        models.append((filename, {slot: mesh["materials"][slot] for slot in material_slots(mesh)}, asset))
        total += len(payload)
        receipts[origin] = dict(name=PurePosixPath(member).name, origin=origin, sha256=sha256(raw).hexdigest())
    files.update(collect_textures(catalog, models, receipts))
    return found, review, shapes, shape_review, shape_version, files, list(receipts.values())
