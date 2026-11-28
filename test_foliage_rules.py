"""Standing rule: ground plants never block players; trees, giant trunks and rocks are not ground plants."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools/community_map_editor"))
from foliage_rules import is_ground_plant


class FoliageRuleTests(unittest.TestCase):
    def test_ground_plants(self):
        for name in ("unrealmesh:swampplanet_landscape.sm.sm_swampplanet_lilypad",
                     "unrealmesh:swampplanet_landscape.sm.sm_swamp_biolamp",
                     "unrealmesh:swampplanet_landscape.sm.sm_swampplanet_waterplant",
                     "unrealmesh:broadside_landscape.sm.sm_broadsides_waterplants02_group",
                     "unrealmesh:swampplanet_landscape.spt_fencevine.sm_fencevine_rt4",
                     "unrealmesh:swampplanet_landscape.sm.sm_swamp_vines", "unrealmesh:arx.sm.sm_arx_grass_clump"):
            self.assertTrue(is_ground_plant(name), name)

    def test_not_ground_plants(self):
        for name in ("unrealmesh:swampplanet_landscape.sm.sm_swamp_mossyrock001",
                     "unrealmesh:swampplanet_landscape.sm.sm_swampplanet_bigtree_canopy",
                     "unrealmesh:swampplanet_landscape.spt_vine_purplelum.sm_swampplanet_vine_purplelum_tree",
                     "unrealmesh:swampplanet_landscape.sm.sm_swampplanet_deadwood",
                     "unrealmesh:katabatic_landscape.sm.sm_katabatic_landscape_rock14",
                     "unrealmesh:swampplanet_ship.sm.sm_swampplanet_ship_scrapmetalsheet",
                     "unrealmesh:common_base.mesh.sm_commonbase_panel_09", ""):
            self.assertFalse(is_ground_plant(name), name)


if __name__ == "__main__":
    unittest.main()
