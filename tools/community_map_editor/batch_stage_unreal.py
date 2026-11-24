"""Capture each rebuilt draft's normal Direct Host inputs before the editor closes."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import unreal

from batch_rebuild import REMAINING, load_batch
from batch_rebuild_unreal import result_is_saved
from batch_stage import archive_stage, read_prepared, stage_root, tree_fingerprint
from conversion_rules import read, sha256_file, write
from minimap import export_host_minimap
from source_library import verified_draft_intake
from source_profiles import draft_edition


def output_label(name, draft):
    suffix = f" ({draft_edition(draft)}) [{draft}]"
    clean = " ".join("".join(char for char in name if char.isprintable()).split())
    return (clean or "Community map")[:64 - len(suffix)].rstrip() + suffix


def prepare_batch(editor, library, ident):
    root = stage_root(library, ident)
    if (root / "prepared").is_dir():
        return read_prepared(root, ident)
    batch = load_batch(library, ident)
    pending = [item for item in batch["items"] if item["status"] in REMAINING]
    results = [item for item in batch["items"] if item["status"] == "complete"]
    if pending or not results:
        raise ValueError("Finish the rebuild batch or release its remaining drafts before staging.")
    root.mkdir(parents=True, exist_ok=True)
    original = editor.world().get_path_name().split(".")[0]
    content = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()))
    with TemporaryDirectory(dir=root) as temp:
        prepared = Path(temp) / "prepared"
        prepared.mkdir()
        items = []
        try:
            for result in results:
                draft = result["attempts"][-1]
                if not result_is_saved(editor, library, draft, result["rules"]):
                    raise ValueError(f"The saved rebuild is unavailable for {draft}.")
                level = f"{editor.LEVEL_ROOT}/{draft}/Arena_{draft}_P"
                if not editor.LEVELS.load_level(level) or editor.draft_id() != draft:
                    raise ValueError(f"Could not open the rebuilt draft {draft}.")
                unreal.AutomationLibrary.finish_loading_before_screenshot()
                path = prepared / draft
                path.mkdir()
                write(path / "build-request.json", editor.marker_snapshot())
                editor.write_host_equipment(path, editor.host_equipment(draft))
                editor.record_build_rules(draft, path)
                intake = verified_draft_intake(library, draft)
                header = read(intake)
                if "minimap" in header:
                    export_host_minimap(intake, header["minimap"], draft, path)
                draft_file = content / (level.removeprefix("/Game/") + ".umap")
                items.append({"draft": draft, "label": output_label(result["name"], draft),
                              "level_sha256": sha256_file(draft_file), "files": tree_fingerprint(path)})
        finally:
            if editor.world().get_path_name().split(".")[0] != original and not editor.LEVELS.load_level(original):
                raise RuntimeError("Could not reopen the map that was active before staging.")
        plan = {"format": 1, "batch": ident, "items": items}
        write(prepared / "index.json", plan)
        prepared.rename(root / "prepared")
    return plan


def request_stage(editor, library, ident, *, fresh=False):
    request = os.environ.get("T3_EDITOR_BATCH_STAGE_REQUEST")
    if os.environ.get("T3_EDITOR_HOST_EXPORT") != "1" or request is None:
        raise ValueError("Run the map editor through the launcher with Direct Host export configured.")
    if not editor.SAVING.save_dirty_packages_with_dialog(True, True):
        return None
    if fresh:
        archive_stage(stage_root(library, ident))
    prepare_batch(editor, library, ident)
    write(Path(request), {"batch": ident})
    unreal.log(f"T3_BATCH_STAGE_REQUEST|{ident}")
    unreal.SystemLibrary.quit_editor()
