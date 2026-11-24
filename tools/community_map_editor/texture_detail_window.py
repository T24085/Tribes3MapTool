"""Choose and build a surface detail revision from the texture batch reviewer."""

from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from texture_detail import CHANNELS, merge_revision


def show_detail(parent, library, draft, root, row, completed):
    window = tk.Toplevel(parent)
    window.title("Merge surface detail")
    window.transient(parent)
    window.grab_set()
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)
    path = tk.StringVar(window)
    strength = tk.StringVar(window, value="50")
    repeats = tk.StringVar(window, value="1")
    operations = {"Roughness": "roughness", "Ambient occlusion": "occlusion"}
    operation = tk.StringVar(window, value="Roughness")
    source = tk.StringVar(window, value="Grayscale")
    targets = CHANNELS[row["source"]["channels"]]
    target = tk.StringVar(window, value="L" if "L" in targets else "")
    normal = row["role"] == "normal"
    busy = False
    result = Queue()

    ttk.Label(body, text="Detail PNG").grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Entry(body, textvariable=path, width=42).grid(row=0, column=1, sticky="ew")

    def browse():
        choice = filedialog.askopenfilename(parent=window, title="Choose detail PNG",
                                            filetypes=[("PNG image", "*.png")])
        if choice:
            path.set(choice)

    ttk.Button(body, text="Browse…", command=browse).grid(row=0, column=2, padx=(8, 0))
    for index, (label, variable, choices) in enumerate((
        ("Surface property", operation, tuple(operations)),
        ("Detail channel", source, ("Grayscale", "R", "G", "B", "A")),
        ("Target channel", target, targets),
    ), 1):
        if not normal:
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", pady=(8, 0))
            ttk.Combobox(body, textvariable=variable, values=choices, state="readonly").grid(
                row=index, column=1, sticky="ew", pady=(8, 0))
    for index, (label, variable, maximum) in enumerate((
        ("Strength (%)", strength, 100), ("Tiles per axis", repeats, min(64, *row["target"])),
    ), 4):
        ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(body, textvariable=variable, from_=1, to=maximum, width=8).grid(
            row=index, column=1, sticky="w", pady=(8, 0))
    message = tk.StringVar(window, value="Choose RGB or RGBA normals with the same tangent-axis convention."
                          if normal else "Select the channels used by this material.")
    ttk.Label(body, textvariable=message, wraplength=520).grid(row=6, columnspan=3, sticky="w", pady=(10, 0))
    bar = ttk.Frame(body)
    bar.grid(row=7, columnspan=3, sticky="e", pady=(12, 0))

    def close():
        if not busy:
            window.destroy()

    def poll():
        nonlocal busy
        try:
            candidate, channel, error = result.get_nowait()
        except Empty:
            window.after(100, poll)
            return
        busy = False
        if error is not None:
            start.configure(state="normal")
            cancel.configure(state="normal")
            message.set(str(error))
            return
        window.destroy()
        completed(candidate, channel)

    def run():
        nonlocal busy
        if busy:
            return
        selected = path.get().strip()
        if not selected:
            messagebox.showerror("Surface detail", "Choose a detail PNG first.", parent=window)
            return
        if not normal and not target.get():
            message.set("Choose the target channel used by this material.")
            return
        try:
            options = {
                "operation": "normal" if normal else operations[operation.get()],
                "strength": int(strength.get()), "repeats": int(repeats.get()),
                "source_channel": "grayscale" if source.get() == "Grayscale" else source.get(),
                "target_channel": target.get(),
            }
        except ValueError:
            message.set("Enter whole numbers for strength and tile count.")
            return
        busy = True
        start.configure(state="disabled")
        cancel.configure(state="disabled")
        message.set("Building an unapproved texture revision…")

        def build():
            try:
                candidate = merge_revision(library, draft, root, row, Path(selected), **options)
                result.put((candidate, "All" if normal else options["target_channel"], None))
            except Exception as error:
                result.put((None, None, error))

        Thread(target=build, daemon=True).start()
        window.after(100, poll)

    cancel = ttk.Button(bar, text="Cancel", command=close)
    cancel.pack(side="left", padx=(0, 8))
    start = ttk.Button(bar, text="Create revision", command=run)
    start.pack(side="left")
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
