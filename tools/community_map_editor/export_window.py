"""Collect the operator inputs for a Direct Host map staging export."""

import json
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .conversion_rules import read
from .source_profiles import draft_edition
from .tk_runtime import configure_tk


FIELDS_NAME = 'export-fields.json'


def fields_path():
    local = os.environ.get('LOCALAPPDATA')
    return (Path(local) / 'Tribes3MapTool' if local else Path.home() / '.tribes3maptool') / FIELDS_NAME


def remembered_fields():
    """Last host baseline and export root, and a label per source map: none of them change between drafts."""
    try:
        fields = json.loads(fields_path().read_text(encoding='utf-8'))
        return fields if isinstance(fields, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_fields(fields):
    try:
        path = fields_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fields, indent=1), encoding='utf-8')
    except OSError:
        pass  # Only a convenience: the export itself does not depend on it.


def draft_intake(library, draft_id):
    """The intake folder a draft was built from (its rules record the intake hash), or None."""
    try:
        rules = json.loads((Path(library) / 'DraftRules' / f'{draft_id}.json').read_text(encoding='utf-8'))
        digest = rules['intake_sha256'][:16]
        found = [path for path in Path(library).glob(f'*-{digest}') if (path / 'manifest.json').is_file()]
        return found[0] if len(found) == 1 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_minimap(intake, draft_id, parent):
    """Write the draft's minimap merge folder from its intake, as the minimap preview's Export for host does."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from minimap import export_host_minimap

    manifest = intake / 'manifest.json'
    header = json.loads(manifest.read_text(encoding='utf-8'))
    return Path(export_host_minimap(manifest, header['minimap'], draft_id, Path(parent)))


def choose(draft_id, *, batch_labels=(), library=None):
    configure_tk()
    window = tk.Tk()
    window.title('Cook and stage batch' if batch_labels else 'Build for Direct Host')
    window.minsize(575, 0)
    form = ttk.Frame(window, padding=16)
    form.grid(sticky='nsew')
    window.columnconfigure(0, weight=1)
    form.columnconfigure(1, weight=1)
    heading = f'{len(batch_labels)} rebuilt maps' if batch_labels else draft_id
    ttk.Label(form, text=heading, font='TkHeadingFont').grid(row=0, columnspan=3, sticky='w', pady=(0, 10))
    default_label = f'{draft_id} ({draft_edition(draft_id)})'
    # Fields that never change for a map are remembered; the minimap is built from the draft's intake.
    fields = remembered_fields()
    intake = draft_intake(library, draft_id) if library and not batch_labels else None
    map_key = intake.name.rsplit('-', 1)[0] if intake else None
    labels = fields.get('labels') if isinstance(fields.get('labels'), dict) else {}
    root = fields.get('export_root') if isinstance(fields.get('export_root'), str) else ''
    label = tk.StringVar(window, value=labels.get(map_key) or default_label)
    host = tk.StringVar(window, value=fields.get('host') if isinstance(fields.get('host'), str) else '')
    folder = tk.StringVar(window, value=str(Path(root) / draft_id.lower()) if root else '')
    try:
        automatic = intake is not None and 'minimap' in json.loads((intake / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        automatic = False
    minimap = tk.StringVar(window, value=str(Path(root) / f'{draft_id}-minimap') if root and automatic else '')
    update = tk.BooleanVar(window, value=False)
    result = None

    def use_installed_label():
        if not update.get() or not host.get().strip() or label.get() != default_label:
            return
        path = Path(host.get().strip())/'lua'/draft_id/'map.json'
        try:
            name = read(path, 16384)['label']
            if isinstance(name, str) and name.strip():
                label.set(name)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def browse(caption, value):
        selection = filedialog.askdirectory(parent=window, title=caption, mustexist=True)
        if selection:
            value.set(selection)
            if value is host:
                use_installed_label()

    inputs = (
        ('Map label', label),
        ('Host baseline', host),
        ('Export to', folder),
        ('Minimap (optional)', minimap),
    )
    if batch_labels:
        inputs = inputs[1:3]
    for row, (caption, value) in enumerate(inputs, 1):
        ttk.Label(form, text=caption).grid(row=row, column=0, padx=(0, 10), pady=4, sticky='w')
        entry = ttk.Entry(form, textvariable=value, width=48)
        entry.grid(row=row, column=1, pady=4, sticky='ew')
        if caption == 'Map label':
            entry.focus_set()
            entry.selection_range(0, 'end')
        else:
            ttk.Button(form, text='Browse…', command=lambda c=caption, v=value: browse(c, v)).grid(
                row=row, column=2, padx=(8, 0), pady=4, sticky='e')

    names = "\n".join(batch_labels[:5])
    if len(batch_labels) > 5:
        names += f"\n… and {len(batch_labels) - 5} more"
    if names:
        ttk.Label(form, text=names, justify='left', wraplength=600).grid(
            row=4, columnspan=3, sticky='w', pady=(8, 0))
    if not batch_labels:
        ttk.Checkbutton(form, text='Update this map ID', variable=update, command=use_installed_label).grid(
            row=5, columnspan=3, sticky='w', pady=(6, 0))
    note = f'Creates {draft_id}-portable inside the selected export folder.'
    if automatic:
        note += " A missing export or minimap folder is created from this draft's intake."
    ttk.Label(form, text=note, wraplength=560).grid(
        row=6, columnspan=3, sticky='w', pady=(6, 12))

    def submit(_event=None):
        nonlocal result
        use_installed_label()
        display = label.get().strip()
        host_path, parent = map(str.strip, (host.get(), folder.get()))
        minimap_path = minimap.get().strip()
        try:
            if not 1 <= len(display) <= 64 or not display.isprintable():
                raise ValueError('Enter a map label of 1 to 64 printable characters.')
            if not host_path or not (Path(host_path) / 'package-manifest.json').is_file():
                raise ValueError('Choose a Direct Host match bundle with package-manifest.json.')
            if update.get() and not (Path(host_path)/'lua'/draft_id/'map.json').is_file():
                raise ValueError('Choose a baseline containing this draft’s current release.')
            if parent and not Path(parent).exists() and Path(parent).parent.is_dir():
                Path(parent).mkdir()  # The per-draft folder under a remembered export root.
            if not parent or not Path(parent).is_dir():
                raise ValueError('Choose an existing folder for the export.')
            if automatic and minimap_path and not Path(minimap_path).exists():
                if Path(minimap_path).name != f'{draft_id}-minimap' or not Path(minimap_path).parent.is_dir():
                    raise ValueError(f'Name a new minimap folder {draft_id}-minimap in an existing folder.')
                minimap_path = str(build_minimap(intake, draft_id, Path(minimap_path).parent))
            destination = Path(parent).resolve(strict=True) / f'{draft_id}-portable'
            sources = (Path(host_path), Path(minimap_path)) if minimap_path else (Path(host_path),)
            for source in sources:
                if destination.is_relative_to(source.resolve(strict=True)):
                    raise ValueError('The export must be outside its input folders.')
            if destination.exists() or destination.is_symlink():
                raise ValueError('That export already exists. Choose another folder.')
            if minimap_path and not all((Path(minimap_path) / name).is_file() for name in (
                    'map-minimap.json', 'manifest-minimap.json', 'minimap.png')):
                raise ValueError('Choose the folder created by Export for host in the minimap preview.')
        except (OSError, ValueError) as error:
            messagebox.showerror('Build for Direct Host', str(error), parent=window)
            return
        fields['host'] = str(Path(host_path).resolve())
        if not batch_labels:
            fields['export_root'] = str(destination.parent.parent)
            if map_key:
                fields['labels'] = {**labels, map_key: display}
        remember_fields(fields)
        if batch_labels:
            result = {'host': Path(host_path).resolve(), 'parent': destination.parent}
        else:
            result = {'label': display, 'host': Path(host_path).resolve(), 'destination': destination,
                      'minimap': Path(minimap_path).resolve() if minimap_path else None,
                      'replace_existing': update.get()}
        window.destroy()

    buttons = ttk.Frame(form)
    buttons.grid(row=7, columnspan=3, sticky='e')
    ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='left', padx=(0, 8))
    ttk.Button(buttons, text='Cook and stage' if batch_labels else 'Cook and export', command=submit).pack(side='left')
    window.bind('<Return>', submit)
    window.bind('<Escape>', lambda _: window.destroy())
    window.mainloop()
    return result
