"""Prefab pieces inherit their mesh, rotation and scale from in-package archetypes (Katabatic)."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
import unreal_binary_scene  # noqa: E402
from unreal_binary_scene import inherit_archetype  # noqa: E402

ARCHETYPE_FIELDS = {
    8631: [dict(line=8631, key="StaticMesh", value="ARCH_DiamondSword.SM.SM_DS_Atrium_Catwalk_02")],
    1548: [dict(line=1548, key="Rotation", value="(Pitch=0,Yaw=16384,Roll=0)"),
           dict(line=1548, key="Location", value="(X=528.0,Y=-1024.0,Z=-256.0)"),
           dict(line=1548, key="Base", value=7)],
}
OBJECTS = {
    8631: dict(index=8631, archetype=-245, object_path="Prefab_DS_GeneratorAtrium_Arc2.StaticMeshComponent0"),
    1548: dict(index=1548, archetype=0, object_path="Prefab_DS_GeneratorAtrium_Arc2"),
}


def fake_properties(stream, summary, export, names, objects):
    return [], [dict(field) for field in ARCHETYPE_FIELDS[export["index"]]], [], 0


def row(line, archetype, *entries):
    return dict(line=line, archetype=archetype, entries=list(entries), property_issues=[])


@mock.patch.object(unreal_binary_scene, "read_properties", fake_properties)
class PrefabArchetypeTests(unittest.TestCase):
    def test_a_component_inherits_its_archetype_mesh(self):
        component = row(12597, 8631)
        self.assertEqual(inherit_archetype(component, None, {}, [], OBJECTS), 1)
        self.assertEqual(component["entries"], [dict(line=12597, key="StaticMesh",
                                                     value="ARCH_DiamondSword.SM.SM_DS_Atrium_Catwalk_02")])

    def test_an_actor_inherits_rotation_but_never_location_or_base(self):
        actor = row(10473, 1548, dict(line=10473, key="Location", value="(X=1,Y=2,Z=3)"))
        self.assertEqual(inherit_archetype(actor, None, {}, [], OBJECTS), 1)
        self.assertEqual({field["key"]: field["value"] for field in actor["entries"]},
                         {"Location": "(X=1,Y=2,Z=3)", "Rotation": "(Pitch=0,Yaw=16384,Roll=0)"})

    def test_saved_values_win_over_the_archetype(self):
        actor = row(10175, 1548, dict(line=10175, key="Rotation", value="(Pitch=0,Yaw=0,Roll=0)"))
        self.assertEqual(inherit_archetype(actor, None, {}, [], OBJECTS), 0)
        self.assertEqual(actor["entries"][0]["value"], "(Pitch=0,Yaw=0,Roll=0)")

    def test_class_defaults_and_imports_are_not_read(self):
        self.assertEqual(inherit_archetype(row(5, -245), None, {}, [], OBJECTS), 0)
        self.assertEqual(inherit_archetype(row(5, 0), None, {}, [], OBJECTS), 0)


if __name__ == "__main__":
    unittest.main()
