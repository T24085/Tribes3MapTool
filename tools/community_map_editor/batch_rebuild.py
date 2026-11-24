"""Plan rule-driven rebuilds and keep an atomic checkpoint for each draft."""

from datetime import datetime, timezone
import re
from statistics import median
from uuid import uuid4

from conversion_rules import checksum, create_snapshot, draft_snapshot, numeric, read, verify_snapshot, write
from intake_contract import load_intake
from map_pipeline import operation_order, pipeline_config
from object_catalog import classify, load_catalog
from source_library import verified_draft_intake
from source_profiles import IMPORT_DRAFT


DRAFT = re.compile(IMPORT_DRAFT)
BATCH = re.compile(r"batch-[0-9a-f]{12}")
REMAINING = {"pending", "building", "failed"}
STATES = REMAINING | {"complete", "released"}


def batch_path(library, ident):
    if not isinstance(ident, str) or not BATCH.fullmatch(ident):
        raise ValueError("Choose a saved batch rebuild.")
    return library / "BatchRebuilds" / (ident + ".json")


def selection_sha(items):
    return checksum([[item["source"], item["base"], item["rules"]["sha256"]] for item in items])


def load_batch(library, ident):
    batch = read(batch_path(library, ident), 32*1024*1024)
    if (not isinstance(batch, dict) or set(batch) != {"format", "id", "created", "selection_sha256", "items"}
            or batch["format"] != 1 or batch["id"] != ident or not isinstance(batch["created"], str)
            or not isinstance(batch["items"], list) or not 1 <= len(batch["items"]) <= 999):
        raise ValueError("Invalid map rebuild batch.")
    used_sources, used_results = set(), set()
    for item in batch["items"]:
        if (not isinstance(item, dict)
                or set(item) != {"source", "base", "rules", "name", "status", "attempts", "seconds", "error"}
                or not isinstance(item["source"], str) or not DRAFT.fullmatch(item["source"])
                or item["source"] in used_sources or not isinstance(item["base"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["base"])
                or not isinstance(item["name"], str) or not isinstance(item["error"], str)
                or item["status"] not in STATES or not isinstance(item["attempts"], list)):
            raise ValueError("Invalid draft in rebuild batch.")
        used_sources.add(item["source"])
        verify_snapshot(item["rules"])
        for target in item["attempts"]:
            if not isinstance(target, str) or not DRAFT.fullmatch(target) or target in used_results:
                raise ValueError("Invalid or duplicate rebuild target.")
            used_results.add(target)
        if item["status"] == "complete":
            if not item["attempts"] or not numeric(item["seconds"]) or item["seconds"] < 0:
                raise ValueError("A completed draft is missing its rebuild result.")
        elif item["seconds"] is not None or item["status"] == "building" and not item["attempts"]:
            raise ValueError("Invalid draft checkpoint.")
    if used_sources & used_results or batch["selection_sha256"] != selection_sha(batch["items"]):
        raise ValueError("The rebuild selection has changed since it was queued.")
    return batch


def saved_batches(library):
    batches, notices = [], []
    for path in (library / "BatchRebuilds").glob("batch-*.json"):
        try:
            batches.append(load_batch(library, path.stem))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            notices.append(f"{path.stem}: {exc}")
    return sorted(batches, key=lambda batch: batch["created"], reverse=True), notices


def create_batch(library, selected):
    if not selected or len(selected) > 999 or any(row["status"] != "Affected" for row in selected):
        raise ValueError("Select one or more affected drafts.")
    items = [{
        "source": row["draft"], "base": row["base"], "name": row["name"], "rules": row["rules"],
        "status": "pending", "attempts": [], "seconds": None, "error": "",
    } for row in selected]
    ident = "batch-" + uuid4().hex[:12]
    batch = {
        "format": 1, "id": ident, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection_sha256": selection_sha(items), "items": items,
    }
    save_batch(library, batch)
    return ident


def save_batch(library, batch):
    write(batch_path(library, batch["id"]), batch)


def comparable(snapshot):
    return {key: entry["value"] for key, entry in snapshot["results"].items()}


def compare_drafts(library, draft_ids):
    batches, notices = saved_batches(library)
    drafts = set(draft_ids)
    completed = {item["source"] for batch in batches for item in batch["items"]
                 if item["status"] == "complete" and item["attempts"][-1] in drafts}
    already_queued = {item["source"] for batch in batches for item in batch["items"]
                      if item["status"] in REMAINING}
    inputs, results = {}, []
    for draft in sorted(drafts):
        if not DRAFT.fullmatch(draft):
            continue
        row = {"draft": draft, "name": draft, "changes": [], "reason": ""}
        try:
            previous = draft_snapshot(library, draft)
            manifest = verified_draft_intake(library, draft)
            if previous is None or manifest is None:
                raise ValueError("This draft has no recorded rules and verified intake.")
            if manifest not in inputs:
                header, terrain, objects = load_intake(manifest)
                inputs[manifest] = header, {key: terrain[key] for key in ("cm_per_m", "y_sign") if key in terrain}, objects
            header, terrain, objects = inputs[manifest]
            row["name"] = header["name"]
            current = create_snapshot(library, header, objects, manifest, previous["options"])
            if previous["intake_sha256"] != current["intake_sha256"]:
                raise ValueError("The intake does not match the original draft.")
            old, new = comparable(previous), comparable(current)
            records = current["profile"]["conversions"]
            row["changes"] = [records.get(key, {}).get("label", key) for key in sorted(old.keys() | new.keys())
                              if old.get(key) != new.get(key)]
            prior_pipeline = pipeline_config(snapshot=previous, recorded=True)
            for phase, label in (("draft", "Draft pipeline steps"), ("host", "Direct Host pipeline steps")):
                if operation_order(prior_pipeline, phase) != operation_order(current["pipeline"], phase):
                    row["changes"].append(label)
            old_catalog = load_catalog(snapshot=previous, recorded=True)
            new_catalog = load_catalog(snapshot=current)
            if (old_catalog.fields != new_catalog.fields or old_catalog.aliases != new_catalog.aliases):
                first = {key for key, item in classify(objects, terrain, old_catalog).items() if item["blocked"]}
                latest = {key for key, item in classify(objects, terrain, new_catalog).items() if item["blocked"]}
                if first != latest:
                    row["changes"].append("Object placement checks")
            row.update(base=previous["sha256"], rules=current,
                       status="Rebuilt" if draft in completed else "In batch" if draft in already_queued
                       else "Affected" if row["changes"] else "Current")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row.update(status="Unavailable", reason=str(exc))
        results.append(row)
    return results, batches, notices


def estimated_seconds(batches, count):
    samples = [item["seconds"] for batch in batches for item in batch["items"] if item["status"] == "complete"]
    return round(count * median(samples)) if samples else None
