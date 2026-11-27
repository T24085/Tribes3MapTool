"""Use a folder of remastered (for example 2x upscaled) textures in place of source base colors (#9).

A remastered file is named after the source texture's object path with each "." replaced by
"__", for example ``Arch_Drydock.Tex.T_DryDock_Crane_01_DIF`` -> ``Arch_Drydock__Tex__T_DryDock_Crane_01_DIF.png``.
That is the name the texture extraction and remaster tools write. A match is used only when it
keeps the original's aspect ratio, is at least as large and fits the intake's texture budget;
otherwise the original texture stays, with a note saying why.
"""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from model_textures import MAX_SIDE, MAX_TEXTURE_BYTES, validate_png

MAX_FILE_BYTES = 128 * 1024 * 1024
# Each intake image file is limited to 16 MiB (intake_contract.intake_file_limit).
MAX_IMAGE_BYTES = 16 * 1024 * 1024
# Original source textures keep this much of the intake texture budget for themselves.
ORIGINAL_RESERVE = 96 * 1024 * 1024
ORIGIN = "Remastered textures"


def remaster_name(object_path):
    return object_path.replace(".", "__") + ".png"


def validate_folder(value):
    """Check a remastered texture folder setting: an absolute path to an existing folder."""
    if not isinstance(value, str) or not 0 < len(value) <= 1024 or not value.isprintable():
        raise ValueError("Choose an existing remastered texture folder.")
    folder = Path(value)
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError("Choose an existing remastered texture folder.")
    return folder


class RemasteredTextures:
    def __init__(self, folder, budget=MAX_TEXTURE_BYTES - ORIGINAL_RESERVE):
        self.folder = validate_folder(str(folder))
        self.files = {path.name.casefold(): path for path in self.folder.glob("*.png") if path.is_file()}
        self.budget = budget
        self.used = 0
        self.loaded = {}  # object path (casefolded) -> (png, size, receipt) or None, note

    def find(self, object_path):
        return self.files.get(remaster_name(object_path).casefold())

    def load(self, object_path, original_size):
        """Return ((png, [width, height], receipt) or None, note) for one source texture.

        ``note`` explains a skipped match; it is None when the remaster is used or none exists.
        """
        key = object_path.casefold()
        if key not in self.loaded:
            self.loaded[key] = self._load(object_path, original_size)
        return self.loaded[key]

    def _load(self, object_path, original_size):
        path = self.find(object_path)
        if path is None:
            return None, None
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("it is larger than 128 MB")
            png, size = rgba_png(raw)
        except (OSError, ValueError, ImportError) as error:
            return None, f"Remastered texture {path.name} was not used: {error}."
        width, height = original_size
        if size[0] < width or size[1] < height or size[0] * height != size[1] * width:
            return None, (f"Remastered texture {path.name} was not used: {size[0]}x{size[1]} does not keep the "
                          f"{width}x{height} original's shape.")
        if self.used + len(png) > self.budget:
            return None, f"Remastered texture {path.name} was not used: the intake texture budget is full."
        self.used += len(png)
        receipt = dict(name=path.name, sha256=sha256(raw).hexdigest())
        return (png, list(size), receipt), None


def rgba_png(raw):
    """Re-encode any PNG as the 8-bit RGBA PNG intakes store, returning (png, (width, height))."""
    from PIL import Image  # Pillow is a Map Tool requirement; imported here so tests need it only when used.

    with Image.open(BytesIO(raw)) as image:
        if image.format != "PNG":
            raise ValueError("it is not a PNG")
        width, height = image.size
        if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
            raise ValueError(f"its sides must be at most {MAX_SIDE} pixels")
        rgba = image.convert("RGBA")
        output = BytesIO()
        rgba.save(output, "PNG")
        if output.tell() > MAX_IMAGE_BYTES:  # Try the strongest lossless compression before giving up.
            output = BytesIO()
            rgba.save(output, "PNG", optimize=True)
    png = output.getvalue()
    if len(png) > MAX_IMAGE_BYTES:
        raise ValueError("it is larger than the 16 MB intake image limit as an RGBA PNG")
    return png, validate_png(png)
