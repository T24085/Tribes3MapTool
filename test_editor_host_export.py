"""Check the owned editor-to-cook handoff with a stand-in exporter."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from tools.community_map_editor import bootstrap, launch


DRAFT = 'DraftT2001'
REQUEST = {'map': DRAFT, 'markers': {
    'T3Spawn1': [[100, 120, 300, 0]],
    'T3Spawn2': [[300, 120, 300, 180]],
    'T3Flag1': [[110, 125, 40, 0]],
    'T3Flag2': [[320, 130, 40, 180]],
}}


class EditorHostExportTests(unittest.TestCase):
    def test_clean_authoring_workspace_opens_with_no_game_or_host_inputs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name in ('UnrealEditor.exe', 'UnrealEditor-Cmd.exe'):
                (root / name).touch()
            settings = bootstrap.create_workspace(root, root)
            original = settings.read_bytes()
            with self.assertRaises(FileExistsError):
                bootstrap.create_workspace(root, root)
            self.assertEqual(settings.read_bytes(), original)
            config = json.loads(original)
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
            (selection['host']/'package-manifest.json').write_text('{}', encoding='utf-8')
            config = {'project': str(project), 'engine_binaries': str(root), 'ddc': str(root),
                      'direct_host_exporter': str(exporter)}
            editor_calls = []

            def editor(args, _cwd, env, _owner, _timeout):
                self.assertEqual(Path(args[0]).name, 'UnrealEditor.exe')
                self.assertEqual(env['T3_EDITOR_HOST_EXPORT'], '1')
                self.assertEqual(env['T3_EDITOR_PLAYTEST'], '0')
                editor_calls.append(args)
                if len(editor_calls) == 1:
                    Path(env['T3_EDITOR_HOST_REQUEST']).write_text(json.dumps(REQUEST), encoding='utf-8')
                return {'status': 'terminal', 'exit_code': 0, 'job_active_processes_after_cleanup': 0}

            def cook(_project, _engine, level, attempt, env):
                self.assertEqual(level, launch.package_path(DRAFT))
                self.assertEqual(env['T3_EDITOR_INTERACTIVE'], '0')
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
