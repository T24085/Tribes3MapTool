"""The build check that a draft's rule-set tags match its recorded rules (Tribes3MapTool#26)."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from conversion_rules import rule_tags_match  # noqa: E402

SNAPSHOT = {"sha256": "a" * 64}
TAG = "T3RuleSet_" + "a" * 64


class RuleTagTests(unittest.TestCase):
    def test_tiled_terrain_repeats_the_same_tag(self):
        # Tartarus imports as 2 x 2 terrain tiles, each tagged with the rule set.
        self.assertTrue(rule_tags_match([TAG], SNAPSHOT))
        self.assertTrue(rule_tags_match([TAG] * 4, SNAPSHOT))

    def test_drafts_without_recorded_rules(self):
        self.assertTrue(rule_tags_match([], None))
        self.assertFalse(rule_tags_match([TAG], None))
        self.assertFalse(rule_tags_match([], SNAPSHOT))

    def test_a_different_or_mixed_rule_set_is_refused(self):
        other = "T3RuleSet_" + "b" * 64
        self.assertFalse(rule_tags_match([other], SNAPSHOT))
        self.assertFalse(rule_tags_match([TAG, other], SNAPSHOT))

    def test_the_editor_uses_the_shared_check(self):
        source = (TOOL / "editor.py").read_text(encoding="utf-8")
        self.assertIn("if not rule_tags_match(tags, snapshot):", source)
        self.assertNotIn("tags != ['T3RuleSet_'", source)


if __name__ == "__main__":
    unittest.main()
