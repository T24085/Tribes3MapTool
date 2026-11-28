"""Build or install a workspace editor plugin: the Object Review panel, or the native authoring mirror.

``--plugin native`` builds CommunityNativeWorldSettings, whose Valhalla stub module lets the editor
place original-game classes such as the lava effect volume (T3BackEndServer#150).
"""

import argparse
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import uuid

TOOL = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL.parents[1]))
from tools.community_map_editor.bootstrap import validate_engine
from tools.community_map_editor.launch import (NativeResourceBusy, digest, editor_plugin_matches, native_resources,
                                                read_json, run_owned_job)


NAME = 'CommunityMapReview'
DESCRIPTOR = f'{NAME}.uplugin'
RUNTIME_FILES = (DESCRIPTOR, 'Binaries/Win64/UnrealEditor.modules', f'Binaries/Win64/UnrealEditor-{NAME}.dll')
REVIEW = dict(name=NAME, label='Object Review', module=NAME, module_type='Editor', files=RUNTIME_FILES)
NATIVE_NAME = 'CommunityNativeWorldSettings'
NATIVE = dict(name=NATIVE_NAME, label='native authoring mirror', module='Valhalla', module_type='Runtime',
              files=(f'{NATIVE_NAME}.uplugin', 'Binaries/Win64/UnrealEditor.modules',
                     'Binaries/Win64/UnrealEditor-Valhalla.dll',
                     'Binaries/Win64/UnrealEditor-CommunityWorldSettingsEditor.dll',
                     'Config/Tags/CommunityHazardTags.ini'))
SPECS = {'review': REVIEW, 'native': NATIVE}


def install_project_tags(package, project):
    """Copy a plugin's gameplay tag lists into the project's Config/Tags, which the editor always reads.

    UE 5.3 doesn't read Config/Tags from a code-only plugin, so the hazard tags (DamageSource.Lava) would
    stay unregistered and effect volumes would save empty tags.
    """
    tags = project.parent / 'Config/Tags'
    for source in sorted((package / 'Config/Tags').glob('*.ini')):
        tags.mkdir(parents=True, exist_ok=True)
        target = tags / source.name
        if not target.is_file() or digest(target) != digest(source):
            shutil.copyfile(source, target)


def install(package, project, engine, spec=REVIEW):
    NAME, label = spec['name'], spec['label']
    if not editor_plugin_matches(package, engine, spec['module']):
        raise ValueError(f'The {label} package must be built for this exact Unreal Editor build.')
    descriptor = read_json(package / f'{NAME}.uplugin', 32768)
    modules = descriptor.get('Modules') if isinstance(descriptor, dict) else None
    if not isinstance(modules, list) or not any(
            isinstance(module, dict) and module.get('Name') == spec['module'] and module.get('Type') == spec['module_type']
            for module in modules):
        raise ValueError(f'The package is missing the {label} module descriptor.')
    hashes = {}
    for name in spec['files']:
        source = package / name
        if source.resolve(strict=True) != source or not source.is_file():
            raise ValueError(f'The {label} package contains a linked or invalid file: {name}')
        hashes[name] = digest(source)

    plugins = project.parent / 'Plugins'
    plugins.mkdir(exist_ok=True)
    destination = plugins / NAME
    if plugins.resolve(strict=True) != plugins or destination.resolve() != destination:
        raise ValueError('The project plugin folder must be inside the selected workspace.')
    install_project_tags(package, project)
    if destination.exists() and all((destination / name).is_file() and digest(destination / name) == expected
                                    for name, expected in hashes.items()):
        return destination, None

    backup = None
    with TemporaryDirectory(prefix='t3-object-review-', dir=project.parent) as temporary:
        pending = Path(temporary) / NAME
        for name, expected in hashes.items():
            target = pending / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, target)
            if digest(target) != expected:
                raise ValueError(f'The {label} package changed while installing: {name}')
        if not editor_plugin_matches(pending, engine, spec['module']):
            raise ValueError(f'The {label} files changed while installing.')
        if destination.exists():
            backups = project.parent / 'Saved/T3PluginBackups'
            backups.mkdir(parents=True, exist_ok=True)
            if backups.resolve(strict=True) != backups:
                raise ValueError('The plugin backup folder must be inside the selected workspace.')
            backup = backups / f'{NAME}-{uuid.uuid4().hex[:8]}'
            destination.rename(backup)
        try:
            pending.rename(destination)
        except OSError:
            if backup is not None and not destination.exists():
                backup.rename(destination)
            raise
    return destination, backup


def build(engine, scratch, logs, spec=REVIEW):
    NAME, DESCRIPTOR = spec['name'], f"{spec['name']}.uplugin"
    engine_root = engine.parent.parent
    automation = engine_root / 'Binaries/DotNET/AutomationTool/AutomationTool.dll'
    dotnet = next(iter(sorted((engine_root / 'Binaries/ThirdParty/DotNet').glob('*/windows/dotnet.exe'))), None)
    if dotnet is None or not automation.is_file():
        raise ValueError('Install Unreal Engine 5.3 with AutomationTool and its bundled .NET SDK, or supply --package.')
    plugin = scratch / NAME
    shutil.copytree(TOOL / 'Plugins' / NAME, plugin)
    package = scratch / 'Package'
    sdk = str(dotnet.parent)
    env = dict(os.environ, DOTNET_ROOT=sdk, DOTNET_MULTILEVEL_LOOKUP='0', UE_DOTNET_DIR=sdk)
    env['PATH'] = sdk + os.pathsep + env.get('PATH', '')
    args = [str(dotnet), str(automation), 'BuildPlugin', '-NoP4', '-NoCompile', '-HostPlatforms=Win64',
            '-NoTargetPlatforms', f'-Plugin={plugin / DESCRIPTOR}', f'-Package={package}']
    print(f'Building {NAME}. See {logs / "stdout.log"} for progress.', flush=True)
    result = run_owned_job(args, automation.parent, env, logs, 1200)
    remaining = result.get('job_active_processes_after_cleanup', result.get('job_active_processes_at_parent_exit'))
    if result['status'] != 'terminal' or result.get('exit_code') != 0 or remaining != 0:
        raise RuntimeError(f'The {spec["label"]} build did not finish successfully. See {logs / "stdout.log"}.')
    for name in spec['files']:
        if name.startswith('Config/') and not (package / name).is_file():
            # BuildPlugin packages binaries; the plugin's tag config is copied as it is.
            (package / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(plugin / name, package / name)
    return package


def install_for_workspace(project, engine, package=None, spec=REVIEW):
    engine = validate_engine(engine)
    if package is not None:
        return install(package.resolve(strict=True), project, engine, spec)
    logs = project.parent / 'Saved/T3PluginBuilds' / uuid.uuid4().hex[:12]
    logs.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='t3-plugin-') as scratch:
        return install(build(engine, Path(scratch), logs, spec), project, engine, spec)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path, help='The workspace local-settings.json')
    parser.add_argument('--package', type=Path, help='Optional trusted BuildPlugin output folder')
    parser.add_argument('--plugin', choices=sorted(SPECS), default='review', help='Which plugin to build')
    args = parser.parse_args()
    try:
        config = read_json(args.config.resolve(strict=True), 16384)
        project = Path(config['project']).resolve(strict=True)
        if not project.is_file() or project.suffix != '.uproject':
            raise ValueError('Select a workspace with an Unreal project.')
        engine = Path(config['engine_binaries'])
        with native_resources():
            spec = SPECS[args.plugin]
            destination, backup = install_for_workspace(project, engine, args.package, spec)
        print(f'Installed {spec["label"]} at {destination}.')
        if backup is not None:
            print(f'Previous plugin saved at {backup}.')
    except NativeResourceBusy as error:
        print(error, file=sys.stderr)
        return 75
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
