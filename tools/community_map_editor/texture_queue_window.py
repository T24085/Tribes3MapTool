"""Texture batch queue setup and live progress in the existing reviewer."""

from decimal import Decimal, InvalidOperation
import json
from queue import Empty, SimpleQueue
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from intake_contract import bounded
from texture_batches import save_json
from texture_repair_queue import pending_jobs, queue_command, run_queue


def estimated_rate(text):
    if not text.strip():
        return None
    try:
        rate = Decimal(text)
    except InvalidOperation as error:
        raise ValueError('Enter a nonnegative estimate or leave the field blank.') from error
    if not rate.is_finite() or rate < 0 or rate > 1_000_000:
        raise ValueError('Enter a nonnegative estimate up to 1,000,000 or leave the field blank.')
    return rate


def show_queue(parent, root, items, refresh):
    dialog = tk.Toplevel(parent)
    dialog.title('External texture queue')
    dialog.transient(parent)
    dialog.minsize(620, 365)
    frame = ttk.Frame(dialog, padding=14)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(5, weight=1)
    settings_path = root / 'queue-settings.json'
    defaults = dict(executable='', arguments='["{source}", "{output}", "{width}", "{height}"]', minutes='', usd='')
    settings = defaults
    if settings_path.is_file():
        try:
            if settings_path.resolve(strict=True) != settings_path:
                raise ValueError('The queue settings must stay inside this batch.')
            saved = json.loads(bounded(settings_path, 192 * 1024))
            if (not isinstance(saved, dict) or saved.keys() != defaults.keys()
                    or any(not isinstance(value, str) for value in saved.values())):
                raise ValueError('Invalid saved texture queue settings.')
            settings = saved
        except (OSError, ValueError) as error:
            messagebox.showerror('Texture queue settings', str(error), parent=dialog)
    values = {key: tk.StringVar(dialog, value=value) for key, value in settings.items()}
    for row, (key, label) in enumerate((('executable', 'Processor executable'), ('arguments', 'Arguments (JSON)'),
                                       ('minutes', 'Minutes per image'), ('usd', 'USD per image'))):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=3)
        ttk.Entry(frame, textvariable=values[key]).grid(row=row, column=1, sticky='ew', pady=3)

    def browse():
        executable = filedialog.askopenfilename(title='Choose the texture processor', parent=dialog,
                                                filetypes=[('Windows executable', '*.exe'), ('All files', '*')])
        if executable:
            values['executable'].set(executable)

    ttk.Button(frame, text='Browse…', command=browse).grid(row=0, column=2, padx=(8, 0))
    ttk.Label(frame, text='Arguments can use {source}, {output}, {width}, and {height}. Estimates are optional.') \
        .grid(row=4, columnspan=3, sticky='w', pady=(8, 4))
    log = tk.Text(frame, height=8, wrap='word', state='disabled')
    log.grid(row=5, columnspan=3, sticky='nsew')
    actions = ttk.Frame(frame)
    actions.grid(row=6, columnspan=3, sticky='ew', pady=(8, 0))
    messages = SimpleQueue()
    stop = Event()
    worker = None

    def append(message):
        log.configure(state='normal')
        log.insert('end', message + '\n')
        log.see('end')
        log.configure(state='disabled')

    def poll():
        nonlocal worker
        try:
            while True:
                append(messages.get_nowait())
        except Empty:
            pass
        if worker.is_alive() or not messages.empty():
            dialog.after(100, poll)
            return
        worker = None
        start.configure(state='normal')
        close.configure(text='Close', state='normal')
        refresh()

    def request_close():
        if worker is None:
            dialog.destroy()
            return
        stop.set()
        close.configure(text='Stopping after current image', state='disabled')

    def estimate_and_run():
        nonlocal worker
        settings = {key: value.get() for key, value in values.items()}
        try:
            command = queue_command(settings['executable'], json.loads(settings['arguments']))
            jobs = pending_jobs(root, items)
            minutes, usd = (estimated_rate(settings[key]) for key in ('minutes', 'usd'))
            if not jobs:
                append('Every item already has a replacement. Use Retry unresolved to start a new batch for rejected edits.')
                return
            pixels = sum(row['target'][0] * row['target'][1] for row in jobs)
            duration = f'{minutes * len(jobs):.2f} minutes' if minutes is not None else 'time not estimated'
            cost = f'${usd * len(jobs):.2f} USD' if usd is not None else 'cost not entered'
            summary = f'{len(jobs)} images · {pixels / 1_000_000:.2f} output MP\n{duration} · {cost}'
            if not messagebox.askokcancel('Run texture queue?', summary + '\n\nExisting replacements will be kept.', parent=dialog):
                return
            save_json(settings_path, settings)
        except (OSError, ValueError) as error:
            messagebox.showerror('Texture queue', str(error), parent=dialog)
            return

        def work():
            try:
                results = run_queue(root, jobs, command, stopped=stop.is_set, report=messages.put)
                ready = sum(result == 'ready for review' for result in results.values())
                remaining = len(pending_jobs(root, items))
                state = 'Stopped' if stop.is_set() else 'Finished'
                messages.put(f'{state} · {ready} new replacements ready for review · {remaining} images still missing')
            except (ImportError, OSError, ValueError) as error:
                messages.put(str(error))

        stop.clear()
        append(summary)
        start.configure(state='disabled')
        close.configure(text='Stop after current')
        worker = Thread(target=work, name='texture-queue', daemon=True)
        worker.start()
        dialog.after(100, poll)

    start = ttk.Button(actions, text='Estimate and run…', command=estimate_and_run)
    start.pack(side='left')
    close = ttk.Button(actions, text='Close', command=request_close)
    close.pack(side='right')
    dialog.protocol('WM_DELETE_WINDOW', request_close)
    dialog.grab_set()
