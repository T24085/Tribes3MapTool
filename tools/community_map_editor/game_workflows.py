"""Game-first workflow layout for the Map Tool (Tribes3-Modded/Tribes3MapTool#8).

Plain data, no Unreal or Tk imports, so it can be tested anywhere. The Unreal editor menu
(``editor.py``) and the conversion window (``conversion_window.py``) both read it:

* choose the source game first;
* each game lists its own tools as numbered steps, each naming its input and output;
* draft tools shared by every game sit in their own submenus, in workflow order.
"""

from source_profiles import ASCEND, NAMES, T1, T2, VENGEANCE

INTAKE_TYPES = "*.json *.zip"

# Each step is (stage, title, instruction). Instructions name only things the author can see:
# button labels in quotes and menu paths, so the window title "Step N of M · <title>" and the
# numbered list in the conversion window always agree. Stages tie a window to its step:
#   open       conversion window, before a source is opened
#   inventory  "Native map package inventory" window (Unreal packages)
#   settings   "Native map import settings" window (Unreal packages)
#   review     conversion window, after a source is opened
#   build, ... steps done in the Unreal editor
UNREAL = "Unreal editor: TRIBES Map Editor"
TEXTURES = ("textures", "Touch up textures (optional)",
            f"{UNREAL} → 3 · Textures, models and sky → \"Export Selected Textures for Repair…\", "
            "then \"Review and Import Texture Batch…\"")
BUILD = ("build", "Build the draft", "Unreal builds the draft and opens it; its progress bar names each stage")
GAMEPLAY = ("gameplay", "Check gameplay",
            f"{UNREAL} → 5 · Gameplay: markers, equipment, shields → \"Check CTF Markers\"")
EXPORT = ("export", "Build and publish", f"{UNREAL} → 6 · Build and publish → \"Build for Direct Host…\"")
REVIEW = ("review", "Review the conversion",
          "Check the object table (tick \"Needs review only\"); optionally \"Boundaries…\" and "
          "\"Terrain layers…\"; then click \"Create Unreal draft\"")


def package_steps(source):
    """Steps for Unreal package sources (Vengeance and Ascend), which open two extra windows."""
    return (("open", "Open the map", f"Click \"Open source or intake…\" and choose {source}"),
            ("inventory", "Check the package", "In the package inventory window, click \"Import as draft…\""),
            ("settings", "Choose import settings",
             "Pick each team's \"Flag or stand\" and spawns and a \"Scale preset\", then click \"Create intake\""))


# Source games in menu order: (key, profile, file types for the conversion window, steps).
GAMES = (
    ("tribes1", T1, "*.vol *.mis",
     (("open", "Open the mission",
       "Click \"Open source or intake…\", choose the mission .vol or .mis, and pick the mission in the drop-down "
       "beside \"Source library…\" if there are several"),
      ("review", "Review the conversion",
       "Add \"Source artwork…\" and \"Interiors and props…\" from the same install, check the object table, "
       "then click \"Create Unreal draft\""),
      BUILD, TEXTURES, GAMEPLAY, EXPORT)),
    ("tribes2", T2, "*.mis *.vl2",
     (("open", "Open the mission",
       "Click \"Open source or intake…\", choose the .mis or .vl2, and pick the mission in the drop-down beside "
       "\"Source library…\""),
      ("review", "Review the conversion",
       "Add \"DIF assets…\", \"DTS assets…\", \"Sky assets…\" and \"Audio assets…\" if asked, check the "
       "object table, optionally \"Boundaries…\", then click \"Create Unreal draft\""),
      BUILD,
      ("interiors", "Interiors and shapes",
       f"{UNREAL} → 1 · Convert from Tribes 2 → \"Step 4: Import Tribes 2 interior (.dif)…\" and the DTS entries"),
      TEXTURES, GAMEPLAY, EXPORT)),
    ("vengeance", VENGEANCE, "*.tvm *.pkg *.t3d",
     (*package_steps("the map .tvm (keep its .pkg companions beside it)"), REVIEW, BUILD, TEXTURES, GAMEPLAY,
      EXPORT)),
    ("ascend", ASCEND, "*.fmap *.udk *.t3d",
     (*package_steps("the gameplay level TrCTF-<Map>.fmap in the client's CookedPC\\Maps\\<Map> folder (its _Ter "
                     "and _Sound levels are added automatically)")[:2],
      ("settings", "Choose import settings",
       "Pick each team's \"Flag or stand\" and spawns and a \"Scale preset\"; keep \"Remaster all textures 2x (sky, base colours, glow)\" "
       "ticked (cached, so it runs once per map; \"Redo remaster\" rebuilds), then click \"Create intake\""),
      REVIEW, BUILD,
      ("sound", "Sound pack (optional)",
       "No button yet: run sound_packs.py pack once per game and sound_packs.py map for this map (see the guide)"),
      TEXTURES, GAMEPLAY, EXPORT)),
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
# (key, label) for a game-specific copy of a shared action (the same operation, a clearer name),
# or (key, label, game key) when the action should start with that game already chosen.
# "@markers" places the CTF marker entries that editor.py builds.
MENU = (
    ("Start", "0 · Start a draft", ("NewBlank", "CopyMap")),
    # Each game's first entry opens the conversion window, whose title shows the step on screen.
    ("ConvertT1", "1 · Convert from Tribes 1",
     (("ImportT2", "Steps 1–2: Open the conversion window…", "tribes1"), "RebuildRecorded", "RebuildLatest")),
    ("ConvertT2", "1 · Convert from Tribes 2",
     (("ImportT2", "Steps 1–2: Open the conversion window…", "tribes2"),
      ("ImportInterior", "Step 4: Import Tribes 2 interior (.dif)…"),
      "RebuildRecorded", "RebuildLatest", "PreviewDTS", "StopDTSPreview", "BakeDTS", "RemoveBakeDTS")),
    ("ConvertTV", "1 · Convert from Tribes: Vengeance",
     (("ImportT2", "Steps 1–4: Open the conversion window…", "vengeance"), "RebuildRecorded", "RebuildLatest")),
    ("ConvertTA", "1 · Convert from Tribes: Ascend",
     (("ImportT2", "Steps 1–4: Open the conversion window…", "ascend"), "RebuildRecorded", "RebuildLatest")),
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


def game_label(key):
    """The conversion window label for a game key such as "ascend", or None."""
    for game_key, profile, _, _ in GAMES:
        if game_key == key:
            return NAMES[profile]
    return None


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
    """Numbered steps for the selected game, each naming the button or menu to use."""
    game = game_by_label(label)
    if game is None:
        return "Choose the game you are converting from to see its steps in order."
    return "\n".join(f"{number}. {title}: {instruction}"
                     for number, (_, title, instruction) in enumerate(game["steps"], 1))


# The game chosen in the conversion window. Its child windows read it to number their titles.
current_game = None


def select_game(label):
    global current_game
    current_game = label


def window_title(stage, default, label=None):
    """"Step N of M · <title>" for a window that is part of the chosen game's steps, else ``default``."""
    game = game_by_label(label if label is not None else current_game)
    if game is None:
        return default
    for number, (key, title, _) in enumerate(game["steps"], 1):
        if key == stage:
            return f"Step {number} of {len(game['steps'])} · {title} · {game['name']}"
    return default


def menu_entries():
    """Yield (submenu key, submenu label, entry key, label, operation, argument) in menu order."""
    for submenu, title, entries in MENU:
        for entry in entries:
            if entry == "@markers":
                yield submenu, title, "@markers", None, None, None
                continue
            key, label, game = (*entry, None)[:3] if isinstance(entry, tuple) else (entry, None, None)
            default, operation, argument = ACTIONS[key]
            yield submenu, title, key, label or default, operation, game or argument
