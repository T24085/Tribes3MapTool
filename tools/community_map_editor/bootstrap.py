"""Create a clean, source-only authoring workspace for the TRIBES map editor."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


PALETTE = {
    'Grass': ((96, 120, 60), 210),
    'Rock': ((120, 117, 109), 222),
    'Forest': ((66, 86, 47), 230),
}


def validate_engine(location):
    location = location.resolve()
    binaries = location / 'Engine/Binaries/Win64' if (location / 'Engine').is_dir() else location
    if not all((binaries / name).is_file() for name in ('UnrealEditor.exe', 'UnrealEditor-Cmd.exe')):
        raise ValueError(f'No Unreal Editor binaries found at {binaries}. Select an Unreal Engine 5.3 installation.')
    version_file = binaries.parent.parent / 'Build/Build.version'
    try:
        version = json.loads(version_file.read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError(f'Could not read the Unreal Engine version at {version_file.resolve()}.') from error
    if not isinstance(version, dict) or (version.get('MajorVersion'), version.get('MinorVersion')) != (5, 3):
        raise ValueError('The map editor requires Unreal Engine 5.3 for its TRIBES 3 map packages.')
    return binaries.resolve()


def discover_engine():
    program_data = os.environ.get('ProgramData')
    if program_data:
        launcher_state = Path(program_data) / 'Epic/UnrealEngineLauncher/LauncherInstalled.dat'
        try:
            installs = json.loads(launcher_state.read_bytes())['InstallationList']
        except (OSError, ValueError, TypeError, KeyError):
            installs = []
        if isinstance(installs, list):
            for install in installs:
                if not isinstance(install, dict) or not isinstance(install.get('InstallLocation'), str):
                    continue
                location = Path(install['InstallLocation'])
                if location.is_absolute():
                    try:
                        return validate_engine(location)
                    except (OSError, ValueError):
                        continue
    raise ValueError('Unreal Engine 5.3 was not found in Epic Games Launcher. Provide the installation folder as the second argument.')


def create_workspace(parent, engine):
    from PIL import Image

    engine = validate_engine(engine)
    root = parent.resolve() / 'CommunityMapEditor'
    if root.exists():
        raise FileExistsError(f'The editor workspace already exists: {root}')
    root.parent.mkdir(parents=True, exist_ok=True)
    settings = root / 'local-settings.json'
    with TemporaryDirectory(prefix='t3-editor-', dir=root.parent) as scratch:
        pending = Path(scratch) / root.name
        project = pending / 'project'
        (project / 'Config').mkdir(parents=True)
        descriptor = 'Stonehenge53.uproject'
        (project / descriptor).write_text(json.dumps({
            'FileVersion': 3, 'EngineAssociation': '5.3', 'Category': 'Map Authoring',
            'Description': 'Local TRIBES map authoring workspace',
        }, indent=2) + '\n', encoding='utf-8')
        ignored = ('Camera', 'Visibility')
        overlaps = ('WorldDynamic', 'Pawn', 'PhysicsBody', 'Destructible', 'Vehicle')
        responses = [f'(Channel="{channel}",Response=ECR_Ignore)' for channel in ignored]
        responses.extend(f'(Channel="{channel}",Response=ECR_Overlap)' for channel in overlaps)
        (project / 'Config/DefaultEngine.ini').write_text(
            '[/Script/WindowsTargetPlatform.WindowsTargetSettings]\nDefaultGraphicsRHI=DefaultGraphicsRHI_DX11\n\n'
            '[Animation.DefaultObjectSettings]\nBoneCompressionSettings="/Engine/Animation/DefaultAnimBoneCompressionSettings"\n\n'
            '[/Script/Engine.CollisionProfile]\n'
            '+Profiles=(Name="WaterBodyCollision",CollisionEnabled=QueryOnly,ObjectTypeName="WorldStatic",'
            f'bCanModify=False,CustomResponses=({",".join(responses)}))\n',
            encoding='utf-8')
        artwork = pending / 'StarterPalette'
        artwork.mkdir()
        for surface, (color, roughness) in PALETTE.items():
            for suffix, rgb in (('Color', color), ('Normal', (128, 128, 255)),
                                ('Surface', (roughness, roughness, roughness))):
                Image.new('RGB', (16, 16), rgb).save(artwork / f'T_{surface}{suffix}.png')
        (pending / 'DerivedDataCache').mkdir()
        (pending / settings.name).write_text(json.dumps({
            'project': str(root / 'project' / descriptor), 'engine_binaries': str(engine),
            'ddc': str(root / 'DerivedDataCache'), 'starter_palette': str(root / artwork.name),
            'authoring_only': True,
        }, indent=2) + '\n', encoding='utf-8')
        pending.rename(root)
    return settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace_parent', type=Path)
    parser.add_argument('engine', type=Path, nargs='?', help='Unreal Engine 5.3 installation or Win64 binaries folder')
    parser.add_argument('--launch', action='store_true', help='Open the new workspace after setup')
    args = parser.parse_args()
    try:
        engine = args.engine if args.engine is not None else discover_engine()
        settings = create_workspace(args.workspace_parent, engine)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f'{error}\n')
    print(settings, flush=True)
    if args.launch:
        return subprocess.call([sys.executable, str(Path(__file__).with_name('launch.py')), str(settings)])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
