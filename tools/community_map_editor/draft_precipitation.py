"""Build local-view rain and snow from the authored mission settings."""

import random
from pathlib import Path
from tempfile import TemporaryDirectory

import unreal

from draft_sky import draft_backdrop
from mission_weather import PRECIPITATION_ACTOR_TAG
from precipitation_shelter import roof_mask
from texture_batches_unreal import import_texture


TIME_OFFSET = 'Precipitation time offset'
QUAD = ((0, 0), (1, 0), (1, 1), (0, 1))


def particle_mesh(folder, name, emitter, half_extent):
    points, uv, seeds, fall, colors, triangles = [], [], [], [], [], []
    rng = random.Random(emitter['source_id'])
    for index in range(emitter['drops']):
        x, y, z, speed = (rng.random() for _ in range(4))
        tint = unreal.LinearColor(*rng.choice(emitter['colors']))
        i = index * 4
        for u, v in QUAD:
            points.append(unreal.Vector(x * 100 + u, y * 100 + v, z * 100))
            uv.append(unreal.Vector2D(u, v))
            seeds.append(unreal.Vector2D(x, y))
            fall.append(unreal.Vector2D(z, speed))
            colors.append(tint)
        triangles.extend((unreal.IntVector(i, i + 1, i + 2), unreal.IntVector(i, i + 2, i + 3)))
    mesh = unreal.DynamicMesh()
    mesh.append_buffers_to_mesh(unreal.GeometryScriptSimpleMeshBuffers(
        vertices=points, uv0=uv, uv1=seeds, uv2=fall, vertex_colors=colors, triangles=triangles))
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
        enable_recompute_normals=True, enable_recompute_tangents=True, enable_collision=False)
    asset, result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        mesh, f'{folder}/{name}', options)
    if not isinstance(asset, unreal.StaticMesh) or result != unreal.GeometryScriptOutcomePins.SUCCESS:
        raise ValueError('Unreal could not create the precipitation mesh.')
    bounds = unreal.Vector(half_extent, half_extent, half_extent)
    for key in ('positive_bounds_extension', 'negative_bounds_extension'):
        asset.set_editor_property(key, bounds)
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
        raise ValueError('Unreal could not save the precipitation mesh.')
    return asset


def bake_shelter(world, folder, level_actors, shelters):
    bounds, roofs = [], []
    checked = set()
    for actor in shelters:
        component = actor.static_mesh_component
        mesh = component.static_mesh
        if (not mesh or component.get_collision_enabled() == unreal.CollisionEnabled.NO_COLLISION
                or component.mobility != unreal.ComponentMobility.STATIC):
            continue
        if mesh.get_path_name() not in checked:
            mesh.get_num_triangles(0)
            checked.add(mesh.get_path_name())
        center, half = actor.get_actor_bounds(True)
        if min(half.x, half.y) > 0:
            bounds.append(((center - half).to_tuple(), (center + half).to_tuple()))
            roofs.append(actor)
    if not roofs:
        return None
    roof_paths = {actor.get_path_name() for actor in roofs}
    excluded = [actor for actor in level_actors if actor.get_path_name() not in roof_paths
                and actor.get_components_by_class(unreal.PrimitiveComponent)]

    def sample(x, y, low, high):
        result = unreal.SystemLibrary.line_trace_single(
            world, unreal.Vector(x, y, high), unreal.Vector(x, y, low),
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, excluded, unreal.DrawDebugTrace.NONE, True)
        if result:
            hit = result.to_tuple()
            if hit[0]:
                return hit[5].z
        return None

    recipe = roof_mask(bounds, sample)
    if recipe is None:
        return None
    png, origin, size, lowest, span = recipe
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / 'T_PrecipitationShelter.png'
        path.write_bytes(png)
        texture = import_texture(path, folder, unreal.Texture2D)
    texture.set_editor_property('srgb', False)
    texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
    texture.set_editor_property('mip_gen_settings', unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
    texture.set_editor_property('filter', unreal.TextureFilter.TF_NEAREST)
    texture.set_editor_property('never_stream', True)
    if not unreal.EditorAssetLibrary.save_loaded_asset(texture, only_if_is_dirty=False):
        raise ValueError('Unreal could not save the precipitation shelter mask.')
    return texture, origin, size, lowest, span


def particle_material(folder, name, emitter, shelter=None):
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise ValueError('Unreal could not create the precipitation material.')
    lib = unreal.MaterialEditingLibrary

    def node(kind, x, y):
        return lib.create_material_expression(material, kind, x, y)

    def shader(name, code, output, inputs, y):
        custom = node(unreal.MaterialExpressionCustom, -120, y)
        custom.set_editor_property('description', name)
        custom.set_editor_property('code', code)
        custom.set_editor_property('output_type', output)
        custom_inputs = []
        for key, _, _ in inputs:
            custom_input = unreal.CustomInput()
            custom_input.set_editor_property('input_name', key)
            custom_inputs.append(custom_input)
        custom.set_editor_property('inputs', custom_inputs)
        for key, source, pin in inputs:
            if not lib.connect_material_expressions(source, pin, custom, key):
                raise ValueError(f'Unreal could not link the precipitation input {key}.')
        return custom

    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property('two_sided', True)
    vertex = node(unreal.MaterialExpressionWorldPosition, -680, 0)
    vertex.set_editor_property('world_position_shader_offset', unreal.WorldPositionIncludedOffsets.WPT_EXCLUDE_ALL_SHADER_OFFSETS)
    position = node(unreal.MaterialExpressionWorldPosition, -680, 580)
    eye = node(unreal.MaterialExpressionCameraPositionWS, -680, -150)
    uv, seed, fall = [node(unreal.MaterialExpressionTextureCoordinate, -680, 130 * (i + 1)) for i in range(3)]
    for i, coordinate in enumerate((uv, seed, fall)):
        coordinate.set_editor_property('coordinate_index', i)
    time = node(unreal.MaterialExpressionTime, -1080, -240)
    offset = node(unreal.MaterialExpressionScalarParameter, -1080, -80)
    offset.set_editor_property('parameter_name', TIME_OFFSET)
    phase = node(unreal.MaterialExpressionAdd, -860, -150)
    if not lib.connect_material_expressions(time, '', phase, 'A') or not lib.connect_material_expressions(
        offset, '', phase, 'B'
    ):
        raise ValueError('Unreal could not link the precipitation clock.')
    rain = emitter['kind'] == 'rain'
    radius = emitter['radius_cm']
    slow, fast = emitter['fall_cm_per_second']
    wind_x, wind_y, wind_z = emitter['wind_cm_per_second']
    drift = emitter['drift_cm_per_second']
    width, length = (2.2, 28.0) if rain else (7.0, 7.0)
    # Wrap world-space particles inside the view's moving volume. The seeds
    # remain fixed in world space as a player walks through the effect.
    move = shader('World-space precipitation', f"""
        float diameter = {2.0 * radius};
        float azimuth = ZSpeed.y * 6.2831853;
        float fallSpeed = lerp({float(slow)}, {float(fast)}, ZSpeed.y);
        float3 jitter = float3(cos(azimuth), sin(azimuth), 0) * {float(drift)};
        float3 velocity = float3({float(wind_x)}, {float(wind_y)}, {float(wind_z)} - fallSpeed) + jitter;
        float3 seeds = float3(SeedUV.xy, ZSpeed.x);
        float3 relative = (frac(seeds + (Phase * velocity - Eye) / diameter + 0.5) - 0.5) * diameter;
        float3 facing = normalize(-relative + float3(0.0001, 0, 0));
        float3 right = normalize(cross(abs(facing.z) < 0.98 ? float3(0, 0, 1) : float3(0, 1, 0), facing));
        float3 up = { 'float3(0, 0, 1)' if rain else 'cross(facing, right)' };
        float3 quad = (ParticleUV.x - 0.5) * {width} * right + (ParticleUV.y - 0.5) * {length} * up;
        return Eye + relative + quad - Vertex;
    """, unreal.CustomMaterialOutputType.CMOT_FLOAT3, (
        ('Eye', eye, ''), ('Vertex', vertex, ''), ('ParticleUV', uv, ''), ('SeedUV', seed, ''),
        ('ZSpeed', fall, ''), ('Phase', phase, ''),
    ), -80)
    color = node(unreal.MaterialExpressionVertexColor, -680, 760)
    shape = ('saturate(1.0 - abs(p.x)) * sqrt(saturate(1.0 - abs(p.y)))'
             if rain else '1.0 - smoothstep(0.2, 1.0, dot(p, p))')
    shelter_code = ''
    alpha_inputs = [('Eye', eye, ''), ('Position', position, ''), ('ParticleUV', uv, ''), ('TintAlpha', color, 'A')]
    if shelter:
        texture, origin, size, lowest, span = shelter
        object_node = node(unreal.MaterialExpressionTextureObject, -680, 900)
        object_node.set_editor_property('texture', texture)
        object_node.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
        alpha_inputs.append(('Shelter', object_node, ''))
        shelter_code = f"""
            float2 RoofUV = (Position.xy - float2({origin[0]}, {origin[1]})) / float2({size[0]}, {size[1]});
            if (all(RoofUV >= 0.0) && all(RoofUV < 1.0)) {{
                float3 PackedHeight = Texture2DSampleLevel(Shelter, ShelterSampler, RoofUV, 0).rgb;
                float HeightFraction = dot(round(PackedHeight.rg * 255.0), float2(256.0, 1.0)) / 65535.0;
                if (PackedHeight.b > 0.5 && Position.z <= {lowest} + HeightFraction * {span}) return 0.0;
            }}
        """
    alpha = shader('Particle mask and volume fade', f"""
        {shelter_code}
        float2 p = ParticleUV * 2 - 1;
        float d = length(Position - Eye);
        return ({shape}) * TintAlpha
             * smoothstep(15.0, 60.0, d)
             * (1 - smoothstep({radius * 0.78}, {float(radius)}, d));
    """, unreal.CustomMaterialOutputType.CMOT_FLOAT1, alpha_inputs, 580)
    # UE 5.3 hides the world-position offset pin from the Python enum.
    if not unreal.CommunityMapMaterialLibrary.connect_world_position_offset(move):
        raise ValueError('Unreal could not link the precipitation vertex offset.')
    for expression, pin, target in (
        (alpha, '', unreal.MaterialProperty.MP_OPACITY),
        (color, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR),
    ):
        if not lib.connect_material_property(expression, pin, target):
            raise ValueError(f'Unreal could not link the precipitation material property {target}.')
    material.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise ValueError('Unreal could not save the precipitation material.')
    return material


def place_precipitation(draft, actors, emitters, *, world=None, shelters=()):
    if not emitters:
        return []
    level_actors = actors.get_all_level_actors()
    _, _, center, play_radius = draft_backdrop(draft, level_actors)
    folder = f'/Game/Community/{draft}/Weather'
    shelter = bake_shelter(world, folder, level_actors, shelters) if shelters else None
    result = []
    for emitter in emitters:
        label = emitter['kind'].title()
        suffix = f"{label}_{emitter['source_id']}"
        material = particle_material(folder, 'M_' + suffix, emitter, shelter)
        mesh = particle_mesh(folder, 'SM_' + suffix, emitter, play_radius + emitter['radius_cm'])
        actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, center)
        if not actor or not actor.static_mesh_component.set_static_mesh(mesh):
            raise ValueError(f'Unreal could not place the draft {label.lower()}.')
        component = actor.static_mesh_component
        component.set_material(0, material)
        component.set_cast_shadow(False)
        component.set_collision_profile_name('NoCollision')
        component.set_editor_property('generate_overlap_events', False)
        component.set_editor_property('allow_cull_distance_volume', False)
        component.set_hidden_in_scene_capture(True)
        actor.set_actor_label(f'{label} · {emitter["drops"]} drops')
        actor.set_folder_path('TRIBES Environment/Precipitation')
        actor.set_editor_property('tags', [PRECIPITATION_ACTOR_TAG])
        result.append(actor)
    return result
