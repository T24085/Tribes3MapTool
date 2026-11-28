"""Remaster a map's textures to 2x during intake, with Real-ESRGAN (#9).

This is the side-loaded pipeline first run by hand for Tartarus (``remaster_2x.py``), built into the
intake so every texture an Ascend map uses is remastered as it is read: base colours, glow maps and the
sky. Results are cached by source hash, so a re-import reuses them.

How each texture is remastered:

- ``ai``: Real-ESRGAN x4plus, then Lanczos down to 2x. The AI result's low-frequency colour is replaced
  by the source's (Real-ESRGAN darkens busy organic textures by up to 20 levels), keeping only its detail.
- ``lanczos``: a plain Lanczos 2x. Glow masks and cubemap faces, where the AI invents detail.
- ``copy``: editor-only thumbnails, unchanged.

Tiling textures are wrap-padded before upscaling so their seams stay seamless; skies, atlases, cubemap
faces, decals and billboards are edge-padded. Alpha is upscaled separately with Lanczos so masks stay clean.
"""

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tempfile

from model_textures import MAX_TEXTURE_BYTES
from remastered_textures import MAX_IMAGE_BYTES, ORIGINAL_RESERVE, remaster_name, rgba_png

# Bump when the method changes so cached results are rebuilt.
PIPELINE = "esrgan-x4plus-2x-tone6-v1"
EXE_NAME = "realesrgan-ncnn-vulkan.exe"
MODEL = "realesrgan-x4plus"
PAD = 16  # source pixels of context added on every side before upscaling
TONE_RADIUS = 6  # output pixels; colour below this scale comes from the source, detail above it from the AI
MAX_SOURCE_SIDE = 2048  # 2x of this is the intake's largest side
NON_TILING = ("cubemap", "skybox", "sky", "thumbnail", "decal", "atlas", "billboard", "backdrop")
LANCZOS_ONLY = ("cubemap",)
COPY_ONLY = ("thumbnailtexture",)
SETTINGS_NAME = "remaster.json"


def method(object_path, role):
    """How one texture is remastered. ``role`` is "base", "glow" or "sky"."""
    name = object_path.casefold()
    if any(key in name for key in COPY_ONLY):
        return "copy"
    if role == "sky":
        return "ai"  # Painted skies are the textures players see largest; the AI keeps them sharp.
    if role == "glow" or name.endswith("_emi") or any(key in name for key in LANCZOS_ONLY):
        return "lanczos"
    return "ai"


def pad_mode(object_path, role):
    name = object_path.casefold()
    return "edge" if role == "sky" or any(key in name for key in NON_TILING) else "wrap"


def validate_tool(value):
    """Check a Real-ESRGAN folder setting: an absolute folder holding the executable and its models."""
    if not isinstance(value, str) or not 0 < len(value) <= 1024 or not value.isprintable():
        raise ValueError("Choose the Real-ESRGAN folder (realesrgan-ncnn-vulkan.exe and models).")
    folder = Path(value)
    if not folder.is_absolute() or not (folder / EXE_NAME).is_file() or not (folder / "models").is_dir():
        raise ValueError("Choose the Real-ESRGAN folder (realesrgan-ncnn-vulkan.exe and models).")
    return folder


def app_folder():
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "Tribes3MapTool" if local else Path.home() / ".tribes3maptool"


def default_cache():
    return app_folder() / "RemasterCache" / PIPELINE


def remembered_tool():
    """The Real-ESRGAN folder chosen before, or None."""
    try:
        value = json.loads((app_folder() / SETTINGS_NAME).read_text(encoding="utf-8")).get("tool")
        return validate_tool(value)
    except (OSError, ValueError, AttributeError):
        return None


def remember_tool(folder):
    folder = validate_tool(str(folder))
    path = app_folder() / SETTINGS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tool": str(folder)}), encoding="utf-8")
    return folder


def padded(image, mode):
    """``image`` with PAD source pixels added on every side, wrapped (tiling) or edge-repeated."""
    from PIL import Image

    width, height = image.size
    out = Image.new(image.mode, (width + 2 * PAD, height + 2 * PAD))
    if mode == "wrap":
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.paste(image, (PAD + dx * width, PAD + dy * height))
        return out
    out.paste(image.crop((0, 0, 1, height)).resize((PAD, height)), (0, PAD))
    out.paste(image.crop((width - 1, 0, width, height)).resize((PAD, height)), (PAD + width, PAD))
    out.paste(image, (PAD, PAD))
    out.paste(out.crop((0, PAD, out.width, PAD + 1)).resize((out.width, PAD)), (0, 0))
    out.paste(out.crop((0, PAD + height - 1, out.width, PAD + height)).resize((out.width, PAD)),
              (0, PAD + height))
    return out


def lanczos2x(image):
    from PIL import Image

    return image.resize((image.width * 2, image.height * 2), Image.LANCZOS)


def unpad2x(image):
    return image.crop((PAD * 2, PAD * 2, image.width - PAD * 2, image.height - PAD * 2))


def tone_matched(ai, reference):
    """Keep the AI's fine detail, but take colour below TONE_RADIUS from the Lanczos reference."""
    from PIL import ImageChops, ImageFilter

    blur = ImageFilter.GaussianBlur(TONE_RADIUS)
    difference = ImageChops.subtract(reference.filter(blur), ai.filter(blur), scale=1.0, offset=128)
    return ImageChops.add(ai, difference, scale=1.0, offset=-128)


def run_esrgan(tool, image):
    """Real-ESRGAN x4 of an RGB image."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder, "in.png"), Path(folder, "out.png")
        image.save(source)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run([str(tool / EXE_NAME), "-i", str(source), "-o", str(target), "-n", MODEL,
                                 "-s", "4", "-m", str(tool / "models"), "-f", "png"],
                                capture_output=True, text=True, timeout=600, creationflags=flags)
        if result.returncode != 0 or not target.is_file():
            raise ValueError(f"Real-ESRGAN failed ({result.returncode}): {result.stderr.strip()[-300:]}")
        with Image.open(target) as up:
            up = up.convert("RGB")
    if up.size != (image.width * 4, image.height * 4):
        raise ValueError(f"Real-ESRGAN returned an unexpected {up.size[0]}x{up.size[1]} image.")
    return up


def remaster_png(png, object_path, role, tool):
    """Return the 2x remastered PNG of one source texture, and the method used."""
    from PIL import Image

    how = method(object_path, role)
    with Image.open(BytesIO(png)) as source:
        source.load()
        image = source.convert("RGBA")
    if how == "copy":
        return png, how
    mode = pad_mode(object_path, role)
    alpha = image.getchannel("A")
    rgb = image.convert("RGB")
    reference = lanczos2x(padded(rgb, mode))
    if how == "ai":
        if tool is None:
            raise ValueError("Real-ESRGAN is not set up.")
        ai = run_esrgan(tool, padded(rgb, mode))
        ai = ai.resize(reference.size, Image.LANCZOS)
        result = unpad2x(tone_matched(ai, reference))
    else:
        result = unpad2x(reference)
    result = result.convert("RGBA")
    if alpha.getextrema()[0] < 255:
        result.putalpha(unpad2x(lanczos2x(padded(alpha, mode))))
    output = BytesIO()
    result.save(output, "PNG")
    return output.getvalue(), how


class AutoRemaster:
    """Remaster each texture an intake reads, on first use, with results cached by source hash.

    Has the same ``load`` contract as ``RemasteredTextures``; a hand-made remaster in ``manual`` (a
    ``RemasteredTextures``) wins over the automatic one.
    """

    def __init__(self, tool, cache=None, manual=None, budget=MAX_TEXTURE_BYTES - ORIGINAL_RESERVE, remaster=None,
                 redo=False):
        self.tool = validate_tool(str(tool)) if tool is not None else None
        self.cache = Path(cache) if cache else default_cache()
        self.manual = manual
        self.budget = budget
        self.used = 0
        self.loaded = {}
        self.methods = {}
        self.remaster = remaster or remaster_png  # Replaced in tests.
        # Redo remaster: rebuild this import's textures once, replacing their cached results.
        self.redo = redo

    def load(self, object_path, original_size, png=None, role="base"):
        if self.manual is not None:
            found, note = self.manual.load(object_path, original_size, png, role)
            if found or note:
                return found, note
        if png is None:
            return None, None
        digest = sha256(png).hexdigest()
        key = (object_path.casefold(), role, digest)
        if key not in self.loaded:
            self.loaded[key] = self._load(object_path, original_size, png, role, digest)
        return self.loaded[key]

    def _load(self, object_path, original_size, png, role, digest):
        width, height = original_size
        if max(width, height) > MAX_SOURCE_SIDE:
            return None, None  # Already as large as an intake texture can be.
        name = remaster_name(object_path)
        path = self.cache / f"{name[:-4]}.{role}.{digest[:16]}.png"
        try:
            if path.is_file() and not self.redo:
                raw = path.read_bytes()
                how = "cached"
            else:
                raw, how = self.remaster(png, object_path, role, self.tool)
                self.cache.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix(".part")
                partial.write_bytes(raw)
                partial.replace(path)
            data, size = rgba_png(raw)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return None, f"Texture not remastered: {error}"
        if list(size) == [width, height]:
            return None, None  # Copied unchanged (editor thumbnails): the original stays.
        if list(size) != [width * 2, height * 2]:
            return None, f"Texture not remastered: the result is {size[0]}x{size[1]}."
        if len(data) > MAX_IMAGE_BYTES or self.used + len(data) > self.budget:
            return None, "Texture not remastered: the intake texture budget is full."
        self.used += len(data)
        self.methods[object_path] = how
        return (data, list(size), dict(name=path.name, sha256=sha256(raw).hexdigest())), None
