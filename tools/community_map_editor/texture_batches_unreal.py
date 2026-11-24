"""Unreal handoff for draft texture reviews and versioned imports."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

import unreal

from draft_materials import SOURCE_TAG, apply_texture_revisions, texture_origin
from sky_contract import verify_sky_build
from sky_intake import checked_sky_file, load_sky_index
from texture_batches import ASSET, approved_replacements, create_batch, load_batch, save_json, texture_png


SKY_DDS_TAG = 'T3SkyDDSHash'


def texture_role(asset):
    value = str(asset.get_editor_property('compression_settings')).casefold()
    name = str(asset.get_name()).casefold()
    if 'normal' in value:
        return 'normal', 'Keep the tangent axes and normal detail.'
    if 'masks' in value or name.endswith(('_orm', '_ao', '_roughness')):
        return 'masks', 'Preserve the packed data channels without tinting them.'
    if 'sky' in name or 'cloud' in name:
        return 'sky', 'Keep the original orientation and match adjoining edges.'
    return 'color', 'Keep painted details, alpha, and the original tiling.'


def export_png(asset, filename):
    task = unreal.AssetExportTask()
    for name, value in (
        ('object', asset), ('filename', str(filename)), ('exporter', unreal.TextureExporterPNG()),
        ('automated', True), ('prompt', False), ('replace_identical', False),
    ):
        task.set_editor_property(name, value)
    if not unreal.Exporter.run_asset_export_task(task) or not filename.is_file():
        raise ValueError(f'Unreal could not export {asset.get_name()} as PNG.')


def export_selected(draft):
    entries = []
    for asset in unreal.EditorUtilityLibrary.get_selected_assets():
        if isinstance(asset, unreal.Texture2D) and ASSET.fullmatch(asset.get_path_name()):
            role, notes = texture_role(asset)
            entries.append((asset.get_path_name(), role, notes, asset))
    path = create_batch(os.environ['T3_TEXTURE_LIBRARY'], draft, entries, export_png)
    unreal.EditorDialog.show_message(
        'Texture batch exported', f'{path}\nPut edited PNGs in replacements using the original file names.',
        unreal.AppMsgType.OK)
    return path


def pin_approved(root, source, digest):
    # Keep the exact approved source art for Unreal reimport after the review.
    folder = root / 'approved' / digest
    folder.mkdir(parents=True, exist_ok=True)
    if folder.resolve(strict=True) != folder:
        raise ValueError('The approved texture folder must not link elsewhere.')
    pinned = folder / source.name
    if not pinned.exists():
        with TemporaryDirectory(dir=folder) as work:
            staged = Path(work) / source.name
            shutil.copyfile(source, staged)
            if texture_png(staged)['sha256'] != digest:
                raise ValueError('The edited PNG changed during import.')
            staged.rename(pinned)
    if texture_png(pinned)['sha256'] != digest:
        raise ValueError('The approved texture source changed.')
    return pinned


def import_texture(pinned, destination, texture_class, *, factory=None, name=None):
    asset_name = name or pinned.stem
    object_path = f'{destination}/{asset_name}.{asset_name}'
    task = unreal.AssetImportTask()
    for option, value in (
        ('automated', True), ('destination_path', destination), ('filename', str(pinned)),
        ('replace_existing', False), ('save', False),
    ):
        task.set_editor_property(option, value)
    if factory is not None:
        task.set_editor_property('factory', factory)
    if name is not None:
        task.set_editor_property('destination_name', name)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    imported = task.get_objects()
    if (len(imported) != 1 or not isinstance(imported[0], texture_class)
            or imported[0].get_path_name() != object_path):
        raise ValueError(f'Unreal did not create the expected {texture_class.__name__} asset: {object_path}')
    return imported[0]


def source_sky_name(slot, reference):
    stem = PurePosixPath(reference).stem
    label = re.sub(r'[^A-Za-z0-9_]', '_', stem[-70:])
    return f'T_Sky_{slot:02}_{label}'


def import_source_sky(folder, inventory, draft, library):
    assets = unreal.EditorAssetLibrary
    batches = []
    for sky in load_sky_index(folder, inventory):
        destination = f"/Game/Community/{draft}/SourceSky/Sky_{sky['source_id']}"
        entries = []
        for slot, entry in enumerate(sky['slots'], 1):
            if 'file' not in entry:
                continue
            source, art = checked_sky_file(folder, entry['file'], inventory, 16 * 1024 * 1024)
            texture = import_texture(source, destination, unreal.Texture2D, name=source_sky_name(slot, entry['reference']))
            texture.set_editor_property('srgb', True)
            assets.set_metadata_tag(texture, 'T3SkySourceReference', entry['reference'])
            assets.set_metadata_tag(texture, 'T3SkySourceHash', entry['source_sha256'])
            assets.set_metadata_tag(texture, 'T3SkyDMLHash', sky['source_sha256'])
            if not assets.save_loaded_asset(texture, only_if_is_dirty=False):
                raise ValueError(f'Unreal could not save the authored sky texture {texture.get_path_name()}.')
            entries.append((texture.get_path_name(), 'sky', f"DML slot {slot} · {entry['reference']}", art))
        if entries:
            batch = create_batch(library, draft, entries, lambda data, output: output.write_bytes(data))
            batches.append(batch.name)
    return batches


def import_version(source_asset, pinned, destination, origin):
    assets = unreal.EditorAssetLibrary
    name = pinned.stem
    object_path = f'{destination}/{name}.{name}'
    if assets.does_asset_exist(object_path):
        current = assets.load_asset(object_path)
        if not isinstance(current, unreal.Texture2D):
            raise ValueError(f'The draft revision path is already in use: {object_path}')
        data = current.get_editor_property('asset_import_data')
        if not data or Path(data.get_first_filename()).resolve() != pinned:
            raise ValueError(f'The draft revision path is already in use: {object_path}')
    else:
        current = import_texture(pinned, destination, unreal.Texture2D)
    for option in ('srgb', 'compression_settings', 'lod_group', 'mip_gen_settings', 'address_x', 'address_y'):
        current.set_editor_property(option, source_asset.get_editor_property(option))
    assets.set_metadata_tag(current, SOURCE_TAG, origin)
    if not assets.save_loaded_asset(current, only_if_is_dirty=False):
        raise ValueError(f'Unreal could not save {object_path}.')
    return current


def import_sky_cube(draft, root, items, name):
    source, digest = verify_sky_build(root, items, name)
    batch = root.name.removeprefix('batch-')
    revision = name.removeprefix('sky-')
    destination = f'/Game/Community/{draft}/TextureRevisions/Sky/B_{batch}/R_{revision}'
    object_path = f'{destination}/SkyCube.SkyCube'
    assets = unreal.EditorAssetLibrary
    if assets.does_asset_exist(object_path):
        cube = assets.load_asset(object_path)
        data = cube.get_editor_property('asset_import_data') if isinstance(cube, unreal.TextureCube) else None
        if (not data or Path(data.get_first_filename()).resolve() != source
                or assets.get_metadata_tag(cube, SKY_DDS_TAG) != digest):
            raise ValueError(f'The draft cubemap path is already in use: {object_path}')
    else:
        cube = import_texture(source, destination, unreal.TextureCube, factory=unreal.TextureFactory())
        assets.set_metadata_tag(cube, SKY_DDS_TAG, digest)
    if not assets.save_loaded_asset(cube, only_if_is_dirty=False):
        raise ValueError(f'Unreal could not save {object_path}.')
    save_json(source.parent / 'sky-imported.json', {'asset': object_path, 'dds_sha256': digest})
    return cube


def review_and_import(draft, get_actors):
    library = os.environ['T3_TEXTURE_LIBRARY']
    result = subprocess.run(
        [os.environ['T3_CONVERTER_PYTHON'], '-E', '-X', 'utf8',
         str(Path(os.environ['T3_CONVERTER_TOOL']) / 'texture_review.py'), library, draft],
        capture_output=True, encoding='utf-8', check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-800:] or 'The texture review window failed.')
    if not result.stdout.strip():
        return
    selection = json.loads(result.stdout)
    if not isinstance(selection, dict) or selection.keys() not in ({'batch', 'sky'}, {'batch', 'apply_to_map'}):
        raise ValueError('Invalid texture review selection.')
    root, items = load_batch(library, draft, selection['batch'])
    if 'sky' in selection:
        cube = import_sky_cube(draft, root, items, selection['sky'])
        unreal.EditorAssetLibrary.sync_browser_to_objects([cube.get_path_name()])
        return [cube]
    if type(selection['apply_to_map']) is not bool:
        raise ValueError('Invalid texture review selection.')
    plan = []
    for row, replacement, digest in approved_replacements(root, items):
        source = unreal.EditorAssetLibrary.load_asset(row['asset'])
        if not isinstance(source, unreal.Texture2D):
            raise ValueError(f'The original texture is missing: {row["asset"]}')
        pinned = pin_approved(root, replacement, digest)
        revision = 'B_' + root.name.removeprefix('batch-') + '/R_' + digest[:16]
        destination = f'/Game/Community/{draft}/TextureRevisions/{revision}'
        origin = texture_origin(source, draft)
        plan.append((source, pinned, destination, row['id'], digest, origin))
    if selection['apply_to_map'] and len({entry[-1] for entry in plan}) != len(plan):
        raise ValueError('Approve one revision per original texture before applying the batch to the draft.')
    completed = []
    revisions = {}
    for source, pinned, destination, ident, digest, origin in plan:
        asset = import_version(source, pinned, destination, origin)
        completed.append(asset)
        revisions[origin] = asset
        save_json(root / f'imported-{ident}-{digest[:16]}.json',
                  {'id': ident, 'sha256': digest, 'asset': asset.get_path_name()})
    if completed:
        unreal.EditorAssetLibrary.sync_browser_to_objects([asset.get_path_name() for asset in completed])
    if completed and selection['apply_to_map']:
        slots = apply_texture_revisions(draft, get_actors(), revisions)
        message = (f'Applied approved textures to {slots} draft mesh material slot(s).' if slots else
                   'Textures imported. No matching draft mesh slots needed a change.')
        unreal.EditorDialog.show_message('Draft texture revisions', message, unreal.AppMsgType.OK)
    return completed
