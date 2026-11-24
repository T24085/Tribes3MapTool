"""Open the local TRIBES editor and hand saved drafts to an isolated game."""
import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import uuid


TOOL = Path(__file__).resolve().parent
REPO = TOOL.parents[1]
sys.path[:0] = [str(REPO), str(REPO/'host/lib')]
from native_resource_gate import NativeResourceBusy, native_resources
from owned_native_session import run_owned_job
from tools.community_map_editor.source_profiles import ANY_DRAFT, BRUSH_PREVIEW_FOLDER, IMPORT_DRAFT, imported_draft


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def editor_cache_path(configured, project_dir):
    # UE 5.3's file-system cache reserves room for the generated cache keys.
    path = (project_dir / configured).resolve()
    if len(str(path).encode('utf-16-le')) // 2 < 119:
        return path
    local = os.environ.get('LOCALAPPDATA')
    if local:
        key = hashlib.sha256(os.path.normcase(str(path)).encode('utf-8')).hexdigest()[:12]
        fallback = Path(local).resolve() / 'Tribes3MapTool' / 'DDC' / key
        if len(str(fallback).encode('utf-16-le')) // 2 < 119:
            fallback.mkdir(parents=True, exist_ok=True)
            print(f'Using a shorter Unreal cache directory: {fallback}', flush=True)
            return fallback
    raise ValueError('Set ddc in local-settings.json to an absolute path shorter than 119 characters.')


def read_json(path, limit=2*1024*1024):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'{path.name} exceeds its size limit.')
    return json.loads(raw)


def validate_id(ident):
    if not isinstance(ident, str) or not re.fullmatch(ANY_DRAFT, ident):
        raise ValueError('Select a draft created with the TRIBES Map Editor.')
    return ident


def build_request(raw):
    if not isinstance(raw, dict) or set(raw) != {'map', 'markers'}:
        raise ValueError('Invalid draft build request.')
    ident = validate_id(raw['map'])
    markers = raw['markers']
    if not isinstance(markers, dict) or set(markers) != {'T3Spawn1', 'T3Spawn2', 'T3Flag1', 'T3Flag2'}:
        raise ValueError('Invalid CTF markers.')
    for key, points in markers.items():
        cap = 24 if key.startswith('T3Spawn') else 1
        if not isinstance(points, list) or not 1 <= len(points) <= cap:
            raise ValueError('Each side requires spawn markers and exactly one flag.')
        for point in points:
            if not isinstance(point, list) or len(point) != 4 or any(
                type(n) not in (int, float) or not -2_000_000 <= n <= 2_000_000 or not math.isfinite(n)
                for n in point):
                raise ValueError('Invalid CTF marker coordinates.')
    return ident, markers


def package_path(ident):
    return f'/Game/Maps/Community/{ident}/Arena_{ident}_P'


def install_editor_scripts(project, env, *, include_seed=True):
    folder = project.parent/'Content/Python'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TOOL/'editor.py', folder/'t3_map_editor.py')
    for name in ('intake_contract.py', 'import_intake.py', 'mesh_mappings.py', 'perimeter.py', 'perimeter_collision.py',
                 'polygon_walls_unreal.py', 'perimeter_visual.py', 'perimeter_warning_unreal.py',
                 'terrain_layers.py', 'native_terrain_paint.py', 'native_terrain_material_unreal.py',
                 'texture_batches.py', 'texture_batches_unreal.py',
                 'draft_materials.py',
                 'sky_contract.py', 'sky_intake.py', 'draft_sky.py', 'draft_clouds.py', 'draft_precipitation.py',
                 'draft_interiors.py', 'draft_movers.py', 'vengeance_mover_plan.py', 'unreal_legacy_reader.py',
                 'draft_elevators.py', 'vengeance_elevators.py',
                 'interior_geometry.py', 't1_dts_motion.py', 'interior_lighting.py',
                 'lighting_preview_unreal.py', 'dts_preview.py', 'dts_preview_unreal.py', 'dts_bake_unreal.py',
                 'model_textures.py', 'draft_model_materials.py', 'mission_equipment.py', 'mission_weather.py',
                 'equipment_unreal.py', 'source_library.py', 'draft_showcase.py', 'model_library.py',
                 'contact_sheets_unreal.py', 'conversion_rules.py', 'tribes2_conversion.json', 'profile_exchange.py',
                 'batch_rebuild.py', 'batch_rebuild_unreal.py', 'batch_stage.py', 'batch_stage_unreal.py',
                 'minimap.py', 'map_pipeline.py', 'tribes2_pipeline.json', 'object_catalog.py', 'tribes2_catalog.json',
                 'dock_review.py', 'source_profiles.py', 'terrain_grid.py', 'terrain_art.py'):
        shutil.copyfile(TOOL/name, folder/name)
    env['T3_CONVERTER_TOOL'] = str(TOOL)
    env['T3_CONVERTER_PYTHON'] = sys.executable
    env['T3_CONVERTER_LIBRARY'] = str((project.parent/'Saved/T3MapIntakes').resolve())
    env['T3_TEXTURE_LIBRARY'] = str((project.parent/'Saved/T3TextureBatches').resolve())
    (folder/'init_unreal.py').write_text(
        'import os\nimport unreal\n'
        "if os.environ.get('T3_EDITOR_INTERACTIVE') == '1':\n"
        '    import t3_map_editor\n'
        '    t3_map_editor.schedule_startup()\n',
        encoding='utf-8')
    if not include_seed:
        return
    env.pop('T3_EDITOR_SEED', None)
    if not env.get('T3_EDITOR_DESCRIPTOR'):
        return
    source = read_json(Path(env['T3_EDITOR_DESCRIPTOR']))['maps']['Damnation2026']
    seed = project.parent/'t3_damnation_seed.json'
    seed.write_text(json.dumps(dict(size=source['map_size_cm'], **source['objectives'])), encoding='utf-8')
    env['T3_EDITOR_SEED'] = str(seed)


def cook(project, engine, package, out, env, *, plugins=None, cancellation_path=None):
    content = out/'cook/Windows'/project.stem/'Content'
    args = [str(engine/'UnrealEditor-Cmd.exe'), str(project), '-run=Cook', '-TargetPlatform=Windows',
            '-CookSinglePackage', '-NoDefaultMaps', '-SkipEditorContent', '-Map='+package,
            '-OutputDir='+str(out/'cook/[Platform]'), '-unattended', '-nop4', '-nullrhi', '-nosound', '-NoCrashDialog',
            '-ini:Engine:[ConsoleVariables]:Editor.AsyncStaticMeshCompilationMaxConcurrency=1',
            '-ini:Engine:[ConsoleVariables]:Editor.AsyncTextureCompilationMaxConcurrency=1',
            '-abslog='+str(out/'cook.log'), *(PLUGINS if plugins is None else plugins)]
    print('Cooking the saved draft and the assets it uses. See', out/'cook.log', flush=True)
    job = run_owned_job(args, project.parent, env, out/'cook-owner', 1600, cancellation_path=cancellation_path)
    active = job.get('job_active_processes_after_cleanup', job.get('job_active_processes_at_parent_exit'))
    if job['status'] != 'terminal' or job.get('exit_code') != 0 or active != 0 or not content.is_dir():
        raise RuntimeError('The Windows cook failed. See ' + str(out/'cook.log'))
    if re.fullmatch(r'/Game/Maps/Community/' + IMPORT_DRAFT + r'/Arena_[A-Za-z0-9_]+_P', package):
        ident = package.split('/')[-2]
        if not all((content/'Community'/ident/('Terrain'+ext)).is_file() for ext in ('.uasset', '.uexp')):
            raise RuntimeError('The imported terrain is missing from the cook. See ' + str(out/'cook.log'))
    with (out/'cook.log').open(encoding='utf-8', errors='replace') as log:
        if any('Failed to compile Material for platform' in line for line in log):
            raise RuntimeError('A material failed to compile. See ' + str(out/'cook.log'))
    return content


def lua(value):
    return json.dumps(value, separators=(',', ':')).replace('[', '{').replace(']', '}')


def package_preview(parent, cooked, target, ident, markers, *, host_export=False):
    target.mkdir()
    manifest = {'files': []} if host_export else read_json(parent/'package-manifest.json')
    records = {}

    def add(name, *, source=None, contents=None):
        rel = PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or ':' in name or '\\' in name or name in records:
            raise ValueError('Invalid or repeated preview inventory path.')
        dest = target.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            dest.write_bytes(contents)
        elif source.drive.casefold() == dest.drive.casefold():
            try:
                os.link(source, dest)
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (1, 50) and exc.errno not in (errno.EXDEV, errno.ENOTSUP):
                    raise
                shutil.copyfile(source, dest)
        else:
            shutil.copyfile(source, dest)
        records[name] = {'path': name, 'bytes': dest.stat().st_size, 'sha256': digest(dest)}

    replacements = {'playtest.py', 'lib/community_maps.py', 'lib/host_catalog_runtime.py', 'server.json'}
    for row in manifest['files']:
        name = row['path']
        if name.startswith('content/') or name in replacements or name == 'local-installation.json':
            continue
        source = (parent/name).resolve(strict=True)
        if not source.is_relative_to(parent):
            raise ValueError('The preview base package contains an escaped file.')
        add(name, source=source)
        if records[name] != row:
            raise ValueError('The private preview base package changed: ' + name)
    if not host_export:
        add('playtest.py', source=REPO/'host/playtest.py')
        for name in ('community_maps.py', 'host_catalog_runtime.py'):
            add('lib/'+name, source=REPO/'host/lib'/name)
        add('server.json', contents=b'{"address":"127.0.0.1","port":7787}\n')

    level = package_path(ident)
    stem = level.rsplit('/', 1)[1]
    path = level.removeprefix('/Game/')
    for suffix in ('.umap', '.uexp'):
        if not (cooked/(path+suffix)).is_file():
            raise ValueError('The cook did not produce both draft level files.')
    shared = {}
    editor_asset_prefix = BRUSH_PREVIEW_FOLDER.removeprefix('/Game/') + '/'
    for source in cooked.rglob('*'):
        rel = source.relative_to(cooked).as_posix()
        if rel.startswith(editor_asset_prefix) or source.suffix.lower() not in (
                '.uasset', '.umap', '.uexp', '.ubulk', '.uptnl'):
            continue
        if rel.startswith('Community/'):
            name = 'content/'+rel
            prefix = '/'.join(name.split('/')[:3])+'/'
            if not prefix.startswith(f'content/Community/{ident}/'):
                shared[prefix] = [ident]
        elif rel.rsplit('.', 1)[0] == path:
            name = 'content/'+rel
        else:
            raise ValueError('A draft dependency must live under Content/Community: ' + rel)
        add(name, source=source)
    if host_export:
        manifest.update(community_maps=[ident], authoring_preview=ident, files=list(records.values()))
        (target/'package-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        return target

    folder = 'lua/'+ident+'/'
    offline = (TOOL/'offline.lua').read_text(encoding='utf-8')
    for token, value in {'DRAFT_WORLD':json.dumps(level+'.'+stem+':'),
                         'DRAFT_STARTS':lua([markers['T3Spawn1'],markers['T3Spawn2']]),
                         'DRAFT_FLAGS':lua([markers['T3Flag1'][0],markers['T3Flag2'][0]]),
                         'DRAFT_COLLISION_FILTER':(REPO/'runtime-fixes/mission_perimeter_collision.lua').read_text(encoding='utf-8'),
                         'DRAFT_FLAG_LIMIT':(REPO/'runtime-fixes/mission_flag_limit.lua').read_text(encoding='utf-8'),
                         'DRAFT_BOUNDARY_WARNING':(REPO/'runtime-fixes/mission_boundary_warning.lua').read_text(encoding='utf-8')}.items():
        offline = offline.replace('@@'+token+'@@', value)
    add(folder+'offline.lua', contents=offline.encode())
    boot = (REPO/'host/lua/Stonehenge/standalone_bootstrap.lua').read_text(encoding='utf-8')
    boot = boot.replace('/Game/Maps/Community/Stonehenge/Arena_Stonehenge_P', level).replace('match_id="Stonehenge"', f'match_id="{ident}"')
    add(folder+'standalone_bootstrap.lua', contents=boot.encode())
    manifest.update(build_id='map-editor-preview', community_maps=[ident], authoring_preview=ident,
                    map_definitions={ident:{'label':ident, 'asset_group':ident, 'level':stem}},
                    map_runtime={ident:[]}, shared_content=shared, files=list(records.values()))
    (target/'package-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return target


PLUGINS = ['-NoEnginePlugins', '-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,'
           'CommunityNativeWorldSettings,TextureFormatOodle,WorldPartitionHLODUtilities,Water,GeometryScripting,'
           'GeometryCache,LevelSequenceEditor,SequencerScripting']
EDITOR_PLUGINS = [PLUGINS[0], PLUGINS[1]+',ContentBrowserAssetDataSource,ContentBrowserClassDataSource']


def editor_plugin_args(project, engine, *, smoke, authoring_only=False):
    args = PLUGINS if smoke else EDITOR_PLUGINS
    enabled = []
    extensions = [('CommunityMapMaterials', 'render rain and snow'),
                  ('CommunityMapMotion', 'bake DTS animation for the game')]
    if not smoke:
        extensions.append(('CommunityMapReview', 'dock the object review'))
    for name, purpose in extensions:
        binaries = project.parent/'Plugins'/name/'Binaries/Win64'
        dll = f'UnrealEditor-{name}.dll'
        if not (binaries/dll).is_file():
            continue
        try:
            plugin = read_json(binaries/'UnrealEditor.modules')
            engine_build = read_json(engine/'UnrealEditor.modules')
            if plugin['Modules'].get(name) == dll and plugin['BuildId'] == engine_build['BuildId']:
                enabled.append(name)
                continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        print(f'Build {name} for this Unreal Editor version to {purpose}.', flush=True)
    plugins = [*args[1].split(','), *enabled]
    if authoring_only:
        plugins.remove('CommunityNativeWorldSettings')
    return [args[0], ','.join(plugins)]


def run_host_export(exporter, attempt, selection, *, equipment_adapter=None):
    args = [sys.executable, '-X', 'utf8', str(exporter), str(attempt), str(selection['host']),
            str(selection['destination']), '--label', selection['label']]
    if selection['minimap'] is not None:
        args += ['--minimap', str(selection['minimap'])]
    if selection.get('replace_existing'):
        args.append('--replace-existing')
    if (attempt/'equipment-review.json').exists() or (attempt/'equipment.json').exists():
        if equipment_adapter is None or not equipment_adapter.is_file():
            raise ValueError('Direct Host equipment needs the configured native equipment adapter.')
        args += ['--equipment-adapter', str(equipment_adapter)]
    result = subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace',
                            cwd=exporter.parent.parent, check=False)
    (attempt/'host-export.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode or not (selection['destination']/'export-receipt.json').is_file():
        raise RuntimeError(result.stderr.strip()[-1000:] or f'Direct Host export failed. See {attempt / "host-export.log"}')
    return selection['destination']


def authored_host_stamp(host):
    """Check the inventoried capability of the exact bundle receiving a stock-editor draft."""
    host = host.resolve(strict=True)
    manifest = host/'package-manifest.json'
    message = 'Choose a verified Direct Host bundle that supports drafts with standard Unreal World Settings.'
    capabilities = {
        'lua/network.lua': (b'native_settings_authored_draft_world_owner_required', b'EDITOR_DRAFT_WORLD_SETTINGS'),
        'lib/host_catalog_runtime.py': (b'.editor_draft=true',),
    }
    if manifest.resolve() != manifest:
        raise ValueError(message)
    with manifest.open('rb') as stream:
        raw = stream.read(8*1024*1024+1)
    if len(raw) > 8*1024*1024:
        raise ValueError(message)
    before = hashlib.sha256(raw).hexdigest()
    header = json.loads(raw)
    rows = header.get('files') if isinstance(header, dict) else None
    if not isinstance(rows, list):
        raise ValueError(message)
    for name, tags in capabilities.items():
        matching = [row for row in rows if isinstance(row, dict) and row.get('path') == name]
        path = host/name
        if len(matching) != 1 or path.resolve() != path or not path.is_file():
            raise ValueError(message)
        with path.open('rb') as stream:
            data = stream.read(2*1024*1024+1)
        row = matching[0]
        if (len(data) > 2*1024*1024 or len(data) != row.get('bytes')
                or hashlib.sha256(data).hexdigest() != row.get('sha256')
                or not all(tag in data for tag in tags)):
            raise ValueError(message)
    if digest(manifest) != before:
        raise ValueError('The Direct Host inventory changed while checking draft compatibility.')
    return before


def run_host_pipeline(project, engine, preview_base, attempt, env, exporter, adapter, draft, selection,
                      *, cancellation_path=None, record_export=True):
    from tools.community_map_editor.conversion_rules import verify_snapshot
    from tools.community_map_editor.map_pipeline import PipelineRun, pipeline_config
    from tools.community_map_editor.map_releases import capture_export

    library = Path(env['T3_CONVERTER_LIBRARY']) if env.get('T3_CONVERTER_LIBRARY') else None
    rule_path = attempt/'conversion-rules.json'
    snapshot = verify_snapshot(read_json(rule_path)) if rule_path.is_file() else None
    profile = pipeline_config(library, snapshot=snapshot, recorded=True)
    run = PipelineRun(profile, 'host', library=library, draft=draft, title=selection['label'])
    markers = cooked = result = base_digest = None
    authoring_only = env.get('T3_EDITOR_AUTHORING_ONLY') == '1'

    def preflight(_):
        nonlocal markers, base_digest
        map_id, markers = build_request(read_json(attempt/'build-request.json', 32768))
        if draft != map_id or not exporter.is_file():
            raise ValueError('The Direct Host build request does not match the draft or the exporter is unavailable.')
        if not (selection['host']/'package-manifest.json').is_file():
            raise ValueError('The Direct Host baseline is missing its package manifest.')
        base_digest = (authored_host_stamp(selection['host']) if authoring_only
                       else digest(selection['host']/'package-manifest.json'))
        if any((attempt/name).is_file() for name in ('equipment-review.json', 'equipment.json')):
            if adapter is None or not adapter.is_file():
                raise ValueError('Direct Host equipment needs the configured native equipment adapter.')

    def cook_draft(_):
        nonlocal cooked
        kwargs = {'cancellation_path': cancellation_path} if cancellation_path is not None else {}
        if authoring_only:
            kwargs['plugins'] = editor_plugin_args(project, engine, smoke=True, authoring_only=True)
        cooked = cook(project, engine, package_path(draft), attempt, env, **kwargs)

    def package(_):
        package_preview(preview_base, cooked, attempt/'preview', draft, markers, host_export=True)

    def stage(_):
        nonlocal result
        if authoring_only and authored_host_stamp(selection['host']) != base_digest:
            raise ValueError('The Direct Host inventory changed during cooking. Prepare a new export.')
        result = run_host_export(exporter, attempt, selection, equipment_adapter=adapter)
        if library is not None and record_export:
            capture_export(library, result, expected_draft=draft, expected_label=selection['label'], expected_base=base_digest)

    def check_minimap(_):
        source = selection['minimap']
        if source is None or not all((source/name).is_file() for name in (
                'map-minimap.json', 'manifest-minimap.json', 'minimap.png')):
            raise ValueError('This conversion profile requires a prepared minimap for Direct Host.')

    run.execute({'preflight': preflight, 'cook': cook_draft, 'package': package, 'stage': stage,
                 'check-minimap': check_minimap})
    return result


def run_stage_batch(project, engine, preview_base, env, exporter, adapter, ident, *, selection=None,
                    cancelled=lambda: False):
    from tools.community_map_editor.batch_stage import (
        baseline_stamp, portable_fingerprint, read_prepared, saved_draft_file, stage_root, stage_settings,
    )
    from tools.community_map_editor.conversion_rules import write
    from tools.community_map_editor.map_releases import capture_export

    root = stage_root(Path(env['T3_CONVERTER_LIBRARY']), ident)
    plan = read_prepared(root, ident)
    if selection is None and not (root/'stage-config.json').is_file():
        from tools.community_map_editor.export_window import choose

        selection = choose(ident, batch_labels=tuple(item['label'] for item in plan['items']))
    settings = stage_settings(root, ident, plan, selection)
    if settings is None:
        return None
    host, destination = Path(settings['host']), Path(settings['destination'])
    cancel_file = root/'cancel.json'
    cancel_file.unlink(missing_ok=True)
    finished, failed = [], []
    print(f'Cooking and staging {len(plan["items"])} maps. Batch details: {root}', flush=True)
    for item in plan['items']:
        if cancelled() or cancel_file.is_file() and read_json(cancel_file, 1024).get('cancel') is True:
            break
        if baseline_stamp(host) != settings['baseline']:
            raise ValueError('The Direct Host baseline changed during staging.')
        draft = item['draft']
        output = destination/f'{draft}-portable'
        checkpoint = root/'completed'/f'{draft}.json'
        try:
            if checkpoint.is_file() and not output.exists():
                raise ValueError('The completed portable output is missing. Restore it before resuming.')
            if not output.exists():
                if digest(saved_draft_file(project, draft)) != item['level_sha256']:
                    raise ValueError('The saved draft changed after staging was prepared.')
                attempt = root/'attempts'/f'{draft}-{uuid.uuid4().hex[:8]}'
                shutil.copytree(root/'prepared'/draft, attempt)
                minimap = attempt/f'{draft}-minimap'
                selection = {'host': host, 'destination': output, 'label': item['label'],
                             'minimap': minimap if minimap.is_dir() else None}
                run_host_pipeline(project, engine, preview_base, attempt, env, exporter, adapter, draft, selection,
                                  cancellation_path=cancel_file, record_export=False)
                if baseline_stamp(host) != settings['baseline']:
                    raise ValueError('The Direct Host baseline changed during export.')
            proof = portable_fingerprint(output, draft, item['label'], settings['baseline']['package-manifest.json'])
            if checkpoint.is_file() and read_json(checkpoint) != proof:
                raise ValueError('The completed portable output changed. Restore its verified files to resume.')
            capture_export(Path(env['T3_CONVERTER_LIBRARY']), output, expected_draft=draft, expected_label=item['label'],
                           expected_base=settings['baseline']['package-manifest.json'], proof=proof)
            write(checkpoint, proof)
            finished.append({'draft': draft, 'folder': output.name, **proof})
            print(f'Staged {draft} at {output}', flush=True)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
            failed.append({'draft': draft, 'reason': str(error)})
            print(f'Batch {draft}: {error}', file=sys.stderr, flush=True)
    summary = {'format': 1, 'batch': ident, 'complete': finished,
               'remaining': [item['draft'] for item in plan['items']
                             if not any(record['draft'] == item['draft'] for record in finished)]}
    write(root/'last-result.json', {**summary, 'errors': failed})
    write(destination/'results.json', summary)
    return summary


def run(config, *, smoke=False):
    project = Path(config['project']).resolve(strict=True)
    engine = Path(config['engine_binaries']).resolve(strict=True)
    root = project.parent.parent
    if project.name != 'Stonehenge53.uproject' or root.name != 'CommunityMapEditor' or project.parent.name != 'project':
        raise ValueError('Select the dedicated CommunityMapEditor project.')
    authoring_only = config.get('authoring_only', False)
    if type(authoring_only) is not bool:
        raise ValueError('authoring_only must be true or false.')
    preview_keys = ('preview_base', 'game_source', 'game_workspace')
    preview = [key for key in preview_keys if config.get(key)]
    if preview and (authoring_only or len(preview) != len(preview_keys)):
        raise ValueError('Offline playtest needs a game-compatible project and all three offline playtest paths.')
    if smoke and not preview:
        raise ValueError('The full smoke test needs an offline playtest configuration.')
    parent, game_source, workspace = (
        (Path(config[key]).resolve(strict=True) for key in preview_keys) if preview else (None, None, None))
    if not authoring_only:
        (project.parent/'.community-worldsettings-authoring').touch()
    out = root/'Runs'/uuid.uuid4().hex[:12]
    out.mkdir(parents=True)
    env = dict(os.environ)
    if config.get('dif_decoder'):
        env['T3_DIF_DECODER'] = str(Path(config['dif_decoder']).resolve())
    if config.get('dif_assets'):
        env['T3_DIF_ASSETS'] = str(Path(config['dif_assets']).resolve())
    for field in ('dts_decoder', 'dts_assets'):
        if config.get(field):
            env['T3_' + field.upper()] = str(Path(config[field]).resolve())
    env['UE-LocalDataCachePath'] = str(editor_cache_path(config['ddc'], project.parent))
    env.pop('T3_EDITOR_DESCRIPTOR', None)
    env.pop('T3_EDITOR_STARTER_PALETTE', None)
    if config.get('starter_palette'):
        env['T3_EDITOR_STARTER_PALETTE'] = str(Path(config['starter_palette']).resolve(strict=True))
    if not authoring_only:
        descriptor = config.get('seed_descriptor')
        if descriptor:
            env['T3_EDITOR_DESCRIPTOR'] = str(Path(descriptor).resolve(strict=True))
        else:
            local_descriptor = REPO.parent.parent/'map-2026-work/runtime-descriptor-v1/maps.json'
            if local_descriptor.is_file():
                env['T3_EDITOR_DESCRIPTOR'] = str(local_descriptor)
    exporter = Path(config.get('direct_host_exporter', REPO/'tools/export_t2_draft.py')).resolve()
    release_tool = exporter.with_name('t2_map_release.py')
    env.pop('T3_EDITOR_RELEASE_TOOL', None)
    if release_tool.is_file():
        env['T3_EDITOR_RELEASE_TOOL'] = str(release_tool)
    adapter = Path(config.get('direct_host_equipment_adapter', exporter.with_name('attach_t2_equipment.py'))).resolve()
    env['T3_EDITOR_AUTHORING_ONLY'] = '1' if authoring_only else '0'
    env['T3_EDITOR_PLAYTEST'] = '1' if preview else '0'
    env['T3_EDITOR_HOST_EXPORT'] = '1' if exporter.is_file() and not smoke else '0'
    env['T3_EDITOR_EQUIPMENT_EXPORT'] = '1' if adapter.is_file() and not smoke else '0'
    install_editor_scripts(project, env)
    plugin_args = editor_plugin_args(project, engine, smoke=smoke, authoring_only=authoring_only)
    env['T3_EDITOR_MAP_REVIEW'] = '1' if 'CommunityMapReview' in plugin_args[1].split(',') else '0'
    level = None
    last = root/'last-draft.txt'
    if last.is_file():
        ident = validate_id(last.read_text(encoding='utf-8').strip())
        if (project.parent/'Content'/(package_path(ident).removeprefix('/Game/')+'.umap')).is_file():
            level = package_path(ident)
    count = 0
    while True:
        count += 1
        attempt = out/f'run-{count}'
        attempt.mkdir()
        request = attempt/'build-request.json'
        host_request = attempt/'host-build-request.json'
        batch_request = attempt/'host-batch-request.json'
        env.update(T3_EDITOR_REQUEST=str(request), T3_EDITOR_INTERACTIVE='1',
                   T3_EDITOR_HOST_REQUEST=str(host_request),
                   T3_EDITOR_BATCH_STAGE_REQUEST=str(batch_request),
                   T3_EDITOR_INITIAL='blank' if level is None else '', T3_EDITOR_LAST_DRAFT=str(last))
        exe = 'UnrealEditor-Cmd.exe' if smoke else 'UnrealEditor.exe'
        args = [str(engine/exe), str(project), *([level] if level else []), '-nop4', '-NoSplash', '-NoSound', '-NoCrashDialog',
                '-abslog='+str(attempt/'editor.log'), *plugin_args]
        if smoke:
            env['T3_EDITOR_INTERACTIVE'] = '0'
            args += ['-unattended', '-nullrhi', '-run=pythonscript', '-script='+str(TOOL/'smoke_editor.py')]
        print('Opening the TRIBES Map Editor. Logs:', attempt, flush=True)
        job = run_owned_job(args, project.parent, env, attempt/'editor-owner', 1000 if smoke else None)
        active = job.get('job_active_processes_after_cleanup', job.get('job_active_processes_at_parent_exit'))
        for_host = host_request.is_file()
        for_batch = batch_request.is_file()
        requested = request.is_file() or for_host or for_batch
        if job['status'] != 'terminal' or active != 0 or (job.get('exit_code') != 0 and not requested):
            raise RuntimeError('The editor stopped before handing off a draft. See ' + str(attempt/'editor.log'))
        if not requested:
            return
        if sum((request.is_file(), for_host, for_batch)) > 1:
            raise ValueError('The editor produced two requests for the same build.')
        if job.get('exit_code') != 0:
            print('Unreal reported a shutdown error. Continuing with the saved draft.', flush=True)
        if for_batch:
            payload = read_json(batch_request, 1024)
            if not isinstance(payload, dict) or set(payload) != {'batch'} or env['T3_EDITOR_HOST_EXPORT'] != '1':
                raise ValueError('Invalid Direct Host batch staging request.')
            env['T3_EDITOR_INTERACTIVE'] = '0'
            try:
                run_stage_batch(project, engine, parent, env, exporter, adapter, payload['batch'])
            except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
                print(f'Batch staging failed. {error}', file=sys.stderr, flush=True)
            if last.is_file():
                level = package_path(validate_id(last.read_text(encoding='utf-8').strip()))
            continue
        ident, markers = build_request(read_json(host_request if for_host else request, 32768))
        level = package_path(ident)
        last.write_text(ident+'\n', encoding='utf-8')
        env['T3_EDITOR_INTERACTIVE'] = '0'
        if for_host:
            from tools.community_map_editor.export_window import choose
            if env['T3_EDITOR_HOST_EXPORT'] != '1' or not imported_draft(ident):
                raise ValueError('Direct Host export requires an imported draft and the configured exporter.')
            selection = choose(ident)
            if selection is None:
                continue
            host_request.rename(request)
            try:
                result = run_host_pipeline(project, engine, parent, attempt, env, exporter, adapter, ident, selection)
                print(f'Direct Host export ready at {result}', flush=True)
                checklist = result / 'playtest-checklist.txt'
                if checklist.is_file():
                    print(f'Playtest checklist at {checklist}', flush=True)
            except (OSError, ValueError, RuntimeError) as error:
                print(f'Direct Host export failed. {error}', file=sys.stderr, flush=True)
            continue
        if not preview:
            raise ValueError('This workspace has no offline playtest configuration.')
        cooked = cook(project, engine, level, attempt, env)
        package = package_preview(parent, cooked, attempt/'preview', ident, markers)
        print('Opening the private TRIBES 3 playtest. Steam must be running.', flush=True)
        args = [str(package/'python/python.exe'), '-B', '-u', str(package/'playtest.py'),
                'offline', '--authoring-preview', '--map', ident, '--game-dir', str(game_source),
                '--workspace', str(workspace)]
        if smoke:
            args += ['--headless', '--seconds', '240']
        job = run_owned_job(args, package, env, attempt/'playtest-owner', 920 if smoke else None)
        if smoke:
            log = (attempt/'playtest-owner/stdout.log').read_text(encoding='utf-8', errors='replace')
            runs = [Path(line.removeprefix('Session: ')) for line in log.splitlines() if line.startswith('Session: ')]
            if len(runs) != 1 or not runs[0].resolve().is_relative_to(workspace):
                raise RuntimeError('The smoke test could not locate its native draft run.')
            events = (runs[0]/'events.txt').read_text(encoding='utf-8')
            execution = read_json(runs[0]/'execution/execution.json')
            if 'DRAFT_CTF_READY|flags_linked=2' not in events or 'RUNTIME_ERROR|' in events or execution.get(
                    'job_active_processes_after_cleanup', execution.get('job_active_processes_at_parent_exit')) != 0:
                raise RuntimeError('The draft did not finish native CTF setup. See ' + str(runs[0]))
            print(f'Native draft {ident} verified: both flags linked and the owned game process cleaned up.', flush=True)
            return
        if job['status'] != 'terminal' or job.get('exit_code') != 0:
            raise RuntimeError('The draft playtest stopped. See ' + str(attempt/'playtest-owner/stdout.log'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--smoke', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        with native_resources(timeout_ms=60000 if args.smoke else 0):
            run(read_json(args.config, 16384), smoke=args.smoke)
    except NativeResourceBusy as error:
        print(error, file=sys.stderr)
        return 75
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
