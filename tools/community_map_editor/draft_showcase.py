"""Build a separate, editable model showcase for an imported CTF draft."""

from pathlib import PurePosixPath
import re

from mesh_mappings import material_reference, source_reference


ROOT = "/Game/MapEditorShowcases"
from source_profiles import IMPORT_DRAFT as DRAFT
CATEGORIES = ("Buildings", "Props", "Objectives", "Equipment")
COLUMNS = 3
SPACING = 1200
GALLERY_OFFSET = 2050
PLINTH_HEIGHT = 55
MODEL_SIZE = 800


def package(draft):
    if not re.fullmatch(DRAFT, draft):
        raise ValueError("Open a draft imported from a mission.")
    return f"{ROOT}/{draft}/Showcase_{draft}"


def exhibits(rows, planned, equipment, overrides=None):
    result = {category: {} for category in CATEGORIES}
    names = {row["id"]: row["name"] for row in rows}
    overrides = overrides or {}
    for row, path, _, _, scale in planned:
        reference = source_reference(row)
        kind, filename = reference.split(":", 1)
        category = "Buildings" if kind == "interiorinstance" else "Props"
        title = PurePosixPath(filename).stem
        look = material_reference(row, overrides)
        key = (reference, tuple((slot, mat.get_path_name()) for slot, mat in sorted(overrides.get(look, {}).items())))
        if look != reference and overrides[look] != overrides.get(reference, {}):
            title = row["name"] or title
        result[category].setdefault(key, (title, look, path, scale, 0))
    for row in rows:
        if row["status"] != "marker":
            continue
        kind, team = row["kind"], row["team"]
        key = (kind, team)
        name = row["name"] or kind.capitalize()
        result["Objectives"].setdefault(key, (name, f"{kind.capitalize()} marker", None, (1, 1, 1), team))
    for row in equipment["equipment"]:
        key = (row["role"], row["datablock"], row["team"])
        name = names[row["source_id"]] or row["datablock"]
        role = row["role"].replace("_", " ").capitalize()
        result["Equipment"].setdefault(key, (name, f"{role} marker", None, (1, 1, 1), row["team"]))
    return {category: sorted(entries.values(), key=lambda entry: (entry[0].casefold(), entry[1], entry[4]))
            for category, entries in result.items() if entries}


def spawn(editor, cls, label, folder, location, rotation=None):
    import unreal

    actor = editor.ACTORS.spawn_actor_from_class(
        cls, unreal.Vector(*location), rotation or unreal.Rotator()
    )
    if not actor:
        raise RuntimeError(f"Could not place {label} in the model showcase.")
    actor.set_actor_label(label)
    actor.set_folder_path(folder)
    return actor


def label(editor, text, category, x, y, z, size, *, team=0):
    import unreal

    actor = spawn(editor, unreal.TextRenderActor, text.replace("\n", " · "), category,
                  (x, y, z), unreal.Rotator(yaw=180))
    component = actor.get_component_by_class(unreal.TextRenderComponent)
    color = {1: (102, 189, 255), 2: (255, 140, 122)}.get(team, (244, 240, 223))
    component.set_text(text.replace("<", "‹").replace(">", "›").replace("\n", "<br>"))
    component.set_world_size(size)
    component.set_text_render_color(unreal.Color(*color, 255))


def display_material(editor, name, color):
    import unreal

    folder = ROOT + "/Materials"
    path = folder + "/M_" + name
    if editor.ASSETS.does_asset_exist(path):
        return editor.ASSETS.load_asset(path)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        "M_" + name, folder, unreal.Material, unreal.MaterialFactoryNew()
    )
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    lib = unreal.MaterialEditingLibrary
    swatch = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -260, 0)
    swatch.set_editor_property("constant", unreal.LinearColor(*color, 1))
    if not lib.connect_material_property(swatch, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise RuntimeError(f"Could not connect the {name} showcase material.")
    lib.recompile_material(material)
    if not editor.ASSETS.save_loaded_asset(material):
        raise RuntimeError(f"Could not save the {name} showcase material.")
    return material


def place_exhibit(editor, category, exhibit, meshes, overrides, shapes, palette, x, y):
    import unreal

    title, detail, path, original_scale, team = exhibit
    plinth = spawn(editor, unreal.StaticMeshActor, "Display base", category, (x, y, PLINTH_HEIGHT / 2))
    plinth.static_mesh_component.set_static_mesh(shapes["Cube"])
    plinth.static_mesh_component.set_material(0, palette["Display"])
    plinth.set_actor_scale3d(unreal.Vector(9.2, 9.2, PLINTH_HEIGHT / 100))
    actor = spawn(editor, unreal.StaticMeshActor, title if path else f"{title} · {detail}", category, (x, y, 0))
    component = actor.static_mesh_component
    if path:
        component.set_static_mesh(meshes[path])
    else:
        shape = {"Flag": "Cone", "Spawn": "Sphere"}.get(detail.split()[0], "Cylinder")
        component.set_static_mesh(shapes[shape])
        component.set_material(0, palette[{1: "Blue", 2: "Red"}.get(team, "Neutral")])
    if path:
        actor.set_actor_scale3d(unreal.Vector(*original_scale))
        _, extent = actor.get_actor_bounds(False)
        scale = MODEL_SIZE / max(1, 2 * extent.x, 2 * extent.y, 2 * extent.z)
        actor.set_actor_scale3d(unreal.Vector(*(value * scale for value in original_scale)))
        for index, material in overrides.get(detail, {}).items():
            component.set_material(index, material)
        kind = "T3ShowcaseModel"
    else:
        actor.set_actor_scale3d(unreal.Vector(1.2, 1.2, 2.2) if team else unreal.Vector(1.5, 1.5, 1.5))
        kind = "T3ShowcaseMarker"
    origin, extent = actor.get_actor_bounds(False)
    actor.set_actor_location(
        unreal.Vector(2 * x - origin.x, 2 * y - origin.y, extent.z - origin.z + PLINTH_HEIGHT + 4), False, False
    )
    actor.set_editor_property("tags", [kind, "T3ShowcaseExhibit"])
    team_name = {1: "Blue • ", 2: "Red • "}.get(team, "")
    subtitle = ("Imported materials" if overrides.get(detail) else "Model") if path else team_name + detail
    title = title.strip() or "Unnamed object"
    if len(title) > 29:
        title = title[:28] + "…"
    label(editor, f"{title}\n{subtitle}", category, x - 450, y - 360, 200, 52, team=team)
    return actor


def populate_stage(editor, groups, meshes, overrides):
    import unreal

    shapes = {name: editor.ASSETS.load_asset("/Engine/BasicShapes/" + name)
              for name in ("Cube", "Cone", "Cylinder", "Sphere")}
    if any(shape is None for shape in shapes.values()):
        raise RuntimeError("The Unreal showcase shapes are unavailable.")
    palette = {name: display_material(editor, name, color) for name, color in (
        ("Floor", (.075, .10, .14)), ("Display", (.22, .29, .37)),
        ("Blue", (.03, .39, .78)), ("Red", (.74, .16, .10)), ("Neutral", (.56, .42, .23)),
    )}
    galleries = list((groups or {"No available models": []}).items())
    x = 0
    displays = []
    for start in range(0, len(galleries), 2):
        band = galleries[start:start + 2]
        for column, (category, entries) in enumerate(band):
            center = (2 * column - 1) * GALLERY_OFFSET if len(band) == 2 else 0
            first = center - (max(1, min(COLUMNS, len(entries))) - 1) * SPACING / 2
            label(editor, category, category, x - 730, first - 360, 590, 135)
            for index, item in enumerate(entries):
                row, cell = divmod(index, COLUMNS)
                row_width = min(COLUMNS, len(entries) - row * COLUMNS)
                y = center + (cell - (row_width - 1) / 2) * SPACING
                display = place_exhibit(editor, category, item, meshes, overrides, shapes, palette,
                                        x + row * SPACING, y)
                displays.append((item, display))
        band_rows = max((len(entries) + COLUMNS - 1) // COLUMNS for _, entries in band)
        x += (max(1, band_rows) + 1) * SPACING
    floor = spawn(editor, unreal.StaticMeshActor, "Showcase floor", "Stage", ((x - 2200) / 2, 0, -30))
    floor.static_mesh_component.set_static_mesh(shapes["Cube"])
    floor.static_mesh_component.set_material(0, palette["Floor"])
    floor.set_actor_scale3d(unreal.Vector((x + 2200) / 100, 82, .6))
    for name, pitch, yaw, intensity in (
            ("Key light", -55, 22, 12), ("Fill light", -60, 156, 6), ("Back light", -54, 262, 5)):
        light = spawn(editor, unreal.DirectionalLight, name, "Stage", (0, 0, 1800),
                      unreal.Rotator(pitch=pitch, yaw=yaw))
        light.light_component.set_editor_property("intensity", intensity)
        light.light_component.set_editor_property("cast_shadows", False)
    editor.EDITOR.set_level_viewport_camera_info(unreal.Vector(-4200, 0, 3900),
                                                  unreal.Rotator(pitch=-42))
    return displays


def build(editor, draft, draft_level, rows, planned, equipment, meshes, overrides):
    import unreal

    groups = exhibits(rows, planned, equipment, overrides)
    target = package(draft)
    if editor.ASSETS.does_asset_exist(target):
        raise ValueError("This draft already has a model showcase.")
    if not editor.LEVELS.new_level(target):
        raise RuntimeError("Could not create the model showcase.")
    try:
        populate_stage(editor, groups, meshes, overrides)
        if not editor.LEVELS.save_current_level():
            raise RuntimeError("Could not save the model showcase.")
        unreal.log(f"T3_MODEL_SHOWCASE_READY|{draft}|exhibits={sum(map(len, groups.values()))}")
    finally:
        if not editor.LEVELS.load_level(draft_level):
            raise RuntimeError("Could not reopen the saved CTF draft.")
    return target


def open_companion(editor, requested):
    own = editor.world().get_path_name().split(".")[0]
    if requested == "draft":
        match = re.fullmatch(ROOT + rf"/({DRAFT})/Showcase_\1", own)
        if not match:
            raise ValueError("Open an imported draft's model showcase first.")
        draft = match[1]
        target = f"{editor.LEVEL_ROOT}/{draft}/Arena_{draft}_P"
    else:
        target = package(editor.draft_id())
    if not editor.ASSETS.does_asset_exist(target):
        raise ValueError("This imported draft has no saved model showcase.")
    if editor.SAVING.save_dirty_packages_with_dialog(True, True) and not editor.LEVELS.load_level(target):
        raise RuntimeError("Could not open the companion level.")
