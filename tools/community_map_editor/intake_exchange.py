"""Export a verified private intake with portable meshes and heightmaps."""

from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from intake_contract import MAX_MANIFEST_BYTES, bounded, intake_file_limit, load_intake
from intake_terrain_assets import write_heightmaps
from mesh_glb import write_mesh_assets
from t2_reader import unpack_intake


def export_private_intake(manifest, destination):
    manifest = Path(manifest).resolve(strict=True)
    destination = Path(destination)
    if destination.suffix.casefold() != '.zip':
        raise ValueError('Save the private intake as a ZIP file.')

    original = bounded(manifest, MAX_MANIFEST_BYTES)
    header, terrain, _ = load_intake(manifest)
    if bounded(manifest, MAX_MANIFEST_BYTES) != original:
        raise ValueError('The intake manifest changed during export.')

    folder = manifest.parent
    with TemporaryDirectory(dir=destination.parent) as work:
        archive = Path(work) / 'private-intake.zip'
        with ZipFile(archive, 'w', ZIP_DEFLATED) as bundle:
            bundle.writestr('manifest.json', original)
            for name, expected in header['files'].items():
                member = (folder / name).resolve(strict=True)
                if member.parent != folder:
                    raise ValueError('Member outside the private intake: ' + name)
                data = bounded(member, intake_file_limit(name, header['format']))
                if sha256(data).hexdigest() != expected:
                    raise ValueError('Intake member changed during export: ' + name)
                bundle.writestr(name, data)
            intake_sha256 = sha256(original).hexdigest()
            write_mesh_assets(bundle, folder, header, intake_sha256=intake_sha256)
            write_heightmaps(bundle, terrain, intake_sha256)
        unpack_intake(archive, Path(work) / 'verified')
        os.replace(archive, destination)
    return destination
