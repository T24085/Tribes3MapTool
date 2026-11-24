"""Display saved decoder, Unreal import, and Direct Host pipeline results."""

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from conversion_rules import read
from tk_runtime import configure_tk


COLORS = {
    "passed": "#137148", "failed": "#b31b30", "running": "#155a95", "review": "#955000",
    "skipped": "#525d68", "cancelled": "#525d68", "pending": "#525d68",
}


def catalog(library, draft=None):
    rows = []
    for path in (library / "PipelineRuns").glob("run-*.json"):
        try:
            report = read(path, 256 * 1024)
            if not report.get("steps") or report.get("phase") not in ("reader", "draft", "host"):
                continue
            rows.append((path.stat().st_mtime_ns, path, report))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    rows.sort(key=lambda row: (bool(draft and row[2].get("draft") == draft), row[0]), reverse=True)
    return [(path, report) for _, path, report in rows]


def show_pipeline(library, draft=None, *, parent=None):
    configure_tk()
    window = tk.Toplevel(parent) if parent is not None else tk.Tk()
    window.title("TRIBES Map Editor · Conversion pipeline")
    window.minsize(810, 335)
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    top = ttk.Frame(body)
    top.pack(fill="x", pady=(0, 9))
    picker = ttk.Combobox(top, state="readonly", width=61)
    picker.pack(side="left", fill="x", expand=True)
    list_body = ttk.Frame(body)
    list_body.pack(fill="both", expand=True)
    grid = ttk.Treeview(list_body, show="headings", columns=("stage", "state", "time", "detail"))
    for key, title, width in (
            ("stage", "Step", 235), ("state", "Status", 100), ("time", "Seconds", 80), ("detail", "Details", 370)):
        grid.heading(key, text=title)
        grid.column(key, width=width, stretch=key == "detail")
    for state, color in COLORS.items():
        grid.tag_configure(state, foreground=color)
    grid.pack(side="left", fill="both", expand=True)
    scroll = ttk.Scrollbar(list_body, command=grid.yview)
    scroll.pack(side="right", fill="y")
    grid.configure(yscrollcommand=scroll.set)
    status = ttk.Label(body, wraplength=760)
    status.pack(anchor="w", pady=(7, 0))
    reports = []

    def update(_event=None):
        grid.delete(*grid.get_children())
        i = picker.current()
        if i < 0:
            status.configure(text="Pipeline runs appear here after a decode, draft import, or Direct Host build.")
            return
        path, report = reports[i]
        status.configure(text=f"Profile {report['profile_sha256'][:12]} · {path.name}")
        for row in report["steps"]:
            state = row["status"]
            time = "" if row["seconds"] is None else row["seconds"]
            grid.insert("", "end", values=(row["label"], state.title(), time, row["detail"]), tags=(state,))

    def refresh():
        selected = reports[picker.current()][0] if picker.current() >= 0 else None
        reports[:] = catalog(library, draft)
        names = [f"{report['phase'].title()} · {report['title']} · {report['draft'] or path.stem[-5:]}"
                 for path, report in reports]
        picker.configure(values=names)
        if reports:
            picker.current(next((index for index, (path, _) in enumerate(reports) if path == selected), 0))
        else:
            picker.set("")
        update()

    ttk.Button(top, text="Refresh", command=refresh).pack(side="right", padx=(8, 0))
    picker.bind("<<ComboboxSelected>>", update)
    window.bind("<Escape>", lambda _event: window.destroy())
    refresh()
    if parent is None:
        window.mainloop()
    return window


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("--draft")
    args = parser.parse_args()
    show_pipeline(args.library, args.draft)
