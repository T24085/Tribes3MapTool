"""Render a reviewed DDS in a scratch Unreal project and verify its cooked dependencies."""

import argparse
import math
import os
from pathlib import Path
import sys

from launch import NativeResourceBusy, TOOL, cook, digest, install_editor_scripts, native_resources
from launch import package_path, read_json, run_owned_job
from sky_contract import FACES
from texture_batches import create_batch, inspect, load_batch, save_json


DRAFT = 'DraftCTF001'
PLUGINS = ['-NoEnginePlugins', '-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,'
           'TextureFormatOodle,GeometryScripting,ContentBrowserAssetDataSource']
AXIS_TESTS = {
    '+X': ((0, 0), (1, 0, 0)), '-X': ((0, 180), (0, 1, 0)),
    '+Y': ((0, 90), (0, 0, 1)), '-Y': ((0, -90), (1, 1, 0)),
    '+Z': ((90, 0), (1, 0, 1)), '-Z': ((-90, 0), (0, 1, 1)),
}


def prepare_sky(library):
    from PIL import Image, ImageDraw
    from sky_cube import build_cube

    entries = [(f'/Game/Community/Synthetic/T_Sky{i}.T_Sky{i}', 'sky', 'Synthetic test face', None)
               for i in range(len(FACES))]

    def original(_, filename):
        with Image.new('RGBA', (16, 16), (20, 20, 20, 255)) as image:
            image.save(filename)

    batch = create_batch(library, DRAFT, entries, original)
    root, items = load_batch(library, DRAFT, batch.name)
    review, plan = {}, {}
    for face, (ident, row) in zip(FACES, items.items()):
        channels = AXIS_TESTS[face][1]
        rgba = tuple(230 if active else 0 for active in channels) + (255,)
        filename = root / 'replacements' / f'{ident}.png'
        with Image.new('RGBA', (32, 32), rgba) as image:
            if face == '+X':
                ImageDraw.Draw(image).rectangle((20, 4, 27, 11), fill=(230, 230, 230, 255))
            image.save(filename)
        review[ident] = {'status': 'approved', 'sha256': inspect(root, row)[2]['sha256']}
        plan[face] = {'id': ident, 'rotation': 0}
    save_json(root / 'review.json', review)
    return root.name, build_cube(root, items, plan).name


def qualify(engine, output):
    executable = engine / 'UnrealEditor-Cmd.exe'
    if not executable.is_file():
        raise ValueError('Select the Win64 binaries directory of the Unreal 5.3 editor.')
    output.mkdir(parents=True, exist_ok=False)
    project = output / 'project' / 'SkyQualification.uproject'
    project.parent.mkdir()
    save_json(project, {'FileVersion': 3})
    env = dict(os.environ, T3_EDITOR_INTERACTIVE='0', T3_SKY_QUALIFICATION=str(output))
    install_editor_scripts(project, env, include_seed=False)
    batch, sky = prepare_sky(Path(env['T3_TEXTURE_LIBRARY']))
    probes = {face: {'rotation': rotation, 'channels': channels}
              for face, (rotation, channels) in AXIS_TESTS.items()}
    # The white patch is in the upper right of the +X face. Sample its corresponding world ray.
    pitch = math.degrees(math.atan2(-0.5, math.hypot(1, 0.5)))
    yaw = math.degrees(math.atan2(0.5, 1))
    probes['+X upper right'] = {'rotation': (pitch, yaw), 'channels': (1, 1, 1)}
    probes['+X upper left'] = {'rotation': (pitch, -yaw), 'channels': (1, 0, 0)}
    save_json(output / 'fixture.json', {'draft': DRAFT, 'level': package_path(DRAFT), 'batch': batch,
                                      'sky': sky, 'probes': probes})
    args = [str(executable), str(project), '-run=pythonscript', '-script='+str(TOOL / 'smoke_sky.py'),
            '-AllowCommandletRendering', '-RenderOffscreen', '-unattended', '-nop4', '-NoSound',
            '-NoCrashDialog', '-ini:Engine:[DevOptions.Shaders]:bAllowAsynchronousShaderCompiling=False',
            '-abslog='+str(output / 'editor.log'), *PLUGINS]
    cancel = output / 'cancel.json'
    job = run_owned_job(args, project.parent, env, output / 'editor-owner', 1600, cancellation_path=cancel)
    active = job.get('job_active_processes_after_cleanup', job.get('job_active_processes_at_parent_exit'))
    probe = output / 'render-proof.json'
    if job['status'] != 'terminal' or job.get('exit_code') != 0 or active != 0 or not probe.is_file():
        raise RuntimeError(f'The isolated sky render failed. See {output / "editor.log"}.')
    evidence = read_json(probe)
    cooked = cook(project, engine, package_path(DRAFT), output, env, plugins=PLUGINS, cancellation_path=cancel)
    revision = f'Community/{DRAFT}/TextureRevisions/Sky/B_{batch[6:]}/R_{sky[4:]}'
    level = package_path(DRAFT).removeprefix('/Game/')
    dependencies = [level+'.umap', level+'.uexp', f'{revision}/SkyCube.uasset',
                    f'{revision}/M_DraftSky.uasset', f'Community/{DRAFT}/TextureRevisions/Sky/SM_DraftSky.uasset']
    if any(not (cooked / name).is_file() or not (cooked / name).stat().st_size for name in dependencies):
        raise RuntimeError(f'The sky cook omitted a required dependency. See {output / "cook.log"}.')
    evidence['cooked_sha256'] = {name: digest(cooked / name) for name in dependencies}
    save_json(output / 'qualification.json', evidence)
    return output / 'qualification.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_binaries', type=Path)
    parser.add_argument('output', type=Path, help='New directory for the isolated project and proof')
    args = parser.parse_args()
    try:
        with native_resources():
            print(qualify(args.engine_binaries.resolve(strict=True), args.output.resolve()))
    except NativeResourceBusy as error:
        print(error, file=sys.stderr)
        return 75
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
