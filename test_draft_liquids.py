"""Draft lava rows reach the client's liquid hook through objectives.lua (T3BackEndServer#150)."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from lupa.lua54 import LuaRuntime

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from export_t2_draft import draft_liquids, objectives_lua, with_client_liquids  # noqa: E402
from hazards_ue5 import liquid_row  # noqa: E402

ROW = liquid_row("utkillzvolume_1", [-24115.2, 45465.6, -127.0], [-19200.0, 50380.8, 5010.0], 4860.0)
EDITOR = ('local bind=(function()\nreturn function(factory) return factory end\nend)()\n'
          'return setmetatable({editor_layout={["format"]=1,["map_id"]="DraftTA023"}},\n'
          ' {__call=function(_,factory) return bind(factory) end})\n')


class DraftLiquidTests(unittest.TestCase):
    def run_file(self, script):
        lua = LuaRuntime()
        return lua.execute(script)

    def test_rows_are_validated_from_the_run_folder(self):
        with TemporaryDirectory() as folder:
            run = Path(folder)
            self.assertIsNone(draft_liquids(run, "DraftTA023"))
            (run / "liquids.json").write_text(json.dumps({"map": "DraftTA023", "liquids": [ROW]}), encoding="utf-8")
            self.assertEqual(draft_liquids(run, "DraftTA023"), [ROW])
            with self.assertRaises(ValueError):
                draft_liquids(run, "DraftTA024")
            bad = dict(ROW, surface_z_cm=9000.0)
            (run / "liquids.json").write_text(json.dumps({"map": "DraftTA023", "liquids": [bad]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                draft_liquids(run, "DraftTA023")

    def test_equipment_script_keeps_the_editor_layout_for_the_host(self):
        table = self.run_file(with_client_liquids(EDITOR, "DraftTA023", [ROW]))
        self.assertEqual(table["editor_layout"]["map_id"], "DraftTA023")
        self.assertEqual((table["format"], table["map_id"]), (1, "DraftTA023"))
        self.assertEqual(table["world"], "/Game/Maps/Community/DraftTA023/Arena_DraftTA023_P.Arena_DraftTA023_P:")
        self.assertEqual(table["liquids"][1]["liquid_type"], "lava")
        self.assertEqual(table["liquids"][1]["surface_z_cm"], 4860.0)

    def test_ctf_only_script_returns_the_factory_then_the_client_table(self):
        script = with_client_liquids(objectives_lua([[1, 0, 0, 0, 0]], [["TeamA", 0, 0, 0]]), "DraftTA023", [ROW])
        lua = LuaRuntime()
        first, second = lua.execute(script)
        self.assertEqual(lua.eval("type")(first), "function")
        self.assertEqual(second["liquids"][1]["splash_pool_tag"], "T3HazardSplash")


if __name__ == "__main__":
    unittest.main()
