"""Choose native terrain, CTF markers, and companion packages in the import dialog."""

from collections import Counter
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from busy import run_busy
from game_workflows import window_title
from intake_contract import finite
from unreal_binary_scene import read_binary_scene
from unreal_legacy_intake import companion_paths, project_markers, usable_marker, validate_unreal_settings
from unreal_native_terrain import terrain_candidates
from unreal_package_reader import GAMES


def marker_choices(actors, ascend_terrain, show_all):
    flags = [actor for actor in actors if (show_all or actor["role"] in {"flag", "capture-point"})
             and usable_marker(actor, "flag", ascend_terrain)]
    spawns = [actor for actor in actors if (show_all or actor["role"] == "spawn")
              and usable_marker(actor, "spawn", ascend_terrain)]
    return flags, spawns


# Team names in flag and stand class names (Ascend TrCTFBase_<Team>, Vengeance Capture<Kind><Faction>).
TEAM_NAMES = (("bloodeagle", "Blood Eagle"), ("diamondsword", "Diamond Sword"), ("beagle", "Blood Eagle"),
              ("phoenix", "Phoenix"), ("imperial", "Imperial"))


def marker_label(actor):
    """A readable dropdown label that leads with FLAG and the team, e.g. "FLAG · Blood Eagle · TrCTFBase_BloodEagle_0"."""
    name = actor["name"] or actor["object_path"].rsplit(".", 1)[-1]
    kind = (actor["source_class"] or "").rsplit(".", 1)[-1]
    if actor["role"] not in {"flag", "capture-point"}:
        return f"{name} ({kind})" if kind else name
    team = next((label for key, label in TEAM_NAMES if key in kind.casefold()), None)
    head = "FLAG STAND" if actor["role"] == "capture-point" else "FLAG"
    return " · ".join(part for part in (head, team, name) if part)


def flag_labels(flags):
    """Map each dropdown label to its object path; repeated labels fall back to the full path."""
    counts = Counter(marker_label(actor) for actor in flags)
    return {(label if counts[label] == 1 else f"{label} · {actor['object_path']}"): actor["object_path"]
            for actor in flags for label in (marker_label(actor),)}


def suggested_teams(flags, spawns, actors=()):
    # Vengeance stores the flag pickup above the stand. The stand anchors the new objective.
    stands = [flag for flag in flags if flag["role"] == "capture-point"]
    pickups = [flag for flag in flags if flag["role"] == "flag"]
    objectives = stands if len(stands) == 2 else pickups if len(pickups) == 2 else flags
    if len(objectives) != 2:
        return []
    teams = [dict(flag=flag["object_path"], spawns=[]) for flag in objectives]
    factions = ("beagle", "phoenix", "imperial")
    by_flag_class = {f"capture{kind}{faction}": f"teaminfo{faction}"
                     for faction in factions for kind in ("stand", "flag")}
    side_by_class = {by_flag_class[kind]: side for side, flag in enumerate(objectives)
                     if (kind := flag["source_class"].rsplit(".", 1)[-1].casefold()) in by_flag_class}
    saved_teams = {actor["line"]: side_by_class[kind] for actor in actors
                   if (kind := (actor["source_class"] or "").rsplit(".", 1)[-1].casefold()) in side_by_class}
    positions = [flag["transform"]["position_m"][:2] for flag in objectives]
    for spawn in spawns:
        side = saved_teams.get(spawn["links"].get("team"))
        if side is not None:
            teams[side]["spawns"].append(spawn["object_path"])
            continue
        first, second = (math.dist(point, spawn["transform"]["position_m"][:2]) for point in positions)
        if not math.isclose(first, second):
            teams[int(second < first)]["spawns"].append(spawn["object_path"])
    return teams


def native_settings(scene, terrain, units, teams, companions):
    if terrain is None:
        raise ValueError("Select a native terrain, or use a settings file with a supplied heightmap.")
    if not finite(units, 1e6) or units <= 0:
        raise ValueError("Enter a positive source units per metre value.")
    settings = dict(format=1, game=scene["profile"], units_per_metre=units, terrain_actor=terrain["object_path"],
                    teams=teams)
    if companions:
        settings["asset_packages"] = companions
    ground = {"export_index": terrain["line"]}
    project_markers(dict(scene, native_terrain=ground), teams)
    return validate_unreal_settings(settings)


def show_native_import(parent, source):
    game = GAMES[source.suffix.casefold()]
    units_default = 100 if game == "ascend" else 50
    scene = run_busy(parent, f"Reading {source.name}: terrain, flags and spawns…",
                     lambda: read_binary_scene(source, game, units_default))
    actors = [actor for actor in scene["actors"] if actor["name"]]
    counts = Counter(actor["object_path"].casefold() for actor in actors)
    actors = sorted((actor for actor in actors if counts[actor["object_path"].casefold()] == 1),
                    key=lambda actor: actor["object_path"].casefold())
    terrains = {actor["object_path"]: actor for actor in terrain_candidates(dict(scene, actors=actors))
                if not actor["references"].keys() & {"base", "attachparent"}}
    window = tk.Toplevel(parent)
    window.title(window_title("settings", "Native map import settings"))
    window.geometry("870x565")
    window.minsize(710, 500)
    window.transient(parent)
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=source.name, font=("TkDefaultFont", 13, "bold")).pack(anchor="w")

    terrain_row = ttk.Frame(body)
    terrain_row.pack(fill="x", pady=(12, 12))
    ttk.Label(terrain_row, text="Native terrain").pack(side="left")
    terrain = ttk.Combobox(terrain_row, values=tuple(terrains), state="readonly")
    terrain.pack(side="left", fill="x", expand=True, padx=(8, 16))
    if len(terrains) == 1:
        terrain.current(0)
    ttk.Label(terrain_row, text="Source units per metre").pack(side="left")
    units = ttk.Entry(terrain_row, width=8)
    units.insert(0, str(units_default))
    units.pack(side="left", padx=(8, 0))

    show_all = tk.BooleanVar(window, False)
    ttk.Checkbutton(body, text="Show other placeable actors", variable=show_all).pack(anchor="w")
    content = ttk.Frame(body)
    content.pack(fill="both", expand=True, pady=(8, 10))
    controls = []
    for index in range(2):
        side = ttk.LabelFrame(content, text=f"Team {index + 1}", padding=10)
        side.grid(row=0, column=index, sticky="nsew", padx=(0, 6) if index == 0 else (6, 0))
        content.columnconfigure(index, weight=1, uniform="team")
        ttk.Label(side, text="Flag or stand").pack(anchor="w")
        flag = ttk.Combobox(side, state="readonly", width=25)
        flag.pack(fill="x", pady=(3, 8))
        ttk.Label(side, text="Spawns (Ctrl or Shift to select several)").pack(anchor="w")
        results = ttk.Frame(side)
        results.pack(fill="both", expand=True, pady=(3, 0))
        spawns = tk.Listbox(results, height=7, width=26, selectmode="extended", exportselection=False)
        scroll = ttk.Scrollbar(results, command=spawns.yview)
        spawns.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        spawns.pack(side="left", fill="both", expand=True)
        controls.append((flag, spawns))
    content.rowconfigure(0, weight=1)

    flag_choices = {}

    def selections():
        return [dict(flag=flag_choices.get(flag.get(), ""), spawns=[spawns.get(i) for i in spawns.curselection()])
                for flag, spawns in controls]

    def refresh(_event=None, *, initial=False):
        chosen = terrains.get(terrain.get())
        ascend_terrain = chosen["line"] if chosen and game == "ascend" else None
        flags, spawns = marker_choices(actors, ascend_terrain, show_all.get())
        preferred = suggested_teams(flags, spawns, actors) if initial else selections()
        flag_choices.clear()
        flag_choices.update(flag_labels(flags))
        label_for = {path: label for label, path in flag_choices.items()}
        spawn_paths = tuple(actor["object_path"] for actor in spawns)
        for index, (flag, starts) in enumerate(controls):
            wanted = preferred[index] if preferred else dict(flag="", spawns=[])
            flag.configure(values=tuple(flag_choices))
            flag.set(label_for.get(wanted["flag"], ""))
            starts.delete(0, "end")
            starts.insert("end", *spawn_paths)
            for row, path in enumerate(spawn_paths):
                if path in wanted["spawns"]:
                    starts.selection_set(row)
        if not terrains:
            notice.set("This package has no selectable native terrain. Use a settings file with a heightmap.")
        elif initial and preferred:
            notice.set("Review the suggestions. Saved team links take priority over distance where available.")
        else:
            notice.set("Choose one flag and at least one spawn per team.")

    package_row = ttk.Frame(body)
    package_row.pack(fill="x")
    companions = []
    package_text = tk.StringVar(window, "No companion packages selected")

    def select_packages():
        extension = "upk" if game == "ascend" else "pkg"
        paths = filedialog.askopenfilenames(parent=window, title="Select companion packages",
                                            initialdir=source.parent,
                                            filetypes=[("Companion packages", "*." + extension)])
        if not paths:
            return
        selected = [Path(path) for path in paths]
        names = [path.name for path in selected]
        try:
            if len(names) > 32 or any(path.resolve(strict=True).parent != source.parent.resolve() for path in selected):
                raise ValueError("Choose up to 32 companion packages beside the source map.")
            companion_paths(source, names)
        except (OSError, ValueError) as error:
            messagebox.showerror("Companion packages", str(error), parent=window)
            return
        companions[:] = names
        package_text.set(f"{len(names)} companion package{'s' if len(names) != 1 else ''} selected")

    def clear_packages():
        companions.clear()
        package_text.set("No companion packages selected")

    ttk.Button(package_row, text="Select companions…", command=select_packages).pack(side="left")
    ttk.Button(package_row, text="Clear", command=clear_packages).pack(side="left", padx=4)
    ttk.Label(package_row, textvariable=package_text).pack(side="left", padx=6)
    notice = tk.StringVar(window)
    ttk.Label(body, textvariable=notice, wraplength=670).pack(fill="x", pady=(10, 6))
    actions = ttk.Frame(body)
    actions.pack(fill="x")
    result = None

    def use_file():
        nonlocal result
        path = filedialog.askopenfilename(parent=window, title="Select Unreal map import settings",
                                          filetypes=[("Import settings", "*.json")])
        if path:
            result = Path(path)
            window.destroy()

    def submit():
        nonlocal result
        try:
            result = native_settings(scene, terrains.get(terrain.get()), float(units.get()), selections(), companions)
        except ValueError as error:
            notice.set(str(error))
            return
        window.destroy()

    ttk.Button(actions, text="Use settings file…", command=use_file).pack(side="left")
    ttk.Button(actions, text="Create intake", command=submit,
               state="normal" if terrains else "disabled").pack(side="right")
    ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right", padx=6)
    terrain.bind("<<ComboboxSelected>>", refresh)
    show_all.trace_add("write", lambda *_: refresh())
    window.bind("<Escape>", lambda _event: window.destroy())
    refresh(initial=True)
    window.grab_set()
    window.wait_window()
    return result
