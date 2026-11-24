"""Edit the applicable conversion rule layer from the map import window."""

import tkinter as tk
from tkinter import messagebox, ttk

from conversion_rules import current_layers, edit_scope, history, profile_config, project, selectors, validate_layers
from profile_exchange import active_profile
from source_profiles import NAMES


def show_rules(parent, library, header, rows, selected):
    bundle = active_profile(library, header["profile"]) or {}
    profile = profile_config(bundle.get("conversion"), source_profile=header["profile"])
    scopes = selectors(profile, header, rows)
    records = profile["conversions"]
    options = {"This map": (scopes["map"], None), f"{NAMES[header['profile']]} defaults": (scopes["game"], None)}
    if selected is not None:
        for group, scope in scopes["objects"].items():
            if scope.endswith("-" + str(selected["id"])):
                options["Selected object"] = (scope, group)
                break

    form = tk.Toplevel(parent)
    form.title("Conversion rules")
    form.transient(parent)
    form.grab_set()
    body = ttk.Frame(form, padding=16)
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=1)
    ttk.Label(body, text=header["name"], font="TkHeadingFont").grid(row=0, sticky="w")
    controls = ttk.Frame(body)
    controls.grid(row=1, sticky="ew", pady=(8, 12))
    ttk.Label(controls, text="Scope").pack(side="left", padx=(0, 7))
    scope = ttk.Combobox(controls, values=tuple(options), state="readonly", width=19)
    scope.pack(side="left")
    scope.set("Selected object" if len(options) == 3 else "This map")
    versions = ttk.Combobox(controls, state="readonly", width=14)
    versions.pack(side="right", padx=(7, 0))
    ttk.Label(controls, text="Load revision").pack(side="right")
    fields = ttk.Frame(body)
    fields.grid(row=2, sticky="nsew")
    fields.columnconfigure(0, weight=1)
    status = tk.StringVar(form)
    ttk.Label(body, textvariable=status, wraplength=525).grid(row=3, sticky="w", pady=(10, 0))
    inputs = {}
    chain = []

    def refresh(_event=None):
        scope_id, group = options[scope.get()]
        chain[:] = history(library, scope_id)
        layers = current_layers(library, scopes)
        validate_layers(profile, scopes, layers)
        values = project(profile, header.get("environment", {}), scopes, layers)
        for widget in fields.winfo_children():
            widget.destroy()
        for col, label in enumerate(("Setting", "Current value", "Override")):
            ttk.Label(fields, text=label, font="TkHeadingFont").grid(row=0, column=col, sticky="w", padx=(0, 14))
        inputs.clear()
        for key, rule in records.items():
            if group is not None and group != rule["group"]:
                continue
            row = len(inputs) + 1
            display = rule["display"]
            factor = display["factor"]
            suffix = " " + display["unit"] if display["unit"] else ""
            effective = values[key]
            current = "Unavailable" if effective["value"] is None else f"{effective['value']/factor:g}{suffix}"
            ttk.Label(fields, text=rule["label"] + (f" ({display['unit']})" if suffix else "")).grid(
                row=row, column=0, sticky="w", pady=3, padx=(0, 12))
            ttk.Label(fields, text=f"{current} · {effective['layer']}").grid(
                row=row, column=1, sticky="w", padx=(0, 12))
            entry = ttk.Entry(fields, width=12)
            entry.grid(row=row, column=2, sticky="w")
            inputs[key] = (entry, factor)
        versions.configure(values=("Inherit all", *(f"Revision {n}" for n in range(1, len(chain)+1))))
        versions.current(len(chain))
        fill_fields()
        status.set("Leave a field empty to inherit. Only new drafts and rebuilds use saved changes."
                   if group is None else "This override applies to the selected source object in this intake.")

    def fill_fields(_event=None):
        number = versions.current()
        values = chain[number-1]["values"] if number > 0 else {}
        for key, (entry, factor) in inputs.items():
            entry.delete(0, "end")
            if key in values:
                entry.insert(0, format(values[key]/factor, ".15g"))

    def save(_event=None):
        try:
            scope_id, group = options[scope.get()]
            values = {key: float(entry.get())*factor for key, (entry, factor) in inputs.items() if entry.get().strip()}
            edit_scope(library, scope_id, profile, values, chain[-1]["sha256"] if chain else None, group=group)
            form.destroy()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Conversion rules", str(exc), parent=form)

    action = ttk.Frame(body)
    action.grid(row=4, sticky="e", pady=(13, 0))
    ttk.Button(action, text="Cancel", command=form.destroy).pack(side="left", padx=(0, 8))
    ttk.Button(action, text="Save rules", command=save).pack(side="left")
    scope.bind("<<ComboboxSelected>>", refresh)
    versions.bind("<<ComboboxSelected>>", fill_fields)
    form.bind("<Return>", save)
    form.bind("<Escape>", lambda _event: form.destroy())
    try:
        refresh()
    except (OSError, ValueError):
        form.destroy()
        raise
    return form
