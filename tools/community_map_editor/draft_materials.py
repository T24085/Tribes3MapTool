"""Apply reviewed texture revisions to materials used by the active draft."""

import uuid

import unreal

from texture_batches import ASSET


SOURCE_TAG = 'T3TextureSource'


def texture_origin(texture, draft):
    path = texture.get_path_name()
    if path.startswith(f'/Game/Community/{draft}/TextureRevisions/'):
        original = unreal.EditorAssetLibrary.get_metadata_tag(texture, SOURCE_TAG)
        if isinstance(original, str) and ASSET.fullmatch(original):
            return original
    return path


def parameter_replacements(material, draft, replacements):
    library = unreal.MaterialEditingLibrary
    if isinstance(material, unreal.MaterialInstanceConstant):
        get = library.get_material_instance_texture_parameter_value
    elif isinstance(material, unreal.Material):
        get = library.get_material_default_texture_parameter_value
    else:
        return {}
    changes = {}
    for parameter in library.get_texture_parameter_names(material):
        current = get(material, parameter)
        if not isinstance(current, unreal.Texture2D):
            continue
        replacement = replacements.get(texture_origin(current, draft))
        if replacement is not None and replacement.get_path_name() != current.get_path_name():
            changes[str(parameter)] = replacement
    return changes


def make_revision(material, changes, folder):
    assets, library = unreal.EditorAssetLibrary, unreal.MaterialEditingLibrary
    name = 'MI_Revision_' + uuid.uuid4().hex[:16]
    path = f'{folder}/{name}'
    if (isinstance(material, unreal.MaterialInstanceConstant)
            and material.get_path_name().startswith(folder + '/')):
        # Copy an earlier managed revision so existing overrides survive without
        # building a chain of dependent material instances.
        revision = assets.duplicate_asset(material.get_path_name(), path)
    else:
        revision = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
        if revision:
            library.set_material_instance_parent(revision, material)
    if not isinstance(revision, unreal.MaterialInstanceConstant):
        raise ValueError(f'Could not create draft material revision for {material.get_name()}.')

    for name, texture in changes.items():
        # UE 5.3 reports false even when this setter writes the parameter.
        library.set_material_instance_texture_parameter_value(revision, name, texture)
        actual = library.get_material_instance_texture_parameter_value(revision, name)
        if not actual or actual.get_path_name() != texture.get_path_name():
            raise ValueError(f'Could not verify the draft texture parameter {name}.')
    library.update_material_instance(revision)
    if not assets.save_loaded_asset(revision, only_if_is_dirty=False):
        raise ValueError('Could not save the draft material revision.')
    return revision


def apply_texture_revisions(draft, actors, replacements):
    material_folder = f'/Game/Community/{draft}/TextureRevisions/Materials'
    level_name = f'Arena_{draft}_P'
    level = f'/Game/Maps/Community/{draft}/{level_name}.{level_name}:'
    revisions = {}
    plan = []
    for actor in actors:
        if not actor.get_path_name().startswith(level):
            continue
        for component in actor.get_components_by_class(unreal.MeshComponent):
            edits = {}
            for index, material in enumerate(component.get_materials()):
                if not material:
                    continue
                asset = material.get_path_name()
                if asset not in revisions:
                    changes = parameter_replacements(material, draft, replacements)
                    revisions[asset] = make_revision(material, changes, material_folder) if changes else None
                if revisions[asset] is not None:
                    edits[index] = revisions[asset]
            if edits:
                original = list(component.get_editor_property('override_materials'))
                replacement = original + [None] * max(0, max(edits) + 1 - len(original))
                for index, material in edits.items():
                    replacement[index] = material
                plan.append((component, original, replacement, edits))
    if not plan:
        return 0

    changed = []
    with unreal.ScopedEditorTransaction('Apply reviewed draft textures') as transaction:
        try:
            for component, original, replacement, edits in plan:
                component.modify()
                changed.append((component, original))
                component.set_editor_property('override_materials', replacement)
                for index, material in edits.items():
                    current = component.get_material(index)
                    if not current or current.get_path_name() != material.get_path_name():
                        raise ValueError('A draft mesh did not accept the material revision.')
        except Exception as error:
            restore_error = None
            for component, original in reversed(changed):
                try:
                    component.set_editor_property('override_materials', original)
                except Exception as failure:
                    restore_error = failure
            if restore_error:
                raise RuntimeError(f'A draft material override could not be restored: {restore_error}') from error
            transaction.cancel()
            raise
    return sum(len(edits) for _, _, _, edits in plan)
