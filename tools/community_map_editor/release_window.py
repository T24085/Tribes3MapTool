"""Inspect staged builds and read the current Direct Host map catalog."""

import argparse
from http.client import HTTPException
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, ttk

from map_releases import HostCatalog, capture_export, release_history, verify_release
from tk_runtime import configure_tk


def add_table(parent, columns):
    table = ttk.Treeview(parent, columns=tuple(name for name, _, _ in columns), show="headings", selectmode="browse")
    for name, title, width in columns:
        table.heading(name, text=title)
        table.column(name, width=width, minwidth=75)
    table.pack(fill="both", expand=True, side="left")
    scroll = ttk.Scrollbar(parent, orient="vertical", command=table.yview)
    scroll.pack(side="right", fill="y")
    table.configure(yscrollcommand=scroll.set)
    return table


def show_releases(library, draft=None, release_tool=None):
    configure_tk()
    window = tk.Tk()
    window.title("TRIBES Map Editor · Map releases")
    window.minsize(900, 485)
    body = ttk.Frame(window, padding=14)
    body.pack(fill="both", expand=True)
    top = ttk.Frame(body)
    top.pack(fill="x", pady=(0, 10))
    ttk.Label(top, text="Host HTTPS address").pack(side="left", padx=(0, 8))
    address = ttk.Entry(top, width=44)
    address.pack(side="left", fill="x", expand=True)
    tabs = ttk.Notebook(body)
    tabs.pack(fill="both", expand=True)
    stages, live_page = ttk.Frame(tabs), ttk.Frame(tabs)
    tabs.add(stages, text="Staged builds")
    tabs.add(live_page, text="Live catalog")
    staged = add_table(stages, (("map", "Map", 265), ("id", "Map ID", 105), ("date", "Recorded (UTC)", 160),
                                ("hash", "Package hash", 120), ("files", "Map files", 90)))
    current = add_table(live_page, (("map", "Map", 265), ("id", "Map ID", 140), ("build", "Content hash", 125),
                                    ("manifest", "Manifest hash", 125)))
    detail = tk.StringVar()
    status = tk.StringVar(value="Select a staged build to verify or compare with the live catalog.")
    ttk.Label(body, textvariable=detail, wraplength=840, justify="left").pack(anchor="w", pady=(8, 0))
    actions = ttk.Frame(body)
    actions.pack(fill="x", pady=(8, 3))
    staging = ttk.Frame(body)
    staging.pack(fill="x", pady=(0, 8))
    links = tk.BooleanVar(value=False)
    ttk.Label(body, textvariable=status, wraplength=840).pack(anchor="w")
    entries, live = {}, {}
    busy = False
    service = None

    def chosen(table):
        selection = table.selection()
        return selection[0] if selection else ""

    def selection_changed(_event=None):
        on_stage = tabs.select() == str(stages)
        entry = entries.get(chosen(staged)) if on_stage else None
        host_map = live.get(chosen(current)) if not on_stage else None
        if entry:
            detail.set(f"Package SHA-256  {entry['files_sha256']}\n"
                       f"Baseline SHA-256  {entry['base_inventory_sha256']}\n{entry['folder']}")
        elif host_map:
            detail.set(f"Content SHA-256  {host_map['contentHash']}\n"
                       f"Manifest SHA-256  {host_map['manifestHash']}\n{live_source.get()}")
        else:
            detail.set("")
        local_ready = not busy and entry is not None
        verify_button.configure(state="normal" if local_ready else "disabled")
        compare_button.configure(state="normal" if local_ready and entry["draft"].casefold() in live else "disabled")
        copy_button.configure(state="normal" if entry or host_map else "disabled")
        add_button.configure(state="disabled" if busy else "normal")
        connect_button.configure(state="disabled" if busy else "normal")
        release_ready = (local_ready and release_tool is not None and release_tool.is_file()
                         and (Path(entry["folder"]) / "map-release.json").is_file())
        for control in (stage_button, rollback_button, links_control):
            control.configure(state="normal" if release_ready else "disabled")

    def refresh_staged(select_id=None):
        staged.delete(*staged.get_children())
        entries.clear()
        for entry in release_history(library, draft):
            key = entry["files_sha256"]
            entries[key] = entry
            stamp = entry["recorded"][:19].replace("T", " ")
            staged.insert("", "end", iid=key, values=(entry["label"], entry["draft"], stamp, key[:12], len(entry["members"])))
        if entries:
            staged.selection_set(select_id if select_id in entries else next(iter(entries)))
        selection_changed()

    def check_async(message, work, done):
        nonlocal busy
        if busy:
            return
        busy = True
        status.set(message)
        selection_changed()
        results = Queue()

        def run():
            try:
                results.put((True, work()))
            except (OSError, HTTPException, ValueError, TypeError, KeyError, AttributeError) as error:
                results.put((False, str(error)))

        def poll():
            nonlocal busy
            try:
                ok, result = results.get_nowait()
            except Empty:
                window.after(60, poll)
                return
            busy = False
            if ok:
                done(result)
            else:
                status.set(result)
            selection_changed()

        Thread(target=run, daemon=True).start()
        window.after(60, poll)

    def add_export():
        folder = filedialog.askdirectory(parent=window, title="Choose a portable export folder", mustexist=True)
        if not folder:
            return

        def added(entry):
            tabs.select(stages)
            refresh_staged(entry["files_sha256"])
            status.set("Verified export added to release history.")

        check_async("Verifying the portable export…", lambda: capture_export(library, Path(folder)), added)

    def connect():
        nonlocal service
        if busy:
            return
        origin = address.get().strip()
        service = None
        live_source.set("")
        live.clear()
        current.delete(*current.get_children())
        selection_changed()
        try:
            check = HostCatalog(origin)
        except ValueError as error:
            status.set(str(error))
            return

        def received(index):
            nonlocal service
            service = check
            live_source.set(origin)
            live.update(index)
            for key, row in sorted(live.items(), key=lambda item: item[1]["label"].casefold()):
                current.insert("", "end", iid=key, values=(row["label"], row["id"], row["contentHash"][:12], row["manifestHash"][:12]))
            if live:
                current.selection_set(draft.casefold() if draft and draft.casefold() in live else current.get_children()[0])
            status.set(f"Read {len(live)} maps from {origin}.")
            tabs.select(live_page)

        check_async("Reading the running host's catalog…", check.index, received)

    def verify():
        entry = entries[chosen(staged)]
        check_async("Checking the staged package bytes…", lambda: verify_release(entry),
                    lambda _: status.set(f"Verified {entry['label']} against the recorded package hash."))

    def compare():
        entry = entries[chosen(staged)]
        host_map = live[entry["draft"].casefold()]

        def match():
            verify_release(entry)
            return service.compare(host_map, entry)

        def report(same):
            result = "Map-owned files, objectives, and minimap match" if same else "The live map differs from this build"
            status.set(f"{result} · {live_source.get()} · {host_map['manifestHash'][:12]}")

        check_async("Comparing the staged map with the host manifest…", match, report)

    def copy_details():
        window.clipboard_clear()
        window.clipboard_append(detail.get())

    def prepare_bundle(action):
        entry = entries[chosen(staged)]
        source = filedialog.askdirectory(parent=window, mustexist=True, title=(
            "Choose the host bundle containing this release" if action == "rollback"
            else "Choose the host baseline for this release"))
        if not source:
            return
        output = filedialog.askdirectory(parent=window, mustexist=True, title="Choose a folder for the separate staging copy")
        if not output:
            return
        destination = Path(output) / f"{entry['draft']}-{action}-{entry['files_sha256'][:10]}"
        argv = [sys.executable, "-E", "-X", "utf8", str(release_tool), action, entry["folder"], source, str(destination)]
        if links.get():
            argv.append("--link-content")

        def build():
            verify_release(entry)
            result = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", check=False)
            if result.returncode:
                raise ValueError(result.stderr.strip()[-700:] or "The staging copy could not be verified.")
            return destination

        check_async("Building and verifying a separate Direct Host bundle…", build,
                    lambda folder: status.set(f"Verified staging bundle ready at {folder}"))

    live_source = tk.StringVar()
    connect_button = ttk.Button(top, text="Read live catalog", command=connect)
    connect_button.pack(side="left", padx=(8, 0))
    add_button = ttk.Button(actions, text="Add existing export…", command=add_export)
    verify_button = ttk.Button(actions, text="Verify staged files", command=verify)
    compare_button = ttk.Button(actions, text="Compare with live", command=compare)
    copy_button = ttk.Button(actions, text="Copy details", command=copy_details)
    for button in (add_button, verify_button, compare_button, copy_button):
        button.pack(side="left", padx=(0, 8))
    stage_button = ttk.Button(staging, text="Stage release…", command=lambda: prepare_bundle("stage"))
    rollback_button = ttk.Button(staging, text="Stage rollback…", command=lambda: prepare_bundle("rollback"))
    links_control = ttk.Checkbutton(staging, text="Hardlink unchanged cooked files on the same drive", variable=links)
    for control in (stage_button, rollback_button, links_control):
        control.pack(side="left", padx=(0, 8))
    for widget in (staged, current):
        widget.bind("<<TreeviewSelect>>", selection_changed)
    tabs.bind("<<NotebookTabChanged>>", selection_changed)
    address.bind("<Return>", lambda _: connect())
    window.bind("<Escape>", lambda _: window.destroy())
    refresh_staged()
    window.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("--draft")
    parser.add_argument("--release-tool", type=Path)
    args = parser.parse_args()
    show_releases(args.library, args.draft, args.release_tool)
