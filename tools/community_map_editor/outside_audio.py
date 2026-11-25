"""Compile the saved draft's outside-only sound bindings for its playtest."""

from hashlib import sha256

ROOM_TAG = "T3AcousticRoom_"
OUTSIDE_TAG = "T3OutsideAudio_"
PAYLOAD_FILE = "outside-audio.json"
MAX_PAYLOAD = 16 * 1024 * 1024


def room_tag(source_id, mesh_path):
    return f"{ROOM_TAG}{source_id}_{sha256(mesh_path.encode('utf-8')).hexdigest()[:16]}"


def draft_payload(library, draft, rooms, tracks):
    if not tracks:
        return None
    from intake_contract import load_intake, load_intake_geometry, load_mission_audio
    from mesh_mappings import source_reference
    from mission_audio import directional_audio
    from source_library import verified_draft_intake

    manifest = verified_draft_intake(library, draft)
    if not manifest:
        raise ValueError("The outside-only sounds need their saved source intake for playtest.")
    header, terrain, rows = load_intake(manifest)
    known_rooms = {row["id"]: source_reference(row) for row in rows}
    by_id = load_mission_audio(manifest, header["files"])
    track_settings = {row["source_id"]: row["settings"] for row in by_id["emitters"]} if by_id else {}
    model_indices, models, placements, emitters = {}, [], [], []
    for source_id, tag in sorted(rooms.items()):
        name = header.get("interiors", {}).get(known_rooms.get(source_id))
        if not name:
            raise ValueError(f"The source data for acoustic room {source_id} is missing.")
        if name not in model_indices:
            member, _ = load_intake_geometry(manifest.parent, name, header["files"][name])
            acoustic = member.get("acoustics", {})
            collision = acoustic.get("collision")
            if not collision:
                raise ValueError(f"The source collision for acoustic room {source_id} is missing.")
            models.append([
                member["vertices_m"], acoustic["nodes"], acoustic["outside_surfaces"], acoustic["portal_centers_m"],
                collision["node_planes"], collision["solid_children"], collision["leaves"], collision["faces"],
            ])
            model_indices[name] = len(models)
        placements.append([tag, model_indices[name]])
    for source_id in sorted(tracks):
        settings = track_settings.get(source_id, {})
        if settings.get("outside_only") is not True or "volume" not in settings:
            raise ValueError(f"The source settings for outside-only emitter {source_id} are missing.")
        cone = []
        if directional_audio(settings):
            cone = [settings["cone_inside_degrees"] / 2, settings["cone_outside_degrees"] / 2,
                    settings["cone_outside_volume"]]
        emitters.append([f"{OUTSIDE_TAG}{source_id}", settings["volume"], cone])
    return [terrain["cm_per_m"], placements, models, emitters]
