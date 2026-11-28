"""TRIBES 3 adapter for the MILF hazard component (T3BackEndServer#150, docs/MILF_HAZARD.md).

Each zone becomes the lava kit the Tribes 2 2026 maps use (read from the cooked Firestorm map):

- a ``ValEffectVolume`` (original game class, placed through the CommunityNativeWorldSettings
  authoring mirror) that applies ``GE_Damage_Generic`` with ``DamageSource.Lava`` and a
  set-by-caller ``Data.Damage`` magnitude every ``PainInterval`` seconds;
- an under-lava ``PostProcessVolume`` (Firestorm: priority 10, blend 50 cm, tint (1, 0.42, 0.1),
  bloom 4, vignette 0.8) below the surface;
- a glowing surface at ``surface_m`` and, for the ``ascend-lava`` look, the source glow spheres.

``plan`` is pure (MILF metres to Unreal centimetres, Y flipped) so it can be tested outside the
editor; ``apply`` places the actors.
"""

import math

EFFECT_VOLUME = "/Script/Valhalla.ValEffectVolume"
DAMAGE_EFFECT = "/Game/Abilities/Effects/GE_Damage_Generic"  # Original game asset; never exported.
DAMAGE_EFFECT_CLASS = DAMAGE_EFFECT + ".GE_Damage_Generic_C"
BRUSH_HALF_CM = 100.0  # A new volume's default cube brush spans 200 cm.
KILL_DAMAGE_PER_SEC = 5000.0  # Two ticks a second remove more health than any armour has.
PAIN_INTERVAL_S = 0.5
CONTACT_CM = 150.0  # Firestorm lifts the damage volume's top this far above the lava surface.
WALL_OVERLAP_CM = 100.0  # Lava raised to a shaft's first wall covers its bottom edge by this much.
BODY_GAP_CM = 10.0  # The lava body's top sits this far under the surface plane, out of sight from above.
UNDER_LAVA = dict(priority=10.0, blend_radius=50.0, tint=(1.0, 0.42, 0.1), bloom=4.0, vignette=0.8)
HAZARD_TAG = "T3Hazard"
LOOK_DEFAULT = {"lava": "t3-lava", "liquid": "t3-lava", "void": "none"}
# Colours as they should be SEEN, after the map's exposure and grade: bright orange molten cracks,
# near-black crust and a softer orange glow, all below full brightness so the tonemapper keeps their
# hue (over-bright lava turned white, and Katabatic's cold grade pulled it pink). ``emissive``
# divides by the map's exposure and grade gain to reach them. A source glow colour only nudges hue.
MOLTEN_SEEN = (0.9, 0.28, 0.02)
CRUST_SEEN = (0.03, 0.005, 0.0)
GLOW_SEEN = (0.35, 0.07, 0.0)
NO_VIEW_SCALE = 10.0  # Emissive scale for a map with no fixed exposure (one UE3 brightness unit).


def emissive(seen, view=None):
    """Emissive that displays as ``seen`` under a map's fixed exposure and per-channel grade gain."""
    exposure = 2 ** view["exposure"]["bias"] if view and view.get("exposure") else 1 / NO_VIEW_SCALE
    gain = (view or {}).get("grade", {}).get("gain", (1.0, 1.0, 1.0)) if view and view.get("grade") else (1.0, 1.0, 1.0)
    return [value / (exposure * max(channel, 0.05)) for value, channel in zip(seen, gain)]
GLOW_LOOKS = {"ascend-lava"}


def unreal_point(point_m):
    x, y, z = point_m
    return [x * 100.0, -y * 100.0, z * 100.0]


def footprint_cm(shape):
    """Axis-aligned XY bounds of a zone shape in Unreal centimetres (Y flipped)."""
    if shape["type"] == "prism":
        xs = [x * 100.0 for x, _ in shape["outline_m"]]
        ys = [-y * 100.0 for _, y in shape["outline_m"]]
        return min(xs), max(xs), min(ys), max(ys)
    x, y, _ = unreal_point(shape["center_m"])
    if shape["type"] == "sphere":
        r = shape["radius_m"] * 100.0
        return x - r, x + r, y - r, y + r
    ex, ey = shape["extent_m"][0] * 100.0, shape["extent_m"][1] * 100.0
    turn = math.radians(shape.get("rotation_deg", 0))
    hx = abs(ex * math.cos(turn)) + abs(ey * math.sin(turn))
    hy = abs(ex * math.sin(turn)) + abs(ey * math.cos(turn))
    return x - hx, x + hx, y - hy, y + hy


def heights_cm(shape):
    if shape["type"] == "prism":
        return shape["floor_m"] * 100.0, shape["top_m"] * 100.0
    z = shape["center_m"][2] * 100.0
    half = (shape["radius_m"] if shape["type"] == "sphere" else shape["extent_m"][2]) * 100.0
    return z - half, z + half


def box(x0, x1, y0, y1, z0, z1):
    return dict(center=[(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2],
                half=[(x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2])


WALL_COLUMN_CM = 100.0  # Meshes whose centres share a column this wide stack into one wall.


def raised_surface(surface_cm, walls, top_cm):
    """Lift lava until every wall column around it is closed, so no open shaft shows.

    ``walls`` holds (x, y, bottom) in cm for meshes standing in the zone's footprint. Each column's
    lowest bottom is where that part of the wall starts; the lava rises to the highest of those that
    lies between the surface and the zone's top. Ascend hid unbuilt pit bottoms in dense glow fog:
    Katabatic's side walls start at 37.6 m but its corner pieces only at 47.6 m, 21 m above the glow.
    Meshes starting above the zone (the catwalk) don't count.
    """
    columns = {}
    for x, y, bottom in walls:
        key = (round(x / WALL_COLUMN_CM), round(y / WALL_COLUMN_CM))
        columns[key] = min(bottom, columns.get(key, bottom))
    starts = [bottom for bottom in columns.values() if surface_cm < bottom < top_cm]
    return max(starts) + WALL_OVERLAP_CM if starts else surface_cm


def plan(component, wall_bottoms=None):
    """Return one placement plan per zone, in Unreal centimetres.

    ``wall_bottoms`` maps a zone id to the bottoms (cm) of static meshes standing in its footprint.
    """
    plans = []
    for zone in component.get("zones", []):
        x0, x1, y0, y1 = footprint_cm(zone["shape"])
        floor, top = heights_cm(zone["shape"])
        surface = zone.get("surface_m")
        surface_cm = surface * 100.0 if surface is not None else None
        if surface_cm is not None:
            surface_cm = raised_surface(surface_cm, (wall_bottoms or {}).get(zone["id"], ()), top)
        damage = zone["damage"]
        per_second = KILL_DAMAGE_PER_SEC if damage["mode"] == "kill" else float(damage["per_second"])
        look = zone.get("look", {})
        preset = look.get("preset", LOOK_DEFAULT[zone["kind"]])
        if surface_cm is not None and preset != "none":
            # Visible lava kills on contact, as on Firestorm: the damage volume ends just above the
            # surface, not at a source kill slab far overhead (Katabatic's is 40 m above its glow).
            top = surface_cm + CONTACT_CM
        item = dict(id=zone["id"], kind=zone["kind"], preset=preset, cause=damage["cause"],
                    volume=box(x0, x1, y0, y1, floor, top), damage_per_sec=per_second, pain_interval=PAIN_INTERVAL_S)
        if surface_cm is not None and preset != "none":
            item["under"] = box(x0, x1, y0, y1, floor, surface_cm)
            item["surface"] = dict(center=[(x0 + x1) / 2, (y0 + y1) / 2, surface_cm], half=[(x1 - x0) / 2, (y1 - y0) / 2])
            # Solid lava below the surface: a camera that sinks in (the death camera) sees glowing lava
            # all around instead of the unbuilt pit below the walls. Ascend's dense glow fog did this.
            item["body"] = box(x0, x1, y0, y1, floor, surface_cm - BODY_GAP_CM)
            item["tint"] = list(look.get("tint", (1.0, 1.0, 1.0)))
        glow = look.get("glow")
        if glow and preset in GLOW_LOOKS:
            item["glow"] = [dict(center=unreal_point(sphere["center_m"]), radius=sphere["radius_m"] * 100.0)
                            for sphere in glow.get("spheres", [])]
            item["glow_color"] = list(glow.get("color", (1.0, 0.45, 0.1)))
        plans.append(item)
    return plans


# ----- Editor placement (runs inside Unreal) -----

def tag_names(item, *extra):
    import unreal

    return [unreal.Name(HAZARD_TAG), unreal.Name(f"{HAZARD_TAG}_{item['id']}"), *(unreal.Name(name) for name in extra)]


def gameplay_tag(name):
    import unreal

    tag = unreal.GameplayTag()
    tag.import_text(f'(TagName="{name}")')
    if tag.export_text() != f'(TagName="{name}")':
        raise ValueError(f"The gameplay tag {name} is not registered. Reinstall the native authoring mirror.")
    return tag


def damage_effect_class():
    """The original GE_Damage_Generic, or an editor-only stand-in at its path (never exported)."""
    import unreal

    loaded = unreal.load_class(None, DAMAGE_EFFECT_CLASS)
    if loaded:
        return loaded
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", unreal.Object)
    folder, name = DAMAGE_EFFECT.rsplit("/", 1)
    blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Blueprint, factory)
    if not blueprint or not unreal.EditorAssetLibrary.save_loaded_asset(blueprint):
        raise ValueError("Could not create the editor stand-in for GE_Damage_Generic.")
    loaded = unreal.load_class(None, DAMAGE_EFFECT_CLASS)
    if not loaded:
        raise ValueError("The GE_Damage_Generic stand-in has no generated class.")
    return loaded


def place_box_volume(actor, spec):
    import unreal

    actor.set_actor_location(unreal.Vector(*spec["center"]), False, False)
    actor.set_actor_scale3d(unreal.Vector(*(max(half, 1.0) / BRUSH_HALF_CM for half in spec["half"])))


def lava_material(folder, name, tint, view=None, two_sided=False):
    """A procedural molten surface: slow drifting noise between dark crust and glowing orange."""
    import unreal

    path = f"{folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    lib = unreal.MaterialEditingLibrary
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", two_sided)
    world = lib.create_material_expression(material, unreal.MaterialExpressionWorldPosition, -1100, 0)
    time = lib.create_material_expression(material, unreal.MaterialExpressionTime, -1100, 200)
    drift = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -900, 200)
    drift.set_editor_property("const_b", 40.0)
    moved = lib.create_material_expression(material, unreal.MaterialExpressionAdd, -720, 0)
    noise = lib.create_material_expression(material, unreal.MaterialExpressionNoise, -520, 0)
    noise.set_editor_property("scale", 0.0012)
    noise.set_editor_property("levels", 4)
    noise.set_editor_property("output_min", 0.0)
    noise.set_editor_property("output_max", 1.0)
    sharpen = lib.create_material_expression(material, unreal.MaterialExpressionPower, -320, 0)
    sharpen.set_editor_property("const_exponent", 2.5)
    crust = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -320, 160)
    crust.set_editor_property("constant", unreal.LinearColor(
        *(value * scale for value, scale in zip(emissive(CRUST_SEEN, view), tint)), 1))
    molten = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -320, 300)
    molten.set_editor_property("constant", unreal.LinearColor(
        *(value * scale for value, scale in zip(emissive(MOLTEN_SEEN, view), tint)), 1))
    blend = lib.create_material_expression(material, unreal.MaterialExpressionLinearInterpolate, -120, 120)
    if not all(lib.connect_material_expressions(*link) for link in (
        (time, "", drift, "A"), (world, "", moved, "A"), (drift, "", moved, "B"), (moved, "", noise, "Position"),
        (noise, "", sharpen, "Base"), (crust, "", blend, "A"), (molten, "", blend, "B"), (sharpen, "", blend, "Alpha"),
    )) or not lib.connect_material_property(blend, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise ValueError("Could not build the lava surface material.")
    lib.recompile_material(material)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise ValueError("Could not save the lava surface material.")
    return material


def glow_material(folder, name, color, view=None):
    """Additive soft glow, brightest through the sphere's middle: the Ascend fog-volume look."""
    import unreal

    path = f"{folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    lib = unreal.MaterialEditingLibrary
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", False)
    fresnel = lib.create_material_expression(material, unreal.MaterialExpressionFresnel, -700, 0)
    fresnel.set_editor_property("exponent", 1.5)
    inverse = lib.create_material_expression(material, unreal.MaterialExpressionOneMinus, -520, 0)
    soft = lib.create_material_expression(material, unreal.MaterialExpressionPower, -360, 0)
    soft.set_editor_property("const_exponent", 2.0)
    tint = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -360, 160)
    seen = [lava * (0.75 + 0.25 * source) for lava, source in zip(GLOW_SEEN, color)]
    tint.set_editor_property("constant", unreal.LinearColor(*emissive(seen, view), 1))
    glow = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -160, 60)
    if not all(lib.connect_material_expressions(*link) for link in (
        (fresnel, "", inverse, ""), (inverse, "", soft, "Base"), (soft, "", glow, "A"), (tint, "", glow, "B"),
    )) or not lib.connect_material_property(glow, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise ValueError("Could not build the lava glow material.")
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise ValueError("Could not save the lava glow material.")
    return material


def unit_mesh(folder, name, sphere):
    """A 100 cm unit plane or sphere, scaled per zone."""
    import unreal

    path = f"{folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)
    mesh = unreal.DynamicMesh()
    options = unreal.GeometryScriptPrimitiveOptions()
    if sphere == "box":
        unreal.GeometryScript_Primitives.append_box(mesh, options, unreal.Transform(), 200.0, 200.0, 200.0, 0, 0, 0,
                                                    unreal.GeometryScriptPrimitiveOriginMode.CENTER)
    elif sphere:
        unreal.GeometryScript_Primitives.append_sphere_lat_long(mesh, options, unreal.Transform(), 100.0, 24, 32)
    else:
        unreal.GeometryScript_Primitives.append_rectangle_xy(mesh, options, unreal.Transform(), 200.0, 200.0, 8, 8)
    create = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=False)
    asset, result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(mesh, path, create)
    if (not isinstance(asset, unreal.StaticMesh) or result != unreal.GeometryScriptOutcomePins.SUCCESS
            or not unreal.EditorAssetLibrary.save_loaded_asset(asset)):
        raise ValueError(f"Could not save the hazard mesh {name}.")
    return asset


def mesh_actor(actors, mesh, material, center, scale, label, item, folder_path):
    import unreal

    actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*center))
    component = actor.get_component_by_class(unreal.StaticMeshComponent)
    component.set_static_mesh(mesh)
    component.set_material(0, material)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    component.set_editor_property("cast_shadow", False)
    actor.set_actor_scale3d(unreal.Vector(*scale))
    actor.set_actor_label(label)
    actor.set_folder_path(folder_path)
    actor.set_editor_property("tags", tag_names(item, "T3HazardLook"))
    return actor


def wall_bottoms(actors, component):
    """Bottoms (cm) of the level's static meshes whose centres stand inside each zone's footprint."""
    import unreal

    boxes = []
    for actor in actors.get_all_level_actors():
        if not isinstance(actor, unreal.StaticMeshActor):
            continue
        if any(str(tag).startswith(HAZARD_TAG) for tag in actor.get_editor_property("tags")):
            continue
        origin, extent = actor.get_actor_bounds(False)
        boxes.append((origin.x, origin.y, origin.z - extent.z))
    result = {}
    for zone in component.get("zones", []):
        x0, x1, y0, y1 = footprint_cm(zone["shape"])
        result[zone["id"]] = [(x, y, z) for x, y, z in boxes if x0 <= x <= x1 and y0 <= y <= y1]
    return result


def apply(actors, component, destination, view=None):
    """Place every zone's lava kit. Returns the number of zones placed."""
    import unreal

    volume_class = unreal.load_class(None, EFFECT_VOLUME)
    if not volume_class:
        raise ValueError("Lava needs the native authoring mirror. Run install_review.py --plugin native.")
    effect = damage_effect_class()
    lava_tag, damage_tag = gameplay_tag("DamageSource.Lava"), gameplay_tag("Data.Damage")
    folder_path = "TRIBES CTF/Hazards"
    placed = 0
    for item in plan(component, wall_bottoms(actors, component)):
        label = f"Hazard · {item['id']}"
        volume = actors.spawn_actor_from_class(volume_class, unreal.Vector(*item["volume"]["center"]))
        place_box_volume(volume, item["volume"])
        volume.set_editor_property("effect_to_apply", effect)
        volume.set_editor_property("damage_source_tag", lava_tag)
        volume.set_editor_property("set_by_caller_magnitude_tag", damage_tag)
        volume.set_editor_property("damage_per_sec", item["damage_per_sec"])
        volume.set_editor_property("pain_interval", item["pain_interval"])
        volume.set_editor_property("tags", tag_names(item, "T3HazardVolume"))
        volume.set_actor_label(label + " · damage")
        volume.set_folder_path(folder_path)
        if "under" in item:
            ppv = actors.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(*item["under"]["center"]))
            place_box_volume(ppv, item["under"])
            ppv.set_editor_property("priority", UNDER_LAVA["priority"])
            ppv.set_editor_property("blend_radius", UNDER_LAVA["blend_radius"])
            settings = ppv.get_editor_property("settings")
            settings.set_editor_property("override_scene_color_tint", True)
            settings.set_editor_property("scene_color_tint", unreal.LinearColor(*UNDER_LAVA["tint"], 1))
            settings.set_editor_property("override_bloom_intensity", True)
            settings.set_editor_property("bloom_intensity", UNDER_LAVA["bloom"])
            settings.set_editor_property("override_vignette_intensity", True)
            settings.set_editor_property("vignette_intensity", UNDER_LAVA["vignette"])
            ppv.set_editor_property("settings", settings)
            ppv.set_editor_property("tags", tag_names(item, "T3HazardUnder"))
            ppv.set_actor_label(label + " · under lava")
            ppv.set_folder_path(folder_path)
            surface = item["surface"]
            plane = unit_mesh(destination, "SM_HazardPlane", sphere=False)
            material = lava_material(destination, "M_HazardLava", item["tint"], view)
            mesh_actor(actors, plane, material, surface["center"],
                       (surface["half"][0] / 100.0, surface["half"][1] / 100.0, 1.0), label + " · surface", item, folder_path)
            body = item["body"]
            cube = unit_mesh(destination, "SM_HazardBody", sphere="box")
            inside = lava_material(destination, "M_HazardLavaBody", item["tint"], view, two_sided=True)
            mesh_actor(actors, cube, inside, body["center"], [max(half, 1.0) / 100.0 for half in body["half"]],
                       label + " · lava body", item, folder_path)
        if item.get("glow"):
            sphere = unit_mesh(destination, "SM_HazardGlowSphere", sphere=True)
            material = glow_material(destination, "M_HazardGlow", item["glow_color"], view)
            for index, glow in enumerate(item["glow"]):
                scale = glow["radius"] / 100.0
                mesh_actor(actors, sphere, material, glow["center"], (scale, scale, scale),
                           f"{label} · glow {index + 1}", item, folder_path)
        placed += 1
    return placed
