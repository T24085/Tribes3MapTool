"""Check the owned editor-to-cook handoff with a stand-in exporter."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tools.community_map_editor import bootstrap, install_review, launch


DRAFT = 'DraftT2001'
REQUEST = {'map': DRAFT, 'markers': {
    'T3Spawn1': [[100, 120, 300, 0]],
    'T3Spawn2': [[300, 120, 300, 180]],
    'T3Flag1': [[110, 125, 40, 0]],
    'T3Flag2': [[320, 130, 40, 180]],
}}


def write_authored_host(root):
    sources = {
        'lua/network.lua': b'native_settings_authored_draft_world_owner_required EDITOR_DRAFT_WORLD_SETTINGS',
        'lib/host_catalog_runtime.py': b'.editor_draft=true',
    }
    rows = []
    for name, data in sources.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rows.append(dict(path=name, bytes=len(data), sha256=sha256(data).hexdigest()))
    (root/'package-manifest.json').write_text(json.dumps({'files': rows}), encoding='utf-8')


def unreal_installation(root, minor):
    binaries = root / 'Engine/Binaries/Win64'
    binaries.mkdir(parents=True)
    for name in ('UnrealEditor.exe', 'UnrealEditor-Cmd.exe'):
        (binaries / name).touch()
    build = root / 'Engine/Build'
    build.mkdir()
    (build / 'Build.version').write_text(json.dumps({'MajorVersion': 5, 'MinorVersion': minor}), encoding='utf-8')
    return binaries


class EditorHostExportTests(unittest.TestCase):
    def test_long_workspace_cache_gets_a_stable_short_local_path(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {'LOCALAPPDATA': folder}):
            root = Path(folder).resolve()
            long_path = root / ('nested-' * 16) / 'DDC'
            fallback = launch.editor_cache_path(long_path, root)
            self.assertTrue(fallback.is_dir())
            self.assertEqual(fallback.parent, root / 'Tribes3MapTool' / 'DDC')
            self.assertEqual(launch.editor_cache_path(long_path, root), fallback)
            self.assertNotEqual(launch.editor_cache_path(long_path.with_name('Other'), root), fallback)
            self.assertEqual(launch.editor_cache_path('DDC', root), root / 'DDC')

    def test_clean_authoring_workspace_opens_with_no_game_or_host_inputs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            engine = root / 'Unreal 5.3'
            binaries = unreal_installation(engine, 3)
            settings = bootstrap.create_workspace(root, engine)
            original = settings.read_bytes()
            with self.assertRaises(FileExistsError):
                bootstrap.create_workspace(root, binaries)
            self.assertEqual(settings.read_bytes(), original)
            config = json.loads(original)
            config['direct_host_exporter'] = str(root/'unavailable.py')
            self.assertEqual(len(list(Path(config['starter_palette']).glob('*.png'))), 9)

            def run_editor(args, cwd, env, _owner, _budget):
                self.assertEqual(cwd, Path(config['project']).parent)
                self.assertIn('PythonScriptPlugin', args[-1])
                self.assertNotIn('CommunityNativeWorldSettings', args[-1].split(','))
                self.assertEqual(env['T3_EDITOR_INITIAL'], 'blank')
                self.assertEqual(env['T3_EDITOR_PLAYTEST'], '0')
                self.assertEqual(env['T3_EDITOR_HOST_EXPORT'], '0')
                self.assertNotIn('T3_EDITOR_SEED', env)
                self.assertTrue((cwd / 'Content/Python/init_unreal.py').is_file())
                return {'status': 'terminal', 'exit_code': 0, 'job_active_processes_at_parent_exit': 0}

            with patch.object(launch, 'run_owned_job', side_effect=run_editor) as owned:
                launch.run(config)
            owned.assert_called_once()

    def test_setup_selects_only_an_actual_unreal_53_installation(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {'ProgramData': folder}):
            root = Path(folder).resolve()
            wrong_engine, right_engine = root / 'Unreal 5.8', root / 'Unreal 5.3'
            unreal_installation(wrong_engine, 8)
            right_binaries = unreal_installation(right_engine, 3)
            manifest = root / 'Epic/UnrealEngineLauncher/LauncherInstalled.dat'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'InstallationList': [
                {'InstallLocation': str(wrong_engine)}, {'InstallLocation': str(right_engine)},
            ]}), encoding='utf-8')

            self.assertEqual(bootstrap.discover_engine(), right_binaries)
            with self.assertRaisesRegex(ValueError, 'requires Unreal Engine 5.3'):
                bootstrap.create_workspace(root, wrong_engine)
            self.assertFalse((root / 'CommunityMapEditor').exists())

    def test_review_install_checks_engine_build_and_retains_the_previous_plugin(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            engine = unreal_installation(root / 'Unreal', 3)
            (engine / 'UnrealEditor.modules').write_text('{"BuildId":"installed-engine"}', encoding='utf-8')
            project = root / 'Map Project' / 'Map.uproject'
            project.parent.mkdir()
            project.touch()
            package = root / 'BuildPlugin'
            binaries = package / 'Binaries/Win64'
            binaries.mkdir(parents=True)
            name = install_review.NAME
            dll = f'UnrealEditor-{name}.dll'
            (binaries / dll).write_bytes(b'compiled review module')
            (package / f'{name}.uplugin').write_text(json.dumps({'Modules': [
                {'Name': name, 'Type': 'Editor'},
            ]}), encoding='utf-8')
            modules = binaries / 'UnrealEditor.modules'
            modules.write_text(json.dumps({'BuildId': 'another-engine', 'Modules': {name: dll}}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'exact Unreal Editor build'):
                install_review.install(package, project, engine)
            self.assertFalse((project.parent / 'Plugins').exists())

            modules.write_text(json.dumps({'BuildId': 'installed-engine', 'Modules': {name: dll}}), encoding='utf-8')
            destination, backup = install_review.install(package, project, engine)
            self.assertIsNone(backup)
            self.assertIn(name, launch.editor_plugin_args(project, engine, smoke=False, authoring_only=True)[1])
            self.assertEqual(install_review.install(package, project, engine), (destination, None))
            (binaries / dll).write_bytes(b'new review binary')
            self.assertEqual(install_review.install(package, project, engine)[0], destination)
            old, = (project.parent / 'Saved/T3PluginBackups').iterdir()
            self.assertEqual((old / 'Binaries/Win64' / dll).read_bytes(), b'compiled review module')
            self.assertEqual((destination / 'Binaries/Win64' / dll).read_bytes(), b'new review binary')

    def test_review_menu_handoff_installs_after_exit_and_reopens_the_draft(self):
        with TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            engine = unreal_installation(parent / 'Unreal', 3)
            (engine / 'UnrealEditor.modules').write_text('{"BuildId":"test-editor"}', encoding='utf-8')
            config = launch.read_json(bootstrap.create_workspace(parent, engine))
            project = Path(config['project'])
            level = launch.package_path(DRAFT)
            asset = project.parent / 'Content' / (level.removeprefix('/Game/') + '.umap')
            asset.parent.mkdir(parents=True)
            asset.touch()
            events = []

            def editor(args, cwd, env, owner, budget):
                if not events:
                    self.assertEqual(env['T3_EDITOR_MAP_REVIEW'], '0')
                    Path(env['T3_EDITOR_REVIEW_REQUEST']).write_text(json.dumps({'draft': DRAFT}), encoding='utf-8')
                    events.append('editor exited')
                else:
                    self.assertEqual(events, ['editor exited', 'review installed'])
                    self.assertEqual(env['T3_EDITOR_MAP_REVIEW'], '1')
                    self.assertIn(level, args)
                    self.assertIn('-ExecCmds=T3.MapReview', args)
                    events.append('draft reopened')
                return {'status': 'terminal', 'exit_code': 0, 'job_active_processes_at_parent_exit': 0}

            def install(selected, binaries, package):
                self.assertEqual(events, ['editor exited'])
                self.assertEqual(selected, project)
                self.assertEqual(binaries, engine)
                self.assertIsNone(package)
                target = project.parent / 'Plugins/CommunityMapReview'
                directory = target / 'Binaries/Win64'
                directory.mkdir(parents=True)
                dll = 'UnrealEditor-CommunityMapReview.dll'
                (directory / dll).touch()
                (directory / 'UnrealEditor.modules').write_text(json.dumps({
                    'BuildId': 'test-editor', 'Modules': {'CommunityMapReview': dll},
                }), encoding='utf-8')
                events.append('review installed')
                return target, None

            with patch.object(launch, 'run_owned_job', side_effect=editor), \
                    patch.object(install_review, 'install_for_workspace', side_effect=install):
                launch.run(config)
            self.assertEqual(events, ['editor exited', 'review installed', 'draft reopened'])

    def test_authoring_host_capability_has_to_be_in_the_verified_inventory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            manifest = root/'package-manifest.json'
            manifest.write_text('{"files":[]}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'supports drafts with standard Unreal'):
                launch.authored_host_stamp(root)
            write_authored_host(root)
            self.assertEqual(launch.authored_host_stamp(root), sha256(manifest.read_bytes()).hexdigest())
            network = root/'lua/network.lua'
            network.write_bytes(network.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'supports drafts with standard Unreal'):
                launch.authored_host_stamp(root)

    def test_batch_handoff_stages_only_after_owned_editor_closes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            project = root / 'CommunityMapEditor/project/Stonehenge53.uproject'
            project.parent.mkdir(parents=True)
            project.touch()
            exporter = root / 'export_t2_draft.py'
            exporter.touch()
            config = {'project': str(project), 'engine_binaries': str(root), 'preview_base': str(root),
                      'game_workspace': str(root), 'game_source': str(root), 'ddc': str(root),
                      'direct_host_exporter': str(exporter)}
            calls = []

            def editor(args, _cwd, env, _owner, _timeout):
                self.assertEqual(Path(args[0]).name, 'UnrealEditor.exe')
                calls.append('editor')
                if len(calls) == 1:
                    Path(env['T3_EDITOR_BATCH_STAGE_REQUEST']).write_text(
                        '{"batch":"batch-001122334455"}', encoding='utf-8')
                return {'status': 'terminal', 'exit_code': 0, 'job_active_processes_after_cleanup': 0}

            def stage(_project, _engine, _base, env, _exporter, _adapter, ident):
                self.assertEqual(ident, 'batch-001122334455')
                self.assertEqual(env['T3_EDITOR_INTERACTIVE'], '0')
                calls.append('stage')

            with patch.object(launch, 'install_editor_scripts'), patch.object(launch, 'run_stage_batch', side_effect=stage), \
                    patch.object(launch, 'run_owned_job', side_effect=editor):
                launch.run(config)
            self.assertEqual(calls, ['editor', 'stage', 'editor'])

    def test_editor_only_enables_a_compatible_weather_plugin(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = root/'project/Draft.uproject'
            engine = root/'engine'
            engine.mkdir()
            plugin = project.parent/'Plugins/CommunityMapMaterials/Binaries/Win64'
            plugin.mkdir(parents=True)
            dll = 'UnrealEditor-CommunityMapMaterials.dll'
            (plugin/dll).touch()
            (plugin/'UnrealEditor.modules').write_text(json.dumps({
                'BuildId': 'current', 'Modules': {'CommunityMapMaterials': dll},
            }), encoding='utf-8')
            engine_manifest = engine/'UnrealEditor.modules'
            engine_manifest.write_text('{"BuildId": "earlier"}', encoding='utf-8')
            with patch('builtins.print'):
                self.assertEqual(launch.editor_plugin_args(project, engine, smoke=False), launch.EDITOR_PLUGINS)
            engine_manifest.write_text('{"BuildId": "current"}', encoding='utf-8')
            self.assertTrue(launch.editor_plugin_args(project, engine, smoke=False)[1].endswith(',CommunityMapMaterials'))
            self.assertNotIn('CommunityMapMaterials', launch.PLUGINS[1])

    def test_cook_requires_inline_map_shaders(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = root/'Preview.uproject'
            content = root/'cook/Windows/Preview/Content'
            content.mkdir(parents=True)
            (root/'cook.log').touch()
            completed = dict(status='terminal', exit_code=0, job_active_processes_at_parent_exit=0)
            with patch.object(launch, 'run_owned_job', return_value=completed) as run, patch('builtins.print'):
                self.assertEqual(launch.cook(project, root, '/Game/Test', root, {}), content)
                self.assertIn('-ini:Game:[/Script/UnrealEd.ProjectPackagingSettings]:bShareMaterialShaderCode=False',
                              run.call_args.args[0])
                (content/'ShaderArchive-Preview-PCD3D_SM5-PCD3D_SM5.ushaderbytecode').touch()
                with self.assertRaisesRegex(RuntimeError, 'external archive'):
                    launch.cook(project, root, '/Game/Test', root, {})

    def test_docked_review_plugin_uses_the_matching_editor_and_stays_out_of_cook(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = root/'project/Draft.uproject'
            engine = root/'engine'
            engine.mkdir()
            name = 'CommunityMapReview'
            plugin = project.parent/'Plugins'/name/'Binaries/Win64'
            plugin.mkdir(parents=True)
            dll = f'UnrealEditor-{name}.dll'
            (plugin/dll).touch()
            (plugin/'UnrealEditor.modules').write_text(json.dumps({
                'BuildId': 'tested', 'Modules': {name: dll},
            }), encoding='utf-8')
            manifest = engine/'UnrealEditor.modules'
            manifest.write_text('{"BuildId": "other"}', encoding='utf-8')
            with patch('builtins.print'):
                self.assertNotIn(name, launch.editor_plugin_args(project, engine, smoke=False)[1])
            manifest.write_text('{"BuildId": "tested"}', encoding='utf-8')
            self.assertIn(name, launch.editor_plugin_args(project, engine, smoke=False)[1])
            self.assertNotIn(name, launch.editor_plugin_args(project, engine, smoke=True)[1])
            self.assertNotIn(name, launch.PLUGINS[1])

    def test_inventory_station_authoring_uses_the_topaz_module_for_editor_and_cook(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = root/'project/Draft.uproject'
            engine = root/'engine'
            engine.mkdir()
            name = 'CommunityInventoryStationAuthoring'
            plugin = project.parent/'Plugins'/name/'Binaries/Win64'
            plugin.mkdir(parents=True)
            dll = 'UnrealEditor-Topaz.dll'
            (plugin/dll).touch()
            (plugin/'UnrealEditor.modules').write_text(json.dumps({
                'BuildId': 'station-build', 'Modules': {'Topaz': dll},
            }), encoding='utf-8')
            receipt = engine/'UnrealEditor.modules'
            receipt.write_text('{"BuildId":"other-build"}', encoding='utf-8')
            with patch('builtins.print'):
                self.assertNotIn(name, launch.editor_plugin_args(project, engine, smoke=False)[1])
            receipt.write_text('{"BuildId":"station-build"}', encoding='utf-8')
            for smoke in (False, True):
                self.assertIn(name, launch.editor_plugin_args(project, engine, smoke=smoke)[1])
            self.assertNotIn(name, launch.PLUGINS[1])

    def test_approved_equipment_requires_and_passes_the_native_adapter(self):
        with TemporaryDirectory() as folder:
            run = Path(folder)
            adapter = run/'attach_t2_equipment.py'
            (run/'equipment-review.json').touch()
            destination = run/'output'
            selection = dict(host=run/'host', destination=destination, label='Equipment', minimap=None)
            with self.assertRaisesRegex(ValueError, 'configured native equipment adapter'):
                launch.run_host_export(run/'export.py', run, selection)
            adapter.touch()
            destination.mkdir()
            (destination/'export-receipt.json').touch()
            response = Mock(returncode=0, stdout='', stderr='')
            with patch.object(launch.subprocess, 'run', return_value=response) as execute:
                self.assertEqual(launch.run_host_export(run/'export.py', run, selection, equipment_adapter=adapter), destination)
            self.assertEqual(execute.call_args.args[0][-2:], ['--equipment-adapter', str(adapter)])

    def test_editor_cooks_and_exports_before_reopening_without_launching_a_game(self):
        for stock in (False, True):
            with self.subTest(stock=stock):
                self.check_host_roundtrip(stock)

    def test_host_compatibility_stops_before_cook_and_saves_a_shareable_issue(self):
        from tools.community_map_editor.host_compatibility import issue_draft

        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            host = root / 'Private host 192.0.2.10'
            library = root / 'Library'
            host.mkdir()
            write_authored_host(host)
            (host / 'playtest.py').write_bytes(b'# Old launcher')
            exporter, adapter = root / 'export_t2_draft.py', root / 'attach_t2_equipment.py'
            exporter.touch()
            adapter.touch()
            selection = dict(host=host, destination=root / 'Export', label='Private map name https://example.test',
                             minimap=None)
            env = {'T3_CONVERTER_LIBRARY': str(library), 'T3_EDITOR_AUTHORING_ONLY': '1'}
            placements = [{'half_size_cm': [20, 100, 150], 'on_power_loss': 'weaken'},
                          {'station_floor_z_cm': 50}]

            def attempt(name):
                path = root / name
                path.mkdir()
                (path / 'build-request.json').write_text(json.dumps(REQUEST), encoding='utf-8')
                (path / 'equipment.json').write_text('{}', encoding='utf-8')
                (path / 'equipment-review.json').write_text(
                    json.dumps(dict(map=DRAFT, placements=placements)), encoding='utf-8')
                (path / 'host-boundary-features.json').write_text(
                    json.dumps(dict(map=DRAFT, features=['flag-height-limits', 'flag-return-timer'])), encoding='utf-8')
                return path

            def execute(path):
                return launch.run_host_pipeline(root / 'Draft.uproject', root, root, path, env,
                                                exporter, adapter, DRAFT, selection)

            before = (host / 'package-manifest.json').read_bytes()
            failed = attempt('Failed attempt')
            with patch.object(launch, 'cook') as cook:
                with self.assertRaisesRegex(ValueError, 'compatibility check failed'):
                    execute(failed)
                cook.assert_not_called()
            self.assertEqual((host / 'package-manifest.json').read_bytes(), before)
            self.assertFalse(selection['destination'].exists())
            run, = (library / 'PipelineRuns').glob('run-*.json')
            journal = json.loads(run.read_bytes())
            self.assertEqual([step['status'] for step in journal['steps']], ['failed', 'pending', 'pending', 'pending'])
            report = journal['host_compatibility']
            self.assertEqual(report, dict(baseline=sha256(before).hexdigest(),
                                          blockers=['grouped-equipment', 'powered-shields',
                                                    'shield-power-policies', 'inventory-stations',
                                                    'portable-equipment-adapters', 'flag-height-limits', 'flag-return-timer']))
            message = (failed / 'host-compatibility-issue.md').read_text(encoding='utf-8')
            self.assertEqual(message, issue_draft(DRAFT, report))
            for private in (str(root), selection['label'], host.name):
                self.assertNotIn(private, message)
            with self.assertRaises(ValueError):
                issue_draft(DRAFT, dict(report, blockers=[str(host)]))

            grouped = b'-- T3_EDITOR_GROUPED_EQUIPMENT_V1\n-- T3_EDITOR_GROUPED_SHIELDS_V1\n'
            shields = b'-- T3_EDITOR_SHIELD_POWER_POLICIES_V1\n'
            stations = b'-- T3_EDITOR_INVENTORY_STATIONS_V2\n'
            flag_markers = (b'T3_EDITOR_FLAG_HEIGHT_LIMITS_V1', b'T3_EDITOR_FLAG_RETURN_TIMER_V1')
            for name, data in (
                    ('lua/network.lua', (host / 'lua/network.lua').read_bytes() + b'\n' + grouped + stations),
                    ('lua/objective_layouts.lua', grouped + shields + stations),
                    ('lua/powered_shields.lua', shields), ('lua/inventory_stations.lua', stations),
                    ('playtest.py', b'merge_map_manifests runtime_paths'),
                    ('lib/portable_maps.py', b'_portable_adapters'),
                    ('match_roster.py', b'\n'.join(b'# ' + flag for flag in flag_markers)),
                    ('runtime-fixes/mission_flag_limit.lua', b'\n'.join(b'-- ' + flag for flag in flag_markers))):
                path = host / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            files = [path for path in host.rglob('*') if path.is_file() and path.name != 'package-manifest.json']
            (host / 'package-manifest.json').write_text(json.dumps({'files': [
                dict(path=path.relative_to(host).as_posix(), bytes=path.stat().st_size,
                     sha256=sha256(path.read_bytes()).hexdigest()) for path in files
            ]}), encoding='utf-8')
            passed = attempt('Passed attempt')
            with patch.object(launch, 'editor_plugin_args', return_value=['', 'CommunityInventoryStationAuthoring']), \
                    patch.object(launch, 'cook', side_effect=RuntimeError('cook reached')) as cook:
                with self.assertRaisesRegex(RuntimeError, 'cook reached'):
                    execute(passed)
                cook.assert_called_once()
                self.assertIn('BP_CommunityInventoryStation', cook.call_args.kwargs['station_package'])
            self.assertFalse((passed / 'host-compatibility-issue.md').exists())

            boundary = host / 'runtime-fixes/mission_flag_limit.lua'
            boundary.write_bytes(boundary.read_bytes() + b'\n')
            changed = attempt('Host changed')
            with patch.object(launch, 'cook') as cook:
                with self.assertRaisesRegex(ValueError, 'compatibility check failed'):
                    execute(changed)
                cook.assert_not_called()
            report = (changed / 'host-compatibility-issue.md').read_text(encoding='utf-8')
            self.assertIn('flag-height-limits', report)
            self.assertIn('flag-return-timer', report)
            self.assertNotIn('grouped-equipment', report)

    def check_host_roundtrip(self, stock):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            project = root / 'CommunityMapEditor/project/Stonehenge53.uproject'
            project.parent.mkdir(parents=True)
            project.touch()
            exporter = root / 'tools/export_t2_draft.py'
            exporter.parent.mkdir()
            exporter.write_text('''import json
from pathlib import Path
import sys
run, host, destination, _label, label, _minimap, minimap = sys.argv[1:]
assert Path(run, 'build-request.json').is_file()
preview = json.loads(Path(run, 'preview/package-manifest.json').read_bytes())
assert preview['authoring_preview'] == 'DraftT2001'
assert all(row['path'].startswith('content/') for row in preview['files'])
Path(destination).mkdir()
Path(destination, 'export-receipt.json').write_text(json.dumps(
    {'label': label, 'host': host, 'minimap': minimap, 'files': len(preview['files'])}))
''', encoding='utf-8')
            output = root / 'draft-portable'
            selection = dict(host=root / 'host', destination=output,
                             label='Test Ridge (T2 2026)', minimap=root / 'minimap')
            selection['host'].mkdir()
            if stock:
                write_authored_host(selection['host'])
            else:
                (selection['host']/'package-manifest.json').write_text('{}', encoding='utf-8')
            config = {'project': str(project), 'engine_binaries': str(root), 'ddc': str(root),
                      'direct_host_exporter': str(exporter), 'authoring_only': stock}
            editor_calls = []

            def editor(args, _cwd, env, _owner, _timeout):
                self.assertEqual(Path(args[0]).name, 'UnrealEditor.exe')
                self.assertEqual(env['T3_EDITOR_HOST_EXPORT'], '1')
                self.assertEqual(env['T3_EDITOR_PLAYTEST'], '0')
                self.assertEqual('CommunityNativeWorldSettings' in args[-1].split(','), not stock)
                editor_calls.append(args)
                if len(editor_calls) == 1:
                    Path(env['T3_EDITOR_HOST_REQUEST']).write_text(json.dumps(REQUEST), encoding='utf-8')
                return {'status': 'terminal', 'exit_code': 0, 'job_active_processes_after_cleanup': 0}

            def cook(_project, _engine, level, attempt, env, *, plugins=None):
                self.assertEqual(level, launch.package_path(DRAFT))
                self.assertEqual(env['T3_EDITOR_INTERACTIVE'], '0')
                if stock:
                    self.assertNotIn('CommunityNativeWorldSettings', plugins[-1].split(','))
                else:
                    self.assertIsNone(plugins)
                content = attempt / 'cook/Content'
                stem = content / level.removeprefix('/Game/')
                stem.parent.mkdir(parents=True)
                for ext in ('.umap', '.uexp'):
                    stem.with_suffix(ext).write_bytes(b'level')
                terrain = content / 'Community' / DRAFT / 'Terrain'
                terrain.parent.mkdir(parents=True)
                for ext in ('.uasset', '.uexp'):
                    terrain.with_suffix(ext).write_bytes(b'terrain')
                return content

            with patch.object(launch, 'install_editor_scripts'), patch.object(launch, 'cook', side_effect=cook), \
                    patch.object(launch, 'run_owned_job', side_effect=editor), \
                    patch('tools.community_map_editor.export_window.choose', Mock(return_value=selection)) as choose:
                launch.run(config)

            choose.assert_called_once_with(DRAFT)
            self.assertEqual(len(editor_calls), 2)
            self.assertIn(launch.package_path(DRAFT), editor_calls[1])
            receipt = json.loads((output / 'export-receipt.json').read_bytes())
            self.assertEqual(receipt, {'label': selection['label'], 'host': str(selection['host']),
                                       'minimap': str(selection['minimap']), 'files': 4})


if __name__ == '__main__':
    unittest.main()
