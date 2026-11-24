"""Review a map's edited texture PNGs before importing draft-local versions."""

import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from sky_review import show_sky_faces
from texture_batches import (decisions, draft_folder, inspect, inspect_replacement, load_batch, make_color_revision,
                             original_png, retry_batch, save_json)
from texture_detail import CHANNELS, tile_edges
from texture_detail_window import show_detail
from tk_runtime import configure_tk


def texture_preview(path, *, tiled=False, channel="All", opaque=False):
    with Image.open(path) as check:
        check.verify()
    with Image.open(path) as source:
        source.load()
        alpha = 'A' in source.getbands() and source.getchannel('A').getextrema()[0] < 255
        image = source if channel == "All" else source.getchannel(channel)
        seams = tile_edges(image)
        if opaque and channel == "All":
            image = image.convert("RGB")
        image.thumbnail((190, 165) if tiled else (380, 330))
        if not tiled:
            return image.copy(), alpha, seams
        preview = Image.new(image.mode, (image.width * 2, image.height * 2))
        for x, y in ((0, 0), (0, 1), (1, 0), (1, 1)):
            preview.paste(image, (x * image.width, y * image.height))
        return preview, alpha, seams


def review(library, draft):
    configure_tk()
    window = tk.Tk()
    window.withdraw()
    parent = draft_folder(library, draft)
    choice = filedialog.askdirectory(title='Open a texture batch', initialdir=parent, parent=window, mustexist=True)
    if not choice:
        window.destroy()
        return
    try:
        choice = Path(choice)
        if choice.parent.resolve(strict=True) != parent:
            raise ValueError('Choose a batch inside this map draft’s texture library.')
        root, items = load_batch(library, draft, choice.name)
        saved = decisions(root, items)
    except (OSError, ValueError) as error:
        messagebox.showerror('Texture batch', str(error), parent=window)
        window.destroy()
        return

    window.title(f'Texture batch · {draft}')
    window.minsize(940, 495)
    body = ttk.Frame(window, padding=12)
    body.pack(fill='both', expand=True)
    body.columnconfigure(1, weight=1)
    body.columnconfigure(2, weight=1)
    body.rowconfigure(1, weight=1)
    ttk.Label(body, text='Textures').grid(row=0, column=0, sticky='w')
    ttk.Label(body, text='Original').grid(row=0, column=1, sticky='w', padx=10)
    ttk.Label(body, text='Replacement').grid(row=0, column=2, sticky='w', padx=10)
    left_panel = ttk.Frame(body)
    left_panel.grid(row=1, column=0, sticky='nsew', pady=(5, 0))
    list_frame = ttk.Frame(left_panel)
    list_frame.pack(fill='both', expand=True)
    table = ttk.Treeview(list_frame, columns=('decision',), show='tree headings', selectmode='browse', height=15)
    table.heading('#0', text='File')
    table.heading('decision', text='Review')
    table.column('#0', width=72, stretch=False)
    table.column('decision', width=95, stretch=False)
    table.pack(side='left', fill='both', expand=True)
    scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    tiles = [ttk.Label(body, anchor='center') for _ in range(2)]
    for col, tile in enumerate(tiles, 1):
        tile.grid(row=1, column=col, sticky='nsew', padx=10)
    note = ttk.Label(body, wraplength=880)
    note.grid(row=2, columnspan=3, sticky='w', pady=(10, 2))
    status = ttk.Label(body, wraplength=880)
    status.grid(row=3, columnspan=3, sticky='w', pady=(0, 8))
    bar = ttk.Frame(body)
    bar.grid(row=4, columnspan=3, sticky='ew')
    active = None
    tiled = tk.BooleanVar(window)
    apply_to_map = tk.BooleanVar(window)
    channel = tk.StringVar(window, value="All")

    def fill(tile, image):
        tile.image = ImageTk.PhotoImage(image, master=window)
        tile.configure(image=tile.image)

    def show():
        nonlocal active
        active = None
        for tile in tiles:
            tile.configure(image='')
            tile.image = None
        approval.configure(state='disabled')
        rejection.configure(state='disabled')
        color_button.configure(state='disabled')
        detail_button.configure(state='disabled')
        channel_picker.configure(state='disabled')
        selected = table.selection()
        if not selected:
            return
        ident = selected[0]
        row = items[ident]
        mask = row['role'] == 'masks'
        channels = ("All", *CHANNELS[row['source']['channels']]) if mask else ("All",)
        if channel.get() not in channels:
            channel.set("All")
        channel_picker.configure(values=channels, state='readonly' if mask else 'disabled')
        preview_options = dict(tiled=tiled.get(), channel=channel.get(), opaque=mask)
        width, height = row['target']
        note.configure(text=f"{row['asset'].rsplit('/', 1)[-1].split('.')[0]} · {row['role'].title()} · "
                            f"{width}×{height} target\n{row['notes']}")
        try:
            original, alpha, original_seams = texture_preview(original_png(root, row), **preview_options)
            fill(tiles[0], original)
            if row['role'] == 'color' and row['source']['channels'] in (2, 6):
                color_button.configure(state='normal')
            if mask or (row['role'] == 'normal' and row['source']['channels'] in (2, 6)):
                detail_button.configure(state='normal')
            replacement, info = inspect_replacement(root, row)
            image, replacement_alpha, replacement_seams = texture_preview(replacement, **preview_options)
            fill(tiles[1], image)
            if not mask and alpha != replacement_alpha:
                raise ValueError('The replacement changed whether the texture has transparent pixels.')
        except (OSError, ValueError, SyntaxError) as error:
            status.configure(text=str(error))
            table.set(ident, 'decision', 'Needs retry')
            return
        active = ident, info['sha256']
        prior = saved.get(ident, {})
        decision = prior.get('status') if prior.get('sha256') == info['sha256'] else None
        table.set(ident, 'decision', decision.title() if decision else 'Ready')
        text = f'{ident}.png is {width}×{height} and matches the source format.'
        if original_seams and replacement_seams:
            edge_labels = ("left/right", "top/bottom")
            edges = [
                f'{label} {old_mean:.1f}/{old_peak} → {mean:.1f}/{peak}'
                for label, (old_mean, old_peak), (mean, peak) in
                zip(edge_labels, original_seams, replacement_seams, strict=True)
            ]
            text += '\nTile edge difference (mean/peak, 0–255) · ' + ' · '.join(edges)
        status.configure(text=text)
        approval.configure(state='normal')
        rejection.configure(state='normal')

    def decide(label):
        if not active:
            return
        ident, digest = active
        try:
            if inspect(root, items[ident])[2]['sha256'] != digest:
                raise ValueError('The PNG changed during review. Refresh and inspect it again.')
            review = dict(saved, **{ident: {'status': label, 'sha256': digest}})
            save_json(root / 'review.json', review)
        except (OSError, ValueError) as error:
            messagebox.showerror('Texture review', str(error), parent=window)
            return
        saved.update(review)
        table.set(ident, 'decision', label.title())
        status.configure(text=f'{ident}.png {label}.')

    def retry():
        try:
            target = retry_batch(library, draft, root, items)
            messagebox.showinfo('Retry batch', f'Saved unresolved textures to\n{target}', parent=window)
        except (OSError, ValueError) as error:
            messagebox.showerror('Retry batch', str(error), parent=window)

    def send_selection(**selection):
        print(json.dumps({'batch': root.name, **selection}), flush=True)
        window.destroy()

    def finish():
        if not any(row['status'] == 'approved' for row in saved.values()):
            messagebox.showinfo('Texture batch', 'Approve at least one replacement first.', parent=window)
            return
        send_selection(apply_to_map=apply_to_map.get())

    def edit_sky_faces():
        show_sky_faces(window, root, items, lambda build: send_selection(sky=build))

    def open_revision(candidate, selected_channel="All"):
        try:
            batch_root, batch_items = load_batch(library, draft, candidate.name)
            channel.set(selected_channel)
            display_batch(batch_root, batch_items, decisions(batch_root, batch_items))
        except (OSError, ValueError, SyntaxError) as error:
            messagebox.showerror('Texture revision', str(error), parent=window)

    def edit_detail():
        selected = table.selection()
        if selected:
            show_detail(window, library, draft, root, items[selected[0]], open_revision)

    def adjust_color(*, use_reference):
        selected = table.selection()
        if not selected:
            return
        tint, reference = None, None
        if use_reference:
            reference = filedialog.askopenfilename(title='Choose the reference PNG', filetypes=[('PNG image', '*.png')],
                                                    parent=window)
            if not reference:
                return
        else:
            channels, _ = colorchooser.askcolor(title='Choose a tint color', parent=window)
            if channels is None:
                return
            tint = tuple(round(value) for value in channels)
        strength = simpledialog.askinteger('Color adjustment', 'Strength (%)', minvalue=1, maxvalue=100,
                                           initialvalue=100 if use_reference else 35, parent=window)
        if strength is None:
            return
        try:
            candidate = make_color_revision(library, draft, root, items[selected[0]], strength=strength,
                                            tint=tint, reference=reference)
        except (OSError, ValueError, SyntaxError) as error:
            messagebox.showerror('Color adjustment', str(error), parent=window)
        else:
            open_revision(candidate)

    def display_batch(batch_root, batch_items, batch_decisions):
        nonlocal root, items, saved
        root, items, saved = batch_root, batch_items, batch_decisions
        for entry in table.get_children():
            table.delete(entry)
        for ident in items:
            table.insert('', 'end', iid=ident, text=ident + '.png',
                         values=(saved.get(ident, {}).get('status', 'Unreviewed').title(),))
        table.selection_set(next(iter(items)))
        if len(items) >= 6:
            sky_button.pack(side='left', padx=(8, 0), after=color_button)
        else:
            sky_button.pack_forget()
        show()

    approval = ttk.Button(bar, text='Approve', state='disabled', command=lambda: decide('approved'))
    rejection = ttk.Button(bar, text='Reject', state='disabled', command=lambda: decide('rejected'))
    for button in (approval, rejection, ttk.Button(bar, text='Refresh', command=show),
                   ttk.Button(bar, text='Retry unresolved…', command=retry)):
        button.pack(side='left', padx=(0, 8))
    ttk.Checkbutton(bar, text='Tile preview', variable=tiled, command=show).pack(side='left')
    color_button = ttk.Menubutton(bar, text='Adjust color', state='disabled')
    color_menu = tk.Menu(color_button, tearoff=False)
    color_menu.add_command(label='Choose tint…', command=lambda: adjust_color(use_reference=False))
    color_menu.add_command(label='Match reference PNG…', command=lambda: adjust_color(use_reference=True))
    color_button.configure(menu=color_menu)
    color_button.pack(side='left', padx=(8, 0))
    sky_button = ttk.Button(bar, text='Sky faces…', command=edit_sky_faces)
    ttk.Button(bar, text='Import approved', command=finish).pack(side='right')
    ttk.Checkbutton(bar, text='Apply to draft mesh materials', variable=apply_to_map).pack(side='right', padx=8)
    detail_button = ttk.Button(left_panel, text='Merge surface detail…', command=edit_detail, state='disabled')
    detail_button.pack(fill='x', pady=(8, 0))
    ttk.Label(left_panel, text='Mask preview channel').pack(anchor='w', pady=(8, 0))
    channel_picker = ttk.Combobox(left_panel, textvariable=channel, width=8, state='disabled')
    channel_picker.bind('<<ComboboxSelected>>', lambda _event: show())
    channel_picker.pack(anchor='w')

    table.bind('<<TreeviewSelect>>', lambda _: show())
    display_batch(root, items, saved)
    window.deiconify()
    window.mainloop()


if __name__ == '__main__':
    review(Path(sys.argv[1]), sys.argv[2])
