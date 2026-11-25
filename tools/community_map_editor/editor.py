"""TRIBES map authoring actions for the private Unreal project."""
import json
import os
from pathlib import Path
import re

import unreal

from perimeter import verify_flag_bounds, verify_walls
from source_profiles import ANY_DRAFT, IMPORT_DRAFT, INVENTORY_STATION_TEMPLATE, T2_DRAFT, imported_draft, inventory_station_package
from terrain_layers import PALETTE_SURFACES


LEVEL_ROOT = '/Game/Maps/Community'
SOURCE_LEVEL = LEVEL_ROOT + '/Damnation/Arena_Damnation_2026_P'
PALETTE = '/Game/Community/MapEditorPalette'
MARKERS = {
    'T3Spawn1': 'Blue Spawn',
    'T3Spawn2': 'Red Spawn',
    'T3Flag1': 'Blue Flag',
    'T3Flag2': 'Red Flag',
}
ROUTE = {'T3RouteStart': 'Route Start', 'T3RouteEnd': 'Route End'}
LEVELS = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
ASSETS = unreal.EditorAssetLibrary
SAVING = unreal.EditorLoadingAndSavingUtils
EDITOR = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def world():
    return EDITOR.get_editor_world()


def draft_id():
    path = world().get_path_name().split('.')[0]
    match = re.fullmatch(LEVEL_ROOT + r'/(Draft[A-Za-z0-9_]{1,34})/Arena_\1_P', path)
    if not match:
        raise ValueError('Open a draft created with New Blank CTF or Copy Damnation (2026).')
    return match[1]


def new_identity(prefix, *, reserved=()):
    for number in range(1, 1000):
        ident = f'{prefix}{number:03d}'
        level = f'{LEVEL_ROOT}/{ident}/Arena_{ident}_P'
        if (ident not in reserved and not ASSETS.does_asset_exist(level)
                and not ASSETS.does_asset_exist(f'/Game/Community/{ident}/Terrain')):
            return ident, level
    raise ValueError('All 999 draft names for this template are in use.')


def marker(kind, location, yaw=0):
    actor = ACTORS.spawn_actor_from_class(unreal.TargetPoint, unreal.Vector(*location), unreal.Rotator(yaw=yaw))
    actor.set_editor_property('tags', [kind])
    actor.set_actor_label((MARKERS | ROUTE)[kind])
    actor.set_folder_path('TRIBES CTF')
    return actor


def pbr_samples():
    return (
        ('Color', 'RGB', unreal.MaterialProperty.MP_BASE_COLOR, None),
        ('Normal', 'RGB', unreal.MaterialProperty.MP_NORMAL, unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL),
        ('Surface', 'R', unreal.MaterialProperty.MP_ROUGHNESS, unreal.MaterialSamplerType.SAMPLERTYPE_MASKS),
    )


def palette_sample(material, surface, suffix, sampler, x, y, *, parameter_name=None):
    name = f'T_{surface}{suffix}'
    package = f'/Game/Community/Damnation2026PolyHaven/{name}'
    texture = ASSETS.load_asset(package) if ASSETS.does_asset_exist(package) else None
    if texture is None and os.environ.get('T3_EDITOR_STARTER_PALETTE'):
        texture = starter_texture(name, suffix)
    if texture is None:
        raise ValueError(f'The palette is missing the {surface.lower()} {suffix.lower()} texture.')
    node = (unreal.MaterialExpressionTextureSampleParameter2D if parameter_name
            else unreal.MaterialExpressionTextureSample)
    expression = unreal.MaterialEditingLibrary.create_material_expression(material, node, x, y)
    if parameter_name:
        expression.set_editor_property('parameter_name', parameter_name)
    expression.set_editor_property('texture', texture)
    if sampler is not None:
        expression.set_editor_property('sampler_type', sampler)
    return expression


def starter_texture(name, suffix):
    from texture_batches_unreal import import_texture

    folder = PALETTE + '/StarterTextures'
    package = f'{folder}/{name}'
    if ASSETS.does_asset_exist(package):
        return ASSETS.load_asset(package)
    source = Path(os.environ['T3_EDITOR_STARTER_PALETTE']) / (name + '.png')
    if not source.is_file():
        raise ValueError(f'The starter palette is missing {name}.png.')
    texture = import_texture(source, folder, unreal.Texture2D)
    if suffix == 'Normal':
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
    elif suffix == 'Surface':
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_MASKS)
    texture.set_editor_property('srgb', suffix == 'Color')
    if not ASSETS.save_loaded_asset(texture):
        raise ValueError(f'Could not save the starter palette texture {name}.')
    return texture


def materials(*, rebuild=False):
    for surface in PALETTE_SURFACES:
        name = 'M_' + surface
        folder = PALETTE + '/Materials'
        path = folder + '/' + name
        exists = ASSETS.does_asset_exist(path)
        if exists and not rebuild:
            continue
        lib = unreal.MaterialEditingLibrary
        if exists:
            material = ASSETS.load_asset(path)
            lib.delete_all_material_expressions(material)
        else:
            material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                name, folder, unreal.Material, unreal.MaterialFactoryNew())
        for index, (suffix, output, prop, sampler) in enumerate(pbr_samples()):
            expression = palette_sample(material, surface, suffix, sampler, -350, index*260)
            lib.connect_material_property(expression, output, prop)
        lib.recompile_material(material)
        if not ASSETS.save_loaded_asset(material):
            raise ValueError(f'Could not save {name}.')


def configure_world(ident, size):
    if os.environ.get('T3_EDITOR_AUTHORING_ONLY') == '1':
        return
    settings = unreal.CommunityWorldSettingsLibrary.convert_world_settings(world(), size, ident)
    if not settings:
        raise ValueError('Could not create the native TRIBES world settings.')


def save_draft(ident):
    if not LEVELS.save_current_level():
        raise ValueError('Could not save the CTF draft.')
    Path(os.environ['T3_EDITOR_LAST_DRAFT']).write_text(ident+'\n', encoding='utf-8')
    unreal.log(f'T3_MAP_EDITOR_CREATED|{ident}')


def create_blank():
    if not SAVING.save_dirty_packages_with_dialog(True, True):
        return
    ident, level = new_identity('DraftCTF')
    if not LEVELS.new_level(level):
        raise ValueError('Could not create the blank CTF level.')
    configure_world(ident, 40000)
    materials()
    floor = ACTORS.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, -100))
    mesh = floor.static_mesh_component
    mesh.set_static_mesh(ASSETS.load_asset('/Engine/BasicShapes/Cube'))
    mesh.set_material(0, ASSETS.load_asset(PALETTE + '/Materials/M_Grass'))
    floor.set_actor_scale3d(unreal.Vector(400, 400, 2))
    floor.set_actor_label('Starter Ground')
    ACTORS.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1800), unreal.Rotator(pitch=-45, yaw=25))
    ACTORS.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 1500))
    ACTORS.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
    for team, x, yaw in ((1, -11500, 0), (2, 11500, 180)):
        for y in (-500, 0, 500):
            marker(f'T3Spawn{team}', (x, y, 160), yaw)
        marker(f'T3Flag{team}', (x * 1.2, 0, 0), yaw)
    save_draft(ident)
    EDITOR.set_level_viewport_camera_info(unreal.Vector(0, -9800, 6200), unreal.Rotator(pitch=-30, yaw=90))


def copy_damnation():
    if not os.environ.get('T3_EDITOR_SEED'):
        raise ValueError('Install the optional Damnation template and configure its descriptor to copy it.')
    if not SAVING.save_dirty_packages_with_dialog(True, True):
        return
    ident, level = new_identity('DraftDamnation')
    source = json.loads(Path(os.environ['T3_EDITOR_SEED']).read_text(encoding='utf-8'))
    if not LEVELS.new_level_from_template(level, SOURCE_LEVEL):
        raise ValueError('Could not copy the Damnation (2026) level.')
    configure_world(ident, source['size'])
    for team, x, y, z in source['starts']:
        spawn = inherited_spawn(x, y, z)
        if spawn is None:
            raise ValueError(f'A copied team {team} spawn has no clear nearby floor at {x:.0f}, {y:.0f}, {z:.0f}.')
        marker(f'T3Spawn{team}', (spawn.x, spawn.y, spawn.z))
        if (spawn.x, spawn.y, spawn.z) != (x, y, z):
            unreal.log(f'T3_MAP_EDITOR_SPAWN_CLEARANCE|team={team}|from={x},{y},{z}|to={spawn.x},{spawn.y},{spawn.z}')
    for team, x, y, z in source['flags']:
        marker('T3Flag1' if team == 'TeamA' else 'T3Flag2', (x, y, z))
    save_draft(ident)


def browse(folder):
    roots = {'meshes': ('/Game/Community', {'StaticMesh'}),
             'palette': (PALETTE+'/Materials', {'Material'}),
             'surfaces': ('/Game/Community', {'Material', 'MaterialInstanceConstant', 'Texture2D', 'TextureCube'})}
    root, classes = roots[folder]
    if folder == 'palette':
        materials()
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    paths = [str(data.package_name) for data in registry.get_assets_by_path(root, recursive=True)
             if str(data.asset_class_path.asset_name) in classes]
    if not paths:
        raise ValueError('There are no assets in this palette.')
    ASSETS.sync_browser_to_objects(paths)


def add_marker(kind):
    draft_id()
    selected = ACTORS.get_selected_level_actors()
    if selected:
        location = selected[0].get_actor_location()
        location.z += 150
    else:
        view = EDITOR.get_level_viewport_camera_info()
        if view is None:
            raise ValueError('Click a level viewport or select an actor before placing a marker.')
        camera, rotation = view
        location = camera + rotation.get_forward_vector() * 800
    marker(kind, (location.x, location.y, location.z))


def capsule_hit(start, end, ignore=()):
    hit = unreal.SystemLibrary.capsule_trace_single(
        world(), start, end, 42, 96, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True, list(ignore), unreal.DrawDebugTrace.NONE, True)
    return hit.to_tuple() if hit and hit.to_tuple()[0] else None


def inherited_spawn(x, y, z):
    point = unreal.Vector(x, y, z)
    if not capsule_hit(point, point + unreal.Vector(0, 0, 2)):
        return point
    directions = ((0,1), (0,-1), (1,0), (-1,0), (0.707,0.707), (0.707,-0.707), (-0.707,0.707), (-0.707,-0.707))
    offsets = [(0,0)] + [(r*dx,r*dy) for r in (150,300,500,800,1200) for dx,dy in directions]
    for dx, dy in offsets:
        above = unreal.Vector(x+dx, y+dy, z+320)
        below = unreal.Vector(x+dx, y+dy, z-480)
        result = unreal.SystemLibrary.line_trace_single(
            world(), above, below, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [],
            unreal.DrawDebugTrace.NONE, True)
        if not result:
            continue
        hit = result.to_tuple()
        if hit[0] and hit[7].z >= 0.55:
            point = hit[5] + unreal.Vector(0, 0, 108)
            if not capsule_hit(point, point + unreal.Vector(0, 0, 2)):
                return point
    return None


def marker_snapshot():
    ident = draft_id()
    rows = {kind: [] for kind in MARKERS}
    routes = {kind: [] for kind in ROUTE}
    actors = ACTORS.get_all_level_actors()
    perimeter_markers = []
    flags = []
    for actor in actors:
        if not isinstance(actor, unreal.TargetPoint):
            continue
        kinds = [str(tag) for tag in actor.tags if str(tag) in MARKERS or str(tag) in ROUTE]
        if not kinds:
            continue
        if len(kinds) != 1:
            raise ValueError('Each CTF marker must have one team and role.')
        kind = kinds[0]
        point = actor.get_actor_location()
        if kind in ROUTE:
            routes[kind].append((point, actor))
            continue
        if kind.startswith('T3Spawn'):
            end = point + unreal.Vector(0, 0, 2)
            if capsule_hit(point, end, [actor]):
                raise ValueError(f'{actor.get_actor_label()} intersects geometry. Move it clear of the ground or wall.')
        rows[kind].append([point.x, point.y, point.z, actor.get_actor_rotation().yaw])
        perimeter_markers.append((actor.get_actor_label(), (point.x, point.y, point.z)))
        if kind.startswith('T3Flag'):
            flags.append(perimeter_markers[-1])
    if any(not 1 <= len(rows[f'T3Spawn{team}']) <= 24 or len(rows[f'T3Flag{team}']) != 1 for team in (1, 2)):
        raise ValueError('Each team needs one flag and between one and 24 spawns.')
    hard_perimeter = verify_walls(world(), actors, perimeter_markers)
    verify_flag_bounds(actors, flags, hard_perimeter)
    from perimeter_warning_unreal import verify_warning

    verify_warning(actors, ident)
    if any(routes.values()):
        if any(len(points) != 1 for points in routes.values()):
            raise ValueError('A route check needs one Route Start and one Route End.')
        (start, first), = routes['T3RouteStart']
        (end, second), = routes['T3RouteEnd']
        hit = capsule_hit(start, end, [first, second])
        if hit:
            obstruction = hit[9]
            raise ValueError(f'The marked route is blocked by {obstruction.get_actor_label()}.')
    return {'map': ident, 'markers': rows}


def host_equipment(ident):
    from equipment_unreal import APPROVED, review

    actors = ACTORS.get_all_level_actors()
    approved = [actor for actor in actors if APPROVED in map(str, actor.tags)]
    if approved:
        if os.environ.get('T3_EDITOR_EQUIPMENT_EXPORT') != '1':
            raise ValueError('Set direct_host_equipment_adapter in the map editor launcher config.')
        return review(world(), approved, ident, interiors=actors)


def write_host_equipment(destination, equipment):
    source_path = destination / 'equipment.json'
    review_path = destination / 'equipment-review.json'
    if equipment is None:
        source_path.unlink(missing_ok=True)
        review_path.unlink(missing_ok=True)
    else:
        source, review_data = equipment
        source_path.write_bytes(source)
        review_path.write_text(json.dumps(review_data), encoding='utf-8')


def write_host_boundary_features(draft, directory):
    from conversion_rules import write
    from perimeter import FLAG_CAPS, FLAG_REQUIREMENTS, FLAG_RETURN_TIMER

    tags = {str(tag) for actor in ACTORS.get_all_level_actors() for tag in actor.tags}
    features = []
    if tags.intersection(FLAG_CAPS.values()):
        features.append('flag-height-limits')
    if FLAG_REQUIREMENTS[FLAG_RETURN_TIMER] in tags:
        features.append('flag-return-timer')
    path = directory/'host-boundary-features.json'
    if features:
        write(path, {'map': draft, 'features': features})
    else:
        path.unlink(missing_ok=True)


def request_build(kind='playtest'):
    if kind == 'playtest' and os.environ.get('T3_EDITOR_PLAYTEST') == '0':
        raise ValueError('Configure the offline playtest paths before building a game preview.')
    payload = marker_snapshot()
    if kind == 'host' and not imported_draft(payload['map']):
        raise ValueError('Open a draft made with Import Mission or Intake.')
    if kind == 'host' and os.environ.get('T3_EDITOR_HOST_EXPORT') != '1':
        raise ValueError('Set direct_host_exporter in the map editor launcher config.')
    equipment = host_equipment(payload['map']) if kind == 'host' else None
    if not SAVING.save_dirty_packages(True, True):
        raise ValueError('Unreal could not save all map and asset edits.')
    request = Path(os.environ['T3_EDITOR_HOST_REQUEST' if kind == 'host' else 'T3_EDITOR_REQUEST'])
    if request.exists():
        raise ValueError('This editor run already submitted a build.')
    record_build_rules(payload['map'], request.parent)
    if kind == 'host':
        write_host_equipment(request.parent, equipment)
        write_host_boundary_features(payload['map'], request.parent)
    write_outside_audio(payload['map'], request.parent)
    temporary = request.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream)
    temporary.rename(request)
    unreal.log(f'T3_MAP_EDITOR_BUILD_REQUEST|{payload["map"]}')
    unreal.SystemLibrary.quit_editor()


def write_outside_audio(draft, directory):
    from outside_audio import MAX_PAYLOAD, OUTSIDE_TAG, PAYLOAD_FILE, ROOM_TAG, draft_payload, room_tag

    rooms, tracks = {}, set()
    for actor in ACTORS.get_all_level_actors():
        for tag in map(str, actor.tags):
            source = re.fullmatch(rf'{OUTSIDE_TAG}([0-9]+)', tag)
            if source:
                source_id = int(source[1])
                component = actor.get_component_by_class(unreal.AudioComponent)
                if source_id in tracks or not component or not component.get_editor_property('sound'):
                    raise ValueError(f'Outside-only emitter {source_id} has a duplicate tag or no sound.')
                tracks.add(source_id)
            room = re.fullmatch(rf'{ROOM_TAG}([0-9]+)_[0-9a-f]{{16}}', tag)
            if room:
                source_id = int(room[1])
                component = actor.get_component_by_class(unreal.StaticMeshComponent)
                mesh = component.get_editor_property('static_mesh') if component else None
                if source_id in rooms or not mesh or tag != room_tag(source_id, mesh.get_path_name()):
                    raise ValueError(f'Acoustic room {source_id} has a duplicate tag or a different source mesh.')
                rooms[source_id] = tag
    if tracks:
        library = Path(os.environ['T3_CONVERTER_LIBRARY'])
        data = json.dumps(draft_payload(library, draft, rooms, tracks), separators=(',', ':'),
                          allow_nan=False).encode()
        if len(data) > MAX_PAYLOAD:
            raise ValueError('The outside-only audio data exceeds the playtest package limit.')
        destination = directory / PAYLOAD_FILE
        temporary = destination.with_suffix('.tmp')
        temporary.write_bytes(data)
        temporary.replace(destination)
    else:
        (directory / PAYLOAD_FILE).unlink(missing_ok=True)


def build_playtest():
    request_build('playtest')


def import_t2():
    from import_intake import choose
    return choose()


def list_drafts():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    pattern = re.compile(LEVEL_ROOT + rf'/({ANY_DRAFT})/Arena_\1_P')
    return sorted({match[1] for data in registry.get_assets_by_path(LEVEL_ROOT, recursive=True)
                   if (match := pattern.fullmatch(str(data.package_name)))})


def batch_rebuild():
    import sys

    from batch_rebuild_unreal import choose_and_run
    return choose_and_run(sys.modules[__name__])


def open_docked_review():
    from dock_review import review_for_existing_draft
    from draft_audio import placed_audio_ids
    from mission_lighting import placed_command_ids

    if os.environ.get('T3_EDITOR_MAP_REVIEW') != '1':
        return request_review_install()
    library = Path(os.environ['T3_CONVERTER_LIBRARY'])
    match = re.search(rf'/Maps/Community/({IMPORT_DRAFT})/Arena_\1_P\.', world().get_path_name())
    if match and not (library/'DraftReviews'/f'{match[1]}.json').is_file():
        actors = ACTORS.get_all_level_actors()
        review_for_existing_draft(library, match[1], placed_audio=placed_audio_ids(actors),
                                  placed_lights=placed_command_ids(actors))
    unreal.SystemLibrary.execute_console_command(world(), 'T3.MapReview')


def request_review_install():
    destination = os.environ.get('T3_EDITOR_REVIEW_REQUEST')
    if destination is None:
        raise ValueError('Open this workspace with the TRIBES Map Editor launcher to install Object Review.')
    request = Path(destination)
    if request.exists():
        raise ValueError('This editor run already requested an Object Review installation.')
    choice = unreal.EditorDialog.show_message(
        'Install Docked Object Review',
        'Save your edits and close Unreal to install Object Review. The launcher will reopen your draft when installation finishes.',
        unreal.AppMsgType.YES_NO,
    )
    if choice != unreal.AppReturnType.YES:
        return
    if not SAVING.save_dirty_packages(True, True):
        raise ValueError('Unreal could not save all map and asset edits.')
    try:
        draft = draft_id()
    except ValueError:
        draft = None
    temporary = request.with_suffix('.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump({'draft': draft}, stream)
    temporary.rename(request)
    unreal.SystemLibrary.quit_editor()


def show_report(name):
    import subprocess

    script = Path(os.environ['T3_CONVERTER_TOOL']) / {
        'pipeline': 'pipeline_window.py', 'releases': 'release_window.py', 'profiles': 'profile_window.py',
    }[name]
    argv = [os.environ['T3_CONVERTER_PYTHON'], '-E', '-X', 'utf8', str(script), os.environ['T3_CONVERTER_LIBRARY']]
    match = re.search(r'/Maps/Community/(Draft[A-Za-z0-9_]{1,34})/', world().get_path_name())
    if match:
        argv += ['--draft', match[1]]
    if name == 'releases' and os.environ.get('T3_EDITOR_RELEASE_TOOL'):
        argv += ['--release-tool', os.environ['T3_EDITOR_RELEASE_TOOL']]
    result = subprocess.run(argv, capture_output=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-700:] or 'The map editor report window failed.')


def record_build_rules(draft, destination):
    from conversion_rules import draft_snapshot, write

    library = os.environ.get('T3_CONVERTER_LIBRARY')
    snapshot = draft_snapshot(Path(library), draft) if library else None
    tags = [tag for actor in ACTORS.get_all_level_actors() for tag in map(str, actor.tags)
            if tag.startswith('T3RuleSet_')]
    if not tags and snapshot is None:
        return
    if snapshot is None or tags != ['T3RuleSet_' + snapshot['sha256']]:
        raise ValueError('The draft and its recorded conversion rules no longer match.')
    write(destination / 'conversion-rules.json', snapshot)


def rebuild_t2(mode):
    from contact_sheets_unreal import installed_intake
    from conversion_rules import draft_snapshot
    from import_intake import import_map

    draft = draft_id()
    library = Path(os.environ['T3_CONVERTER_LIBRARY'])
    snapshot = draft_snapshot(library, draft)
    intake = installed_intake(draft)
    if snapshot is None or intake is None:
        raise ValueError('This draft has no recorded conversion rules and verified source intake.')
    return import_map(intake, **snapshot['options'], recorded_rules=snapshot if mode == 'recorded' else None)


def texture_batch(operation):
    from texture_batches_unreal import export_selected, review_and_import
    draft = draft_id()
    if operation == 'export':
        return export_selected(draft)
    return review_and_import(draft, ACTORS.get_all_level_actors)


def apply_sky():
    from draft_sky import apply_selected_sky
    return apply_selected_sky(draft_id(), ACTORS)


def open_model_showcase(requested):
    import sys

    from draft_showcase import open_companion
    open_companion(sys.modules[__name__], requested)


def shared_model_library(operation):
    import sys

    from model_library import open_library, register_selected, return_to_draft
    editor = sys.modules[__name__]
    if operation == 'add':
        return register_selected(editor)
    if operation == 'open':
        return open_library(editor)
    return return_to_draft(editor, place=operation == 'place')


def refresh_contact_sheets():
    import sys

    from contact_sheets_unreal import export_report, installed_intake
    draft = draft_id()
    if not SAVING.save_dirty_packages_with_dialog(True, True):
        return
    result = export_report(sys.modules[__name__], draft, intake=installed_intake(draft))
    unreal.EditorDialog.show_message('Map contact sheets saved', str(result), unreal.AppMsgType.OK)
    return result


def import_t2_interior():
    from draft_interiors import import_interior
    return import_interior()


def dts_preview(operation):
    import sys

    from dts_preview_unreal import show_selected, stop
    if operation == 'stop':
        return stop()
    return show_selected(sys.modules[__name__])


def dts_bake(remove):
    import sys

    from dts_bake_unreal import selected_action
    return selected_action(sys.modules[__name__], remove)


def capture_interior_lighting():
    from lighting_preview_unreal import capture_viewport

    folder = capture_viewport(draft_id(), EDITOR, ACTORS)
    unreal.EditorDialog.show_message('Interior lighting previews', str(folder), unreal.AppMsgType.OK)


def lighting_camera(operation):
    from lighting_preview_unreal import capture_saved_cameras, frame_selected_camera, save_viewport_camera, saved_cameras

    draft = draft_id()
    if operation == 'save':
        return save_viewport_camera(EDITOR, ACTORS)
    if operation == 'frame':
        return frame_selected_camera(EDITOR, ACTORS)
    cameras = saved_cameras(ACTORS, selected_only=True) or saved_cameras(ACTORS)
    folder = capture_saved_cameras(draft, world(), ACTORS, cameras)
    unreal.EditorDialog.show_message('Saved lighting previews', str(folder), unreal.AppMsgType.OK)
    return folder


def set_interior_circuit(linked):
    from equipment_unreal import set_interior_link

    draft_id()
    set_interior_link(ACTORS.get_selected_level_actors(), linked)


def set_equipment_approval(approved):
    from equipment_unreal import set_approval

    set_approval(world(), ACTORS.get_selected_level_actors(), draft_id(), approved)


def set_shield_power(setting):
    from equipment_unreal import set_shield_settings

    draft_id()
    set_shield_settings(ACTORS.get_selected_level_actors(), setting)


def create_inventory_station_blueprint():
    if not hasattr(unreal, 'TopInventoryStation'):
        raise ValueError('Install the inventory station authoring plugin for this Unreal Editor build.')
    current = world()
    level = current.get_path_name().split('.')[0] if current else ''
    draft = re.fullmatch(LEVEL_ROOT + rf'/({T2_DRAFT})/Arena_\1_P', level)
    path = inventory_station_package(draft[1]) if draft else INVENTORY_STATION_TEMPLATE
    if not ASSETS.does_asset_exist(path):
        if draft and ASSETS.does_asset_exist(INVENTORY_STATION_TEMPLATE):
            asset = ASSETS.duplicate_asset(INVENTORY_STATION_TEMPLATE, path)
        else:
            folder, name = path.rsplit('/', 1)
            factory = unreal.BlueprintFactory()
            factory.set_editor_property('parent_class', unreal.TopInventoryStation.static_class())
            library = unreal.AssetToolsHelpers.get_asset_tools()
            asset = library.create_asset(name, folder, unreal.Blueprint, factory)
        if not asset or not ASSETS.save_loaded_asset(asset):
            raise ValueError('Unreal could not save the inventory station Blueprint.')
    generated = ASSETS.load_blueprint_class(path)
    if not generated or not isinstance(unreal.get_default_object(generated), unreal.TopInventoryStation):
        raise ValueError('The inventory station path already contains an incompatible asset.')
    if os.environ.get('T3_EDITOR_INTERACTIVE') == '1':
        ASSETS.sync_browser_to_objects([path])
    return generated


def action(name, argument=None):
    commands = {'blank': create_blank, 'copy': copy_damnation, 'build': request_build,
                'validate': marker_snapshot, 'browse': browse, 'marker': add_marker, 'import_t2': import_t2,
                'texture_batch': texture_batch, 'apply_sky': apply_sky, 'import_interior': import_t2_interior,
                'approve_equipment': set_equipment_approval, 'capture_lighting': capture_interior_lighting,
                'interior_circuit': set_interior_circuit, 'shield_power': set_shield_power,
                'model_showcase': open_model_showcase,
                'model_library': shared_model_library, 'contact_sheets': refresh_contact_sheets,
                'dts_preview': dts_preview, 'dts_bake': dts_bake,
                'lighting_camera': lighting_camera, 'rebuild_t2': rebuild_t2, 'batch_rebuild': batch_rebuild,
                'report': show_report, 'docked_review': open_docked_review,
                'inventory_station': create_inventory_station_blueprint}
    try:
        if name in {'blank', 'copy', 'build', 'import_t2', 'rebuild_t2', 'batch_rebuild', 'model_showcase', 'model_library'}:
            from dts_preview_unreal import stop
            stop()
        result = commands[name]() if argument is None else commands[name](argument)
        if name == 'validate':
            unreal.EditorDialog.show_message('CTF markers', 'Flags, spawns and the marked route passed the checks.', unreal.AppMsgType.OK)
        return result
    except Exception as exc:
        unreal.log_error(f'T3 Map Editor: {exc}')
        unreal.EditorDialog.show_message('TRIBES Map Editor', str(exc), unreal.AppMsgType.OK)


def register():
    menus = unreal.ToolMenus.get()
    parent = menus.find_menu('LevelEditor.MainMenu')
    if parent is None:
        raise RuntimeError('Unreal did not register the Level Editor main menu.')
    menu = parent.add_sub_menu('LevelEditor.MainMenu', '', 'TribesMapEditor', 'TRIBES Map Editor')
    shields = menu.add_sub_menu('LevelEditor.MainMenu.TribesMapEditor', 'TribesTools', 'ForceShields', 'Force Shields')
    items = [
        ('NewBlank', 'New Blank CTF Map', 'blank', None),
        ('CopyMap', 'Copy Damnation (2026)', 'copy', None),
        ('ImportT2', 'Import Mission or Intake…', 'import_t2', None),
        ('RebuildRecorded', 'Create New Draft with Recorded Rules', 'rebuild_t2', 'recorded'),
        ('RebuildLatest', 'Create New Draft with Latest Rules', 'rebuild_t2', 'latest'),
        ('BatchRebuild', 'Batch Rebuilds…', 'batch_rebuild', None),
        ('Pipeline', 'Conversion Pipeline…', 'report', 'pipeline'),
        ('GameProfiles', 'Game Profiles…', 'report', 'profiles'),
        ('MapReleases', 'Map Releases…', 'report', 'releases'),
        ('ObjectReview', 'Docked Object Review', 'docked_review', None),
        ('ImportInterior', 'Import Tribes 2 Interior…', 'import_interior', None),
        ('PreviewDTS', 'Preview Selected DTS Animation…', 'dts_preview', 'open'),
        ('StopDTSPreview', 'Stop DTS Preview', 'dts_preview', 'stop'),
        ('BakeDTS', 'Bake Selected DTS Animation…', 'dts_bake', False),
        ('RemoveBakeDTS', 'Remove Baked DTS Animation', 'dts_bake', True),
        ('ModelShowcase', 'Open Model Showcase', 'model_showcase', 'showcase'),
        ('ContactSheets', 'Refresh Map Contact Sheets', 'contact_sheets', None),
        ('ReturnToDraft', 'Return from Showcase to Draft', 'model_showcase', 'draft'),
        ('RegisterModels', 'Add Selected Models to Shared Library', 'model_library', 'add'),
        ('BrowseModels', 'Open Shared Model Library', 'model_library', 'open'),
        ('PlaceModels', 'Place Selected Library Models in Draft', 'model_library', 'place'),
        ('LeaveModels', 'Return from Shared Library to Draft', 'model_library', 'return'),
        ('MapAssets', 'Browse Structures and Props', 'browse', 'meshes'),
        ('MapMaterials', 'Browse Grass, Rock and Forest Materials', 'browse', 'palette'),
        ('MapSurfaces', 'Browse Existing Materials and Textures', 'browse', 'surfaces'),
        ('ExportTextures', 'Export Selected Textures for Repair…', 'texture_batch', 'export'),
        ('ReviewTextures', 'Review and Import Texture Batch…', 'texture_batch', 'review'),
        ('ApplySky', 'Apply Selected Cubemap as Draft Sky', 'apply_sky', None),
        ('CaptureLighting', 'Capture Normal and Alarm Lighting…', 'capture_lighting', None),
        ('SaveLightingCamera', 'Add Lighting Camera from Viewport', 'lighting_camera', 'save'),
        ('FrameLightingCamera', 'Frame Selected Lighting Camera', 'lighting_camera', 'frame'),
        ('RenderLightingCameras', 'Render Saved Lighting Cameras…', 'lighting_camera', 'render'),
        ('LinkLighting', 'Link Interior Lighting to Selected Generator', 'interior_circuit', True),
        ('UnlinkLighting', 'Clear Selected Interior Lighting Links', 'interior_circuit', False),
        ('Validate', 'Check CTF Markers', 'validate', None),
        ('ApproveEquipment', 'Approve Selected Equipment for Direct Host', 'approve_equipment', True),
        ('RemoveEquipmentApproval', 'Remove Selected Equipment Approval', 'approve_equipment', False),
        ('InventoryStation', 'Create Inventory Station Blueprint', 'inventory_station', None),
        ('LinkShields', 'Link Selected Shields to Generator', 'shield_power', 'link'),
        ('UnlinkShields', 'Set Selected Shields to Independent Power', 'shield_power', 'unlink'),
        ('SourceShieldCircuit', 'Restore Source Shield Circuit', 'shield_power', 'source'),
        ('DropShields', 'Shield Power Loss · Drop', 'shield_power', 'drop'),
        ('WeakenShields', 'Shield Power Loss · Weaken', 'shield_power', 'weaken'),
        ('KeepShields', 'Shield Power Loss · Stay Up', 'shield_power', 'stay'),
        ('Build', 'Build & Playtest', 'build', 'playtest'),
        ('BuildForHost', 'Build for Direct Host…', 'build', 'host'),
    ]
    for kind, label in (MARKERS | ROUTE).items():
        items.append((kind, 'Add ' + label, 'marker', kind))
    playtest = os.environ.get('T3_EDITOR_PLAYTEST') != '0'
    for key, label, operation, argument in items:
        if (key == 'CopyMap' and not os.environ.get('T3_EDITOR_SEED')
                or key == 'Build' and not playtest
                or key == 'InventoryStation' and not hasattr(unreal, 'TopInventoryStation')
                or key == 'BuildForHost' and os.environ.get('T3_EDITOR_HOST_EXPORT') != '1'):
            continue
        entry = unreal.ToolMenuEntry(name=key, type=unreal.MultiBlockType.MENU_ENTRY)
        entry.set_label(label)
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, '',
                                 f'import t3_map_editor; t3_map_editor.action({operation!r}, {argument!r})')
        target = shields if operation == 'shield_power' else menu
        target.add_menu_entry('TribesTools', entry)
    if playtest:
        toolbar = menus.extend_menu('LevelEditor.LevelEditorToolBar.User')
        build = unreal.ToolMenuEntry(name='TribesBuildPlaytest', type=unreal.MultiBlockType.TOOL_BAR_BUTTON)
        build.set_label('Build & Playtest')
        build.set_tool_tip('Save this draft, cook it, and open a private TRIBES 3 game.')
        build.set_icon('AppStyle', 'PlayWorld.PlayInViewport')
        build.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, '',
                                 "import t3_map_editor; t3_map_editor.action('build', 'playtest')")
        toolbar.add_menu_entry('TribesTools', build)
    menus.refresh_all_widgets()
    unreal.log('T3_MAP_EDITOR_MENU_READY')


def schedule_startup():
    def ready(_):
        if unreal.ToolMenus.get().find_menu('LevelEditor.MainMenu') is None or world() is None:
            return
        unreal.unregister_slate_post_tick_callback(handle)
        register()
        if os.environ.get('T3_EDITOR_INITIAL') == 'blank':
            action('blank')
    handle = unreal.register_slate_post_tick_callback(ready)
