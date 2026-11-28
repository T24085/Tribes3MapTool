"""Layered Ascend materials (Tartarus's moss ship): the binding check."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from model_textures import valid_layers

SOURCE = {"file": "texture_x.png", "source": "x.png", "source_sha256": "0" * 64, "size": [512, 512]}
SHIP = {"a": {**SOURCE, "uv": [0, 20.0, 20.0]}, "b": {**SOURCE, "uv": [0, 15.0, 15.0]},
        "mask": {**SOURCE, "uv": [0, 1.0, 1.0], "blend": "B", "shade": "R"}}


class LayerTests(unittest.TestCase):
    def test_moss_ship_binding_is_valid(self):
        self.assertTrue(valid_layers(SHIP, 1))
        self.assertTrue(valid_layers({**SHIP, "mask": {**SHIP["mask"], "shade": "G"}}, 1))
        no_shade = dict(SHIP["mask"])
        del no_shade["shade"]
        self.assertTrue(valid_layers({**SHIP, "mask": no_shade}, 1))

    def test_broken_bindings(self):
        for broken in ({**SHIP, "mask": {**SHIP["mask"], "blend": "X"}}, {"a": SHIP["a"], "b": SHIP["b"]},
                       {**SHIP, "a": {**SHIP["a"], "uv": [2, 20.0, 20.0]}}, {**SHIP, "b": {**SHIP["b"], "extra": 1}},
                       {**SHIP, "a": {**SHIP["a"], "blend": "B"}}):
            self.assertFalse(valid_layers(broken, 1), broken)


if __name__ == "__main__":
    unittest.main()
