"""Draw an ordered boundary polygon on the imported terrain."""

import tkinter as tk
from tkinter import ttk

from minimap import framing, terrain_pixels, view_transform
from perimeter import MAX_VERTICES, check_polygon_clearance, check_polygon_outline, parse_polygon, rectangle_outline


def show_polygon(parent, header, terrain, rows, initial, on_save, *, solid=False, constrain_pawns=True):
    window = tk.Toplevel(parent)
    label = "Solid perimeter" if solid else "Flag-only boundary"
    boundary = "mission perimeter" if solid else "flag-only limit"
    color = "#7ce0de" if solid else "#f28aad"
    window.title("Draw " + label.lower())
    window.transient(parent)
    window.resizable(False, False)
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    instruction = "Click each corner in order."
    if not solid:
        instruction += " Keep both flags inside."
    elif constrain_pawns:
        instruction += " Keep flags and spawns inside."
    ttk.Label(body, text=instruction).pack(anchor="w")
    size = 416
    canvas = tk.Canvas(body, width=size, height=size, highlightthickness=0)
    canvas.pack(pady=(6, 0))
    bounds = header["minimap"]["image_bounds"]
    project, world = view_transform(framing(bounds), size)
    pixels = terrain_pixels(terrain, size, size, world)
    canvas.image = tk.PhotoImage(master=window, data=f"P5\n{size} {size}\n255\n".encode() + pixels, format="PPM")
    canvas.create_image(0, 0, image=canvas.image, anchor="nw")
    positions = rectangle_outline(bounds)
    canvas.create_line(*(value for point in (*positions, positions[0]) for value in project(*point)),
                       fill="#f2ce7d", dash=(5, 3), width=2)
    markers = []
    for row in rows:
        if row["status"] != "marker":
            continue
        point = row["position_cm"]
        flag = row["kind"] == "flag"
        if (solid and constrain_pawns) or (flag and not solid):
            markers.append((row["name"] or row["kind"], point))
        x, y = project(*point[:2])
        team_color = "#6ab7ff" if row["team"] == 1 else "#ff7575"
        if flag:
            canvas.create_polygon(x, y - 6, x + 6, y, x, y + 6, x - 6, y, fill=team_color, outline="#111b23")
        elif solid:
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=team_color, outline="#111b23")
    vertices = list(initial)
    message = ttk.Label(body, wraplength=size)
    message.pack(fill="x", pady=(6, 0))

    def redraw():
        canvas.delete("vertices")
        points = [project(x * 100, y * 100) for x, y in vertices]
        if len(points) >= 2:
            outline = (*points, points[0]) if len(points) >= 3 else points
            canvas.create_line(*(value for point in outline for value in point), fill=color, width=2, tags="vertices")
        for index, (x, y) in enumerate(points, 1):
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline="#111b23", tags="vertices")
            canvas.create_text(x + 7, y - 6, text=str(index), anchor="sw", fill="white", tags="vertices")
        message.configure(text=f"{len(points)} of {MAX_VERTICES} corners")

    def add(event):
        if len(vertices) == MAX_VERTICES:
            return
        point = [round(value / 100, 2) for value in world(event.x, event.y)]
        if not all(low < value * 100 < high for low, value, high in zip(bounds[0], point, bounds[1])):
            message.configure(text="Place each corner inside the gold line.")
            return
        vertices.append(point)
        redraw()

    def undo():
        if vertices:
            vertices.pop()
            redraw()

    def clear():
        vertices.clear()
        redraw()

    def close():
        window.destroy()
        if parent.winfo_exists():
            parent.grab_set()

    def save():
        try:
            polygon = parse_polygon(vertices, label)
            check_polygon_outline(polygon, label, bounds)
            check_polygon_clearance(polygon, markers, boundary)
        except ValueError as exc:
            message.configure(text=str(exc))
            return
        on_save(vertices)
        close()

    buttons = ttk.Frame(body)
    buttons.pack(fill="x", pady=(10, 0))
    ttk.Button(buttons, text="Undo corner", command=undo).pack(side="left")
    ttk.Button(buttons, text="Clear", command=clear).pack(side="left", padx=6)
    ttk.Button(buttons, text="Use polygon", command=save).pack(side="right")
    ttk.Button(buttons, text="Cancel", command=close).pack(side="right", padx=6)
    canvas.bind("<Button-1>", add)
    window.bind("<Escape>", lambda _event: close())
    window.protocol("WM_DELETE_WINDOW", close)
    redraw()
    window.grab_set()
