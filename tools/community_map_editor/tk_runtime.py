"""Find Tcl/Tk data in a locally installed Windows Python."""

import os
from pathlib import Path
import sys
import tkinter


def configure_tk():
    root = Path(sys.base_prefix) / 'tcl'
    for variable, folder, script in (
        ('TCL_LIBRARY', f'tcl{tkinter.TclVersion}', 'init.tcl'),
        ('TK_LIBRARY', f'tk{tkinter.TkVersion}', 'tk.tcl'),
    ):
        if (root / folder / script).is_file():
            os.environ[variable] = str(root / folder)
