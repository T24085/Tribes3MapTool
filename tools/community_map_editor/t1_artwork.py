"""Resolve referenced artwork within an explicitly selected source folder."""

from hashlib import sha256
from pathlib import Path, PurePosixPath

from intake_contract import bounded
from t1_bitmap import UNKEYED, artwork_png, palette_banks
from t1_terrain import ARCHIVE_LIMIT, MEMBER_LIMIT, Volume
from terrain_art import MAX_ART_BYTES


BITMAP_SUFFIXES = (".bmp", ".png", ".pbmp")
PALETTE_SUFFIXES = (".ppl", ".pal")


def collect_artwork(root, layers):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose a folder containing terrain artwork.")
    names = {slot: name.casefold() for slot, name in layers if PurePosixPath(name).suffix.casefold() in BITMAP_SUFFIXES}
    required = set(names.values())
    required.update(PurePosixPath(name).with_suffix(".png").as_posix() for name in names.values())
    if not required:
        raise ValueError("The mission has no named terrain artwork slots. Supply its material list first.")
    bitmaps, palettes, problems = {}, {}, {}
    raw_bytes = 0

    def texture(name, origin, raw, priority):
        nonlocal raw_bytes
        key = PurePosixPath(name).name.casefold()
        previous = bitmaps.get(key)
        if previous and priority >= previous[0]:
            if priority == previous[0] and raw != previous[1][2]:
                problems[key] = "Several archives contain different source bitmaps with this name."
            return
        raw_bytes += len(raw) - (len(previous[1][2]) if previous else 0)
        if raw_bytes > MAX_ART_BYTES:
            raise ValueError("The selected terrain source artwork exceeds the intake size limit.")
        problems.pop(key, None)
        bitmaps[key] = priority, (PurePosixPath(name).name, origin, raw)

    def palette(name, origin, raw, priority):
        receipt = PurePosixPath(name).name, origin, sha256(raw).hexdigest()
        for key, colors in palette_banks(raw).items():
            prior = palettes.get(key)
            if prior is None or priority < prior[0]:
                palettes[key] = priority, colors, receipt
            elif priority == prior[0] and prior[1] != colors:
                palettes[key] = priority, None, None

    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.resolve().parent != root:
            continue
        if path.name.casefold() in required:
            texture(path.name, path.name, bounded(path, MEMBER_LIMIT), 0)
        elif path.suffix.casefold() in PALETTE_SUFFIXES:
            palette(path.name, path.name, bounded(path, MEMBER_LIMIT), 0)
        elif path.suffix.casefold() == ".vol":
            archive = Volume(bounded(path, ARCHIVE_LIMIT))
            for name, _, _, _ in archive.entries.values():
                if PurePosixPath(name).name.casefold() in required:
                    texture(name, f"{path.name}:{name}", archive.read(name), 1)
                elif PurePosixPath(name).suffix.casefold() in PALETTE_SUFFIXES:
                    palette(name, f"{path.name}:{name}", archive.read(name), 1)

    def find_palette(key):
        entry = palettes.get(key)
        if entry is None and key == UNKEYED and len(palettes) == 1:
            entry = next(iter(palettes.values()))
        if entry is None:
            raise ValueError(f"Palette bank {key:#x} is missing.")
        if entry[1] is None:
            raise ValueError(f"Palette bank {key:#x} is ambiguous. Place the chosen palette beside the archives.")
        return entry[1:]

    receipts, records, images, converted = {}, {}, {}, {}
    image_bytes = 0
    for slot, original in sorted(names.items()):
        key = PurePosixPath(original).with_suffix(".png").as_posix()
        key = key if key in bitmaps else original
        if key not in converted:
            if key not in bitmaps or key in problems:
                converted[key] = {"reason": problems.get(key, "Referenced artwork is missing from the selected folder.")}
            else:
                _, (name, origin, raw) = bitmaps[key]
                try:
                    data, selected_palette = artwork_png(raw, find_palette)
                except ValueError as exc:
                    converted[key] = {"reason": str(exc)[:240]}
                else:
                    digest = sha256(raw).hexdigest()
                    receipts[origin] = dict(name=name, origin=origin, sha256=digest)
                    image = f"terrain-art-{sha256(data).hexdigest()[:32]}.png"
                    if image not in images:
                        image_bytes += len(data)
                        if image_bytes > MAX_ART_BYTES:
                            raise ValueError("Converted terrain artwork exceeds the intake size limit.")
                        images[image] = data
                    row = dict(file=image, source=name, source_sha256=digest)
                    if selected_palette:
                        pal_name, pal_origin, pal_hash = selected_palette
                        receipts[pal_origin] = dict(name=pal_name, origin=pal_origin, sha256=pal_hash)
                        row.update(palette=pal_name, palette_sha256=pal_hash)
                    converted[key] = row
        records[str(slot)] = converted[key]
    return records, images, list(receipts.values())
