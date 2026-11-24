"""Project-wide catalog of reusable static models and their material assignments."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

import unreal

from draft_model_materials import BAKED_MATERIAL_PREFIX
from draft_showcase import ROOT, populate_stage
from equipment_unreal import mark_interior_lighting
from intake_contract import bounded, finite
from mesh_mappings import source_reference
from source_library import DRAFT_ID
from texture_batches import save_json


LEVEL_ROOT = ROOT + "/Library"
MODEL_TAG = "T3LibraryModel_"
LIBRARY_READY_TAG = "T3ModelLibraryReady"
ASSET = re.compile(r"/(?:Game|Engine)/(?:[A-Za-z0-9_]+/)*([A-Za-z0-9_]+)\.\1")
return_context = None


def project_records():
    return Path(unreal.Paths.project_saved_dir()).resolve() / "T3ModelLibrary"


def model_key(row):
    look = (row["mesh"], row["materials"], row["scale"], row["rotation"])
    return sha256(json.dumps(look, sort_keys=True).encode()).hexdigest()[:24]


def validate(row):
    if (not isinstance(row, dict)
            or set(row) != {"name", "category", "mesh", "materials", "scale", "rotation"}
            or row["category"] not in ("Buildings", "Props")
            or not isinstance(row["name"], str) or not row["name"].isprintable()
            or not 1 <= len(row["name"]) <= 240
            or not isinstance(row["mesh"], str) or not ASSET.fullmatch(row["mesh"])
            or not isinstance(row["materials"], dict) or len(row["materials"]) > 1024
            or not all(isinstance(slot, str) and re.fullmatch(r"(?:0|[1-9][0-9]{0,3})", slot)
                       and isinstance(path, str) and ASSET.fullmatch(path)
                       for slot, path in row["materials"].items())
            or not isinstance(row["scale"], list) or len(row["scale"]) != 3
            or not all(finite(value, 100000) and value != 0 for value in row["scale"])
            or not isinstance(row["rotation"], list) or len(row["rotation"]) != 4
            or not all(finite(value, 1.001) for value in row["rotation"])
            or abs(sum(value * value for value in row["rotation"]) - 1) > .001):
        raise ValueError("Invalid shared model record.")


def read_records(path):
    if not path.is_file():
        return []
    document = json.loads(bounded(path, 8 * 1024 * 1024))
    if (not isinstance(document, dict) or set(document) != {"version", "models"}
            or type(document["version"]) is not int or document["version"] != 1):
        raise ValueError(f"Invalid shared model catalog file {path.name}.")
    rows = document["models"]
    if not isinstance(rows, list):
        raise ValueError(f"Invalid model list in {path.name}.")
    for row in rows:
        validate(row)
    return rows


def save_models(draft, rows):
    if not DRAFT_ID.fullmatch(draft):
        raise ValueError("Open a saved CTF draft to register models.")
    path = project_records() / (draft + ".json")
    entries = {model_key(row): row for row in read_records(path)}
    for row in rows:
        validate(row)
        entries[model_key(row)] = row
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, {"version": 1, "models": list(entries.values())})


def asset_path(asset):
    path = asset.get_path_name()
    if not ASSET.fullmatch(path):
        raise ValueError("Save each mesh and material as a project asset before registering it.")
    return path


def record_import(draft, planned, meshes, material_overrides):
    rows = []
    for row, path, _, rotation, scale in planned:
        reference = source_reference(row)
        kind, filename = reference.split(":", 1)
        rows.append({
            "name": PurePosixPath(filename).stem[:240],
            "category": "Buildings" if kind == "interiorinstance" else "Props",
            "mesh": asset_path(meshes[path]),
            "materials": {str(slot): asset_path(mat)
                          for slot, mat in material_overrides.get(reference, {}).items()},
            "scale": list(scale), "rotation": list(rotation),
        })
    save_models(draft, rows)


def register_selected(editor):
    draft = editor.draft_id()
    actors = editor.ACTORS.get_selected_level_actors()
    if not actors or any(not isinstance(actor, unreal.StaticMeshActor) for actor in actors):
        raise ValueError("Select the static mesh actors to add to the shared model library.")
    rows = []
    for actor in actors:
        component = actor.static_mesh_component
        mesh = component.static_mesh
        if not mesh:
            raise ValueError(f"{actor.get_actor_label()} has no mesh.")
        materials = {str(index): asset_path(mat) for index, mat in enumerate(component.get_materials())
                     if mat and mat != mesh.get_material(index)}
        rows.append({
            "name": actor.get_actor_label()[:240], "category": "Props",
            "mesh": asset_path(mesh), "materials": materials,
            "scale": list(actor.get_actor_scale3d().to_tuple()),
            "rotation": list(actor.get_actor_rotation().quaternion().to_tuple()),
        })
    save_models(draft, rows)
    unreal.log(f"T3_MODEL_LIBRARY_REGISTERED|{draft}|models={len(rows)}")


def catalog():
    entries = {}
    for path in sorted(project_records().glob("*.json")):
        if DRAFT_ID.fullmatch(path.stem):
            for row in read_records(path):
                entries.setdefault(model_key(row), row)
    if not entries:
        raise ValueError("Import a map with models or register selected static mesh actors first.")
    return entries


def resolve_assets(editor, rows):
    meshes, overrides = {}, {}
    for key, row in rows.items():
        mesh_path = row["mesh"]
        if mesh_path not in meshes:
            mesh = editor.ASSETS.load_asset(mesh_path.split(".", 1)[0])
            if not isinstance(mesh, unreal.StaticMesh):
                raise ValueError(f"The library mesh for {row['name']} is unavailable.")
            meshes[mesh_path] = mesh
        override = {}
        for slot, path in row["materials"].items():
            material = editor.ASSETS.load_asset(path.split(".", 1)[0])
            if (not isinstance(material, unreal.MaterialInterface)
                    or int(slot) >= len(meshes[mesh_path].get_editor_property("static_materials"))):
                raise ValueError(f"A library material for {row['name']} is unavailable.")
            override[int(slot)] = material
        if override:
            overrides[key] = override
    return meshes, overrides


def placement_point(editor):
    for actor in editor.ACTORS.get_selected_level_actors():
        return actor.get_actor_location()
    view = editor.EDITOR.get_level_viewport_camera_info()
    if view is None:
        raise ValueError("Click a level viewport or select a placement marker in the draft.")
    camera, rotation = view
    direction = rotation.get_forward_vector()
    hit = unreal.SystemLibrary.line_trace_single(
        editor.world(), camera, camera + direction * 100000, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True, [], unreal.DrawDebugTrace.NONE, True)
    if hit and hit.to_tuple()[0]:
        return hit.to_tuple()[5]
    return camera + direction * 800


def open_library(editor):
    global return_context

    draft = editor.draft_id()
    point = placement_point(editor)
    destination = editor.world().get_path_name().split(".")[0]
    entries = catalog()
    digest = sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:16]
    target = f"{LEVEL_ROOT}/SharedModels_{digest}"
    if not editor.SAVING.save_dirty_packages_with_dialog(True, True):
        return
    try:
        if editor.ASSETS.does_asset_exist(target):
            if not editor.LEVELS.load_level(target):
                raise RuntimeError("Could not open the shared model library.")
        else:
            meshes, overrides = resolve_assets(editor, entries)
            groups = {}
            for key, row in sorted(entries.items(), key=lambda pair: (
                    pair[1]["category"], pair[1]["name"].casefold(), pair[0])):
                groups.setdefault(row["category"], []).append(
                    (row["name"], key, row["mesh"], row["scale"], 0))
            if not editor.LEVELS.new_level(target):
                raise RuntimeError("Could not create the shared model library level.")
            for (_, key, *_), actor in populate_stage(editor, groups, meshes, overrides):
                name = entries[key]["name"]
                actor.set_actor_label(name)
                actor.set_editor_property("tags", [MODEL_TAG + key])
            ready = editor.ACTORS.spawn_actor_from_class(unreal.TargetPoint, unreal.Vector())
            if not ready:
                raise RuntimeError("Could not mark the model library complete.")
            ready.set_actor_label("Shared model catalog")
            ready.set_editor_property("tags", [LIBRARY_READY_TAG])
            ready.set_editor_property("is_editor_only_actor", True)
            if not editor.LEVELS.save_current_level():
                raise RuntimeError("Could not save the shared model library level.")
        actors = editor.ACTORS.get_all_level_actors()
        ready = any(LIBRARY_READY_TAG in map(str, actor.tags) for actor in actors)
        found = [str(tag).removeprefix(MODEL_TAG) for actor in actors for tag in actor.tags
                 if str(tag).startswith(MODEL_TAG)]
        if not ready or sorted(found) != sorted(entries):
            raise ValueError("The saved library level differs from its model catalog.")
    except Exception:
        if (editor.world().get_path_name().split(".")[0] != destination
                and not editor.LEVELS.load_level(destination)):
            raise RuntimeError("Could not reopen the draft after the library failed to load.")
        raise
    return_context = (target, destination, draft, point, entries)
    return target


def return_to_draft(editor, *, place=False):
    global return_context

    current = editor.world().get_path_name().split(".")[0]
    if return_context is None or current != return_context[0]:
        raise ValueError("Open the shared model library from the draft where you want to place the models.")
    _, destination, draft, point, catalog_entries = return_context
    selected = {}
    if place:
        for actor in editor.ACTORS.get_selected_level_actors():
            keys = [str(tag).removeprefix(MODEL_TAG) for tag in actor.tags if str(tag).startswith(MODEL_TAG)]
            if not isinstance(actor, unreal.StaticMeshActor) or len(keys) != 1 or keys[0] not in catalog_entries:
                raise ValueError("Select only model exhibits in the shared library.")
            row = catalog_entries[keys[0]]
            component = actor.static_mesh_component
            if not component.static_mesh or component.static_mesh.get_path_name() != row["mesh"]:
                raise ValueError(f"The displayed mesh for {row['name']} differs from the registered model.")
            selected[keys[0]] = row
        if not selected:
            raise ValueError("Select the models you want to place in the draft.")
        meshes, overrides = resolve_assets(editor, selected)
    if not editor.SAVING.save_dirty_packages_with_dialog(True, True):
        return
    if not editor.LEVELS.load_level(destination) or editor.draft_id() != draft:
        raise RuntimeError("Could not return to the target CTF draft.")
    return_context = None
    if not selected:
        return
    placed = []
    right_edge = None
    with unreal.ScopedEditorTransaction("Place shared library models"):
        try:
            for key, row in selected.items():
                rotation = unreal.Quat(*row["rotation"]).rotator()
                actor = editor.ACTORS.spawn_actor_from_class(unreal.StaticMeshActor, point, rotation)
                if not actor:
                    raise RuntimeError(f"Could not place {row['name']}.")
                placed.append(actor)
                component = actor.static_mesh_component
                if not component.set_static_mesh(meshes[row["mesh"]]):
                    raise RuntimeError(f"Could not assign the mesh for {row['name']}.")
                for slot, material in overrides.get(key, {}).items():
                    component.set_material(slot, material)
                for material in component.get_materials():
                    base = material.get_base_material() if material else None
                    if base and base.get_name().startswith(BAKED_MATERIAL_PREFIX):
                        mark_interior_lighting(actor, None, None)
                        break
                actor.set_actor_scale3d(unreal.Vector(*row["scale"]))
                origin, extent = actor.get_actor_bounds(False)
                y = point.y if right_edge is None else right_edge + extent.y + 100
                actor.set_actor_location(unreal.Vector(2 * point.x - origin.x, point.y + y - origin.y,
                                                       2 * point.z + extent.z - origin.z), False, False)
                right_edge = y + extent.y
                actor.set_actor_label(row["name"])
                actor.set_folder_path("TRIBES Import/Shared models")
        except Exception:
            for actor in reversed(placed):
                editor.ACTORS.destroy_actor(actor)
            raise
    editor.ACTORS.set_selected_level_actors(placed)
    unreal.log(f"T3_MODEL_LIBRARY_PLACED|{draft}|models={len(placed)}")
    return placed
