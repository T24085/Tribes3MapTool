"""Build a continuous terrain color blend from verified private source artwork."""

from hashlib import sha256

import unreal

from intake_contract import bounded, intake_file_limit
from terrain_art import SOURCE_ART, available_art
from terrain_layers import FALLBACK, suggested_surface
from texture_batches_unreal import import_texture


def apply_source_color(editor, material, ident, folder, header, choices, mask_sizes):
    lib = unreal.MaterialEditingLibrary
    destination = f"/Game/Community/{ident}"
    artwork = available_art(header)
    textures, coordinates = {}, {}

    def node(kind, x, y, **properties):
        expression = lib.create_material_expression(material, kind, x, y)
        for name, value in properties.items():
            expression.set_editor_property(name, value)
        return expression

    def connect(source, output, target, pin):
        if not lib.connect_material_expressions(source, output, target, pin):
            raise ValueError(f"Could not connect native terrain artwork to {pin}.")

    def texture_sample(slot, filename, *, mask, y):
        if filename not in textures:
            path = (folder / filename).resolve(strict=True)
            if (path.parent != folder
                    or sha256(bounded(path, intake_file_limit(filename, header["format"]))).hexdigest() != header["files"][filename]):
                raise ValueError("Native terrain source image changed after intake verification.")
            family = "Mask" if mask else "Color"
            asset = import_texture(path, destination, unreal.Texture2D, name=f"T_SourceTerrain{family}_{slot:02d}")
            asset.set_editor_property("srgb", not mask)
            if mask:
                asset.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_GRAYSCALE)
            if not editor.ASSETS.save_loaded_asset(asset, only_if_is_dirty=False):
                raise ValueError("Could not save the source terrain texture.")
            textures[filename] = asset
        kind = unreal.MaterialExpressionTextureSample if mask else unreal.MaterialExpressionTextureSampleParameter2D
        metadata = {} if mask else {"parameter_name": f"SourceTerrainColor_{slot:02d}"}
        return node(
            kind, -610 if mask else -310, y,
            texture=textures[filename],
            sampler_source=(unreal.SamplerSourceMode.SSM_CLAMP_WORLD_GROUP_SETTINGS if mask
                            else unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SETTINGS),
            sampler_type=(unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE if mask
                          else unreal.MaterialSamplerType.SAMPLERTYPE_COLOR),
            **metadata,
        )

    def mask_coordinates(size, y):
        if size not in coordinates:
            width, height = size
            uv = node(unreal.MaterialExpressionTextureCoordinate, -1170, y, coordinate_index=1,
                      u_tiling=(width - 1) / width, v_tiling=(height - 1) / height)
            center = node(unreal.MaterialExpressionConstant2Vector, -1170, y + 120,
                          r=.5 / width, g=.5 / height)
            offset = node(unreal.MaterialExpressionAdd, -870, y)
            connect(uv, "", offset, "A")
            connect(center, "", offset, "B")
            coordinates[size] = offset
        return coordinates[size]

    def projected_uv(world, axes, y):
        pair = node(unreal.MaterialExpressionAppendVector, -570, y)
        for pin, (x_coef, y_coef, z_coef, bias) in zip(("A", "B"), axes):
            factor = node(unreal.MaterialExpressionConstant3Vector, -1180, y,
                          constant=unreal.LinearColor(x_coef, y_coef, z_coef, 1))
            dot = node(unreal.MaterialExpressionDotProduct, -960, y)
            connect(world, "", dot, "A")
            connect(factor, "", dot, "B")
            translate = node(unreal.MaterialExpressionAdd, -780, y, const_b=bias)
            connect(dot, "", translate, "A")
            connect(translate, "", pair, pin)
            y += 125
        return pair

    y = 3200
    current = editor.palette_sample(material, FALLBACK, "Color", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
                                    -310, y - 250, parameter_name=FALLBACK + "Color")
    output = "RGB"
    world = node(unreal.MaterialExpressionWorldPosition, -1450, y) if header["terrain_uv"] else None
    paint = header["terrain_paint"]
    for slot, name, choice in sorted(choices):
        if slot in paint.get("hidden_slots", ()):
            continue
        key = str(slot)
        if choice == SOURCE_ART:
            color = texture_sample(slot, artwork[slot], mask=False, y=y)
            axes = header["terrain_uv"].get(key)
            if axes:
                connect(projected_uv(world, axes, y + 100), "", color, "UVs")
        else:
            surface = choice or suggested_surface(name) or FALLBACK
            color = editor.palette_sample(material, surface, "Color", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
                                          -310, y, parameter_name=surface + "Color")
        if slot == paint.get("base_slot"):
            current, output = color, "RGB"
            y += 470
            continue
        alpha = texture_sample(slot, paint["masks"][key], mask=True, y=y)
        connect(mask_coordinates(mask_sizes[slot], y), "", alpha, "UVs")
        blend = node(unreal.MaterialExpressionLinearInterpolate, 10, y)
        connect(current, output, blend, "A")
        connect(color, "RGB", blend, "B")
        connect(alpha, "R", blend, "Alpha")
        current, output = blend, ""
        y += 470
    if not lib.connect_material_property(current, output, unreal.MaterialProperty.MP_BASE_COLOR):
        raise ValueError("Could not bind native terrain artwork to the blend material.")
