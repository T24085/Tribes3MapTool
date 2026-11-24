"""Protect actor, component and relationship fidelity across legacy T3D exports."""

from hashlib import sha256
import math
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

TOOL = Path(__file__).resolve().parent / 'tools/community_map_editor'
sys.path.insert(0, str(TOOL))
from unreal_legacy_reader import read_unreal_export


VENGEANCE = """Begin Map Name=OriginalFixture
 Begin Actor Class=Gameplay.TeamInfo Name=Team0
  LocalizedName="Café"
 End Actor
 Begin Actor Class=Gameplay.BaseInfo Name=BlueBase
  team=TeamInfo'OriginalFixture.Team0'
 End Actor
 Begin Actor Class=Gameplay.PlayerStart Name=BlueLaunch
  Team=TeamInfo'Team0'
  BaseInfo=BaseInfo'BlueBase'
  Location=(X=250,Y=-500,Z=100)
  Rotation=(Yaw=16384)
  DrawScale=2
  DrawScale3D=(X=1,Y=-0.5,Z=1.25)
  bObserverStart=False
 End Actor
 Begin Actor Class=Gameplay.MPFlag Name=BlueFlag
  Team=TeamInfo'Team0'
 End Actor
 Begin Actor Class=Engine.Brush Name=OriginalBrush
  Begin Brush Name=FixtureBSP
   Begin PolyList
    Begin Polygon Item=Example Texture=Example.Surface
     Vertex +00001.000000,+00000.000000,+00000.000000
    End Polygon
   End PolyList
  End Brush
 End Actor
End Map
"""

ASCEND = """Begin Map
 Begin Level Name=PersistentLevel
  Begin Actor Class=Engine.StaticMeshActor Name=Platform Archetype=StaticMeshActor'Engine.Default__StaticMeshActor'
   Begin Object Class=Engine.StaticMeshComponent Name=PlatformMesh
    StaticMesh=StaticMesh'FixtureDeck.Platform'
    Translation=(X=50,Y=125,Z=-25)
    Rotation=(Pitch=-16384)
    Scale=0.5
   End Object
   RootComponent=StaticMeshComponent'PlatformMesh'
   CustomProperties(1)="value = preserved"
   AttachParent=Actor'OtherLevel.Pylon'
  End Actor
  Begin Actor Class=Fixture.SpecialTrigger Name=Pylon
   Location=(Y=50)
  End Actor
 End Level
 Begin Level Name=OtherLevel
  Begin Actor Class=Fixture.SpecialTrigger Name=Pylon
   Location=(Y=-50)
  End Actor
 End Level
End Map
"""


class UnrealLegacyReaderTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def read(self, text, game, encoding='utf-8', units=50):
        path = self.root / 'Fixture.t3d'
        data = text.replace('\n', '\r\n').encode(encoding)
        path.write_bytes(data)
        result = read_unreal_export(path, game, units)
        self.assertEqual(result['source']['sha256'], sha256(data).hexdigest())
        return result

    def test_vengeance_roles_and_transforms_retain_source_relationships_and_brush(self):
        data = self.read(VENGEANCE, 'vengeance', 'cp1252')
        team, base, start, flag, brush = data['actors']
        self.assertEqual([item['role'] for item in data['actors']], ['team', 'base', 'spawn', 'flag', 'brush'])
        self.assertEqual(start['links'], {'team': team['line'], 'baseinfo': base['line']})
        self.assertEqual(base['links'], {'team': team['line']})
        self.assertEqual(flag['links'], {'team': team['line']})
        placement = start['transform']
        self.assertEqual(placement['position_m'], [5, -10, 2])
        self.assertEqual(placement['scale'], [2, -1, 2.5])
        self.assertTrue(placement['authored'])
        self.assertAlmostEqual(placement['rotation_xyzw'][2], math.sqrt(.5))
        self.assertAlmostEqual(placement['rotation_xyzw'][3], math.sqrt(.5))
        self.assertEqual(start['unmapped_fields'][0]['key'], 'bObserverStart')
        entries = data['scene'][0]['entries']
        self.assertEqual(entries[0]['entries'][0]['value'], '"Café"')
        bsp = entries[-1]['entries'][0]['entries'][0]['entries'][0]
        self.assertEqual(bsp['block'], 'Polygon')
        self.assertEqual(bsp['entries'][0]['statement'], 'Vertex +00001.000000,+00000.000000,+00000.000000')
        self.assertEqual(brush['source_class'], 'Engine.Brush')
        self.assertTrue(any('brush polygons' in reason for reason in brush['review']))

    def test_ascend_components_dont_override_actor_transforms_or_bind_another_level(self):
        data = self.read(ASCEND, 'ascend', 'utf-16', units=100)
        platform, local, distant = data['actors']
        self.assertEqual(platform['role'], 'static-mesh')
        self.assertEqual(platform['level_name'], 'PersistentLevel')
        self.assertEqual(platform['level_lines'], local['level_lines'])
        self.assertNotEqual(platform['level_lines'], distant['level_lines'])
        self.assertEqual(platform['transform']['position_m'], [0, 0, 0])
        part, = platform['components']
        matrix = part['transform']
        self.assertEqual(matrix['position_m'], [.5, 1.25, -.25])
        self.assertEqual(matrix['scale'], [.5, .5, .5])
        self.assertEqual(matrix['space'], 'component-relative')
        self.assertAlmostEqual(matrix['rotation_xyzw'][1], math.sqrt(.5))
        self.assertEqual(platform['assets'][0]['source'], 'FixtureDeck.Platform')
        self.assertEqual(platform['unmapped_fields'][0]['key'], 'CustomProperties(1)')
        self.assertNotIn('attachparent', platform['links'])
        self.assertTrue(any('root component' in reason for reason in platform['review']))
        self.assertEqual(local['role'], None)
        self.assertEqual(data['coverage']['roles'], {'static-mesh': 1, 'unmapped': 2})

    def test_bad_placement_is_reviewed_and_bad_export_hierarchy_is_rejected(self):
        text = VENGEANCE.replace('Location=(X=250,Y=-500,Z=100)', 'Location=(X=1,X=2)')
        data = self.read(text, 'vengeance')
        start = data['actors'][2]
        self.assertIsNone(start['transform'])
        self.assertTrue(any('duplicate axis' in reason for reason in start['review']))
        self.assertEqual(len(data['actors']), 5)
        with self.assertRaisesRegex(ValueError, 'Unmatched'):
            self.read(text.replace('End Polygon', 'End Brush'), 'vengeance')
        with self.assertRaisesRegex(ValueError, 'positive'):
            self.read(text, 'vengeance', units=0)
        absolute = ASCEND.replace('Scale=0.5', 'Scale=0.5\n    AbsoluteTranslation=True')
        platform = self.read(absolute, 'ascend')['actors'][0]
        self.assertIsNone(platform['components'][0]['transform'])
        self.assertTrue(any('absolute transform' in reason for reason in platform['review']))

    def test_cli_detects_native_package_and_never_overwrites_review(self):
        binary = self.root / 'Original.fmap'
        binary.write_bytes(bytes.fromhex('c1832a9e00000000'))
        with self.assertRaisesRegex(ValueError, 'package reader'):
            read_unreal_export(binary, 'ascend', 50)
        text = self.root / 'Original.t3d'
        text.write_text(ASCEND, encoding='utf-8')
        output = self.root / 'review.json'
        command = [sys.executable, '-B', str(TOOL / 'unreal_legacy_reader.py'), str(text), str(output),
                   '--game', 'ascend', '--units-per-metre', '50']
        subprocess.run(command, check=True, capture_output=True)
        published = output.read_bytes()
        repeated = subprocess.run(command, capture_output=True)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(output.read_bytes(), published)
