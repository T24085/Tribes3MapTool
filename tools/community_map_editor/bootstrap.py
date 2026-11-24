"""Create a clean, source-only authoring workspace for the TRIBES map editor."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory


PALETTE = {
    'Grass': ((96, 120, 60), 210),
    'Rock': ((120, 117, 109), 222),
    'Forest': ((66, 86, 47), 230),
}


def create_workspace(parent, engine):
    from PIL import Image

    engine = engine.resolve(strict=True)
    if not (engine / 'UnrealEditor.exe').is_file() or not (engine / 'UnrealEditor-Cmd.exe').is_file():
        raise ValueError('Select the Win64 binaries folder of your Unreal Engine 5.3 installation.')
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
    parser.add_argument('engine_binaries', type=Path)
    args = parser.parse_args()
    try:
        settings = create_workspace(args.workspace_parent, args.engine_binaries)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f'{error}\n')
    print(settings)


if __name__ == '__main__':
    main()
