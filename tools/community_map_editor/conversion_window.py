"""Local file picker and conversion coverage window for the UE menu."""

import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import zipfile

from tk_runtime import configure_tk

# Started from the Unreal menu, the window takes a while to appear (imports and the conversion catalog):
# show "Loading Map Tool..." at once so step 1 never looks dead. run() closes it when the window is ready.
SPLASH = None
if __name__ == "__main__":
    configure_tk()
    SPLASH = tk.Tk()
    SPLASH.title("TRIBES Map Editor")
    SPLASH.resizable(False, False)
    frame = ttk.Frame(SPLASH, padding=20)
    frame.pack()
    ttk.Label(frame, text="Loading Map Tool…").pack()
    SPLASH.update()

from conversion_rules import checksum, sha256_file, write
from game_workflows import game_choices, open_filetypes, select_game, steps_text, window_title
from boundary_polygon_window import show_polygon
from busy import run_busy
from intake_contract import load_intake, load_mission_audio, playable_bounds
from intake_exchange import export_private_intake
from map_pipeline import PipelineRun, pipeline_config
from interior_lighting import lighting_coverage, load_lighting_index
from mesh_glb import export_meshes
from mesh_mappings import placements, source_reference
from minimap import export_host_minimap, framing, terrain_pixels, view_transform
from mission_audio import audio_review
from mission_equipment import mission_equipment, power_context
from mission_weather import cloud_layers, mission_weather, precipitation_emitters
from model_textures import load_texture_index
from native_import_window import show_native_import
from object_catalog import applicable_mappings, build_rows, classify, load_catalog
from object_review_window import ObjectReview
from perimeter import (DEFAULT_BOUNDARY_OPTIONS, DEFAULT_CEILING_M, DEFAULT_DEPTH_M, PolygonWalls, WARNING_AUDIENCES,
                       plan_boundaries, read_boundary_recipe, rectangle_outline)
from perimeter_collision import blocks_pawns, validate_channels
from pipeline_window import show_pipeline
from public_layout import READER as PUBLIC_LAYOUT_READER, export_public_layout
from rule_window import show_rules
from sky_intake import load_sky_index
from source_library import catalog_rows, record_source
from source_profiles import T1, T2, T2_GENERATOR_SCOPE_VERSIONS, UNREAL_PROFILES
from t1_reader import convert_legacy, legacy_mission
from t1_terrain import volume_missions
from t2_reader import convert, members, unpack_intake
from terrain_layers import FALLBACK, PALETTE_SURFACES, layer_choices
from terrain_art import available_art
from unreal_legacy_intake import convert_unreal, read_unreal_settings, split_level_set
from unreal_package_reader import GAMES as NATIVE_PACKAGE_TYPES, inspect_package, write_report as write_package_report


WALL_COLLISION_CHOICES = {
    "Default (BlockAll)": None,
    "Players and bots (Pawn)": ("pawn",),
    "Vehicles (Vehicle)": ("vehicle",),
    "Pawn and Vehicle": ("pawn", "vehicle"),
}
WALL_VISUAL_CHOICES = {"Invisible": None, "Cyan energy": "energy", "Amber warning": "warning",
                       "T3 style (retail border)": "t3"}


def show_boundary_settings(parent, header, terrain, rows, selection, on_save):
    window = tk.Toplevel(parent)
    window.title("Mission boundaries")
    window.transient(parent)
    window.resizable(False, False)
    body = ttk.Frame(window, padding=14)
    body.pack(fill="both", expand=True)

    limits = selection.get("wall_limits") or {"ceiling_m": DEFAULT_CEILING_M, "floor_m": None}
    solid = tk.BooleanVar(window, value=selection["perimeter"])
    ceiling = tk.BooleanVar(window, value=limits["ceiling_m"] is not None)
    floor = tk.BooleanVar(window, value=limits["floor_m"] is not None)
    warn = tk.BooleanVar(window, value=selection.get("warn_players", False))
    warning_polygon = list(selection.get("warning_polygon_m", ()))
    custom_warning = tk.BooleanVar(window, value=bool(warning_polygon))
    saved_warning_limits = selection.get("warning_limits")
    warning_limits = saved_warning_limits or {"ceiling_m": None, "floor_m": None}
    custom_warning_height = tk.BooleanVar(window, value=saved_warning_limits is not None)
    audience = tk.StringVar(window, value=WARNING_AUDIENCES[selection.get("warning_audience")])
    solid_polygon = list(selection.get("wall_polygon_m", ()))
    custom = tk.BooleanVar(window, value=bool(solid_polygon))
    channels = validate_channels(selection.get("wall_block_channels"))
    collision_choice = tk.StringVar(window, value=next(label for label, mode in WALL_COLLISION_CHOICES.items()
                                                       if mode == channels))
    appearance = selection.get("wall_visual", {"style": None, "distance_m": 80})
    style_choice = tk.StringVar(window, value=next(label for label, style in WALL_VISUAL_CHOICES.items()
                                                   if style == appearance["style"]))
    entries = {}
    toggles = []
    warning_fields = {}

    def update_fields():
        for toggle in toggles:
            toggle.configure(state="normal" if solid.get() else "disabled")
        for key, enabled in (("ceiling_m", ceiling), ("floor_m", floor)):
            entries[key].configure(state="normal" if solid.get() and enabled.get() else "disabled")
        wall_draw.configure(state="normal" if solid.get() and custom.get() else "disabled")
        wall_collision.configure(state="readonly" if solid.get() else "disabled")
        wall_style.configure(state="readonly" if solid.get() else "disabled")
        fade_distance.configure(state="normal" if solid.get() and WALL_VISUAL_CHOICES[style_choice.get()] else "disabled")
        warning_shape.configure(state="normal" if warn.get() else "disabled")
        warning_audience.configure(state="readonly" if warn.get() else "disabled")
        warning_draw.configure(state="normal" if warn.get() and custom_warning.get() else "disabled")
        warning_height.configure(state="normal" if warn.get() else "disabled")
        adjust_height = warn.get() and custom_warning_height.get()
        for enabled, toggle, entry in warning_fields.values():
            toggle.configure(state="normal" if adjust_height else "disabled")
            entry.configure(state="normal" if adjust_height and enabled.get() else "disabled")

    ttk.Checkbutton(body, text="Add solid side walls", variable=solid, command=update_fields).grid(
        row=0, columnspan=2, sticky="w", pady=(0, 6),
    )
    for index, (key, title, enabled, initial) in enumerate((
        ("ceiling_m", "Ceiling above highest terrain (m)", ceiling, DEFAULT_CEILING_M),
        ("floor_m", "Floor below lowest terrain (m)", floor, DEFAULT_DEPTH_M),
    ), 1):
        toggle = ttk.Checkbutton(body, text=title, variable=enabled, command=update_fields)
        toggle.grid(row=index, column=0, sticky="w", padx=(12, 10), pady=3)
        toggles.append(toggle)
        entry = ttk.Entry(body, width=10)
        entry.insert(0, str(initial if limits[key] is None else limits[key]))
        entry.grid(row=index, column=1, sticky="w")
        entries[key] = entry
    def draw_solid():
        def keep(vertices):
            solid_polygon[:] = vertices
            wall_draw.configure(text=f"Edit {len(solid_polygon)} corners…")

        show_polygon(window, header, terrain, rows, solid_polygon, keep, kind="wall",
                     constrain_pawns=blocks_pawns(WALL_COLLISION_CHOICES[collision_choice.get()]))

    wall_shape = ttk.Checkbutton(body, text="Use a solid polygon", variable=custom, command=update_fields)
    wall_shape.grid(row=3, column=0, sticky="w", padx=(12, 10), pady=3)
    toggles.append(wall_shape)
    wall_draw = ttk.Button(body, text=f"Edit {len(solid_polygon)} corners…" if solid_polygon else "Draw solid polygon…",
                           command=draw_solid)
    wall_draw.grid(row=3, column=1, sticky="w")
    ttk.Label(body, text="Walls block").grid(row=4, column=0, sticky="w", padx=(12, 10), pady=3)
    wall_collision = ttk.Combobox(body, textvariable=collision_choice, values=list(WALL_COLLISION_CHOICES), width=23)
    wall_collision.grid(row=4, column=1, sticky="w")
    ttk.Label(body, text="Wall appearance").grid(row=5, column=0, sticky="w", padx=(12, 10), pady=3)
    wall_style = ttk.Combobox(body, textvariable=style_choice, values=list(WALL_VISUAL_CHOICES), width=23)
    wall_style.grid(row=5, column=1, sticky="w")
    wall_style.bind("<<ComboboxSelected>>", lambda _event: update_fields())
    ttk.Label(body, text="Visible from inside within (m)").grid(row=6, column=0, sticky="w", padx=(12, 10), pady=3)
    fade_distance = ttk.Entry(body, width=10)
    fade_distance.insert(0, str(appearance["distance_m"]))
    fade_distance.grid(row=6, column=1, sticky="w")
    ttk.Separator(body).grid(row=7, columnspan=2, sticky="ew", pady=10)
    polygon = list(selection.get("flag_polygon_m", ()))
    shape = tk.StringVar(window, value="polygon" if polygon else "inset")

    def shape_changed():
        flag.configure(state="normal" if shape.get() == "inset" else "disabled")
        draw.configure(state="normal" if shape.get() == "polygon" else "disabled")

    def draw_polygon():
        def keep(vertices):
            polygon[:] = vertices
            draw.configure(text=f"Edit {len(polygon)} corners…")

        show_polygon(window, header, terrain, rows, polygon, keep)

    ttk.Radiobutton(body, text="Flag-only inset (m, optional)", variable=shape, value="inset",
                    command=shape_changed).grid(row=8, column=0, sticky="w", padx=(0, 10))
    flag = ttk.Entry(body, width=10)
    if selection["flag_inset_m"] is not None:
        flag.insert(0, str(selection["flag_inset_m"]))
    flag.grid(row=8, column=1, sticky="w")
    ttk.Radiobutton(body, text="Flag-only polygon", variable=shape, value="polygon",
                    command=shape_changed).grid(row=9, column=0, sticky="w")
    draw = ttk.Button(body, text=f"Edit {len(polygon)} corners…" if polygon else "Draw on minimap…", command=draw_polygon)
    draw.grid(row=9, column=1, sticky="w", pady=3)
    flag_altitude = ttk.LabelFrame(body, text="Optional flag-only height limits (metres)", padding=(8, 4))
    flag_altitude.grid(row=10, columnspan=2, sticky="ew", pady=(6, 0))
    flag_height_fields = {}
    for column, (key, label) in enumerate((("floor_m", "Below lowest terrain"), ("ceiling_m", "Above highest terrain"))):
        ttk.Label(flag_altitude, text=label).grid(row=0, column=2 * column, sticky="w", padx=(0, 6))
        field = ttk.Entry(flag_altitude, width=7)
        previous = selection.get("flag_limits", {}).get(key)
        if previous is not None:
            field.insert(0, str(previous))
        field.grid(row=0, column=2 * column + 1, sticky="w", padx=(0, 8))
        flag_height_fields[key] = field
    ttk.Label(body, text="Return flag after time outside (s)").grid(row=11, column=0, sticky="w", pady=(6, 0))
    flag_timer = ttk.Entry(body, width=8)
    if selection.get("flag_return_delay_s") is not None:
        flag_timer.insert(0, str(selection["flag_return_delay_s"]))
    flag_timer.grid(row=11, column=1, sticky="w", pady=(6, 0))
    ttk.Label(body, text="Leave blank to drop the flag immediately.").grid(row=12, columnspan=2, sticky="w")
    def draw_warning():
        def keep(vertices):
            warning_polygon[:] = vertices
            warning_draw.configure(text=f"Edit {len(warning_polygon)} corners…")

        show_polygon(window, header, terrain, rows, warning_polygon, keep, kind="warning")

    warning_group = ttk.LabelFrame(body, text="Player warning and sound", padding=(8, 4))
    warning_group.grid(row=13, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Checkbutton(warning_group, text="Warn players on crossing", variable=warn, command=update_fields).grid(
        row=0, columnspan=2, sticky="w", pady=(0, 3),
    )
    ttk.Label(warning_group, text="Warn").grid(row=1, column=0, sticky="w")
    warning_audience = ttk.Combobox(warning_group, textvariable=audience, values=list(WARNING_AUDIENCES.values()), width=22)
    warning_audience.grid(row=1, column=1, sticky="w", pady=3)
    warning_shape = ttk.Checkbutton(warning_group, text="Draw a separate outline", variable=custom_warning,
                                    command=update_fields)
    warning_shape.grid(row=2, column=0, sticky="w", padx=(0, 10))
    warning_draw = ttk.Button(warning_group, text=f"Edit {len(warning_polygon)} corners…" if warning_polygon else
                              "Draw on minimap…", command=draw_warning)
    warning_draw.grid(row=2, column=1, sticky="w", pady=3)
    warning_height = ttk.Checkbutton(warning_group, text="Set warning height limits", variable=custom_warning_height,
                                     command=update_fields)
    warning_height.grid(row=3, columnspan=2, sticky="w")
    for index, (key, title, initial) in enumerate((
        ("ceiling_m", "Above highest terrain (m)", DEFAULT_CEILING_M),
        ("floor_m", "Below lowest terrain (m)", DEFAULT_DEPTH_M),
    ), 4):
        saved = warning_limits[key]
        enabled = tk.BooleanVar(window, value=saved is not None)
        toggle = ttk.Checkbutton(warning_group, text=title, variable=enabled, command=update_fields)
        toggle.grid(row=index, column=0, sticky="w", padx=(12, 10), pady=2)
        entry = ttk.Entry(warning_group, width=10)
        entry.insert(0, str(initial if saved is None else saved))
        entry.grid(row=index, column=1, sticky="w")
        warning_fields[key] = enabled, toggle, entry
    kill_row = ttk.Frame(body)
    kill_row.grid(row=14, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Label(kill_row, text="Kill floor, metres below lowest terrain").pack(side="left")
    kill_floor = ttk.Entry(kill_row, width=8)
    if selection.get("kill_floor_m") is not None:
        kill_floor.insert(0, f"{selection['kill_floor_m']:g}")
    kill_floor.pack(side="left", padx=(8, 0))
    ttk.Label(body, text="Leave blank for no kill floor. Players below it die (the source game's KillZ).",
              wraplength=320).grid(row=15, columnspan=2, sticky="w")
    flight_row = ttk.Frame(body)
    flight_row.grid(row=16, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Label(flight_row, text="Flight ceiling, m above lowest terrain").pack(side="left")
    flight_height = ttk.Entry(flight_row, width=8)
    flight_height.pack(side="left", padx=(8, 0))
    ttk.Label(flight_row, text="fade over").pack(side="left", padx=(8, 0))
    flight_fade = ttk.Entry(flight_row, width=6)
    flight_fade.pack(side="left", padx=(6, 0))
    ttk.Label(flight_row, text="m").pack(side="left", padx=(4, 0))
    previous_ceiling = selection.get("flight_ceiling") or {}
    if previous_ceiling.get("height_m") is not None:
        flight_height.insert(0, f"{previous_ceiling['height_m']:g}")
    if previous_ceiling.get("fade_m") is not None:
        flight_fade.insert(0, f"{previous_ceiling['fade_m']:g}")
    ttk.Label(body, text="Leave blank for no flight ceiling. Climbing fades through the band and stops at the "
                         "ceiling (Tribes 2 flightCeiling, Ascend StallZ).", wraplength=320).grid(
        row=17, columnspan=2, sticky="w")
    message = ttk.Label(body, wraplength=320)
    message.grid(row=18, columnspan=2, sticky="w", pady=(6, 0))

    def distance(entry, label):
        try:
            return float(entry.get())
        except ValueError as exc:
            raise ValueError(f"Enter {label} as a distance in metres.") from exc

    def save():
        try:
            options = {
                "perimeter": solid.get(),
                "flag_inset_m": distance(flag, "the flag-only inset") if shape.get() == "inset" and flag.get().strip() else None,
            }
            if shape.get() == "polygon":
                options["flag_polygon_m"] = polygon
            if any(field.get().strip() for field in flag_height_fields.values()):
                options["flag_limits"] = {
                    key: distance(field, "the flag-only height limit") if field.get().strip() else None
                    for key, field in flag_height_fields.items()
                }
            if kill_floor.get().strip():
                options["kill_floor_m"] = distance(kill_floor, "the kill floor depth")
            if flight_height.get().strip():
                options["flight_ceiling"] = {"height_m": distance(flight_height, "the flight ceiling height")}
                if flight_fade.get().strip():
                    options["flight_ceiling"]["fade_m"] = distance(flight_fade, "the flight ceiling fade band")
            elif flight_fade.get().strip():
                raise ValueError("Enter a flight ceiling height before its fade band.")
            if flag_timer.get().strip():
                try:
                    options["flag_return_delay_s"] = int(flag_timer.get())
                except ValueError as exc:
                    raise ValueError("Enter the flag return delay in whole seconds.") from exc
            if warn.get():
                options["warn_players"] = True
                if selected := next(key for key, label in WARNING_AUDIENCES.items() if label == audience.get()):
                    options["warning_audience"] = selected
                if custom_warning.get():
                    options["warning_polygon_m"] = warning_polygon
                if custom_warning_height.get():
                    options["warning_limits"] = {
                        key: distance(entry, "the warning height") if enabled.get() else None
                        for key, (enabled, _toggle, entry) in warning_fields.items()
                    }
            if solid.get():
                options["wall_limits"] = {
                    "ceiling_m": distance(entries["ceiling_m"], "the ceiling height") if ceiling.get() else None,
                    "floor_m": distance(entries["floor_m"], "the floor depth") if floor.get() else None,
                }
                if custom.get():
                    options["wall_polygon_m"] = solid_polygon
                if mode := WALL_COLLISION_CHOICES[collision_choice.get()]:
                    options["wall_block_channels"] = list(mode)
                if style := WALL_VISUAL_CHOICES[style_choice.get()]:
                    options["wall_visual"] = {"style": style, "distance_m": distance(fade_distance, "the fade distance")}
            plan_boundaries(header, terrain, rows, **options)
        except ValueError as exc:
            message.configure(text=str(exc))
            return
        on_save(options)
        window.destroy()

    buttons = ttk.Frame(body)
    buttons.grid(row=19, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="left")
    ttk.Button(buttons, text="Apply", command=save).pack(side="right")
    window.bind("<Escape>", lambda _event: window.destroy())
    update_fields()
    shape_changed()
    window.grab_set()


def show_minimap(parent, manifest, header, terrain, rows, *, flag_limit=None, wall_polygon=None, warning=None):
    window = tk.Toplevel(parent)
    window.title(f"{header['name']} · Minimap preview")
    window.geometry("660x425")
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    size = 344
    canvas = tk.Canvas(body, width=size, height=size, background="#18242d", highlightthickness=0)
    canvas.grid(row=0, column=0, rowspan=9, sticky="n", padx=(0, 16))
    ttk.Label(body, text="Minimap framing", font="TkHeadingFont").grid(row=0, column=1, columnspan=2, sticky="w")

    initial = header["minimap"]
    bounds = playable_bounds(header, rows)
    fields = (
        ("Rotation (°)", initial["rotation_deg"]), ("Scale", initial["scale"]),
        ("Minimum zoom", initial["zoom"]["min"]), ("Default zoom", initial["zoom"]["default"]),
        ("Maximum zoom", initial["zoom"]["max"]),
    )
    inputs = []
    for index, (label, value) in enumerate(fields, 1):
        ttk.Label(body, text=label).grid(row=index, column=1, sticky="w", padx=(0, 10), pady=2)
        entry = ttk.Entry(body, width=9)
        entry.insert(0, str(value))
        entry.grid(row=index, column=2, sticky="w")
        inputs.append(entry)

    def current_settings():
        try:
            values = [float(entry.get()) for entry in inputs]
        except ValueError as exc:
            raise ValueError("Enter a number in each framing field.") from exc
        return framing(bounds, *values, image_bounds=initial["image_bounds"])

    def draw(settings):
        project, world = view_transform(settings, size)
        pixels = terrain_pixels(terrain, size, size, world)
        graphic = tk.PhotoImage(master=window, data=f"P5\n{size} {size}\n255\n".encode() + pixels, format="PPM")
        canvas.delete("all")
        canvas.create_image(0, 0, image=graphic, anchor="nw")
        canvas.image = graphic
        corners = rectangle_outline(bounds)
        canvas.create_line(*(v for corner in (*corners, corners[0]) for v in project(*corner)),
                           fill="#f2ce7d", width=2, dash=(5, 3))
        if wall_polygon is not None:
            canvas.create_line(*(v for corner in (*wall_polygon, wall_polygon[0]) for v in project(*corner)),
                               fill="#7ce0de", width=2)
        if flag_limit is not None:
            points = flag_limit.points
            corners = rectangle_outline(points) if len(points) == 2 else points
            canvas.create_line(*(v for corner in (*corners, corners[0]) for v in project(*corner)),
                               fill="#f28aad", width=2, dash=(3, 3))
        if warning is not None:
            corners = warning.vertices
            canvas.create_line(*(v for corner in (*corners, corners[0]) for v in project(*corner)),
                               fill="#b8a6ff", width=2, dash=(7, 4))
        for row in rows:
            if row["status"] != "marker":
                continue
            x, y = project(*row["position_cm"][:2])
            color = "#6ab7ff" if row["team"] == 1 else "#ff7575"
            if row["kind"] == "flag":
                canvas.create_polygon(x, y - 6, x + 6, y, x, y + 6, x - 6, y, fill=color, outline="#111b23")
            else:
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="#111b23")

    def apply():
        try:
            draw(current_settings())
        except ValueError as exc:
            messagebox.showerror("Minimap framing", str(exc), parent=window)

    def export():
        try:
            settings = current_settings()
            map_id = simpledialog.askstring("Portable map ID", "Map ID from the host catalog", parent=window)
            if not map_id:
                return
            folder = filedialog.askdirectory(title="Choose a parent folder for the minimap export", parent=window)
            if not folder:
                return
            result = export_host_minimap(manifest, settings, map_id.strip(), folder)
            messagebox.showinfo("Minimap exported", f"Saved to {result}", parent=window)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Minimap export", str(exc), parent=window)

    ttk.Button(body, text="Update preview", command=apply).grid(row=6, column=1, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Button(body, text="Export for host…", command=export).grid(row=7, column=1, columnspan=2, sticky="ew", pady=(4, 0))
    legend = "Gold line · Playable boundary\nDiamonds · Flags\nDots · Team spawns"
    if flag_limit is not None:
        legend += "\nPink line · Flag-only limit"
    if wall_polygon is not None:
        legend += "\nCyan line · Solid perimeter"
    if warning is not None:
        legend += "\nPurple dashes · Player warning"
    ttk.Label(body, text=legend).grid(
        row=8, column=1, columnspan=2, sticky="nw", pady=(8, 0)
    )
    draw(initial)
    return window


def save_mappings(path, mappings):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(mappings, indent=2, sort_keys=True) + "\n"
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def show_terrain_layers(parent, terrain, current, on_save, *, artwork, review):
    dialog = tk.Toplevel(parent)
    dialog.title("Terrain layers")
    dialog.transient(parent)
    dialog.grab_set()
    outer = ttk.Frame(dialog, padding=12)
    outer.pack(fill="both", expand=True)
    layers = layer_choices(terrain, artwork=artwork)
    canvas = tk.Canvas(outer, width=540, height=min(460, 35 * (len(layers) + 1)), highlightthickness=0,
                       background=ttk.Style(dialog).lookup("TFrame", "background"))
    scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)
    frame = ttk.Frame(canvas)
    content = canvas.create_window(0, 0, anchor="nw", window=frame)
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content, width=event.width))
    frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    ttk.Label(frame, text="Source terrain layer").grid(row=0, column=0, sticky="w")
    ttk.Label(frame, text="Draft surface").grid(row=0, column=1, sticky="w")
    controls = {}
    for row, (slot, name, suggested) in enumerate(layers, 1):
        automatic = f"Auto · {suggested or FALLBACK + ' fallback'}"
        label = f"{slot} · {name}" if "tile_grid" in terrain else name
        detail = review.get(str(slot), {}).get("reason")
        ttk.Label(frame, text=f"{label}\n{detail}" if detail else label, wraplength=330).grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=3)
        choice = ttk.Combobox(frame, values=(automatic, *PALETTE_SURFACES), state="readonly", width=24)
        choice.set(current.get(str(slot), automatic))
        choice.grid(row=row, column=1, sticky="ew", pady=3)
        controls[str(slot)] = choice

    def save():
        on_save({slot: choice.get() for slot, choice in controls.items() if choice.current() > 0})
        dialog.destroy()

    buttons = ttk.Frame(outer)
    buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text="Apply", command=save).pack(side="left")
    dialog.bind("<Escape>", lambda _: dialog.destroy())
    return dialog


def coverage_status(row, *, equipment, target, mapped, decoded, texture_issue):
    if equipment:
        return "Equipment review", True
    if mapped:
        return "Mapped", False
    if target:
        return "Missing mesh", True
    if texture_issue:
        return "Texture review", True
    if decoded:
        return "Decoded", False
    return row["status"], row["status"] == "review"


def show_source_library(parent, library, drafts, open_intake):
    window = tk.Toplevel(parent)
    window.title("Map source library")
    window.geometry("780x450")
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    view = ttk.Frame(body)
    view.pack(fill="both", expand=True)
    table = ttk.Treeview(view, columns=("draft", "status", "origin"), show="tree headings",
                         selectmode="browse")
    for key, label, width in (("#0", "Map", 180), ("draft", "Draft", 120),
                               ("status", "Source status", 170), ("origin", "Original location", 220)):
        table.heading(key, text=label)
        table.column(key, width=width)
    table.pack(side="left", fill="both", expand=True)
    scroll = ttk.Scrollbar(view, command=table.yview)
    scroll.pack(side="right", fill="y")
    table.configure(yscrollcommand=scroll.set)
    rows = catalog_rows(library, drafts)
    for index, row in enumerate(rows):
        table.insert("", "end", iid=str(index), text=row["name"],
                     values=(row["draft"] or "", row["status"], row["origin"]))
    details = tk.Text(body, height=6, wrap="word", state="disabled")
    details.pack(fill="x", pady=(10, 6))

    def selected():
        indexes = table.selection()
        return rows[int(indexes[0])] if indexes else None

    def update(_event=None):
        row = selected()
        message = row["details"] if row else "No saved sources or drafts." if not rows else ""
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", message)
        details.configure(state="disabled")
        open_button.configure(state="normal" if row and row["manifest"] else "disabled")

    def open_selected():
        row = selected()
        if row and row["manifest"] and open_intake(row["manifest"]):
            window.destroy()

    open_button = ttk.Button(body, text="Open intake", command=open_selected)
    open_button.pack(anchor="e")
    table.bind("<<TreeviewSelect>>", update)
    table.bind("<Return>", lambda _event: open_selected())
    table.bind("<Double-1>", lambda event: open_selected() if table.identify_row(event.y) else None)
    if rows:
        table.selection_set("0")
    update()
    return window


def show_package_inventory(parent, path, import_draft):
    report = run_busy(parent, f"Reading package inventory of {path.name}…", lambda: inspect_package(path))
    summary = report["summary"]
    window = tk.Toplevel(parent)
    window.title(window_title("inventory", "Native map package inventory"))
    window.geometry("840x520")
    top = ttk.Frame(window, padding=12)
    top.pack(fill="x")
    ttk.Label(top, text=path.name).pack(anchor="w")
    ttk.Label(top, text=f"Version {summary['version']}/{summary['licensee_version']} · "
                        f"{summary['compression']} compression").pack(anchor="w")
    row = ttk.Frame(top)
    row.pack(fill="x", pady=(8, 0))
    ttk.Label(row, text="Search objects").pack(side="left")
    query = tk.StringVar()
    search = ttk.Entry(row, textvariable=query)
    search.pack(side="left", fill="x", expand=True, padx=8)
    view = ttk.Frame(window, padding=(12, 0, 12, 0))
    view.pack(fill="both", expand=True)
    columns = ("kind", "class", "path")
    table = ttk.Treeview(view, columns=columns, show="headings")
    for key, label, width in (("kind", "Table", 80), ("class", "Class", 180), ("path", "Object path", 500)):
        table.heading(key, text=label)
        table.column(key, width=width, stretch=key == "path")
    scroll = ttk.Scrollbar(view, command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    table.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    bottom = ttk.Frame(window, padding=12)
    bottom.pack(fill="x")
    status = ttk.Label(bottom)
    status.pack(side="left")

    def refresh(*_):
        text = query.get().casefold().strip()
        table.delete(*table.get_children())
        count = 0
        for kind in ("imports", "exports"):
            for item in report[kind]:
                if text and text not in item["object_path"].casefold() and text not in item["class_name"].casefold():
                    continue
                if count < 400:
                    table.insert("", "end", values=(kind[:-1], item["class_name"], item["object_path"]))
                count += 1
        status.configure(text=f"Showing {min(count, 400):,} of {count:,} matches · Package inventory")

    def save():
        name = filedialog.asksaveasfilename(parent=window, defaultextension=".json",
                                           initialfile=path.stem + "-package.json")
        if name:
            try:
                write_package_report(report, name)
                status.configure(text="Full inventory saved.")
            except OSError as error:
                messagebox.showerror("Save package inventory", str(error), parent=window)

    def create_draft():
        try:
            if import_draft(path, window):
                window.destroy()
        except (OSError, ValueError) as error:
            messagebox.showerror("Open package settings", str(error), parent=window)

    ttk.Button(bottom, text="Save full inventory…", command=save).pack(side="right")
    ttk.Button(bottom, text="Import as draft…", command=create_draft).pack(side="right", padx=8)
    query.trace_add("write", refresh)
    refresh()
    search.focus_set()


def run(library, meshes=(), drafts=(), game=None):
    # A desktop launcher can inherit Tcl paths for a different Python installation.
    configure_tk()
    catalog = load_catalog(library)
    global SPLASH
    if SPLASH is not None:
        SPLASH.destroy()
        SPLASH = None
    root = tk.Tk()
    mesh_paths = set(meshes)
    root.title("TRIBES Map Editor · Convert a map")
    root.geometry("1110x720")
    root.minsize(900, 520)
    # Game-first workflow (#8): choose the source game, then follow its numbered steps.
    start = ttk.Frame(root, padding=(10, 10, 10, 0))
    start.pack(fill="x")
    ttk.Label(start, text="Converting from").pack(side="left")
    source_game = ttk.Combobox(start, state="readonly", values=game_choices(), width=34)
    source_game.current(0)
    source_game.pack(side="left", padx=(8, 0))
    steps = tk.StringVar(value=steps_text(source_game.get()))
    ttk.Label(root, textvariable=steps, justify="left", wraplength=1060).pack(anchor="w", padx=10, pady=(4, 0))
    stage = {"current": "open"}

    def show_stage(name=None):
        """Number the window title after the chosen game's step for what is on screen (#8)."""
        if name:
            stage["current"] = name
        root.title(window_title(stage["current"], "TRIBES Map Editor · Convert a map"))

    def choose_game(_event=None):
        select_game(source_game.get())
        steps.set(steps_text(source_game.get()))
        show_stage()

    source_game.bind("<<ComboboxSelected>>", choose_game)
    if game in game_choices():
        # Opened from a game's own menu: start on that game and its steps.
        source_game.set(game)
        choose_game()
    bar = ttk.Frame(root, padding=10)
    bar.pack(fill="x")
    notice = tk.StringVar(
        value="Choose a Tribes mission, Unreal map package or export, or existing intake."
    )
    ttk.Label(root, textvariable=notice, wraplength=780).pack(anchor="w", padx=10)
    review_area = ttk.Frame(root)
    review_area.pack(fill="both", expand=True)
    object_review = ObjectReview(review_area, catalog)
    table = object_review.table
    choices = ttk.Combobox(bar, state="disabled", width=38)
    boundary_options = DEFAULT_BOUNDARY_OPTIONS.copy()
    state = {}
    configured_decoder = os.environ.get("T3_DIF_DECODER")
    dif_decoder = Path(configured_decoder) if configured_decoder and Path(configured_decoder).is_file() else None
    configured_assets = os.environ.get("T3_DIF_ASSETS")
    dif_assets = Path(configured_assets) if configured_assets and Path(configured_assets).exists() else None
    sky_assets = None
    audio_assets = None

    configured_dts = os.environ.get("T3_DTS_DECODER")
    dts_decoder = Path(configured_dts) if configured_dts and Path(configured_dts).is_dir() else None
    configured_dts_assets = os.environ.get("T3_DTS_ASSETS")
    dts_assets = Path(configured_dts_assets) if configured_dts_assets and Path(configured_dts_assets).exists() else None

    def selected_reference():
        selected = table.selection()
        row = state.get("objects", {}).get(selected[0]) if selected else None
        return source_reference(row) if row else None

    def update_mesh_button(event=None):
        mesh_button.configure(state="normal" if meshes and selected_reference() else "disabled")
        selected = table.selection()
        row = state.get("objects", {}).get(selected[0]) if selected else None
        if row is None:
            object_review.show_details(None, None, {})
            return
        ident = row["id"]
        parent = state["objects"].get(str(row["parent"]))
        team = state["teams"].get(ident)
        circuit = state["scopes"].get(ident)
        links = {}
        if parent:
            links["parent"] = parent["name"] or parent["source_class"]
        if team:
            links["team"] = str(team)
        if circuit is not None and circuit in state["contexts"]:
            links["power"] = state["contexts"][circuit]["name"] or f"Group {circuit}"
        if reference := source_reference(row):
            links["model"] = reference.split(":", 1)[1]
        object_review.show_details(row, state["report"][ident], links)

    def show_rows(*, write_report=False):
        selected = table.selection()
        table.delete(*table.get_children())
        mappings = state.get("mappings", {})
        report = {}
        for row in state.get("objects", {}).values():
            assessment = state["classified"][row["id"]]
            equipment = state["equipment"].get(row["id"])
            weather = state["weather"].get(row["id"])
            reference = source_reference(row)
            target = mappings.get(reference)
            mapped = target in mesh_paths
            filename = state.get("models", {}).get(reference)
            decoded = not target and filename is not None
            slots = state.get("textures", {}).get(filename, {}) if decoded else {}
            lighting = state["lighting"].get(filename)
            texture_issue = next((record['reason'] for record in slots.values() if 'reason' in record), None)
            status, needs_review = coverage_status(row, equipment=equipment, target=target,
                                                   mapped=mapped, decoded=decoded, texture_issue=texture_issue)
            if lighting and decoded:
                status, needs_review = "Lighting review", True
            props = {k.lower(): v for k, v in row["properties"].items()}
            source = row["name"] or next(
                (
                    props[key]
                    for key in ("interiorfile", "shapename", "datablock", "terrainfile")
                    if props.get(key)
                ),
                f"Object {row['id']}",
            )
            reason = row["reason"]
            if equipment:
                group = state["power_groups"].get(equipment["power_group"])
                labels = [equipment["role"].replace("_", " ").capitalize()]
                if equipment["team"]:
                    labels.append(f"Team {equipment['team']}")
                if group:
                    labels.append(group["name"] or f"Power group {group['source_id']}")
                reason = " · ".join(labels) + ". " + " ".join(equipment["review"] or ["Host wiring needs review."])
            elif target:
                reason = target if mapped else "Mapped mesh missing: " + target
            elif decoded:
                reason = "Decoded mesh will be placed at the source transform."
                if slots:
                    total = sum('file' in record for record in slots.values())
                    reason += f" Source textures {total}/{len(slots)}."
                if texture_issue:
                    reason += " " + texture_issue
                if lighting:
                    level_label = "level" if lighting["levels"] == 1 else "levels"
                    reason += (f" Baked lighting for {lighting['normal_surfaces']:,} normal and "
                               f"{lighting['alarm_surfaces']:,} alarm surfaces across "
                               f"{lighting['levels']} detail {level_label}. Power and animation need review.")
            elif reference in state.get("model_review", {}):
                reason = state["model_review"][reference]
            elif audio := state["audio"].get(row["id"]):
                status, needs_review = "Audio retained", True
                reason = audio
            elif weather:
                status = "Weather review"
                if weather["class"] == "sky":
                    reason += " Source sky and cloud settings recorded."
                    if sky := state["sky_artwork"].get(row["id"]):
                        resolved = sum("file" in slot for slot in sky["slots"])
                        reason += f" Artwork found for {resolved} of {len(sky['slots'])} material slots."
                        count = sum(layer["source_id"] == row["id"] for layer in state["cloud_layers"])
                        if count:
                            reason += f" {count} cloud layer{'s' if count != 1 else ''} ready to build."
                    elif "material_list" in weather["values"]:
                        reason += " The sky material list could not be read from the supplied content."
                else:
                    values = weather["values"]
                    if emitter := state["precipitation"].get(row["id"]):
                        reason = f"Draft projection: {emitter['drops']:,} {values['kind']} particles within {emitter['radius_cm'] / 100:g} m."
                    elif values["kind"] != "unmapped":
                        reason = f"Authored {values['kind']} is disabled by its intensity, count, or radius."
                    elif "datablock" in values:
                        reason = f"{values['datablock']} needs a weather conversion rule."
                    else:
                        reason = "The precipitation datablock needs review."
                if weather["invalid_source_fields"]:
                    reason += " Invalid fields: " + ", ".join(weather["invalid_source_fields"]) + "."
            feature = assessment["feature"]
            if equipment:
                feature = "stations" if equipment["role"] in ("inventory_station", "vehicle_station") else "equipment"
            if row["status"] == "excluded":
                status = "Excluded mode"
            elif assessment["blocked"]:
                status, needs_review = "Not placed", True
                reason = " ".join(assessment["blocked"])
            else:
                if status == "review" and catalog.features[feature]["draft"] == "retained":
                    status = "Source only"
                if assessment["notices"]:
                    needs_review = True
                    reason += " " + " ".join(assessment["notices"])
                if assessment["source_only_fields"]:
                    needs_review = True
                    reason += f" {len(assessment['source_only_fields'])} additional source fields."
            report[row["id"]] = {
                **assessment, "feature": feature, "status": status, "object": source, "type": row["source_class"],
                "reason": reason, "needs_review": needs_review,
            }
        state["report"] = report
        query = object_review.query.get().strip().casefold()
        feature_filter = object_review.labels.get(object_review.feature.get())
        ordered = sorted(report.items(), key=lambda item: (not item[1]["blocked"], not item[1]["needs_review"]))
        for ident, assessment in ordered:
            if object_review.review_only.get() and not assessment["needs_review"]:
                continue
            if feature_filter and assessment["feature"] != feature_filter:
                continue
            if query and query not in " ".join(assessment[key].casefold() for key in ("object", "type", "reason")):
                continue
            values = {**assessment, "feature": catalog.features[assessment["feature"]]["label"]}
            tags = ("blocked",) if assessment["blocked"] else ("review",) if assessment["needs_review"] else ()
            table.insert("", "end", iid=str(ident), values=[values[key] for key in object_review.columns], tags=tags)
        visible = table.get_children()
        reviews = sum(row["needs_review"] for row in report.values())
        withheld = sum(bool(row["blocked"]) for row in report.values())
        object_review.summary.configure(text=f"{len(visible)} of {len(report)} objects · {reviews} to review · {withheld} withheld")
        if selected and table.exists(selected[0]):
            table.selection_set(selected[0])
            table.see(selected[0])
        elif visible:
            table.selection_set(visible[0])
        update_mesh_button()
        if write_report and state.get("manifest"):
            digest = sha256_file(state["manifest"])
            write(library / "Coverage" / (digest + ".json"), {
                "format": 1, "intake_sha256": digest, "profile_sha256": checksum(catalog.document), "objects": report,
            })

    def inspect(path):
        nonlocal catalog, object_review, table
        show_stage("review")
        # Reading and verifying a large intake takes a while (Katabatic: 4,375 objects): show progress.
        header, terrain, rows = run_busy(root, f"Opening and verifying intake {Path(path).parent.name}…",
                                         lambda: load_intake(path))
        if not state.get("manifest"):
            boundary_options.clear()
            recipe = read_boundary_recipe(rows)
            boundary_options.update(recipe["options"] if recipe else DEFAULT_BOUNDARY_OPTIONS)
        new_catalog = load_catalog(library, source_profile=header["profile"])
        if new_catalog.document != catalog.document:
            for child in review_area.winfo_children():
                child.destroy()
            catalog = new_catalog
            object_review = ObjectReview(review_area, catalog)
            table = object_review.table
            bind_review()
        assessed = classify(rows, terrain, catalog)
        active = build_rows(rows, assessed)
        mapping_file = library / "MeshMappings" / (header["files"]["objects.json"] + ".json")
        mappings, warning = {}, ""
        if mapping_file.is_file():
            try:
                mappings = json.loads(mapping_file.read_text(encoding="utf-8"))
                placements(active, terrain, applicable_mappings(mappings, rows, active))
            except (OSError, ValueError):
                mappings = {}
                warning = " · Saved mesh mappings could not be read"
        state["manifest"] = path
        state["classified"], state["active_rows"] = assessed, active
        generator_scopes = header["format"] in T2_GENERATOR_SCOPE_VERSIONS
        state["audio"] = audio_review(load_mission_audio(path, header["files"]))
        state["teams"], state["scopes"], state["contexts"] = power_context(rows, generator_scopes=generator_scopes)
        state["rule_source"] = (header, rows)
        state["models"] = {**header.get("interiors", {}), **header.get("shapes", {})}
        state["model_review"] = {**header.get("interior_review", {}), **header.get("shape_review", {})}
        state["lighting"] = lighting_coverage(load_lighting_index(path.resolve().parent, header["files"]))
        state["textures"] = load_texture_index(path.resolve().parent, header["files"])
        equipment = mission_equipment(rows, terrain, generator_scopes=generator_scopes)
        state["equipment"] = {row["source_id"]: row for row in equipment["equipment"]}
        state["power_groups"] = {row["source_id"]: row for row in equipment["power_groups"]}
        weather = mission_weather(rows, terrain)
        state["weather"] = {row["source_id"]: row for row in weather["objects"]}
        state["sky_artwork"] = {sky["source_id"]: sky for sky in load_sky_index(path.resolve().parent, header["files"])}
        state["cloud_layers"] = cloud_layers(weather, state["sky_artwork"].values(), terrain.get("y_sign", -1))
        state["precipitation"] = {emitter["source_id"]: emitter
                                  for emitter in precipitation_emitters(weather, terrain["cm_per_m"])}
        state["terrain"] = terrain
        state["artwork"] = available_art(header)
        state["artwork_review"] = header.get("terrain_art", {})
        state["mappings"] = mappings
        state["mapping_file"] = mapping_file
        terrain_file = library / "TerrainMappings" / (header["files"]["terrain.json"] + ".json")
        terrain_mappings = {}
        if terrain_file.is_file():
            try:
                terrain_mappings = json.loads(terrain_file.read_text(encoding="utf-8"))
                layer_choices(terrain, terrain_mappings)
            except (OSError, ValueError, TypeError):
                terrain_mappings = {}
                warning += " · Saved terrain mappings could not be read"
        choices_for_terrain = layer_choices(terrain, terrain_mappings, artwork=state["artwork"])
        state["terrain_file"] = terrain_file
        state["terrain_mappings"] = terrain_mappings
        state["minimap"] = (path, header, terrain, rows) if "minimap" in header else None
        state["objects"] = {
            str(row["id"]): row
            for row in sorted(rows, key=lambda row: row["status"] != "review")
        }
        show_rows(write_report=True)
        notice.set(
            f"{header['name']} · {header['source_game']} · "
            + " · ".join(f"{n} {k}" for k, n in header["coverage"].items()) + warning
            + (f" · {len(state['models'])} models decoded" if state["models"] else "")
            + (f" · {len(state['equipment'])} equipment records" if state["equipment"] else "")
            + (f" · {len(terrain['tile_grid'][0])} × {len(terrain['tile_grid'])} terrain tiles"
               if "tile_grid" in terrain else " · One binary terrain tile" if "legacy_grid" in terrain else "")
            + (f" · {len(state['artwork'])} source artwork slots loaded" if state["artwork_review"] else "")
            + (" · Flat placeholder terrain and local model assignments" if header["reader"] == PUBLIC_LAYOUT_READER else "")
        )
        submit.configure(state="normal")
        preview.configure(state="normal" if state["minimap"] else "disabled")
        boundary_button.configure(state="normal" if state["minimap"] else "disabled")
        terrain_button.configure(state="normal" if choices_for_terrain else "disabled")
        if state.get("source_profile") == T1:
            dif_button.configure(text="Interiors and props…", state="normal")
        if state.get("source_profile") == T1 and choices_for_terrain:
            artwork_button.pack(side="right", padx=(0, 8))
        else:
            artwork_button.pack_forget()
        rule_button.configure(state="normal")
        public_button.configure(state="normal")
        private_button.configure(state="normal")
        mesh_export_button.configure(state="normal" if state["models"] else "disabled")

    def decode(event=None):
        try:
            src = state["source"]
            source_profile = state.get("source_profile", T2)
            notice.set("Decoding mission and resolving referenced assets…")
            root.update_idletasks()

            member = choices.get() or None  # read Tk state here; decoding runs on a worker thread

            def decode_pipeline(terrain_source):
                path = None
                pipeline = PipelineRun(pipeline_config(library, source_profile=source_profile),
                                       "reader", library=library, title=src.name)

                def decode_source(_):
                    nonlocal path
                    if source_profile == T1:
                        path = convert_legacy(src, library, terrain_source, mission_member=member,
                                              artwork=state.get("artwork_source"), interiors=state.get("interior_source"))
                    elif source_profile in UNREAL_PROFILES.values():
                        path = convert_unreal(src, library, state["unreal_metadata"],
                                              native_settings=state.get("unreal_native_settings"))
                    else:
                        path = convert(src, library, member, terrain_source,
                                       dif_decoder=dif_decoder, dif_assets=dif_assets,
                                       dts_decoder=dts_decoder, dts_assets=dts_assets, sky_assets=sky_assets,
                                       audio_assets=audio_assets)

                pipeline.execute({"decode": decode_source})
                return path

            try:
                path = run_busy(root, f"Converting {src.name}. Large maps can take a few minutes…",
                                lambda: decode_pipeline(state.get("terrain_source")))
            except FileNotFoundError as exc:
                if source_profile in UNREAL_PROFILES.values():
                    raise
                name = filedialog.askopenfilename(
                    parent=root, title=str(exc),
                    filetypes=([("Tribes terrain", "*.ted *.vol *.dtf *.dtb *.png")]
                               if source_profile == T1 else [("Terrain", "*.ter")]),
                )
                if not name:
                    return
                state["terrain_source"] = Path(name)
                path = run_busy(root, f"Converting {src.name}…", lambda: decode_pipeline(Path(name)))
            inspect(path)
            try:
                record_source(library, path, src, state.get("terrain_source"))
            except OSError as exc:
                notice.set(notice.get() + " · Original location could not be saved.")
                messagebox.showwarning("Map source location", str(exc), parent=root)
            return True
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            notice.set("Map conversion could not finish.")
            messagebox.showerror("Map conversion", str(exc), parent=root)
        except Exception as exc:  # noqa: BLE001 - an unexpected bug must still be reported, not just vanish.
            import traceback

            notice.set("Map conversion stopped on an unexpected error.")
            where = traceback.extract_tb(exc.__traceback__)[-1]
            messagebox.showerror("Map conversion", f"Unexpected error: {type(exc).__name__}: {exc}\n\n"
                                 f"at {Path(where.filename).name}:{where.lineno}. Please report this.", parent=root)

    def clear():
        show_stage("open")
        state.clear()
        boundary_options.clear()
        boundary_options.update(DEFAULT_BOUNDARY_OPTIONS)
        notice.set("Choose a Tribes mission, Unreal map package or export, or existing intake.")
        table.delete(*table.get_children())
        update_mesh_button()
        choices.set("")
        choices.configure(values=(), state="disabled")
        submit.configure(state="disabled")
        preview.configure(state="disabled")
        boundary_button.configure(state="disabled")
        dif_button.configure(text="DIF assets…", state="disabled")
        dts_button.configure(state="disabled")
        sky_button.configure(state="disabled")
        audio_button.configure(state="disabled")
        artwork_button.pack_forget()
        rule_button.configure(state="disabled")
        public_button.configure(state="disabled")
        private_button.configure(state="disabled")
        mesh_export_button.configure(state="disabled")

    def import_unreal(path, parent):
        metadata = path.with_suffix(".import.json")
        native_settings = None
        if not metadata.is_file() and path.suffix.casefold() in NATIVE_PACKAGE_TYPES:
            # A split Ascend map imports from its world (_Ter) level and merges the gameplay
            # and sound levels, so opening TrCTF-Tartarus.fmap brings in the whole map.
            path, levels = split_level_set(path)
            metadata = path.with_suffix(".import.json")
            selection = show_native_import(parent, path, levels)
            if selection is None:
                return False
            if isinstance(selection, Path):
                metadata = selection
            else:
                native_settings = selection
                metadata = None
        elif not metadata.is_file():
            settings_path = filedialog.askopenfilename(
                parent=parent, title="Select Unreal map import settings",
                filetypes=[("Import settings", "*.json")],
            )
            if not settings_path:
                return False
            metadata = Path(settings_path)
        settings = native_settings if native_settings is not None else read_unreal_settings(metadata)[0]
        clear()
        heightmap = settings.get("heightmap")
        state.update(source=path, source_profile=UNREAL_PROFILES[settings["game"]], unreal_metadata=metadata,
                     unreal_native_settings=native_settings,
                     terrain_source=metadata.with_name(heightmap) if heightmap else None)
        return decode()

    def open_file():
        name = filedialog.askopenfilename(
            parent=root,
            filetypes=open_filetypes(source_game.get()),
        )
        if not name:
            return
        clear()
        path = Path(name)
        state["source"] = path
        try:
            if path.suffix.casefold() in NATIVE_PACKAGE_TYPES:
                show_package_inventory(root, path, import_unreal)
                return
            if path.suffix.lower() == ".json":
                inspect(path)
                return
            if path.suffix.lower() == ".zip":
                inspect(run_busy(root, f"Unpacking {path.name}…", lambda: unpack_intake(path, library)))
                return
            if path.suffix.lower() == ".t3d":
                import_unreal(path, root)
                return
            if path.suffix.lower() in (".vl2", ".vol"):
                if path.suffix.lower() == ".vol":
                    state["source_profile"] = T1
                    names = volume_missions(path)
                else:
                    dif_button.configure(state="normal")
                    dts_button.configure(state="normal")
                    sky_button.configure(state="normal")
                    audio_button.configure(state="normal")
                    with zipfile.ZipFile(path) as archive:
                        names = members(archive, ".mis")
                if not names:
                    raise ValueError("This archive contains no missions.")
                choices.configure(values=names, state="readonly")
                if len(names) > 1:
                    notice.set("Select a mission from the archive.")
                    return
                choices.set(names[0])
            if path.suffix.lower() == ".mis":
                if legacy_mission(path):
                    state["source_profile"] = T1
                else:
                    dif_button.configure(state="normal")
                    dts_button.configure(state="normal")
                    sky_button.configure(state="normal")
                    audio_button.configure(state="normal")
            decode()
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Open map", str(exc), parent=root)

    def open_saved_intake(path):
        clear()
        try:
            inspect(path)
            return True
        except (ValueError, KeyError, TypeError, OSError) as exc:
            messagebox.showerror("Open intake", str(exc), parent=root)
            return False

    def choose_archive_mission(event):
        state.pop("terrain_source", None)
        state.pop("manifest", None)
        state.pop("objects", None)
        state.pop("minimap", None)
        submit.configure(state="disabled")
        preview.configure(state="disabled")
        boundary_options.clear()
        boundary_options.update(DEFAULT_BOUNDARY_OPTIONS)
        boundary_button.configure(state="disabled")
        rule_button.configure(state="disabled")
        public_button.configure(state="disabled")
        private_button.configure(state="disabled")
        mesh_export_button.configure(state="disabled")
        notice.set("Select a mission from the archive.")
        table.delete(*table.get_children())
        update_mesh_button()
        decode()

    def choose_dif_assets():
        nonlocal dif_decoder, dif_assets
        if state.get("source_profile") == T1:
            folder = filedialog.askdirectory(parent=root, title="Folder with Tribes interiors, DTS props, or VOL archives",
                                             mustexist=True)
            if folder:
                state["interior_source"] = Path(folder)
                decode()
            return
        if dif_decoder is None:
            filename = filedialog.askopenfilename(
                parent=root, title="Select the installed hxDif.py decoder", filetypes=[("Python decoder", "*.py")])
            if not filename:
                return
            dif_decoder = Path(filename)
        folder = filedialog.askdirectory(parent=root, title="Tribes 2 content folder (optional)", mustexist=True)
        if folder:
            dif_assets = Path(folder)
        if state["source"].suffix.lower() == ".mis" or choices.get():
            decode()

    def choose_dts_assets():
        nonlocal dts_decoder, dts_assets
        if dts_decoder is None:
            folder = filedialog.askdirectory(parent=root, title="Select the installed io_scene_dts or dtslib folder",
                                              mustexist=True)
            if not folder:
                return
            dts_decoder = Path(folder)
        folder = filedialog.askdirectory(parent=root, title="Tribes 2 content folder (optional)", mustexist=True)
        if folder:
            dts_assets = Path(folder)
        if state["source"].suffix.lower() == ".mis" or choices.get():
            decode()

    def choose_sky_assets():
        nonlocal sky_assets
        folder = filedialog.askdirectory(parent=root, title="Tribes 2 sky content folder containing artwork or VL2 packs",
                                         mustexist=True)
        if folder:
            sky_assets = Path(folder)
            if state["source"].suffix.lower() == ".mis" or choices.get():
                decode()

    def choose_audio_assets():
        nonlocal audio_assets
        folder = filedialog.askdirectory(parent=root, title="Tribes 2 content folder with WAV files or VL2 packs",
                                         mustexist=True)
        if folder:
            audio_assets = Path(folder)
            if state["source"].suffix.lower() == ".mis" or choices.get():
                decode()

    def choose_terrain_art():
        folder = filedialog.askdirectory(parent=root, title="Folder with authored terrain bitmaps, palettes or VOL archives",
                                         mustexist=True)
        if folder:
            state["artwork_source"] = Path(folder)
            decode()

    def map_mesh():
        reference = selected_reference()
        if not reference:
            return
        dialog = tk.Toplevel(root)
        dialog.title("Map source object to mesh")
        dialog.transient(root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=reference.split(":", 1)[1], wraplength=495).pack(anchor="w")
        ttk.Label(body, text="Applies to all instances of this source reference.").pack(anchor="w", pady=(0, 8))
        search = tk.StringVar()
        ttk.Label(body, text="Filter project meshes").pack(anchor="w")
        query = ttk.Entry(body, textvariable=search)
        query.pack(fill="x", pady=(2, 6))
        choice = ttk.Combobox(body, values=meshes, state="readonly", width=74)
        choice.pack(fill="x")
        choice.set(state["mappings"].get(reference, ""))

        def filter_meshes(*_):
            term = search.get().casefold()
            filtered = tuple(path for path in meshes if term in path.casefold())
            choice.configure(values=filtered)
            if choice.get() not in filtered:
                choice.set("")

        def finish(target):
            mappings = dict(state["mappings"])
            if target:
                mappings[reference] = target
            else:
                mappings.pop(reference, None)
            try:
                active = state["active_rows"]
                placements(active, state["terrain"], applicable_mappings(mappings, state["objects"].values(), active))
            except ValueError as exc:
                messagebox.showerror("Mesh mapping", str(exc), parent=dialog)
                return
            state["mappings"] = mappings
            dialog.destroy()
            show_rows()

        def use_mesh():
            if choice.get() in mesh_paths:
                finish(choice.get())
            else:
                messagebox.showinfo("Mesh mapping", "Select a mesh from the list.", parent=dialog)

        search.trace_add("write", filter_meshes)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Clear mapping", command=lambda: finish(None)).pack(side="left")
        ttk.Button(buttons, text="Use mesh", command=use_mesh).pack(side="right")
        query.focus_set()

    def details(event):
        item = table.identify_row(event.y)
        if item:
            row = state["objects"][item]
            detail = tk.Toplevel(root)
            detail.title(row["name"] or row["source_class"])
            body = tk.Text(detail, width=80, height=28, wrap="word")
            scroll = ttk.Scrollbar(detail, command=body.yview)
            body.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            body.pack(fill="both", expand=True)
            filename = state.get("models", {}).get(source_reference(row))
            textures = state.get("textures", {}).get(filename)
            equipment = state["equipment"].get(row["id"])
            details = dict(row)
            if textures:
                details["Source texture slots"] = textures
            if lighting := state["lighting"].get(filename):
                details["Source interior lighting"] = lighting
            if equipment:
                details["Equipment review"] = equipment
                group = state["power_groups"].get(equipment["power_group"])
                if group:
                    details["Source power group"] = group
            if weather := state["weather"].get(row["id"]):
                details["Source weather"] = weather
            if sky := state["sky_artwork"].get(row["id"]):
                details["Source sky artwork"] = sky
            if layers := [layer for layer in state["cloud_layers"] if layer["source_id"] == row["id"]]:
                details["Draft cloud layers"] = layers
            if emitter := state["precipitation"].get(row["id"]):
                details["Draft precipitation"] = emitter
            body.insert("1.0", json.dumps(details, indent=2, ensure_ascii=False))
            body.configure(state="disabled")

    def edit_boundaries():
        _, header, terrain, rows = state["minimap"]

        def save(options):
            boundary_options.clear()
            boundary_options.update(options)

        show_boundary_settings(root, header, terrain, rows, boundary_options, save)

    def preview_map():
        try:
            _, header, terrain, rows = state["minimap"]
            walls, limit, warning = plan_boundaries(header, terrain, rows, **boundary_options)
            show_minimap(root, *state["minimap"], flag_limit=limit,
                         wall_polygon=walls.vertices if isinstance(walls, PolygonWalls) else None, warning=warning)
        except ValueError as exc:
            messagebox.showerror("Mission boundaries", str(exc), parent=root)

    def edit_rules():
        selection = table.selection()
        selected = state["objects"].get(selection[0]) if selection else None
        try:
            show_rules(root, library, *state["rule_source"], selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Conversion rules", str(exc), parent=root)

    def save_public_layout():
        path = filedialog.asksaveasfilename(
            parent=root, title="Export public layout with placeholder terrain",
            initialfile="shared-map-layout.zip", defaultextension=".zip", filetypes=[("Intake ZIP", "*.zip")],
        )
        if not path:
            return
        try:
            export_public_layout(state["manifest"], path, boundary_options=boundary_options)
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
            messagebox.showerror("Export public layout", str(exc), parent=root)
            return
        notice.set("Public layout saved. Reopen the ZIP with Open source or intake.")

    def save_private_intake():
        path = filedialog.asksaveasfilename(
            parent=root, title="Export private intake with original assets",
            initialfile="private-map-intake.zip", defaultextension=".zip", filetypes=[("Intake ZIP", "*.zip")],
        )
        if not path:
            return
        notice.set("Exporting and verifying the private intake…")
        root.update_idletasks()
        try:
            run_busy(root, "Exporting and verifying the private intake…",
                     lambda: export_private_intake(state["manifest"], path))
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
            notice.set("Private intake export failed.")
            messagebox.showerror("Export private intake", str(exc), parent=root)
            return
        notice.set("Private intake saved. Reopen the ZIP with Open source or intake.")

    def save_mesh_assets():
        path = filedialog.asksaveasfilename(
            parent=root, title="Export private GLB mesh assets",
            initialfile="private-mesh-assets.zip", defaultextension=".zip", filetypes=[("ZIP archive", "*.zip")],
        )
        if not path:
            return
        notice.set("Exporting private GLB mesh assets…")
        root.update_idletasks()
        try:
            run_busy(root, "Exporting GLB mesh assets…", lambda: export_meshes(state["manifest"], path))
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
            notice.set("GLB mesh export failed.")
            messagebox.showerror("Export GLB meshes", str(exc), parent=root)
            return
        notice.set("Private GLB mesh assets saved.")

    def accept():
        try:
            mappings = state["mappings"]
            if set(mappings.values()) - mesh_paths:
                raise ValueError("A mapped mesh is missing. Update or clear the missing mapping.")
            if mappings or state["mapping_file"].is_file():
                save_mappings(state["mapping_file"], mappings)
            terrain_mappings = state["terrain_mappings"]
            if terrain_mappings or state["terrain_file"].is_file():
                save_mappings(state["terrain_file"], terrain_mappings)
            show_rows(write_report=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Create Unreal draft", str(exc), parent=root)
            return
        print(json.dumps({
            "manifest": str(state["manifest"].resolve()), **boundary_options,
            "mesh_mappings": mappings, "terrain_layers": terrain_mappings,
        }), flush=True)
        root.destroy()

    ttk.Button(bar, text="Open source or intake…", command=open_file).pack(
        side="left", padx=(0, 8)
    )
    ttk.Button(bar, text="Source library…", command=lambda:
               show_source_library(root, library, drafts, open_saved_intake)).pack(side="left", padx=(0, 8))
    public_button = ttk.Button(bar, text="Export public layout…", state="disabled", command=save_public_layout)
    public_button.pack(side="left", padx=(0, 8))
    private_button = ttk.Button(bar, text="Export private intake…", state="disabled", command=save_private_intake)
    private_button.pack(side="left", padx=(0, 8))
    choices.pack(side="left")
    choices.bind("<<ComboboxSelected>>", choose_archive_mission)
    def bind_review():
        object_review.register_filters(show_rows)
        table.bind("<Double-1>", details)
        table.bind("<<TreeviewSelect>>", update_mesh_button)

    bind_review()
    asset_options = ttk.Frame(root)
    # Bottom bars claim their space before the review table, so a short window shrinks the table, not the buttons.
    asset_options.pack(side="bottom", fill="x", padx=10, pady=(0, 6), before=review_area)
    mesh_export_button = ttk.Button(asset_options, text="Export GLB meshes…", state="disabled", command=save_mesh_assets)
    mesh_export_button.pack(side="left")
    mesh_button = ttk.Button(asset_options, text="Map selected mesh…", state="disabled", command=map_mesh)
    mesh_button.pack(side="right")
    dif_button = ttk.Button(asset_options, text="DIF assets…", state="disabled", command=choose_dif_assets)
    dif_button.pack(side="right", padx=(0, 8))
    dts_button = ttk.Button(asset_options, text="DTS assets…", state="disabled", command=choose_dts_assets)
    dts_button.pack(side="right", padx=(0, 8))
    sky_button = ttk.Button(asset_options, text="Sky assets…", state="disabled", command=choose_sky_assets)
    sky_button.pack(side="right", padx=(0, 8))
    audio_button = ttk.Button(asset_options, text="Audio assets…", state="disabled", command=choose_audio_assets)
    audio_button.pack(side="right", padx=(0, 8))
    terrain_button = ttk.Button(asset_options, text="Terrain layers…", state="disabled", command=lambda:
        show_terrain_layers(root, state["terrain"], state["terrain_mappings"],
                            lambda choices: state.update(terrain_mappings=choices), artwork=state["artwork"],
                            review=state["artwork_review"]))
    terrain_button.pack(side="right", padx=(0, 8))
    artwork_button = ttk.Button(asset_options, text="Source artwork…", command=choose_terrain_art)
    actions = ttk.Frame(root)
    actions.pack(side="bottom", fill="x", padx=10, pady=(0, 10), before=asset_options)
    preview = ttk.Button(
        actions, text="Preview minimap", state="disabled",
        command=preview_map,
    )
    preview.pack(side="left")
    rule_button = ttk.Button(actions, text="Conversion rules…", state="disabled", command=edit_rules)
    rule_button.pack(side="left", padx=(8, 0))
    ttk.Button(actions, text="Pipeline status…", command=lambda: show_pipeline(library, parent=root)).pack(
        side="left", padx=(8, 0))
    boundary_button = ttk.Button(actions, text="Boundaries…", command=edit_boundaries, state="disabled")
    boundary_button.pack(side="left", padx=(8, 0))
    submit = ttk.Button(actions, text="Create Unreal draft", state="disabled", command=accept)
    submit.pack(side="right")
    root.mainloop()


if __name__ == "__main__":
    config = json.load(sys.stdin)
    if isinstance(config, list):
        config = dict(meshes=config)
    run(Path(sys.argv[1]), **config)
