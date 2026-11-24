"""Import and export game profiles from the Unreal editor menu."""

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from conversion_rules import write
from profile_exchange import active_profile, collect_bundle, install_bundle
from source_profiles import NAMES, PREFIX, T2
from tk_runtime import configure_tk


def show_profiles(library, draft=None):
    configure_tk()
    window = tk.Tk()
    window.title("TRIBES Map Editor · Game profiles")
    window.minsize(525, 170)
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Game profile", font="TkHeadingFont").pack(anchor="w")
    choices = {name: profile for profile, name in NAMES.items()}
    default = next((profile for profile, prefix in PREFIX.items() if (draft or "").startswith(prefix)), T2)
    picker = ttk.Combobox(body, state="readonly", values=tuple(choices))
    picker.set(NAMES[default])
    picker.pack(fill="x", pady=(6, 8))
    status = tk.StringVar(window)
    ttk.Label(body, textvariable=status, wraplength=475).pack(anchor="w")
    bar = ttk.Frame(body)
    bar.pack(fill="x", pady=(12, 0))

    def refresh(_event=None):
        try:
            active = active_profile(library, choices[picker.get()])
            detail = (f"Imported profile · {active['sha256'][:12]}" if active else "Workspace defaults")
            status.set(f"{detail}. Changes apply to new drafts and rebuilds using the latest rules.")
        except (OSError, ValueError, TypeError, RecursionError) as error:
            status.set(str(error))

    def export_profile():
        profile = choices[picker.get()]
        filename = filedialog.asksaveasfilename(parent=window, title="Export game profile",
                                                initialfile=profile + ".json", defaultextension=".json",
                                                filetypes=[("Game profile", "*.json")])
        if not filename:
            return
        try:
            write(Path(filename), collect_bundle(library, profile))
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            messagebox.showerror("Export game profile", str(error), parent=window)
        else:
            status.set(f"Exported the {NAMES[profile]} profile.")

    def import_profile():
        filename = filedialog.askopenfilename(parent=window, title="Import game profile",
                                             filetypes=[("Game profile", "*.json")])
        if not filename:
            return
        try:
            bundle = install_bundle(library, Path(filename))
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            messagebox.showerror("Import game profile", str(error), parent=window)
        else:
            picker.set(NAMES[bundle["profile"]])
            refresh()

    ttk.Button(bar, text="Export profile…", command=export_profile).pack(side="left", padx=(0, 8))
    ttk.Button(bar, text="Import profile…", command=import_profile).pack(side="left")
    ttk.Button(bar, text="Close", command=window.destroy).pack(side="right")
    picker.bind("<<ComboboxSelected>>", refresh)
    window.bind("<Escape>", lambda _event: window.destroy())
    refresh()
    window.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("--draft")
    args = parser.parse_args()
    show_profiles(args.library, args.draft)
