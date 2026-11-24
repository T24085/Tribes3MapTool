"""Private source provenance for saved map intakes and Unreal drafts."""

from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from intake_contract import MAX_MANIFEST_BYTES, bounded
from source_profiles import ANY_DRAFT


DRAFT_ID = re.compile(ANY_DRAFT)


def manifest_record(manifest):
    raw = bounded(manifest, MAX_MANIFEST_BYTES)
    header = json.loads(raw)
    if (not isinstance(header, dict) or not isinstance(header.get("name"), str)
            or not isinstance(header.get("sources"), list) or not header["sources"]):
        raise ValueError("Incomplete intake source metadata.")
    sources = header["sources"]
    if any(not isinstance(row, dict) or not isinstance(row.get("origin"), str)
           or not isinstance(row.get("name"), str) for row in sources):
        raise ValueError("Invalid intake source metadata.")
    return sha256(raw).hexdigest(), header["name"], sources


def _save(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _read(path):
    try:
        row = json.loads(bounded(path, 16 * 1024))
        if isinstance(row, dict):
            return row
    except (OSError, ValueError):
        pass
    return {}


def record_source(library, manifest, source, terrain):
    sig, _, sources = manifest_record(manifest)
    source = Path(source).resolve()
    if terrain is None and source.suffix.casefold() == ".mis" and len(sources) > 1:
        name = Path(sources[1]["name"]).name
        candidates = (source.parent / name, source.parent.parent / "terrains" / name)
        terrain = next((item for item in candidates if item.is_file()), candidates[0])
    _save(library / "SourceLocations" / (sig + ".json"), {
        "source": str(source),
        "terrain": str(Path(terrain).resolve()) if terrain else None,
    })


def record_draft(library, draft, manifest):
    if not DRAFT_ID.fullmatch(draft):
        raise ValueError("Invalid map draft ID.")
    sig, _, _ = manifest_record(manifest)
    _save(library / "DraftSources" / (draft + ".json"), {
        "manifest": str(manifest.resolve()), "sha256": sig,
    })


def verified_draft_intake(library, draft):
    if not DRAFT_ID.fullmatch(draft):
        raise ValueError("Invalid map draft ID.")
    path = library / "DraftSources" / (draft + ".json")
    if not path.is_file():
        return None
    record = _read(path)
    source, expected = record.get("manifest"), record.get("sha256")
    if not isinstance(source, str) or not isinstance(expected, str):
        raise ValueError("The draft has no valid source link.")
    manifest = Path(source).resolve(strict=True)
    if manifest_record(manifest)[0] != expected:
        raise ValueError("The saved intake changed since the draft was imported.")
    return manifest


def _intake_row(library, manifest, draft=None, expected=None):
    sig, name, sources = manifest_record(manifest)
    if expected is not None and expected != sig:
        raise ValueError("The intake manifest changed since this draft was created.")
    record = _read(library / "SourceLocations" / (sig + ".json"))
    original, terrain = record.get("source"), record.get("terrain")
    if isinstance(original, str):
        present = Path(original).is_file() and (not isinstance(terrain, str) or Path(terrain).is_file())
        status = "Source present" if present else "Original source missing"
    else:
        status = "Intake only"
        original = sources[0]["origin"]
    lines = [f"Mission: {sources[0]['name']}", f"Original: {original}"]
    if len(sources) > 1:
        lines.append(f"Terrain in source: {sources[1]['name']}")
    if isinstance(terrain, str):
        lines.append(f"Terrain: {terrain}")
    lines.append(f"Intake: {manifest}")
    return dict(name=name, draft=draft, status=status, origin=original,
                details="\n".join(lines), manifest=manifest)


def catalog_rows(library, drafts):
    """List existing draft IDs and retained intake headers without loading their payloads."""
    rows, linked = [], set()
    for draft in sorted(set(drafts)):
        if not DRAFT_ID.fullmatch(draft):
            continue
        link_file = library / "DraftSources" / (draft + ".json")
        link = _read(link_file)
        try:
            if not link.get("manifest") or not re.fullmatch(r"[0-9a-f]{64}", link.get("sha256", "")):
                raise ValueError("No saved source link.")
            manifest = Path(link["manifest"]).resolve(strict=True)
            rows.append(_intake_row(library, manifest, draft, link["sha256"]))
            linked.add(manifest)
        except (OSError, TypeError, ValueError) as exc:
            status = "Intake unavailable" if link_file.is_file() else "No source link"
            rows.append(dict(name=draft, draft=draft, status=status, origin="",
                             details=str(exc) if link_file.is_file() else "No source intake is linked to this draft.",
                             manifest=None))
    for path in library.glob("*/manifest.json"):
        try:
            manifest = path.resolve(strict=True)
            if manifest not in linked:
                rows.append(_intake_row(library, manifest))
        except (OSError, ValueError) as exc:
            rows.append(dict(name=path.parent.name, draft=None, status="Intake unavailable", origin="",
                             details=str(exc), manifest=None))
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["draft"] or ""))
