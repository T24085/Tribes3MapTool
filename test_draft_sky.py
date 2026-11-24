"""Exercise the draft-local sky assets and actor handoff with Unreal API fixtures."""

import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch


sys.path.insert(0, str(Path(__file__).with_name('tools') / 'community_map_editor'))
DRAFT = 'DraftT2001'
LEVEL = f'/Game/Maps/Community/{DRAFT}/Arena_{DRAFT}_P.Arena_{DRAFT}_P:PersistentLevel.'
SKY_FOLDER = f'/Game/Community/{DRAFT}/TextureRevisions/Sky'


class Vector:
    def __init__(self, *values):
        self.values = values

    def to_tuple(self):
        return self.values

    def __add__(self, other):
        return Vector(*(a + b for a, b in zip(self.values, other.values)))

    def __sub__(self, other):
        return Vector(*(a - b for a, b in zip(self.values, other.values)))


class DraftSkyTest(unittest.TestCase):
    def setUp(self):
        self.cube = Mock(spec=type('TextureCube', (), {}))
        self.material = Mock(spec=type('Material', (), {}))
        self.mesh = Mock(spec=type('StaticMesh', (), {}))
        self.actor = Mock(spec=type('StaticMeshActor', (), {}))
        self.component = Mock()
        self.actor.static_mesh_component = self.component
        self.actor.get_path_name = Mock(return_value=LEVEL + 'Backdrop')
        self.actor.actor_has_tag = Mock(side_effect=lambda tag: tag == 'T3DraftSkyBackdrop')
        self.actor.set_actor_scale3d = Mock()
        self.actor.set_actor_label = Mock()
        self.actor.set_folder_path = Mock()
        self.actor.set_editor_property = Mock()
        self.cube.get_editor_property = Mock(return_value=True)
        self.cube.get_path_name = Mock(return_value=SKY_FOLDER + '/B_abcdef123456/R_abcdef1234567890/SkyCube.SkyCube')
        self.material.get_path_name = Mock(return_value=f"{self.cube.get_path_name().rsplit('/', 1)[0]}/M_DraftSky.M_DraftSky")
        self.material.set_editor_property = Mock()
        self.component.get_editor_property.return_value = []
        self.component.get_material.return_value = self.material
        self.unreal = Mock(
            Vector=Vector, TextureCube=self.cube.__class__, Material=self.material.__class__,
            StaticMesh=self.mesh.__class__, StaticMeshActor=self.actor.__class__,
            ScopedEditorTransaction=MagicMock())
        self.assets = self.unreal.EditorAssetLibrary
        self.assets.does_asset_exist.return_value = False
        self.assets.get_metadata_tag.return_value = 'a' * 64
        self.unreal.EditorUtilityLibrary.get_selected_assets.return_value = [self.cube]
        self.unreal.AssetToolsHelpers.get_asset_tools.return_value.create_asset.return_value = self.material
        self.unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh.return_value = (
            self.mesh, self.unreal.GeometryScriptOutcomePins.SUCCESS)
        self.ground = Mock()
        self.ground.get_path_name.return_value = LEVEL + 'Ground'
        self.ground.actor_has_tag.return_value = False
        self.ground.get_actor_bounds.return_value = (Vector(20000, -50000, 1000), Vector(40000, 30000, 4000))
        self.ground_mesh = Mock()
        self.ground_mesh.get_materials.return_value = []
        self.ground.get_components_by_class.return_value = [self.ground_mesh]
        self.actors = Mock()
        self.cloud = Mock()
        self.cloud.get_path_name.return_value = LEVEL + 'GeneratedCloud'
        self.cloud.actor_has_tag.side_effect = lambda tag: tag == 'T3DraftCloudLayer'
        self.cloud.get_actor_bounds.side_effect = AssertionError('Do not frame a sky around its own cloud canopy.')
        self.actors.get_all_level_actors.return_value = [self.ground, self.cloud]
        self.actors.spawn_actor_from_class.return_value = self.actor
        self.enterContext(patch.dict(sys.modules, {'unreal': self.unreal}))
        self.sky = importlib.import_module('draft_sky')

    def test_creates_draft_owned_shell_and_switches_existing_material(self):
        self.assertIs(self.sky.apply_selected_sky(DRAFT, self.actors), self.actor)
        kind, center = self.actors.spawn_actor_from_class.call_args.args
        self.assertIs(kind, self.actor.__class__)
        self.assertEqual(center.to_tuple(), (20000, -50000, 1000))
        self.assertGreater(min(self.actor.set_actor_scale3d.call_args.args[0].to_tuple()), 2000)
        geometry = self.unreal.GeometryScriptSimpleMeshBuffers.call_args.kwargs
        self.assertEqual(len(geometry['vertices']), 24)
        self.assertEqual(len(geometry['triangles']), 12)
        self.assertEqual(len(geometry['uv0']), 24)
        self.assertEqual({abs(value) for point in geometry['vertices'] for value in point.to_tuple()}, {50})
        self.component.set_collision_profile_name.assert_called_once_with('NoCollision')
        self.component.set_editor_property.assert_any_call('generate_overlap_events', False)
        self.component.set_cast_shadow.assert_called_once_with(False)
        self.component.set_hidden_in_scene_capture.assert_called_once_with(True)
        self.material.set_editor_property.assert_any_call('is_sky', True)
        graph = self.unreal.MaterialEditingLibrary
        self.assertEqual([call.args[-1] for call in graph.connect_material_expressions.call_args_list], ['', ''])
        graph.connect_material_property.assert_called_once_with(
            graph.create_material_expression.return_value, 'RGB', self.unreal.MaterialProperty.MP_EMISSIVE_COLOR)

        self.actors.get_all_level_actors.return_value = [self.ground, self.cloud, self.actor]
        earlier_cube_path = SKY_FOLDER + '/B_abcdef123456/R_654321abcdef1234/SkyCube.SkyCube'
        earlier = Mock(spec=self.material.__class__)
        earlier.get_path_name = Mock(return_value=f"{earlier_cube_path.rsplit('/', 1)[0]}/M_DraftSky.M_DraftSky")
        self.cube.get_path_name.return_value = earlier_cube_path
        self.assets.does_asset_exist.return_value = True
        self.assets.load_asset.return_value = earlier
        self.assets.get_metadata_tag.side_effect = lambda asset, _: 'a' * 64 if asset is self.cube else earlier_cube_path
        self.component.get_material.return_value = earlier
        self.assertIs(self.sky.apply_selected_sky(DRAFT, self.actors), self.actor)
        self.component.set_material.assert_called_with(0, earlier)
        self.assertEqual(self.actors.spawn_actor_from_class.call_count, 1)
        self.assertEqual(self.unreal.GeometryScriptSimpleMeshBuffers.call_count, 1)

    def test_preserves_authored_sky_and_rejects_another_drafts_cube(self):
        base = Mock()
        base.get_editor_property.return_value = True
        self.ground_mesh.get_materials.return_value = [Mock(get_base_material=Mock(return_value=base))]
        with self.assertRaisesRegex(ValueError, 'already has a sky mesh'):
            self.sky.apply_selected_sky(DRAFT, self.actors)
        self.actors.spawn_actor_from_class.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'imported into this draft'):
            self.sky.apply_selected_sky('DraftT2002', self.actors)
        self.unreal.GeometryScriptSimpleMeshBuffers.assert_not_called()

    def test_removes_only_the_failed_new_backdrop(self):
        self.component.get_material.return_value = None
        with self.assertRaisesRegex(ValueError, 'did not accept its material'):
            self.sky.apply_selected_sky(DRAFT, self.actors)
        self.actors.destroy_actor.assert_called_once_with(self.actor)
