"""Choose a source animation clip and optionally inspect a single frame."""

import json
import sys
import tkinter as tk
from tkinter import ttk

from tk_runtime import configure_tk


def choose(clips, *, bake=False):
    configure_tk()
    root = tk.Tk()
    root.title('Bake DTS animation' if bake else 'Preview DTS animation')
    root.resizable(False, False)
    root.attributes('-topmost', True)
    body = ttk.Frame(root, padding=18)
    body.grid(sticky='nsew')
    ttk.Label(body, text='Animation clip').grid(row=0, column=0, columnspan=3, sticky='w')
    labels = [f'{clip["name"] or f"Clip {index}"} · {clip["seconds"]:g} s'
              + (' · loops' if clip['loop'] else '') for index, clip in enumerate(clips, 1)]
    clip = ttk.Combobox(body, state='readonly', values=labels, width=44)
    clip.grid(row=1, column=0, columnspan=3, pady=(4, 14), sticky='ew')
    clip.current(0)
    position = tk.IntVar(value=0)
    if not bake:
        ttk.Label(body, text='Frame position').grid(row=2, column=0, columnspan=3, sticky='w')
        value_label = ttk.Label(body, text='0%', width=5)
        value_label.grid(row=3, column=2, sticky='e')
        slider = tk.Scale(body, from_=0, to=100, orient='horizontal', variable=position, showvalue=False,
                          highlightthickness=0, command=lambda _: value_label.configure(text=f'{position.get()}%'))
        slider.grid(row=3, column=0, columnspan=2, pady=(2, 14), sticky='ew')
    answer = None

    def finish(frame=False):
        nonlocal answer
        answer = [clip.current(), position.get() / 100 if frame else None]
        root.destroy()

    ttk.Button(body, text='Bake clip' if bake else 'Play clip', command=finish).grid(row=4, column=0, sticky='w')
    if not bake:
        ttk.Button(body, text='Show frame', command=lambda: finish(True)).grid(row=4, column=1)
    ttk.Button(body, text='Cancel', command=root.destroy).grid(row=4, column=2, sticky='e')
    root.bind('<Return>', lambda _: finish())
    root.bind('<Escape>', lambda _: root.destroy())
    clip.focus_set()
    root.mainloop()
    return answer


if __name__ == '__main__':
    selected = choose(json.load(sys.stdin), bake='--bake' in sys.argv[1:])
    if selected is not None:
        print(json.dumps(selected))
