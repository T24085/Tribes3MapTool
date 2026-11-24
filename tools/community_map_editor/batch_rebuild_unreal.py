"""Run selected batch rebuilds through the normal Unreal draft importer."""

import json
import os
from pathlib import Path
import subprocess
from time import monotonic

import unreal

from batch_rebuild import REMAINING, load_batch, save_batch, saved_batches
from conversion_rules import draft_snapshot, sha256_file
from import_intake import import_map
from source_library import verified_draft_intake
from source_profiles import PREFIX


def result_is_saved(editor, library, draft, rules):
    level = f"{editor.LEVEL_ROOT}/{draft}/Arena_{draft}_P"
    linked = verified_draft_intake(library, draft)
    return (editor.ASSETS.does_asset_exist(level) and linked is not None
            and sha256_file(linked) == rules["intake_sha256"] and draft_snapshot(library, draft) == rules)


def run_batch(editor, library, ident, *, cancelled=lambda: False, step=lambda _message: None):
    batch = load_batch(library, ident)
    history, _ = saved_batches(library)
    reserved = {draft for plan in history for item in plan["items"] for draft in item["attempts"]}
    items = batch["items"]
    for item in items:
        if item["status"] == "complete" and not result_is_saved(editor, library, item["attempts"][-1], item["rules"]):
            raise ValueError(f"The completed result for {item['source']} is missing. Restore it before resuming this batch.")
    for item in items:
        if item["status"] not in REMAINING:
            continue
        if cancelled():
            break
        item["status"], item["error"] = "building", ""
        target = None
        try:
            source = item["source"]
            archived = draft_snapshot(library, source)
            manifest = verified_draft_intake(library, source)
            level = f"{editor.LEVEL_ROOT}/{source}/Arena_{source}_P"
            if (archived is None or manifest is None or archived["sha256"] != item["base"]
                    or not editor.ASSETS.does_asset_exist(level)):
                raise ValueError("The original draft or its linked rule revision is unavailable.")
            target, _ = editor.new_identity(PREFIX[item["rules"]["profile"]["id"]], reserved=reserved)
            reserved.add(target)
            item["attempts"].append(target)
            save_batch(library, batch)
            started = monotonic()
            step(f"{item['name']} ({source})")

            def complete(draft):
                if draft != target or not result_is_saved(editor, library, draft, item["rules"]):
                    raise ValueError("The rebuilt draft could not be confirmed.")
                item["status"], item["seconds"] = "complete", round(monotonic() - started, 2)
                save_batch(library, batch)

            import_map(manifest, **item["rules"]["options"], recorded_rules=item["rules"],
                       batch_target=target, on_finished=complete)
        except (OSError, ValueError, RuntimeError) as exc:
            item["status"], item["seconds"], item["error"] = "failed", None, str(exc)[-500:]
            save_batch(library, batch)
            unreal.log_warning(f"Batch rebuild failed for {item['source']}: {exc}")
    return batch


def choose_and_run(editor):
    library = Path(os.environ["T3_CONVERTER_LIBRARY"])
    window = Path(os.environ["T3_CONVERTER_TOOL"]) / "batch_window.py"
    config = {"drafts": editor.list_drafts(), "can_stage": os.environ.get("T3_EDITOR_HOST_EXPORT") == "1"}
    result = subprocess.run([os.environ["T3_CONVERTER_PYTHON"], "-E", "-X", "utf8", str(window), str(library)],
                            input=json.dumps(config), capture_output=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-700:] or "The batch rebuild window failed.")
    raw = result.stdout.strip()
    if not raw:
        return None
    choice = json.loads(raw)
    if (not isinstance(choice, dict) or set(choice) != {"action", "batch"}
            or choice["action"] not in ("rebuild", "stage", "stage-new")):
        raise ValueError("The batch rebuild window returned an invalid action.")
    ident = choice["batch"]
    if choice["action"] != "rebuild":
        from batch_stage_unreal import request_stage
        return request_stage(editor, library, ident, fresh=choice["action"] == "stage-new")
    batch = load_batch(library, ident)
    remaining = sum(item["status"] in REMAINING for item in batch["items"])
    if not remaining or not editor.SAVING.save_dirty_packages_with_dialog(True, True):
        return None
    with unreal.ScopedSlowTask(remaining, "Rebuilding map drafts") as progress:
        progress.make_dialog(True)
        batch = run_batch(editor, library, ident, cancelled=progress.should_cancel,
                          step=lambda title: progress.enter_progress_frame(1, title))
    done = [f"{item['source']} → {item['attempts'][-1]}" for item in batch["items"] if item["status"] == "complete"]
    now = sum(item["status"] in REMAINING for item in batch["items"])
    if len(done) > 12:
        done = done[:12] + ["Open Batch Rebuilds for the full list."]
    contents = "\n".join(done)
    unreal.EditorDialog.show_message("Batch rebuild", f"{len(batch['items']) - now} processed · {now} remaining\n{contents}",
                                     unreal.AppMsgType.OK)
    return batch
