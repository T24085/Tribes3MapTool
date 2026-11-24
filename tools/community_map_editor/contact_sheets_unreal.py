"""Capture map exhibits and textures from their saved Unreal draft."""

import json
from math import hypot
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

import unreal

from draft_showcase import CATEGORIES, package
from source_library import verified_draft_intake
from texture_batches import ASSET
from texture_batches_unreal import export_png


def image_record(filename):
    return {"path": str(filename), "sha256": None, "label": ""}


def installed_intake(draft):
    library = os.environ.get("T3_CONVERTER_LIBRARY")
    return verified_draft_intake(Path(library), draft) if library else None


def capture_textures(editor, work):
    materials = {}
    for actor in editor.ACTORS.get_all_level_actors():
        if isinstance(actor, unreal.StaticMeshActor):
            for material in actor.static_mesh_component.get_materials():
                if material is not None:
                    materials[material.get_path_name()] = material
    library = unreal.MaterialEditingLibrary
    texture_assets = {}
    for material in materials.values():
        base = material.get_base_material()
        if base is None:
            continue
        textures = list(library.get_used_textures(base))
        if isinstance(material, unreal.MaterialInstanceConstant):
            textures.extend(library.get_material_instance_texture_parameter_value(material, name)
                            for name in library.get_texture_parameter_names(base))
        for asset in textures:
            if isinstance(asset, unreal.Texture2D) and ASSET.fullmatch(asset.get_path_name()):
                texture_assets[asset.get_path_name()] = asset
    items, notices = [], []
    for index, asset in enumerate(sorted(texture_assets.values(), key=lambda asset: asset.get_path_name())):
        filename = work / f"texture-{index:04}.png"
        try:
            export_png(asset, filename)
        except (OSError, ValueError, RuntimeError) as exc:
            notices.append({"asset": asset.get_name(), "reason": str(exc)})
            continue
        items.append({"category": "Textures", "title": asset.get_name(), "detail": "Draft material",
                      "pictures": [image_record(filename)]})
    return items, notices


def capture_exhibits(editor, work):
    subjects = [actor for actor in editor.ACTORS.get_all_level_actors()
                if isinstance(actor, unreal.StaticMeshActor) and "T3ShowcaseExhibit" in map(str, actor.tags)]
    if not subjects:
        return []
    renderer = unreal.RenderingLibrary
    world = editor.world()
    rotation = unreal.Rotator(pitch=-39, yaw=35)
    camera = editor.ACTORS.spawn_actor_from_class(unreal.SceneCapture2D, unreal.Vector(), rotation, transient=True)
    if not camera:
        raise RuntimeError("Unreal did not create the contact sheet camera.")
    target = None
    try:
        target = renderer.create_render_target2d(world, 320, 256, unreal.TextureRenderTargetFormat.RTF_RGBA8)
        if not target:
            raise RuntimeError("Unreal did not allocate a contact sheet render target.")
        capture = camera.capture_component2d
        for key, value in (
                ("capture_every_frame", False), ("capture_on_movement", False), ("fov_angle", 45),
                ("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR),
                ("primitive_render_mode", unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST),
                ("texture_target", target)):
            capture.set_editor_property(key, value)
        unreal.AutomationLibrary.finish_loading_before_screenshot()
        items = []
        for index, actor in enumerate(subjects):
            category = str(actor.get_folder_path())
            if category not in CATEGORIES:
                continue
            capture.clear_show_only_components()
            capture.show_only_component(actor.static_mesh_component)
            origin, extent = actor.get_actor_bounds(False)
            distance = max(240, 3.6 * hypot(extent.x, extent.y, extent.z))
            camera.set_actor_location(origin - rotation.get_forward_vector() * distance, False, False)
            capture.capture_scene()
            name = f"exhibit-{index:04}.png"
            renderer.export_render_target(world, target, str(work), name)
            filename = work / name
            with filename.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise RuntimeError("Unreal did not export the model preview.")
            title, _, detail = actor.get_actor_label().partition(" · ")
            items.append({"category": category, "title": title, "detail": detail,
                          "pictures": [image_record(filename)]})
        return items
    finally:
        editor.ACTORS.destroy_actor(camera)
        if target:
            renderer.release_render_target2d(target)


def export_report(editor, draft, *, intake=None):
    draft_level = editor.world().get_path_name().split(".")[0]
    showcase = package(draft)
    if editor.draft_id() != draft or not editor.ASSETS.does_asset_exist(showcase):
        raise ValueError("Open an imported draft with a saved model showcase.")
    if "-nullrhi" in unreal.SystemLibrary.get_command_line().lower().split():
        raise RuntimeError("Contact sheets need an editor session with rendering enabled.")
    saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    parent = Path(saved) / "T3ContactSheets" / draft
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="t3-contact-") as temp, TemporaryDirectory(dir=parent) as output:
        work = Path(temp)
        items, notices = capture_textures(editor, work)
        try:
            if not editor.LEVELS.load_level(showcase):
                raise RuntimeError("Could not open the model showcase for capture.")
            items.extend(capture_exhibits(editor, work))
        finally:
            if not editor.LEVELS.load_level(draft_level):
                raise RuntimeError("Could not reopen the draft after capturing its contact sheets.")
        request = work / "request.json"
        request.write_text(json.dumps({
            "draft": draft, "intake": str(intake) if intake else None, "items": items, "notices": notices,
            "texture_library": os.environ.get("T3_TEXTURE_LIBRARY"),
        }), encoding="utf-8")
        staged = Path(output) / "sheets"
        tool = Path(os.environ["T3_CONVERTER_TOOL"]) / "contact_sheets.py"
        result = subprocess.run([os.environ["T3_CONVERTER_PYTHON"], "-E", "-X", "utf8",
                                 str(tool), str(request), str(staged)], capture_output=True, text=True, encoding="utf-8")
        if result.returncode or not (staged / "index.json").is_file():
            raise RuntimeError(result.stderr.strip()[-1000:] or "The contact sheet compositor did not finish.")
        destination = parent / ("sheets-" + uuid4().hex[:12])
        staged.rename(destination)
    unreal.log(f"T3_MAP_CONTACT_SHEETS|{draft}|{destination}")
    return destination
