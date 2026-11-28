"""What slow Map Tool work is doing right now, for the progress window (busy.py) to show.

Tk-free, so the package readers and converters can report from any thread or process context; outside a
progress window the text is simply never shown.
"""

MAX_STATUS = 90
_status = {"text": ""}


def status(text):
    """Report the step slow work is on ("Reading TrCTF-Tartarus.fmap", "Remastering T_..._DIF")."""
    text = " ".join(str(text).split())
    _status["text"] = text if len(text) <= MAX_STATUS else text[:MAX_STATUS - 1] + "…"


def current():
    return _status["text"]


def clear():
    _status["text"] = ""
