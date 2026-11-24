"""Choose affected map drafts or resume a queued rebuild in the map editor."""

import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from batch_rebuild import REMAINING, compare_drafts, create_batch, estimated_seconds, load_batch, save_batch
from batch_stage import stage_root
from conversion_rules import read
from tk_runtime import configure_tk


def make_table(parent, columns, *, mode="browse"):
    outer = ttk.Frame(parent)
    outer.pack(fill="both", expand=True)
    table = ttk.Treeview(outer, columns=[key for key, _, _ in columns], show="headings", selectmode=mode)
    for key, title, width in columns:
        table.heading(key, text=title)
        table.column(key, width=width, stretch=key in {"name", "details"})
    table.pack(side="left", fill="both", expand=True)
    scroll = ttk.Scrollbar(outer, command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    return table


def timing_label(batches, total):
    estimate = estimated_seconds(batches, total)
    if estimate is None:
        return f"{total} drafts · No previous rebuild timings"
    if estimate < 60:
        return f"{total} drafts · About {max(1, estimate)} seconds"
    return f"{total} drafts · About {round(estimate/60)} minutes"


def run(library, draft_ids, *, can_stage=True):
    configure_tk()
    root = tk.Tk()
    root.title("TRIBES Map Editor · Batch Rebuilds")
    root.minsize(860, 430)
    body = ttk.Frame(root, padding=12)
    body.pack(fill="both", expand=True)
    notice = ttk.Label(body, text="Checking which saved drafts use changed conversion rules…")
    notice.pack(anchor="w", pady=(0, 8))
    root.update_idletasks()
    rows, batches, notices = compare_drafts(library, draft_ids)
    affected = {row["draft"] for row in rows if row["status"] == "Affected"}
    notice.configure(text=f"{len(affected)} affected drafts. Rebuilds create new local drafts from the original intake.")
    if notices:
        ttk.Label(body, text=f"{len(notices)} unreadable batch records. {notices[0]}", wraplength=780).pack(anchor="w")
    tabs = ttk.Notebook(body)
    tabs.pack(fill="both", expand=True)
    current = ttk.Frame(tabs, padding=10)
    saved = ttk.Frame(tabs, padding=10)
    tabs.add(current, text="Drafts")
    tabs.add(saved, text="Saved batches")
    table = make_table(current, (("draft", "Draft", 120), ("name", "Map", 200),
                                 ("status", "Status", 110), ("details", "Changed rule values", 360)), mode="extended")
    by_draft = {row["draft"]: row for row in rows}
    for row in sorted(rows, key=lambda item: (item["status"] != "Affected", item["name"].casefold())):
        table.insert("", "end", iid=row["draft"], values=(
            row["draft"], row["name"], row["status"], row["reason"] or ", ".join(row["changes"]),
        ))
    if affected:
        table.selection_set(*sorted(affected))
    buttons = ttk.Frame(current)
    buttons.pack(fill="x", pady=(8, 0))
    estimate = ttk.Label(buttons)
    estimate.pack(side="left")
    outcome = None

    def selected():
        return [by_draft[draft] for draft in table.selection() if draft in affected]

    def draft_selection(_event=None):
        count = len(selected())
        estimate.configure(text=timing_label(batches, count) if count else "Select affected drafts to rebuild")
        start.configure(state="normal" if count else "disabled")

    def start_rebuild():
        nonlocal outcome
        try:
            outcome = {"action": "rebuild", "batch": create_batch(library, selected())}
        except (OSError, ValueError) as exc:
            messagebox.showerror("Batch rebuilds", str(exc), parent=root)
            return
        root.destroy()

    start = ttk.Button(buttons, text="Rebuild selected", command=start_rebuild)
    start.pack(side="right")
    table.bind("<<TreeviewSelect>>", draft_selection)
    draft_selection()

    chooser = ttk.Combobox(saved, values=[batch["id"] for batch in batches], state="readonly", width=24)
    chooser.pack(anchor="w", pady=(0, 8))
    details = make_table(saved, (("draft", "Draft", 110), ("name", "Map", 155), ("status", "State", 95),
                                 ("result", "New draft", 110), ("details", "Last error", 300)))
    saved_buttons = ttk.Frame(saved)
    saved_buttons.pack(fill="x", pady=(8, 0))
    resume_estimate = ttk.Label(saved_buttons)
    resume_estimate.pack(side="left")
    selected_batch = None

    def load_selected(_event=None):
        nonlocal selected_batch
        details.delete(*details.get_children())
        selected_batch = next((batch for batch in batches if batch["id"] == chooser.get()), None)
        staging_result = {}
        staging = stage_root(library, selected_batch["id"]) if selected_batch else None
        if staging is not None and (staging/"last-result.json").is_file():
            try:
                staging_result = read(staging/"last-result.json")
            except (OSError, ValueError):
                pass
        complete = {row["draft"] for row in staging_result.get("complete", [])}
        errors = {row["draft"]: row["reason"] for row in staging_result.get("errors", [])}
        remaining = 0
        for item in selected_batch["items"] if selected_batch else ():
            remaining += int(item["status"] in REMAINING)
            built = item["attempts"][-1] if item["status"] == "complete" else ""
            state = "Interrupted" if item["status"] == "building" else item["status"].title()
            state = "Staged" if built in complete else state
            details.insert("", "end", values=(item["source"], item["name"], state, built, errors.get(built, item["error"])))
        resume.configure(state="normal" if remaining else "disabled")
        release.configure(state="normal" if remaining else "disabled")
        ready = selected_batch and not remaining and any(item["status"] == "complete" for item in selected_batch["items"])
        prepared = staging is not None and (staging/"prepared").is_dir()
        stage.configure(state="normal" if can_stage and ready else "disabled",
                        text="Resume cook & stage…" if prepared else "Cook & stage…")
        stage_new.configure(state="normal" if can_stage and ready and prepared else "disabled")
        if can_stage and ready and prepared and complete:
            count = sum(item["status"] == "complete" for item in selected_batch["items"])
            resume_estimate.configure(text=f"{len(complete)} / {count} maps staged")
            return
        resume_estimate.configure(text=timing_label(batches, remaining) if remaining else "No drafts remaining")

    def resume_batch():
        nonlocal outcome
        if selected_batch:
            outcome = {"action": "rebuild", "batch": selected_batch["id"]}
            root.destroy()

    def stage_batch(action="stage"):
        nonlocal outcome
        if selected_batch:
            outcome = {"action": action, "batch": selected_batch["id"]}
            root.destroy()

    def release_drafts():
        try:
            plan = load_batch(library, selected_batch["id"])
            for item in plan["items"]:
                if item["status"] in REMAINING:
                    item["status"] = "released"
            save_batch(library, plan)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Batch rebuilds", str(exc), parent=root)
            return
        root.destroy()

    resume = ttk.Button(saved_buttons, text="Resume batch", command=resume_batch, state="disabled")
    resume.pack(side="right")
    stage = ttk.Button(saved_buttons, text="Cook & stage…", command=stage_batch, state="disabled")
    stage.pack(side="right", padx=(0, 8))
    stage_new = ttk.Button(saved_buttons, text="New staging run…", command=lambda: stage_batch("stage-new"), state="disabled")
    stage_new.pack(side="right", padx=(0, 8))
    release = ttk.Button(saved_buttons, text="Release remaining drafts", command=release_drafts, state="disabled")
    release.pack(side="right", padx=(0, 8))
    chooser.bind("<<ComboboxSelected>>", load_selected)
    unfinished = next((index for index, batch in enumerate(batches)
                       if any(item["status"] in REMAINING for item in batch["items"])), None)
    if batches:
        chooser.current(unfinished if unfinished is not None else 0)
        load_selected()
    if unfinished is not None:
        tabs.select(saved)
    root.bind("<Escape>", lambda _event: root.destroy())
    root.mainloop()
    return outcome


if __name__ == "__main__":
    config = json.load(sys.stdin)
    selected = run(Path(sys.argv[1]), config["drafts"], can_stage=config["can_stage"])
    if selected is not None:
        print(json.dumps(selected), flush=True)
