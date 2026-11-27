"""Visible progress for slow Map Tool steps (Tribes3-Modded/Tribes3MapTool#8).

``run_busy`` runs slow, Tk-free work (reading packages, converting a map, exporting an intake) on a
worker thread. Meanwhile a small window shows what is happening, a moving progress bar and the time
elapsed. The caller still gets the result, or the exception, back in order, so existing code keeps its
shape. ``work`` must not touch Tk: read any widget values before calling.
"""

import threading
import time
import tkinter as tk
from tkinter import ttk

POLL_SECONDS = 0.05


def elapsed_text(seconds):
    seconds = int(seconds)
    return f"{seconds // 60} min {seconds % 60:02d} s elapsed" if seconds >= 60 else f"{seconds} s elapsed"


def run_busy(parent, message, work):
    """Run ``work()`` on a worker thread behind a modal progress window; return its result or re-raise."""
    outcome = {}

    def target():
        try:
            outcome["value"] = work()
        except BaseException as error:  # handed back to the caller's thread below
            outcome["error"] = error

    owner = parent.winfo_toplevel()
    window = tk.Toplevel(owner)
    window.title("Working…")
    window.transient(owner)
    window.resizable(False, False)
    window.protocol("WM_DELETE_WINDOW", lambda: None)  # the work cannot be stopped part-way
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=message, wraplength=420, justify="left").pack(anchor="w")
    bar = ttk.Progressbar(body, mode="indeterminate", length=420)
    bar.pack(fill="x", pady=(10, 4))
    elapsed = tk.StringVar(window, elapsed_text(0))
    ttk.Label(body, textvariable=elapsed).pack(anchor="e")
    window.update_idletasks()
    x = owner.winfo_rootx() + max(0, (owner.winfo_width() - window.winfo_width()) // 2)
    y = owner.winfo_rooty() + max(0, (owner.winfo_height() - window.winfo_height()) // 3)
    window.geometry(f"+{x}+{y}")
    cursor = owner.cget("cursor")
    owner.configure(cursor="watch")
    try:
        window.grab_set()
    except tk.TclError:  # another window holds the grab; the progress window still shows
        pass
    bar.start(12)
    started = time.monotonic()
    thread = threading.Thread(target=target, name="map-tool-busy", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            elapsed.set(elapsed_text(time.monotonic() - started))
            window.update()
            thread.join(POLL_SECONDS)
    finally:
        bar.stop()
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()
        owner.configure(cursor=cursor)
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")
