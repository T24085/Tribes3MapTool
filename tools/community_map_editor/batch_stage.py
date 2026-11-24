"""Durable manifest and output checks for a local Direct Host staging batch."""

from pathlib import Path, PurePosixPath
import re
from uuid import uuid4

if __package__:
    from .conversion_rules import checksum, read, sha256_file, write
    from .source_profiles import IMPORT_DRAFT
else:
    from conversion_rules import checksum, read, sha256_file, write
    from source_profiles import IMPORT_DRAFT


BATCH_ID = re.compile(r"batch-[0-9a-f]{12}")
DRAFT_ID = re.compile(IMPORT_DRAFT)


def stage_root(library, batch):
    if not isinstance(batch, str) or not BATCH_ID.fullmatch(batch):
        raise ValueError("Invalid batch staging request.")
    return library / "BatchStages" / batch


def archive_stage(root):
    if not root.is_dir():
        return
    archive = root.parent / "Archived"
    archive.mkdir(exist_ok=True)
    root.rename(archive / f"{root.name}-{uuid4().hex[:8]}")


def tree_fingerprint(root):
    if not root.is_dir() or root.resolve(strict=True) != root:
        raise ValueError(f"The staging folder is missing or linked: {root.name}")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.resolve(strict=True) != path:
            raise ValueError(f"Linked file in staging folder: {path.name}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256_file(path)
    return files


def read_prepared(root, batch):
    prepared = root / "prepared"
    plan = read(prepared / "index.json")
    if (plan.get("format") != 1 or plan.get("batch") != batch
            or not isinstance(plan.get("items"), list) or not plan["items"]):
        raise ValueError("Invalid prepared staging batch.")
    ids = set()
    for item in plan["items"]:
        draft = item.get("draft", "")
        if not DRAFT_ID.fullmatch(draft) or draft in ids:
            raise ValueError("Invalid or duplicate draft in staging batch.")
        if tree_fingerprint(prepared / draft) != item["files"]:
            raise ValueError(f"The prepared build inputs changed for {draft}.")
        ids.add(draft)
    return plan


def saved_draft_file(project, draft):
    return project.parent / "Content" / "Maps" / "Community" / draft / f"Arena_{draft}_P.umap"


def baseline_stamp(host):
    entries = [host / "package-manifest.json"]
    for folder in (host / "lua").iterdir() if (host / "lua").is_dir() else ():
        if folder.is_dir():
            entries.extend(path for path in (folder / "manifest.json", folder / "map.json") if path.is_file())
    return {path.relative_to(host).as_posix(): sha256_file(path) for path in sorted(entries)}


def stage_settings(root, batch, plan, selection=None):
    path = root / "stage-config.json"
    source_digest = checksum(plan)
    if path.is_file():
        settings = read(path)
        if (settings.get("format") != 1 or settings.get("batch") != batch
                or settings.get("prepared_sha256") != source_digest):
            raise ValueError("The prepared staging inputs changed since the batch was started.")
        host, destination = Path(settings["host"]), Path(settings["destination"])
    else:
        if selection is None:
            return None
        host = Path(selection["host"]).resolve(strict=True)
        destination = Path(selection["parent"]).resolve(strict=True) / f"{batch}-portable"
        if destination.exists() or destination.is_symlink() or destination.is_relative_to(host):
            raise ValueError("Choose an unused output folder outside the Direct Host baseline.")
        settings = {"format": 1, "batch": batch, "prepared_sha256": source_digest, "host": str(host),
                    "baseline": baseline_stamp(host), "destination": str(destination)}
        write(path, settings)
    if settings["baseline"] != baseline_stamp(host):
        raise ValueError("The Direct Host baseline inventory changed. Restore the selected baseline to resume.")
    destination.mkdir(exist_ok=True)
    if destination.resolve(strict=True) != destination:
        raise ValueError("The stage output folder is linked.")
    owner = destination / "batch.json"
    public_owner = {key: settings[key] for key in ("batch", "prepared_sha256")}
    public_owner["base_inventory_sha256"] = settings["baseline"]["package-manifest.json"]
    if owner.is_file():
        if read(owner) != public_owner:
            raise ValueError("The output folder belongs to a different staging batch.")
    else:
        if any(destination.iterdir()):
            raise ValueError("The stage output folder already contains other files.")
        write(owner, public_owner)
    return settings


def portable_fingerprint(destination, draft, label, base_digest):
    files = tree_fingerprint(destination)
    receipt = read(destination / "export-receipt.json")
    if receipt.get("map") != draft or receipt.get("base_inventory_sha256") != base_digest:
        raise ValueError(f"The portable receipt does not match {draft} or its host baseline.")
    if receipt.get("release_plan_sha256") != files.get("map-release.json"):
        raise ValueError("The map release plan differs from the export receipt.")
    runtime = destination / "payload" / "lua" / draft
    definition = read(runtime / "map.json")
    if definition.get("id") != draft or definition.get("label") != label:
        raise ValueError("The staged map definition changed.")
    own = read(runtime / "manifest.json")["files"]
    if not own:
        raise ValueError("The staged map has no inventory.")
    expected = list(own)
    shared = receipt.get("new_shared_files", [])
    if shared:
        central = read(destination / "payload" / "package-manifest.json", 64*1024*1024)
        rows = {item["path"]: item for item in central["files"]}
        expected.extend(rows[name] for name in shared)
    for row in expected:
        path = row["path"]
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or ":" in path or "\\" in path
                or files.get("payload/" + path) != row["sha256"]
                or (destination / "payload" / relative).stat().st_size != row["bytes"]):
            raise ValueError(f"Staged file failed verification: {path}")
    return {"receipt_sha256": files["export-receipt.json"], "files_sha256": checksum(files), "files": len(files)}
