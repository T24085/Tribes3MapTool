"""Publish a private, profile-driven object review for the Unreal dock tab."""

from pathlib import Path

from conversion_rules import DRAFT, draft_snapshot, write
from intake_contract import load_intake, load_mission_audio
from mesh_mappings import source_reference
from mission_audio import audio_review
from mission_equipment import EQUIPMENT_ROLES, power_context
from object_catalog import SUPPORT, classify, load_catalog, source_fields
from source_library import verified_draft_intake
from source_profiles import T2_GENERATOR_SCOPE_VERSIONS


def object_review_document(draft, header, terrain, rows, catalog, mappings, *, audio=None):
    if not DRAFT.fullmatch(draft):
        raise ValueError("Invalid draft ID for object review.")
    assessed = classify(rows, terrain, catalog)
    by_id = {row["id"]: row for row in rows}
    teams, groups, contexts = power_context(rows, generator_scopes=header["format"] in T2_GENERATOR_SCOPE_VERSIONS)
    audio_notes = audio_review(audio)
    models = {**header.get("interiors", {}), **header.get("shapes", {})}
    model_notes = {**header.get("interior_review", {}), **header.get("shape_review", {})}
    entries = []
    for row in rows:
        ident, cls = row["id"], row["source_class"]
        coverage = assessed[ident]
        feature = catalog.features[coverage["feature"]]
        props = {key.casefold(): str(value) for key, value in row["properties"].items()}
        reference = source_reference(row)
        role = EQUIPMENT_ROLES.get((cls.casefold(), props.get("datablock", "").casefold()))
        details = row["reason"]
        review = False
        if coverage["blocked"]:
            status = "Not placed"
            details = " ".join(coverage["blocked"])
            review = True
        elif row["status"] == "excluded":
            status = "Excluded mode"
        elif row["status"] == "marker":
            status = "Objective marker"
        elif row["status"] == "terrain":
            status = "Terrain"
        elif row["status"] == "boundary":
            status = "Boundary source"
        elif role:
            status = "Equipment review"
            details = "Source equipment requires placement review and host approval."
            review = True
        elif ident in audio_notes:
            status, details, review = "Audio retained", audio_notes[ident], True
        elif reference in mappings:
            status, details = "Mapped mesh", mappings[reference]
        elif reference in models:
            if cls.casefold() == "movement.elevatorvolume" and props.get("saved event senders"):
                status, review = "Linked field review", True
            else:
                status, details = "Decoded model", "The intake contains a decoded model for this source reference."
        elif reference:
            status, details = "Needs asset", model_notes.get(reference, "No decoded mesh or project mapping.")
            review = True
        elif feature["draft"] == "retained":
            status = "Source only"
            review = row["status"] == "review"
        else:
            status = "Review"
            review = True
        if row["status"] != "excluded" and not coverage["blocked"]:
            if coverage["notices"]:
                review = True
                details += " " + " ".join(coverage["notices"])
            if coverage["source_only_fields"]:
                review = True
                details += f" {len(coverage['source_only_fields'])} additional source fields."
        parent = by_id.get(row["parent"])
        circuit = contexts.get(groups.get(ident))
        links = {}
        if parent:
            links["parent"] = parent["name"] or parent["source_class"]
        if teams.get(ident):
            links["team"] = str(teams[ident])
        if circuit:
            links["power"] = circuit["name"] or f"Group {circuit['source_id']}"
        if reference:
            links["model"] = reference.split(":", 1)[1]
        fields = []
        for field in source_fields(row, catalog):
            value = f"{field['value']} {field['unit']}".strip()
            if field["problem"]:
                value = f"{value} · {field['problem']}".strip(" ·")
            fields.append({"label": field["label"], "value": value, "status": field["status"]})
        entries.append({
            "id": str(ident), "object": row["name"] or (links.get("model") or props.get("datablock") or cls),
            "type": cls, "feature": feature["label"], "status": status, "reason": details,
            "blocked": bool(coverage["blocked"]), "review": review, "fields": fields,
            "links": [{"label": label, "value": links[key]} for key, label in catalog.relationships.items() if key in links],
        })
    return {
        "format": 1, "draft": draft, "title": header["name"],
        "layout": catalog.layout, "objects": sorted(entries, key=lambda row: (not row["blocked"], not row["review"])),
        "features": [{**row, "draft": SUPPORT[row["draft"]], "host": SUPPORT[row["host"]]}
                     for row in catalog.features.values()],
    }


def publish_review(library, draft, header, terrain, rows, catalog, mappings, *, manifest=None):
    path = library / "DraftReviews" / f"{draft}.json"
    audio = load_mission_audio(manifest, header["files"]) if manifest is not None else None
    write(path, object_review_document(draft, header, terrain, rows, catalog, mappings, audio=audio))
    return path


def review_for_existing_draft(library, draft):
    manifest = verified_draft_intake(library, draft)
    if manifest is None:
        return None
    snapshot = draft_snapshot(library, draft)
    header, terrain, rows = load_intake(manifest)
    catalog = load_catalog(library, snapshot=snapshot, recorded=snapshot is not None, source_profile=header["profile"])
    mappings = snapshot["options"]["mesh_mappings"] if snapshot else {}
    return publish_review(library, draft, header, terrain, rows, catalog, mappings, manifest=manifest)
