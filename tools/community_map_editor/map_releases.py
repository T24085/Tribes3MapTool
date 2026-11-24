"""Record verified Direct Host exports and compare them with a running catalog."""

from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPSConnection
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

if __package__:
    from .batch_stage import DRAFT_ID, portable_fingerprint
    from .conversion_rules import HEX, read, sha256_file, write
else:
    from batch_stage import DRAFT_ID, portable_fingerprint
    from conversion_rules import HEX, read, sha256_file, write


INDEX = "/api/maps-v3"
MAP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
MAX_DOCUMENT = 4 * 1024 * 1024
HEADERS = {"X-T3-Map-Sessions": "3", "X-T3-Map-Minimap": "1", "Accept": "application/json"}


def is_hash(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def is_label(value):
    return isinstance(value, str) and 1 <= len(value) <= 64 and value.isprintable()


def record_directory(library):
    return library / "MapReleases"


def capture_export(library, folder, *, expected_draft=None, expected_label=None, expected_base=None, proof=None):
    folder = Path(folder).resolve(strict=True)
    receipt_path = folder / "export-receipt.json"
    receipt = read(receipt_path, 64 * 1024)
    if not isinstance(receipt, dict):
        raise ValueError("Invalid portable export receipt.")
    draft, base = receipt.get("map"), receipt.get("base_inventory_sha256")
    if (receipt.get("format") != 1 or not isinstance(draft, str) or not DRAFT_ID.fullmatch(draft)
            or not is_hash(base) or expected_draft is not None and draft != expected_draft
            or expected_base is not None and base != expected_base):
        raise ValueError("The export receipt does not match this draft or its baseline.")
    runtime = folder / "payload" / "lua" / draft
    definition = read(runtime / "map.json", 256 * 1024)
    label = definition.get("label") if isinstance(definition, dict) else None
    if (not is_label(label) or expected_label is not None and label != expected_label
            or definition.get("id") != draft or definition.get("asset_group") != draft
            or definition.get("level") != f"Arena_{draft}_P"):
        raise ValueError("The package has a missing or different map identity.")
    if proof is None:
        proof = portable_fingerprint(folder, draft, label, base)
    members = file_rows(read(runtime / "manifest.json", MAX_DOCUMENT)["files"])
    fingerprint = proof["files_sha256"]
    path = record_directory(library) / f"{fingerprint}.json"
    recorded = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    if path.is_file():
        prior = read(path, MAX_DOCUMENT)
        recorded = prior["recorded"]
    if sha256_file(receipt_path) != proof["receipt_sha256"]:
        raise ValueError("The export receipt changed during verification.")
    entry = {"format": 1, "draft": draft, "label": label, "recorded": recorded, "folder": str(folder),
             "base_inventory_sha256": base, "files_sha256": fingerprint, "receipt_sha256": proof["receipt_sha256"],
             "members": members, "minimap": definition.get("minimap")}
    write(path, entry)
    return entry


def release_history(library, draft=None):
    entries = []
    for path in record_directory(library).glob("*.json"):
        try:
            entry = read(path, MAX_DOCUMENT)
            if (entry.get("format") == 1 and entry.get("files_sha256") == path.stem and is_hash(path.stem)
                    and isinstance(entry["draft"], str) and DRAFT_ID.fullmatch(entry["draft"])
                    and is_label(entry["label"]) and isinstance(entry["recorded"], str)
                    and isinstance(entry["folder"], str) and isinstance(entry["members"], dict)
                    and all(is_hash(entry.get(key)) for key in ("receipt_sha256", "base_inventory_sha256"))):
                entries.append(entry)
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            continue
    return sorted(entries, key=lambda item: (item["draft"] == draft, item["recorded"], item["files_sha256"]), reverse=True)


def verify_release(entry):
    proof = portable_fingerprint(Path(entry["folder"]), entry["draft"], entry["label"], entry["base_inventory_sha256"])
    if any(proof[key] != entry[key] for key in ("files_sha256", "receipt_sha256")):
        raise ValueError("The export folder changed since this release was recorded.")


def file_rows(rows):
    if not isinstance(rows, list) or len(rows) > 20_000:
        raise ValueError("Invalid map file inventory.")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or row.keys() != {"path", "bytes", "sha256"}
                or not isinstance(row["path"], str) or not row["path"] or row["path"] in result
                or not is_hash(row["sha256"]) or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise ValueError("Invalid map file row.")
        result[row["path"]] = [row["bytes"], row["sha256"]]
    return result


class HostCatalog:
    def __init__(self, origin):
        parsed = urlsplit(origin.strip())
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Enter the host HTTPS address, with its port if needed.")
        self.hostname, self.port = parsed.hostname, parsed.port

    def request(self, route):
        conn = HTTPSConnection(self.hostname, self.port, timeout=8)
        try:
            conn.request("GET", route, headers=HEADERS)
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError(f"Host catalog returned HTTP {response.status}.")
            raw = response.read(MAX_DOCUMENT + 1)
            if len(raw) > MAX_DOCUMENT:
                raise ValueError("The host's map catalog response is too large.")
        finally:
            conn.close()
        return json.loads(raw), sha256(raw).hexdigest()

    def index(self):
        document, _ = self.request(INDEX)
        if (not isinstance(document, dict) or document.get("format") != 3
                or not is_hash(document.get("coreHash")) or not isinstance(document.get("maps"), list)
                or len(document["maps"]) > 512):
            raise ValueError("The host did not return a supported map catalog.")
        found = {}
        for row in document["maps"]:
            map_id = row.get("id") if isinstance(row, dict) else None
            if (not isinstance(map_id, str) or not MAP_ID.fullmatch(map_id) or map_id.casefold() in found
                    or not is_label(row.get("label")) or not is_hash(row.get("contentHash"))
                    or not is_hash(row.get("manifestHash"))):
                raise ValueError("The host returned an invalid or duplicate map entry.")
            found[map_id.casefold()] = row
        return found

    def compare(self, live, release):
        if live["id"] != release["draft"]:
            raise ValueError("Choose a local release for this live map.")
        document, digest = self.request(f"{INDEX}/map/{live['id']}")
        if (digest != live["manifestHash"] or not isinstance(document, dict) or document.get("format") != 3
                or document.get("id") != live["id"] or document.get("contentHash") != live["contentHash"]):
            raise ValueError("The host catalog changed. Refresh before comparing again.")
        minimap = document.get("minimap")
        rows = document["files"] + document["objectives"]
        if minimap and minimap["image"] is not None:
            rows = [*rows, minimap["image"]]
        definition = {"label": release["label"], "asset_group": live["id"], "level": f"Arena_{live['id']}_P"}
        return (document.get("definition") == definition and release["members"] == file_rows(rows)
                and release["label"] == live["label"]
                and release["minimap"] == (minimap["settings"] if minimap else None))
