"""Select cubemap faces and compare their original and edited seams."""

import tkinter as tk
from tkinter import messagebox, ttk

from sky_contract import CONTACT_SHEET, FACES, ROTATIONS, collect_faces, face_plan
from sky_cube import MEAN_WARNING, PEAK_WARNING, build_cube, load_plan, seam_report
from texture_batches import save_json


def show_sky_faces(parent, root, items, on_import):
    try:
        saved = load_plan(root, items)
    except (OSError, ValueError) as error:
        messagebox.showerror('Sky faces', str(error), parent=parent)
        return
    dialog = tk.Toplevel(parent)
    dialog.title('Sky faces')
    dialog.transient(parent)
    dialog.minsize(700, 0)
    body = ttk.Frame(dialog, padding=12)
    body.pack(fill='both', expand=True)
    body.columnconfigure(1, weight=1)
    labels = {}
    for ident, row in items.items():
        name = row['asset'].rsplit('/', 1)[-1].split('.')[0]
        labels[ident] = f'{ident} · {name}'
    identifiers = {label: ident for ident, label in labels.items()}
    inputs = {}
    for column, text in enumerate(('Face', 'Batch texture', 'Clockwise rotation (°)')):
        ttk.Label(body, text=text).grid(row=0, column=column, sticky='w', padx=(0, 10))
    for row, face in enumerate(FACES, 1):
        ttk.Label(body, text=face).grid(row=row, column=0, sticky='w', pady=2)
        texture = ttk.Combobox(body, values=('', *labels.values()), state='readonly')
        rotation = ttk.Combobox(body, values=ROTATIONS, state='readonly', width=7)
        prior = saved.get(face, {})
        texture.set(labels.get(prior.get('id'), ''))
        rotation.set(prior.get('rotation', 0))
        texture.grid(row=row, column=1, sticky='ew', padx=(0, 10), pady=2)
        rotation.grid(row=row, column=2, sticky='w', pady=2)
        inputs[face] = (texture, rotation)

    use_originals = tk.BooleanVar(value=False)
    ttk.Checkbutton(body, text='Use original images', variable=use_originals).grid(
        row=7, columnspan=3, sticky='w', pady=(8, 0))
    columns = (('edge', 'Shared edge', 225), ('before', 'Original mean', 100),
               ('after', 'Build mean', 95), ('peak', 'Build peak', 95), ('status', 'Status', 78))
    table = ttk.Treeview(body, columns=[name for name, _, _ in columns], show='headings', height=12)
    for name, caption, width in columns:
        table.heading(name, text=caption)
        table.column(name, width=width, stretch=name == 'edge')
    table.grid(row=8, columnspan=3, sticky='ew', pady=(10, 8))
    status = ttk.Label(body, wraplength=650, text='Check the seams before building the cubemap.')
    status.grid(row=9, columnspan=3, sticky='w', pady=(0, 8))

    def selection():
        chosen = {face: {'id': identifiers[texture.get()], 'rotation': int(rotation.get())}
                  for face, (texture, rotation) in inputs.items() if texture.get()}
        plan = face_plan(chosen, items)
        save_json(root / 'sky-faces.json', plan)
        return plan

    def check():
        for item in table.get_children():
            table.delete(item)
        try:
            faces = collect_faces(root, items, selection(), use_originals=use_originals.get())
            earlier = seam_report(faces, replacement=False)
            current = seam_report(faces, replacement=True)
        except (OSError, ValueError, SyntaxError) as error:
            status.configure(text=str(error))
            return
        for before, after in zip(earlier, current):
            table.insert('', 'end', values=(
                f"{after['first']} ↔ {after['second']}", before['mean'], after['mean'], after['peak'],
                'Review' if after['review'] else 'OK',
            ))
        count = sum(edge['review'] for edge in current)
        status.configure(text=f'{count} of 12 seams marked for review. '
                              f'Thresholds are mean {MEAN_WARNING} and peak {PEAK_WARNING} on a 0–255 scale.')

    def build(*, import_to_editor=False):
        try:
            target = build_cube(root, items, selection(), use_originals=use_originals.get())
        except (OSError, ValueError, SyntaxError) as error:
            status.configure(text=str(error))
            return
        if import_to_editor:
            on_import(target.name)
        else:
            files = f'SkyCube.dds and {CONTACT_SHEET}' if (target / CONTACT_SHEET).is_file() else 'SkyCube.dds'
            status.configure(text=f'Saved {files} in {target}')

    bar = ttk.Frame(body)
    bar.grid(row=10, columnspan=3, sticky='ew')
    ttk.Button(bar, text='Check seams', command=check).pack(side='left', padx=(0, 8))
    ttk.Button(bar, text='Build DDS', command=build).pack(side='left', padx=(0, 8))
    ttk.Button(bar, text='Build and import cubemap', command=lambda: build(import_to_editor=True)).pack(side='left')
    ttk.Button(bar, text='Close', command=dialog.destroy).pack(side='right')
    dialog.bind('<Escape>', lambda _: dialog.destroy())
    dialog.grab_set()
    return dialog
