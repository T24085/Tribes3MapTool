"""Check that batch revisions affect only the intended draft material slots."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).with_name('tools') / 'community_map_editor'))
DRAFT = 'DraftT2001'
LEVEL = f'/Game/Maps/Community/{DRAFT}/Arena_{DRAFT}_P.Arena_{DRAFT}_P:'


class Asset:
    def __init__(self, path):
        self.path = path

    def get_path_name(self):
        return self.path

    def get_name(self):
        return self.path.rsplit('/', 1)[-1]


class Texture(Asset):
    def __init__(self, path, origin=''):
        super().__init__(path)
        self.origin = origin


class Material(Asset):
    def __init__(self, path, values):
        super().__init__(path)
        self.values = values


class Instance(Material):
    def __init__(self, path, values, parent=None):
        super().__init__(path, values)
        self.parent = parent


class Component:
    def __init__(self, defaults, overrides=None, fail_once=False):
        self.defaults = defaults
        self.overrides = list(overrides or ())
        self.fail_once = fail_once

    def get_materials(self):
        return [self.get_material(index) for index in range(len(self.defaults))]

    def get_material(self, index):
        return (self.overrides[index] if index < len(self.overrides) else None) or self.defaults[index]

    def get_editor_property(self, name):
        assert name == 'override_materials'
        return self.overrides

    def modify(self):
        return True

    def set_editor_property(self, name, value):
        assert name == 'override_materials'
        if self.fail_once:
            self.fail_once = False
            return
        self.overrides = list(value)


class TextureMaterialTest(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.duplicates = []
        self.transactions = []

        def create_asset(name, folder, _type, _factory):
            return Instance(f'{folder}/{name}.{name}', {})

        def duplicate_asset(source, target):
            original = next(asset for asset in self.saved if asset.path == source)
            self.duplicates.append(source)
            return Instance(f'{target}.{target.rsplit("/",1)[-1]}', dict(original.values), original.parent)

        def set_parameter(asset, key, value):
            asset.values[key] = value
            return True

        def read_parameter(asset, key):
            if key in asset.values:
                return asset.values[key]
            return read_parameter(asset.parent, key) if isinstance(asset, Instance) and asset.parent else None

        def parameter_names(asset):
            names = set(asset.values)
            if isinstance(asset, Instance) and asset.parent:
                names.update(parameter_names(asset.parent))
            return sorted(names)

        def transaction(_):
            item = Mock()
            item.__enter__ = Mock(return_value=item)
            item.__exit__ = Mock(return_value=False)
            self.transactions.append(item)
            return item

        library = SimpleNamespace(
            get_material_instance_texture_parameter_value=read_parameter,
            get_material_default_texture_parameter_value=read_parameter,
            get_texture_parameter_names=parameter_names,
            set_material_instance_parent=lambda material, parent: setattr(material, 'parent', parent),
            set_material_instance_texture_parameter_value=set_parameter,
            update_material_instance=Mock(),
        )
        assets = SimpleNamespace(
            get_metadata_tag=lambda texture, _: texture.origin,
            duplicate_asset=duplicate_asset,
            save_loaded_asset=lambda asset, **_: self.saved.append(asset) or True,
        )
        unreal = SimpleNamespace(
            Texture2D=Texture, Material=Material, MaterialInstanceConstant=Instance,
            MaterialInstanceConstantFactoryNew=object, MaterialEditingLibrary=library, EditorAssetLibrary=assets,
            MeshComponent=Component, ScopedEditorTransaction=transaction,
            AssetToolsHelpers=SimpleNamespace(get_asset_tools=lambda: SimpleNamespace(create_asset=create_asset)),
        )
        source = Path(__file__).with_name('tools') / 'community_map_editor' / 'draft_materials.py'
        spec = importlib.util.spec_from_file_location('draft_material_test_module', source)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'unreal': unreal}):
            spec.loader.exec_module(self.module)
        self.original = Texture('/Game/Community/Source/T_Grass.T_Grass')
        self.normal = Texture('/Game/Community/Source/T_Normal.T_Normal')
        self.shared = Material('/Game/Community/Source/M_Grass.M_Grass',
                               {'GrassColor': self.original, 'GrassNormal': self.normal})
        self.change = Texture(f'/Game/Community/{DRAFT}/TextureRevisions/A/T_001.T_001', self.original.path)

    def actor(self, suffix, component, level=LEVEL):
        return SimpleNamespace(get_path_name=lambda: level + suffix,
                               get_components_by_class=lambda _: [component])

    def test_applies_only_matching_slots_and_keeps_prior_revisions(self):
        one, two, foreign = (Component([self.shared]) for _ in range(3))
        actors = [self.actor('First', one), self.actor('Second', two),
                  self.actor('DifferentLevel', foreign, level='/Game/Maps/Another:')]
        apply = self.module.apply_texture_revisions
        self.assertEqual(apply(DRAFT, actors, {self.original.path: self.change}), 2)
        first = one.get_material(0)
        self.assertIs(first, two.get_material(0))
        self.assertIs(first.parent, self.shared)
        self.assertIs(foreign.get_material(0), self.shared)
        self.assertIs(self.shared.values['GrassColor'], self.original)
        self.assertEqual(apply(DRAFT, actors, {self.original.path: self.change}), 0)

        first.values['GrassNormal'] = self.normal
        next_texture = Texture(f'/Game/Community/{DRAFT}/TextureRevisions/B/T_001.T_001', self.original.path)
        self.assertEqual(apply(DRAFT, actors, {self.original.path: next_texture}), 2)
        later = one.get_material(0)
        self.assertIs(later.parent, self.shared)
        self.assertIs(later.values['GrassNormal'], self.normal)
        self.assertIs(later.values['GrassColor'], next_texture)
        self.assertIs(first.values['GrassColor'], self.change)
        self.assertEqual(self.duplicates, [first.path])

    def test_a_failed_mesh_assignment_restores_every_original_override(self):
        one = Component([self.shared])
        two = Component([self.shared], overrides=[self.shared], fail_once=True)
        with self.assertRaisesRegex(ValueError, 'did not accept'):
            self.module.apply_texture_revisions(DRAFT, [self.actor('First', one), self.actor('Second', two)],
                                                {self.original.path: self.change})
        self.assertEqual(one.overrides, [])
        self.assertEqual(two.overrides, [self.shared])
        self.transactions[-1].cancel.assert_called_once()


if __name__ == '__main__':
    unittest.main()
