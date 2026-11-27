"""Apply verified source albedo to generated map actors without changing shared meshes."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import unreal

from dif_light_animation import mask_texture
from intake_contract import bounded
from interior_geometry import ASCEND_BSP, ASCEND_STATIC_MESH, VENGEANCE_BSP, VENGEANCE_STATIC_MESH, material_sections
from interior_lighting import model_lighting
from mesh_mappings import source_reference
from model_textures import has_shader_inputs, load_texture_index, placement_key, shader_signature, validate_png
from source_profiles import BRUSH_PREVIEW_FOLDER, BRUSH_PREVIEW_REFERENCE
from texture_batches_unreal import import_texture


DESTINATION = '/Game/Community/T2ModelTextures'
LEGACY_DESTINATION = '/Game/Community/T1ModelTextures'
VENGEANCE_DESTINATIONS = {VENGEANCE_BSP: '/Game/Community/VengeanceBSPTextures',
                          VENGEANCE_STATIC_MESH: '/Game/Community/VengeanceMeshTextures'}
HASH_TAG = 'T3ModelTextureHash'
ALARM_BLEND = 'T2 Alarm Blend'
LIGHT_PREVIEW_TIME = 'T2 Light Preview Time'
BAKED_MATERIAL_PREFIX = 'M_T2Baked_'
LEGACY_BAKED_PREFIX = 'M_T1Baked_'
BAKED_MATERIAL_PREFIXES = (BAKED_MATERIAL_PREFIX, LEGACY_BAKED_PREFIX)


def source_texture(folder, filename, digest, *, srgb, destination=DESTINATION, normal=False, intensity=False):
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
        elif intensity:
            texture.set_editor_property('mip_gen_settings', unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
            texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_GRAYSCALE)
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


def bind_ascend_base(material, lib, sample, settings):
    if 'uv' in settings:
        channel, u_scale, v_scale = settings['uv']
        uv = lib.create_material_expression(material, unreal.MaterialExpressionTextureCoordinate, -650, 0)
        uv.set_editor_property('coordinate_index', channel)
        uv.set_editor_property('u_tiling', u_scale)
        uv.set_editor_property('v_tiling', v_scale)
        if not lib.connect_material_expressions(uv, '', sample, 'UVs'):
            raise ValueError('Unreal did not bind the Ascend source UV channel.')
    if 'vertex_rgb' not in settings:
        return sample, 'RGB'
    color = lib.create_material_expression(material, unreal.MaterialExpressionVertexColor, -360, 280)
    tint = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -100, 0)
    if (not lib.connect_material_expressions(sample, 'RGB', tint, 'A')
            or not lib.connect_material_expressions(color, '', tint, 'B')):
        raise ValueError('Unreal did not bind the Ascend source vertex colors.')
    return tint, ''


def source_material(folder, inventory, record, *, destination, mode='opaque', two_sided=False):
    filename = record['file']
    digest = inventory[filename]
    texture = source_texture(folder, filename, digest, srgb=True, destination=destination)
    masked = mode == 'masked'
    prefix = 'M_Masked_' if masked else 'M_'
    if two_sided:
        prefix += 'TwoSided_'
    extended = has_shader_inputs(record) or 'ascend_base' in record
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
        base, output = (bind_ascend_base(material, lib, sample, record['ascend_base'])
                        if 'ascend_base' in record else (sample, 'RGB'))
        if not lib.connect_material_property(base, output, unreal.MaterialProperty.MP_BASE_COLOR):
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


def loop_expression(node, connect, uv, time, preview, dimensions, track, destination, y, start_defaults):
    duration, states = track[:2]
    activation = track[2] if len(track) > 2 else None
    mode = track[3] if len(track) > 3 else "loop"
    width, height = dimensions
    png, tiles, columns, rows = mask_texture(states, width, height)
    digest = sha256(png).hexdigest()
    with TemporaryDirectory() as staging:
        root = Path(staging).resolve()
        filename = f'dif_intensity_{digest[:32]}.png'
        (root / filename).write_bytes(png)
        intensity = source_texture(root, filename, digest, srgb=False, intensity=True, destination=destination)
    mask = node(unreal.MaterialExpressionTextureObject, -900, y)
    mask.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE)
    mask.set_editor_property('texture', intensity)
    custom = node(unreal.MaterialExpressionCustom, -650, y)
    scope = 'triggered' if activation else 'ambient'
    custom.set_editor_property('description', f'Tribes 2 {scope} light {mode}')
    custom.set_editor_property('output_type', unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    pins = []
    inputs = [('UV', uv), ('Clock', time), ('PreviewTime', preview), ('Mask', mask)]
    if activation:
        start = node(unreal.MaterialExpressionScalarParameter, -1160, y + 220)
        start.set_editor_property('parameter_name', activation)
        start.set_editor_property('default_value', start_defaults.get(activation, -1.0))
        inputs.append(('StartTime', start))
    if mode == 'flicker':
        origin = node(unreal.MaterialExpressionActorPositionWS, -1160, y + 320)
        inputs.append(('Origin', origin))
    for key, _ in inputs:
        pin = unreal.CustomInput()
        pin.set_editor_property('input_name', key)
        pins.append(pin)
    custom.set_editor_property('inputs', pins)
    for key, source in inputs:
        connect(source, '', custom, key)

    def color(rgb):
        return 'float3(' + ','.join(f'{channel}.0' for channel in rgb) + ')'

    # The DIF uses the active state's mask while its color ramps to the next state.
    code = []
    clock = 'Clock'
    if activation:
        code.append('if ((StartTime < 0.0 && StartTime != -2.0) || (PreviewTime < 0.0 && Clock < StartTime)) return float3(0.0,0.0,0.0);')
        clock = '(Clock - StartTime)'
    code.append(f'float Elapsed = max(0.0, PreviewTime >= 0.0 ? PreviewTime : {clock}) * 1000.0;')
    if activation:
        code.append('if (StartTime == -2.0) Elapsed = 0.0;')
    if mode == 'flicker':
        # The second state's timestamp is the refresh interval. The draw
        # ranges between zero and duration, so the timestamps weight each
        # state's odds. Derive it from the placement and phase so all atlas
        # contributions agree without a game-side random-number service.
        period = max(67, states[1][0])
        seed = track[4]
        code.extend([f'uint Step = (uint)floor(Elapsed / {period}.0);',
                     f'uint Draw = Step ^ {seed}u ^ asuint(Origin.x) ^ (asuint(Origin.y) * 1664525u)'
                     ' ^ (asuint(Origin.z) * 1013904223u);',
                     'Draw = (Draw ^ (Draw >> 16)) * 0x7feb352du;',
                     'Draw = (Draw ^ (Draw >> 15)) * 0x846ca68bu;',
                     'Draw ^= Draw >> 16;',
                     f'float Phase = Step == 0u ? 0.0 : float(Draw % {duration + 1}u);'])
    elif mode == 'ramp':
        code.append(f'float Phase = min(Elapsed, {duration}.0);')
    else:
        code.append(f'float Phase = fmod(Elapsed, {duration}.0);')
    code.extend(['float3 Color = float3(0.0,0.0,0.0);', 'float2 Tile = float2(0.0,0.0);'])
    for i, (start, rgb, _) in enumerate(states):
        last = i + 1 == len(states)
        end, following = (duration, states[0][1]) if last else states[i + 1][:2]
        tile = tiles[i]
        branch = ('if' if not i else 'else if') + f' (Phase < {end}.0)'
        if last and mode != 'loop':
            branch = 'else' if i else ''
        expression = color(rgb)
        if mode == 'loop' or (mode == 'ramp' and not last):
            expression = f'lerp({expression}, {color(following)}, (Phase - {start}.0) / {end - start}.0)'
        code.extend([f'{branch} {{', f' Color = {expression};',
                     f' Tile = float2({tile % columns}.0, {tile // columns}.0);', '}'])
    code.extend([f'float2 HalfTexel = 0.5 / float2({width}.0,{height}.0);',
                 f'float2 At = (clamp(UV, HalfTexel, 1.0 - HalfTexel) + Tile) / float2({columns}.0,{rows}.0);',
                 'float Intensity = Texture2DSampleLevel(Mask, MaskSampler, At, 0).r * 255.0;',
                 'return floor((floor(Color + 0.5) * Intensity + 128.0) / 256.0) / 255.0;'])
    custom.set_editor_property('code', '\n'.join(code))
    return custom, ''


def lighted_material(folder, inventory, albedo, normal, alarm, *, legacy=False, masked=False,
                     normal_loops=(), alarm_loops=(), startup_lights=None):
    from dif_light_animation import LIGHT_START_PREFIX

    names_in_slot = {track[2] for track in (*normal_loops, *alarm_loops) if len(track) > 2 and track[2]}
    defaults = {LIGHT_START_PREFIX + name: value for name, value in (startup_lights or {}).items()
                if LIGHT_START_PREFIX + name in names_in_slot}
    names = (albedo, normal, alarm)
    signature = '|'.join(inventory[name] if name else '-' for name in names)
    if legacy:
        signature += '|t1-masked' if masked else '|t1-opaque'
    if normal_loops or alarm_loops:
        signature += '|dif-ambient-v1|' + json.dumps([normal_loops, alarm_loops], separators=(',', ':'))
    if defaults:
        signature += '|dif-startup-v1|' + json.dumps(defaults, separators=(',', ':'), sort_keys=True)
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

    def add_loops(bake, name, loops, y):
        if not loops:
            return bake
        # Texture streaming can report a smaller resident mip in a rendering editor.
        dimensions = validate_png(bounded(folder / name, 16 * 1024 * 1024))
        time = node(unreal.MaterialExpressionTime, -1160, y)
        preview = node(unreal.MaterialExpressionScalarParameter, -1160, y + 150)
        preview.set_editor_property('parameter_name', LIGHT_PREVIEW_TIME)
        preview.set_editor_property('default_value', -1.0)
        value = bake
        for index, loop in enumerate(loops):
            row = y + index * 300
            add = node(unreal.MaterialExpressionAdd, -390, row)
            connect(*value, add, 'A')
            connect(*loop_expression(node, connect, uv, time, preview, dimensions, loop, destination, row, defaults), add, 'B')
            value = add, ''
        clamp = node(unreal.MaterialExpressionSaturate, -270, y)
        inputs = list(lib.get_material_expression_input_names(clamp))
        connect(*value, clamp, str(inputs[0]))
        return clamp, ''

    try:
        uv = node(unreal.MaterialExpressionTextureCoordinate, -780, 140)
        uv.set_editor_property('coordinate_index', 1)
        color = sample(albedo, 'SourceColor', -220)
        bake = add_loops(sample(normal, 'NormalLightmap', 40, uv), normal, normal_loops, 820)
        if alarm is not None and (legacy or alarm != normal or normal_loops != alarm_loops):
            emergency_uv = uv
            if legacy:
                emergency_uv = node(unreal.MaterialExpressionTextureCoordinate, -780, 360)
                emergency_uv.set_editor_property('coordinate_index', 2)
            emergency = add_loops(sample(alarm, 'AlarmLightmap', 340, emergency_uv), alarm, alarm_loops, 1900)
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
        if legacy or normal_loops or alarm_loops:
            lib.recompile_material(material)
        save_material(material, digest)
    except Exception as error:
        unreal.log_warning(f'Baked interior material {material.get_name()}: {error}')
        unreal.EditorAssetLibrary.delete_loaded_asset(material)
        raise
    return material


def generate_overrides(folder, inventory, models, lighting_index=None, *, moving_references=(), placements=(),
                       startup_lights=None):
    index = load_texture_index(folder, inventory)
    lighting_index = lighting_index or {}
    materials, overrides = {}, {}
    variants = {}
    placement_lights = {}
    for row, *_ in placements:
        key = placement_key(row['id'])
        light_defaults = (startup_lights or {}).get(row['id'])
        if (key in index or light_defaults) and source_reference(row) in models:
            variants[key] = models[source_reference(row)]
            if light_defaults:
                placement_lights[key] = light_defaults
    models = {**models, **variants}
    for reference, (filename, geometry) in models.items():
        authored = index.get(filename, {})
        if reference in variants:
            authored = {**authored, **index.get(reference, {})}
        lighting = model_lighting(lighting_index, filename, geometry)
        if not authored and not lighting:
            continue
        if lighting:
            for warning in lighting.get('animation_warnings', ()):
                unreal.log_warning(f"{geometry['source']['name']}: {warning}")
        sections = material_sections(geometry, lighting)
        vengeance_destination = VENGEANCE_DESTINATIONS.get(geometry['format'])
        if reference.startswith(BRUSH_PREVIEW_REFERENCE) and reference not in moving_references:
            vengeance_destination = BRUSH_PREVIEW_FOLDER + '/Materials'
        slots = {}
        for component_slot, (source_slot, normal, alarm) in enumerate(sections):
            record = authored.get(str(source_slot), {})
            name = record.get('file')
            legacy, mode = geometry['format'] >= 3, record.get('mode', 'opaque')
            # Vengeance and Ascend record the source material's sidedness (Ascend foliage, #35).
            two_sided = bool((vengeance_destination or geometry['format'] in (ASCEND_STATIC_MESH, ASCEND_BSP))
                             and record.get('two_sided', False))
            if normal >= 0 or alarm >= 0:
                atlases = lighting['atlases']
                normal_map = atlases[normal]['color']['file'] if normal >= 0 else None
                alarm_map = atlases[alarm]['color']['file'] if alarm >= 0 else None
                slots[component_slot] = lighted_material(folder, inventory, name, normal_map, alarm_map,
                                                         legacy=legacy, masked=mode == 'masked',
                                                         normal_loops=atlases[normal].get('animated_loops', ()) if normal >= 0 else (),
                                                         alarm_loops=atlases[alarm].get('animated_loops', ()) if alarm >= 0 else (),
                                                         startup_lights=placement_lights.get(reference))
            elif not name:
                continue
            else:
                if geometry['format'] in (ASCEND_STATIC_MESH, ASCEND_BSP):
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
