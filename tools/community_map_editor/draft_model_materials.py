"""Apply verified source albedo to generated map actors without changing shared meshes."""

from hashlib import sha256
import json

import unreal

from intake_contract import bounded
from interior_geometry import ASCEND_STATIC_MESH, VENGEANCE_BSP, VENGEANCE_STATIC_MESH, material_sections
from interior_lighting import model_lighting
from mesh_mappings import source_reference
from model_textures import has_shader_inputs, load_texture_index, placement_key, shader_signature
from source_profiles import BRUSH_PREVIEW_FOLDER, BRUSH_PREVIEW_REFERENCE
from texture_batches_unreal import import_texture


DESTINATION = '/Game/Community/T2ModelTextures'
LEGACY_DESTINATION = '/Game/Community/T1ModelTextures'
VENGEANCE_DESTINATIONS = {VENGEANCE_BSP: '/Game/Community/VengeanceBSPTextures',
                          VENGEANCE_STATIC_MESH: '/Game/Community/VengeanceMeshTextures'}
HASH_TAG = 'T3ModelTextureHash'
ALARM_BLEND = 'T2 Alarm Blend'
BAKED_MATERIAL_PREFIX = 'M_T2Baked_'
LEGACY_BAKED_PREFIX = 'M_T1Baked_'
BAKED_MATERIAL_PREFIXES = (BAKED_MATERIAL_PREFIX, LEGACY_BAKED_PREFIX)


def source_texture(folder, filename, digest, *, srgb, destination=DESTINATION, normal=False):
    assets = unreal.EditorAssetLibrary
    pinned = (folder / filename).resolve(strict=True)
    if pinned.parent != folder or sha256(bounded(pinned, 16 * 1024 * 1024)).hexdigest() != digest:
        raise ValueError('The source texture changed after intake validation.')
    texture_path = f'{destination}/{pinned.stem}.{pinned.stem}'
    if assets.does_asset_exist(texture_path):
        texture = assets.load_asset(texture_path)
        if not isinstance(texture, unreal.Texture2D) or assets.get_metadata_tag(texture, HASH_TAG) != digest:
            raise ValueError(f'The generated texture path is already in use: {texture_path}')
    else:
        texture = import_texture(pinned, destination, unreal.Texture2D)
        texture.set_editor_property('srgb', srgb)
        if normal:
            texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
        elif destination == LEGACY_DESTINATION and not srgb:
            texture.set_editor_property('mip_gen_settings', unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
            texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
        assets.set_metadata_tag(texture, HASH_TAG, digest)
        if not assets.save_loaded_asset(texture, only_if_is_dirty=False):
            raise ValueError(f'Unreal could not save the source texture: {texture_path}')
    return texture


def material_asset(name, digest, destination=DESTINATION):
    assets = unreal.EditorAssetLibrary
    material_path = f'{destination}/{name}.{name}'
    if assets.does_asset_exist(material_path):
        material = assets.load_asset(material_path)
        if not isinstance(material, unreal.Material) or assets.get_metadata_tag(material, HASH_TAG) != digest:
            raise ValueError(f'The generated material path is already in use: {material_path}')
        return material, False
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, destination, unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise ValueError(f'Unreal did not create the source material: {material_path}')
    return material, True


def save_material(material, digest):
    assets = unreal.EditorAssetLibrary
    assets.set_metadata_tag(material, HASH_TAG, digest)
    if not assets.save_loaded_asset(material, only_if_is_dirty=False):
        raise ValueError(f'Unreal could not save the source material: {material.get_path_name()}')


def bind_shader_inputs(material, lib, sample, folder, inventory, record, destination):
    def texture_input(channel, label, x, y, *, normal=False):
        name = channel['file']
        if not normal and name == record['file'] and 'pan' not in channel:
            return sample
        node = lib.create_material_expression(material, unreal.MaterialExpressionTextureSampleParameter2D, x, y)
        node.set_editor_property('parameter_name', label)
        texture_folder = destination + '/Normals' if normal else destination
        node.set_editor_property('texture', source_texture(folder, name, inventory[name], srgb=not normal,
                                                           destination=texture_folder, normal=normal))
        if normal:
            node.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        if 'pan' in channel:
            panner = lib.create_material_expression(material, unreal.MaterialExpressionPanner, x - 240, y)
            x_rate, y_rate = channel['pan']
            panner.set_editor_property('speed_x', x_rate)
            panner.set_editor_property('speed_y', y_rate)
            panner.set_editor_property('fractional_part', True)
            if not lib.connect_material_expressions(panner, '', node, 'UVs'):
                raise ValueError('Unreal did not bind the source UV panner.')
        return node

    mode = record['mode']
    if mode in ('masked', 'translucent'):
        if mode == 'masked':
            material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
            target = unreal.MaterialProperty.MP_OPACITY_MASK
        else:
            material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
            lighting = (unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING if 'specular_mask' in record
                        else unreal.TranslucencyLightingMode.TLM_SURFACE)
            material.set_editor_property('translucency_lighting_mode', lighting)
            target = unreal.MaterialProperty.MP_OPACITY
        opacity = texture_input(record['opacity'], 'SourceOpacity', -660, 240) if 'opacity' in record else sample
        if not lib.connect_material_property(opacity, 'A', target):
            raise ValueError('Unreal did not bind the source alpha input.')
    if 'glow' in record:
        glow = record['glow']
        color = texture_input(glow['color'], 'SourceGlowColor', -840, 520)
        mask = color if glow['mask'] == glow['color'] else texture_input(glow['mask'], 'SourceGlowMask', -840, 810)
        masked = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -550, 545)
        scaled = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -275, 545)
        scaled.set_editor_property('const_b', glow['brightness'] / 255)
        if (not lib.connect_material_expressions(color, 'RGB', masked, 'A')
                or not lib.connect_material_expressions(mask, 'A', masked, 'B')
                or not lib.connect_material_expressions(masked, '', scaled, 'A')
                or not lib.connect_material_property(scaled, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)):
            raise ValueError('Unreal did not bind the source glow color and mask.')
    if 'normal' in record:
        normal = texture_input(record['normal'], 'SourceNormal', -660, 1110, normal=True)
        if not lib.connect_material_property(normal, 'RGB', unreal.MaterialProperty.MP_NORMAL):
            raise ValueError('Unreal did not bind the source tangent-space normal map.')
    if 'specular_mask' in record:
        specular = texture_input(record['specular_mask'], 'SourceSpecularMask', -660, 1400)
        if not lib.connect_material_property(specular, 'A', unreal.MaterialProperty.MP_SPECULAR):
            raise ValueError('Unreal did not bind the source specular mask.')


def source_material(folder, inventory, record, *, destination, mode='opaque', two_sided=False):
    filename = record['file']
    digest = inventory[filename]
    texture = source_texture(folder, filename, digest, srgb=True, destination=destination)
    masked = mode == 'masked'
    prefix = 'M_Masked_' if masked else 'M_'
    if two_sided:
        prefix += 'TwoSided_'
    extended = has_shader_inputs(record)
    if extended:
        digest = sha256(json.dumps(shader_signature(record, inventory), separators=(',', ':')).encode()).hexdigest()
    name = 'M_Shader_' + digest[:32] if extended else prefix + texture.get_name()
    material, created = material_asset(name, digest, destination)
    if not created:
        return material
    try:
        lib = unreal.MaterialEditingLibrary
        sample = lib.create_material_expression(material, unreal.MaterialExpressionTextureSampleParameter2D, -360, 0)
        sample.set_editor_property('parameter_name', 'SourceColor')
        sample.set_editor_property('texture', texture)
        if two_sided:
            material.set_editor_property('two_sided', True)
        if not lib.connect_material_property(sample, 'RGB', unreal.MaterialProperty.MP_BASE_COLOR):
            raise ValueError('Unreal did not bind the source texture to the base color.')
        if extended:
            bind_shader_inputs(material, lib, sample, folder, inventory, record, destination)
        elif masked:
            material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
            if not lib.connect_material_property(sample, 'A', unreal.MaterialProperty.MP_OPACITY_MASK):
                raise ValueError('Unreal did not bind the source texture to the opacity mask.')
        lib.recompile_material(material)
        save_material(material, digest)
    except Exception:
        unreal.EditorAssetLibrary.delete_loaded_asset(material)
        raise
    return material


def lighted_material(folder, inventory, albedo, normal, alarm, *, legacy=False, masked=False):
    names = (albedo, normal, alarm)
    signature = '|'.join(inventory[name] if name else '-' for name in names)
    if legacy:
        signature += '|t1-masked' if masked else '|t1-opaque'
    digest = sha256(signature.encode()).hexdigest()
    prefix = LEGACY_BAKED_PREFIX if legacy else BAKED_MATERIAL_PREFIX
    destination = LEGACY_DESTINATION if legacy else DESTINATION
    material, created = material_asset(prefix + digest[:32], digest, destination)
    if not created:
        return material
    lib = unreal.MaterialEditingLibrary

    def node(kind, x, y):
        return lib.create_material_expression(material, kind, x, y)

    def connect(source, output, target, pin):
        if not lib.connect_material_expressions(source, output, target, pin):
            raise ValueError(f'Unreal could not link the baked interior material input {pin}.')

    def sample(name, label, y, coords=None):
        if name is None:
            value = node(unreal.MaterialExpressionConstant3Vector, -540, y)
            value.set_editor_property('constant', unreal.LinearColor(1, 1, 1, 1))
            return value, ''
        value = node(unreal.MaterialExpressionTextureSampleParameter2D, -540, y)
        value.set_editor_property('parameter_name', label)
        value.set_editor_property('texture', source_texture(folder, name, inventory[name],
                                                            srgb=coords is None, destination=destination))
        if coords is not None:
            value.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
            connect(coords, '', value, 'UVs')
        return value, 'RGB'

    try:
        uv = node(unreal.MaterialExpressionTextureCoordinate, -780, 140)
        uv.set_editor_property('coordinate_index', 1)
        color = sample(albedo, 'SourceColor', -220)
        bake = sample(normal, 'NormalLightmap', 40, uv)
        if alarm is not None and (legacy or alarm != normal):
            emergency_uv = uv
            if legacy:
                emergency_uv = node(unreal.MaterialExpressionTextureCoordinate, -780, 360)
                emergency_uv.set_editor_property('coordinate_index', 2)
            emergency = sample(alarm, 'AlarmLightmap', 340, emergency_uv)
            blend = node(unreal.MaterialExpressionScalarParameter, -540, 650)
            blend.set_editor_property('parameter_name', ALARM_BLEND)
            lerp = node(unreal.MaterialExpressionLinearInterpolate, -290, 140)
            connect(*bake, lerp, 'A')
            connect(*emergency, lerp, 'B')
            connect(blend, '', lerp, 'Alpha')
            bake = lerp, ''
        shaded = node(unreal.MaterialExpressionMultiply, -90, 0)
        connect(*color, shaded, 'A')
        connect(*bake, shaded, 'B')
        if not lib.connect_material_property(shaded, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR):
            raise ValueError('Unreal could not link the baked interior material color.')
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
        if masked and albedo:
            material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
            if not lib.connect_material_property(color[0], 'A', unreal.MaterialProperty.MP_OPACITY_MASK):
                raise ValueError('Unreal could not link the baked interior opacity mask.')
        if legacy:
            lib.recompile_material(material)
        save_material(material, digest)
    except Exception:
        unreal.EditorAssetLibrary.delete_loaded_asset(material)
        raise
    return material


def generate_overrides(folder, inventory, models, lighting_index=None, *, moving_references=(), placements=()):
    index = load_texture_index(folder, inventory)
    lighting_index = lighting_index or {}
    materials, overrides = {}, {}
    variants = {}
    for row, *_ in placements:
        key = placement_key(row['id'])
        if key in index and source_reference(row) in models:
            variants[key] = models[source_reference(row)]
    models = {**models, **variants}
    for reference, (filename, geometry) in models.items():
        authored = index.get(filename, {})
        if reference in variants:
            authored = {**authored, **index[reference]}
        lighting = model_lighting(lighting_index, filename, geometry)
        if not authored and not lighting:
            continue
        sections = material_sections(geometry, lighting)
        vengeance_destination = VENGEANCE_DESTINATIONS.get(geometry['format'])
        if reference.startswith(BRUSH_PREVIEW_REFERENCE) and reference not in moving_references:
            vengeance_destination = BRUSH_PREVIEW_FOLDER + '/Materials'
        slots = {}
        for component_slot, (source_slot, normal, alarm) in enumerate(sections):
            record = authored.get(str(source_slot), {})
            name = record.get('file')
            legacy, mode = geometry['format'] >= 3, record.get('mode', 'opaque')
            two_sided = bool(vengeance_destination and record.get('two_sided', False))
            if normal >= 0 or alarm >= 0:
                atlases = lighting['atlases']
                normal_map = atlases[normal]['color']['file'] if normal >= 0 else None
                alarm_map = atlases[alarm]['color']['file'] if alarm >= 0 else None
                slots[component_slot] = lighted_material(folder, inventory, name, normal_map, alarm_map,
                                                         legacy=legacy, masked=mode == 'masked')
            elif not name:
                continue
            else:
                if geometry['format'] == ASCEND_STATIC_MESH:
                    destination = '/Game/Community/AscendMeshTextures'
                else:
                    destination = vengeance_destination or (LEGACY_DESTINATION if legacy else DESTINATION)
                key = (name, destination, mode, two_sided)
                if has_shader_inputs(record):
                    key += (json.dumps(shader_signature(record, inventory), separators=(',', ':')),)
                if key not in materials:
                    materials[key] = source_material(folder, inventory, record, destination=destination,
                                                     mode=mode, two_sided=two_sided)
                slots[component_slot] = materials[key]
        if slots or reference in variants:
            overrides[reference] = slots
    return overrides
