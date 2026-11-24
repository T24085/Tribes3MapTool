"""Compose private per-draft PNG contact sheets from verified art and rendered exhibits."""

import argparse
from collections import defaultdict
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw, ImageFont

from intake_contract import MAX_MANIFEST_BYTES, bounded
from model_textures import load_texture_index
from sky_intake import load_sky_index
from texture_batches import (
    BATCH, DRAFT, MAX_TEXTURE_BYTES, decisions, inspect, load_batch, save_json,
)


CATEGORIES = ("Buildings", "Props", "Objectives", "Equipment", "Textures", "Texture comparisons")
MARGIN = 24
GAP = 16
TILE = (282, 250)
PAIR = (490, 250)


def picture(path, label="", digest=None):
    return {"path": str(path), "label": label, "sha256": digest}


def source_art(manifest):
    if manifest is None:
        return []
    path = Path(manifest).resolve(strict=True)
    header = json.loads(bounded(path, MAX_MANIFEST_BYTES))
    folder, inventory = path.parent, header["files"]
    names = {}
    for slots in load_texture_index(folder, inventory).values():
        for item in slots.values():
            if "file" in item:
                names[item["file"]] = item["source"]
    for sky in load_sky_index(folder, inventory):
        for item in sky["slots"]:
            if "file" in item:
                names[item["file"]] = PurePosixPath(item["reference"]).name
    items = []
    for filename, digest in sorted(inventory.items()):
        if not filename.lower().endswith(".png"):
            continue
        asset = folder / filename
        if asset.parent != folder or asset.resolve(strict=True) != asset:
            raise ValueError("An intake image is outside its verified source folder.")
        name = names.get(filename)
        if not name:
            prefix = "Lightmap" if filename.startswith("lightmap_") else "Source"
            name = f"{prefix} · {Path(filename).stem}"
        items.append({"category": "Textures", "title": name, "detail": "Source artwork",
                      "pictures": [picture(asset, digest=digest)]})
    return items


def texture_pairs(library, draft):
    if library is None:
        return [], []
    items, notices = [], []
    parent = Path(library) / draft
    for path in sorted(parent.iterdir()) if parent.is_dir() else ():
        if not BATCH.fullmatch(path.name):
            continue
        root, rows = load_batch(library, draft, path.name)
        status = decisions(root, rows)
        for ident, row in rows.items():
            candidate = root / "replacements" / (ident + ".png")
            if not candidate.is_file():
                continue
            name = row["asset"].rsplit(".", 1)[-1]
            try:
                original, replacement, current = inspect(root, row)
            except (OSError, ValueError) as exc:
                notices.append({"asset": name, "batch": path.name, "reason": str(exc)})
                continue
            previous = status.get(ident, {})
            choice = previous.get("status", "Ready for review").capitalize()
            if previous and current["sha256"] != previous["sha256"]:
                choice = "Changed since review"
            if current["sha256"] == row["source"]["sha256"]:
                continue
            items.append({"category": "Texture comparisons", "title": name, "detail": choice,
                          "pictures": [
                              picture(original, "Original", row["source"]["sha256"]),
                              picture(replacement, "Replacement", current["sha256"]),
                          ]})
    return items, notices


def font(size, *, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"),
                 ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def display_text(draw, position, text, width, typeface, color):
    if draw.textlength(text, font=typeface) > width:
        while text and draw.textlength(text + "…", font=typeface) > width:
            text = text[:-1]
        text += "…"
    draw.text(position, text, font=typeface, fill=color)


def draw_picture(sheet, draw, box, record, caption_font):
    path = Path(record["path"])
    raw = bounded(path, MAX_TEXTURE_BYTES)
    digest = sha256(raw).hexdigest()
    if record["sha256"] is not None and digest != record["sha256"]:
        raise ValueError(f"{path.name} changed before contact sheet rendering.")
    left, top, width, height = box
    draw.rectangle((left, top, left + width - 1, top + height - 1), fill="#111923")
    with Image.open(io.BytesIO(raw), formats=("PNG",)) as source:
        w, h = source.size
        source.thumbnail((width - 2, height - 2), Image.Resampling.LANCZOS)
        with source.convert("RGBA") as preview:
            sheet.paste(preview, (left + (width - preview.width) // 2,
                                  top + (height - preview.height) // 2), preview)
    subtitle = (record["label"] + "   " if record["label"] else "") + f"{w} × {h}"
    draw.text((left, top + height + 4), subtitle, font=caption_font, fill="#93adbf")
    return {"label": record["label"], "size": [w, h], "sha256": digest}


def compose_sheet(output, draft, category, items, page, page_count, cols, card):
    cell_w, cell_h = card
    ncols = min(cols, len(items))
    nrows = (len(items) + cols - 1) // cols
    header = 78
    width = ncols * cell_w + MARGIN * 2 + GAP * (ncols - 1)
    height = header + nrows * cell_h + MARGIN + GAP * (nrows - 1)
    heading, title, meta = font(26, bold=True), font(17, bold=True), font(13)
    rows = []
    with Image.new("RGB", (width, height), "#101620") as sheet:
        draw = ImageDraw.Draw(sheet)
        draw.text((MARGIN, 15), category, font=heading, fill="#f4f7fb")
        note = draft if page_count == 1 else f"{draft}   ·   {page} of {page_count}"
        draw.text((MARGIN, 49), note, font=meta, fill="#90acbe")
        for index, item in enumerate(items):
            y, x = divmod(index, cols)
            left = MARGIN + x * (cell_w + GAP)
            top = header + y * (cell_h + GAP)
            draw.rounded_rectangle((left, top, left + cell_w - 1, top + cell_h - 1),
                                   radius=9, fill="#202c3a")
            display_text(draw, (left + 12, top + 9), item["title"], cell_w - 24, title, "#f4f7fb")
            display_text(draw, (left + 12, top + 32), item["detail"], cell_w - 24, meta, "#a8bed0")
            count = len(item["pictures"])
            tile_w = (cell_w - 24 - (count - 1) * 10) // count
            pictures = [
                draw_picture(sheet, draw, (left + 12 + i * (tile_w + 10), top + 55, tile_w, cell_h - 85),
                             record, meta)
                for i, record in enumerate(item["pictures"])
            ]
            rows.append({"title": item["title"], "detail": item["detail"], "images": pictures})
        sheet.save(output, format="PNG")
    return {"file": output.name, "size": [width, height], "items": rows,
            "sha256": sha256(bounded(output, MAX_TEXTURE_BYTES)).hexdigest()}


def build_report(request, destination):
    draft = request["draft"]
    if not DRAFT.fullmatch(draft):
        raise ValueError("Choose a CTF draft for the contact sheets.")
    grouped = defaultdict(list)
    comparisons, notices = texture_pairs(request.get("texture_library"), draft)
    notices = request.get("notices", []) + notices
    for item in request["items"] + source_art(request.get("intake")) + comparisons:
        category = item["category"]
        if category not in CATEGORIES or len(item["pictures"]) != (2 if category == CATEGORIES[-1] else 1):
            raise ValueError("Invalid contact sheet item.")
        grouped[category].append(item)
    destination.mkdir()
    pages = []
    for category in CATEGORIES:
        if category not in grouped:
            continue
        cols, page_size, card = (2, 8, PAIR) if category == CATEGORIES[-1] else (4, 12, TILE)
        items = sorted(grouped[category], key=lambda item: (item["title"].casefold(), item["detail"]))
        num_pages = (len(items) + page_size - 1) // page_size
        for start in range(0, len(items), page_size):
            page = start // page_size + 1
            name = f"{category.lower().replace(' ', '-')}-{page:02d}.png"
            written = compose_sheet(destination / name, draft, category, items[start:start + page_size],
                                    page, num_pages, cols, card)
            pages.append({"category": category, **written})
    save_json(destination / "index.json", {"format": 1, "draft": draft, "sheets": pages, "notices": notices})
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_report(json.loads(bounded(args.request, 8 * 1024 * 1024)), args.destination)
