"""Game-first workflow layout for the Map Tool (Tribes3-Modded/Tribes3MapTool#8).

Plain data, no Unreal or Tk imports, so it can be tested anywhere. The Unreal editor menu
(``editor.py``) and the conversion window (``conversion_window.py``) both read it:

* choose the source game first;
* each game lists its own tools as numbered steps, each naming its input and output;
* draft tools shared by every game sit in their own submenus, in workflow order.
"""

from source_profiles import ASCEND, NAMES, T1, T2, VENGEANCE

INTAKE_TYPES = "*.json *.zip"

# Source games in menu order: (key, profile, file types for the conversion window, steps).
# Each step is (title, what it needs, what it produces).
GAMES = (
    ("tribes1", T1, "*.vol *.mis",
     (("Pick source", "The mission .vol or .mis from the Tribes 1 install", "A source in the conversion window"),
      ("Read and review", "Terrain, interiors and shapes from the same install", "Object Review and a coverage report"),
      ("Textures", "Palette textures from the volume files", "Repaired texture batches"),
      ("Build draft", "The reviewed intake", "An Unreal draft (DraftT1…)"),
      ("Gameplay", "The draft", "Checked CTF markers and equipment"),
      ("Check and export", "The finished draft", "A host map bundle"))),
    ("tribes2", T2, "*.mis *.vl2",
     (("Pick source", "The mission .mis or the map .vl2", "A source in the conversion window"),
      ("Read and review", "DIF interiors, DTS shapes, sky and audio assets", "Object Review and a coverage report"),
      ("Textures", "Building, terrain and sky textures", "Repaired texture batches"),
      ("Build draft", "The reviewed intake", "An Unreal draft (DraftT2…)"),
      ("Interiors and shapes", "The draft and DIF/DTS files", "Imported interiors, baked DTS animation"),
      ("Gameplay", "The draft", "Checked CTF markers, equipment and force shields"),
      ("Check and export", "The finished draft", "A host map bundle"))),
    ("vengeance", VENGEANCE, "*.tvm *.pkg *.t3d",
     (("Pick source", "The map .tvm (or a .t3d export) and its .pkg companions", "A source in the conversion window"),
      ("Read and review", "Native terrain, flags and spawns in the import dialog", "Object Review and a coverage report"),
      ("Textures", "Package textures", "Repaired texture batches"),
      ("Build draft", "The reviewed intake, at the chosen scale", "An Unreal draft (DraftTV…)"),
      ("Gameplay", "The draft", "Checked CTF markers and equipment"),
      ("Check and export", "The finished draft", "A host map bundle"))),
    ("ascend", ASCEND, "*.fmap *.udk *.t3d",
     (("Pick source", "The gameplay level TrCTF-<Map>.fmap in the client's CookedPC\\Maps folder; "
                      "its _Ter and _Sound levels are found automatically",
       "A source in the conversion window"),
      ("Read and review", "Flags, spawns and native terrain in the import dialog, scale preset",
       "Object Review and a coverage report"),
      ("Textures", "Textures.tfc beside or above the map", "Repaired or remastered texture batches"),
      ("Sound pack", "The client's CookedPC folder (once per game) and the map's levels",
       "The TA game pack and this map's sound override (sound_packs.py)"),
      ("Build draft", "The reviewed intake", "An Unreal draft (DraftTA…)"),
      ("Gameplay", "The draft", "Checked CTF markers and equipment"),
      ("Check and export", "The finished draft", "A host map bundle"))),
)

# Every Unreal editor menu entry: key -> (label, operation, argument). Keys are stable; labels
# may change. ``editor.py`` adds the CTF marker entries itself.
ACTIONS = {
    "NewBlank": ("New Blank CTF Map", "blank", None),
    "CopyMap": ("Copy Damnation (2026)", "copy", None),
    "ImportT2": ("Import Map or Intake…", "import_t2", None),
    "RebuildRecorded": ("Rebuild Draft with Recorded Rules", "rebuild_t2", "recorded"),
    "RebuildLatest": ("Rebuild Draft with Latest Rules", "rebuild_t2", "latest"),
    "BatchRebuild": ("Batch Rebuilds…", "batch_rebuild", None),
    "Pipeline": ("Conversion Pipeline…", "report", "pipeline"),
    "GameProfiles": ("Game Profiles…", "report", "profiles"),
    "MapReleases": ("Map Releases…", "report", "releases"),
    "ObjectReview": ("Docked Object Review", "docked_review", None),
    "ImportInterior": ("Import Tribes 2 Interior…", "import_interior", None),
    "PreviewDTS": ("Preview Selected DTS Animation…", "dts_preview", "open"),
    "StopDTSPreview": ("Stop DTS Preview", "dts_preview", "stop"),
    "BakeDTS": ("Bake Selected DTS Animation…", "dts_bake", False),
    "RemoveBakeDTS": ("Remove Baked DTS Animation", "dts_bake", True),
    "ModelShowcase": ("Open Model Showcase", "model_showcase", "showcase"),
    "ContactSheets": ("Refresh Map Contact Sheets", "contact_sheets", None),
    "ReturnToDraft": ("Return from Showcase to Draft", "model_showcase", "draft"),
    "RegisterModels": ("Add Selected Models to Shared Library", "model_library", "add"),
    "BrowseModels": ("Open Shared Model Library", "model_library", "open"),
    "PlaceModels": ("Place Selected Library Models in Draft", "model_library", "place"),
    "LeaveModels": ("Return from Shared Library to Draft", "model_library", "return"),
    "MapAssets": ("Browse Structures and Props", "browse", "meshes"),
    "MapMaterials": ("Browse Grass, Rock and Forest Materials", "browse", "palette"),
    "MapSurfaces": ("Browse Existing Materials and Textures", "browse", "surfaces"),
    "ExportTextures": ("Export Selected Textures for Repair…", "texture_batch", "export"),
    "ReviewTextures": ("Review and Import Texture Batch…", "texture_batch", "review"),
    "ApplySky": ("Apply Selected Cubemap as Draft Sky", "apply_sky", None),
    "CaptureLighting": ("Capture Normal and Alarm Lighting…", "capture_lighting", None),
    "SaveLightingCamera": ("Add Lighting Camera from Viewport", "lighting_camera", "save"),
    "FrameLightingCamera": ("Frame Selected Lighting Camera", "lighting_camera", "frame"),
    "RenderLightingCameras": ("Render Saved Lighting Cameras…", "lighting_camera", "render"),
    "LinkLighting": ("Link Interior Lighting to Selected Generator", "interior_circuit", True),
    "UnlinkLighting": ("Clear Selected Interior Lighting Links", "interior_circuit", False),
    "Validate": ("Check CTF Markers", "validate", None),
    "ApproveEquipment": ("Approve Selected Equipment for Direct Host", "approve_equipment", True),
    "RemoveEquipmentApproval": ("Remove Selected Equipment Approval", "approve_equipment", False),
    "InventoryStation": ("Create Inventory Station Blueprint", "inventory_station", None),
    "LinkShields": ("Link Selected Shields to Generator", "shield_power", "link"),
    "UnlinkShields": ("Set Selected Shields to Independent Power", "shield_power", "unlink"),
    "SourceShieldCircuit": ("Restore Source Shield Circuit", "shield_power", "source"),
    "DropShields": ("Shield Power Loss · Drop", "shield_power", "drop"),
    "WeakenShields": ("Shield Power Loss · Weaken", "shield_power", "weaken"),
    "KeepShields": ("Shield Power Loss · Stay Up", "shield_power", "stay"),
    "Build": ("Build & Playtest", "build", "playtest"),
    "BuildForHost": ("Build for Direct Host…", "build", "host"),
}

# Editor menu, in workflow order: (submenu key, label, entries). An entry is an ACTIONS key, or
# (key, label) for a game-specific copy of a shared action (the same operation, a clearer name).
# "@markers" places the CTF marker entries that editor.py builds.
MENU = (
    ("Start", "0 · Start a draft", ("NewBlank", "CopyMap")),
    ("ConvertT1", "1 · Convert from Tribes 1",
     (("ImportT2", "1. Import Tribes 1 mission…"), ("RebuildRecorded", "2. Rebuild with recorded rules"),
      ("RebuildLatest", "3. Rebuild with latest rules"))),
    ("ConvertT2", "1 · Convert from Tribes 2",
     (("ImportT2", "1. Import Tribes 2 mission or .vl2…"), ("ImportInterior", "2. Import Tribes 2 interior (.dif)…"),
      ("RebuildRecorded", "3. Rebuild with recorded rules"), ("RebuildLatest", "4. Rebuild with latest rules"),
      "PreviewDTS", "StopDTSPreview", "BakeDTS", "RemoveBakeDTS")),
    ("ConvertTV", "1 · Convert from Tribes: Vengeance",
     (("ImportT2", "1. Import Vengeance map (.tvm)…"), ("RebuildRecorded", "2. Rebuild with recorded rules"),
      ("RebuildLatest", "3. Rebuild with latest rules"))),
    ("ConvertTA", "1 · Convert from Tribes: Ascend",
     (("ImportT2", "1. Import Ascend gameplay level (TrCTF-<Map>.fmap)…"),
      ("RebuildRecorded", "2. Rebuild with recorded rules"), ("RebuildLatest", "3. Rebuild with latest rules"))),
    ("Review", "2 · Review conversion",
     ("ObjectReview", "Pipeline", "GameProfiles", "BatchRebuild")),
    ("Art", "3 · Textures, models and sky",
     ("ExportTextures", "ReviewTextures", "ApplySky", "MapAssets", "MapMaterials", "MapSurfaces",
      "ModelShowcase", "ContactSheets", "ReturnToDraft", "RegisterModels", "BrowseModels", "PlaceModels",
      "LeaveModels")),
    ("Lighting", "4 · Lighting",
     ("CaptureLighting", "SaveLightingCamera", "FrameLightingCamera", "RenderLightingCameras", "LinkLighting",
      "UnlinkLighting")),
    ("Gameplay", "5 · Gameplay: markers, equipment, shields",
     ("@markers", "Validate", "ApproveEquipment", "RemoveEquipmentApproval", "InventoryStation")),
    ("ForceShields", "5a · Force shields",
     ("LinkShields", "UnlinkShields", "SourceShieldCircuit", "DropShields", "WeakenShields", "KeepShields")),
    ("Publish", "6 · Build and publish", ("Build", "BuildForHost", "MapReleases")),
)


def game_choices():
    """Labels for the conversion window's source game selector, any-game first."""
    return ("Any game (open anything)", *(NAMES[profile] for _, profile, _, _ in GAMES))


def game_by_label(label):
    for key, profile, types, steps in GAMES:
        if NAMES[profile] == label:
            return dict(key=key, profile=profile, name=label, filetypes=types, steps=steps)
    return None


def open_filetypes(label):
    """File dialog types for the selected game; intakes can always be reopened."""
    game = game_by_label(label)
    if game is None:
        return [("Mission, package or intake", "*.mis *.vl2 *.vol *.t3d *.tvm *.pkg *.fmap *.udk *.json *.zip")]
    return [(f"{game['name']} source", game["filetypes"]), ("Existing intake", INTAKE_TYPES)]


def steps_text(label):
    """Numbered steps for the selected game, each with what it needs and what it produces."""
    game = game_by_label(label)
    if game is None:
        return "Choose the game you are converting from to see its steps in order."
    return "\n".join(f"{number}. {title}: {needs} → {produces}"
                     for number, (title, needs, produces) in enumerate(game["steps"], 1))


def menu_entries():
    """Yield (submenu key, submenu label, entry key, label, operation, argument) in menu order."""
    for submenu, title, entries in MENU:
        for entry in entries:
            if entry == "@markers":
                yield submenu, title, "@markers", None, None, None
                continue
            key, label = entry if isinstance(entry, tuple) else (entry, None)
            default, operation, argument = ACTIONS[key]
            yield submenu, title, key, label or default, operation, argument
