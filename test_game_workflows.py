"""Game-first workflow layout (#8): menu coverage, step text and file types."""

import ast
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
import game_workflows
import re
from game_workflows import ACTIONS, GAMES, MENU, game_choices, menu_entries, open_filetypes, steps_text, window_title
from source_profiles import NAMES


def editor_commands():
    """The operation names editor.action() accepts, read from its source without importing Unreal."""
    tree = ast.parse((TOOL / "editor.py").read_text(encoding="utf-8"))
    action = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "action")
    commands = next(node for node in ast.walk(action) if isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", None) == "commands")
    return {key.value for key in commands.value.keys}


class GameWorkflowTests(unittest.TestCase):
    def test_every_editor_action_is_reachable_and_valid(self):
        placed = [key for _, _, key, *_ in menu_entries() if key != "@markers"]
        self.assertEqual(set(placed), set(ACTIONS), "every action appears in the menu")
        self.assertTrue(any(key == "@markers" for _, _, key, *_ in menu_entries()))
        commands = editor_commands()
        for key, (label, operation, _) in ACTIONS.items():
            self.assertIn(operation, commands, key)
            self.assertTrue(label)

    def test_submenus_are_unique_and_keys_are_unique_within_each(self):
        self.assertEqual(len({key for key, _, _ in MENU}), len(MENU))
        for submenu, _, entries in MENU:
            keys = [entry[0] if isinstance(entry, tuple) else entry for entry in entries]
            self.assertEqual(len(keys), len(set(keys)), submenu)

    def test_each_game_has_a_convert_submenu_that_starts_with_import(self):
        titles = {title for _, title, _ in MENU}
        for _, profile, _, _ in GAMES:
            title = f"1 · Convert from {NAMES[profile]}"
            self.assertIn(title, titles)
            first = next(key for _, menu_title, key, *_ in menu_entries() if menu_title == title)
            self.assertEqual(first, "ImportT2")

    def test_each_game_menu_opens_the_window_on_that_game(self):
        # Opening the conversion window from "1 · Convert from <game>" starts it on that game.
        for key, profile, _, _ in GAMES:
            title = f"1 · Convert from {NAMES[profile]}"
            operation, argument = next((operation, argument) for _, menu_title, entry, _, operation, argument
                                       in menu_entries() if menu_title == title and entry == "ImportT2")
            self.assertEqual((operation, argument), ("import_t2", key))
            self.assertEqual(game_workflows.game_label(argument), NAMES[profile])
            self.assertIn(NAMES[profile], game_choices())
        self.assertIsNone(game_workflows.game_label("unknown"))
        window = (TOOL / "conversion_window.py").read_text(encoding="utf-8")
        self.assertIn("def run(library, meshes=(), drafts=(), game=None):", window)
        self.assertIn("source_game.set(game)", window)
        intake = (TOOL / "import_intake.py").read_text(encoding="utf-8")
        self.assertIn("game=game_label(game) if game else None", intake)
        editor = (TOOL / "editor.py").read_text(encoding="utf-8")
        self.assertIn("def import_t2(game=None):", editor)

    def test_source_game_choice_filters_files_and_lists_numbered_steps(self):
        choices = game_choices()
        self.assertEqual(choices[0], "Any game (open anything)")
        self.assertEqual(set(choices[1:]), {NAMES[profile] for _, profile, _, _ in GAMES})
        self.assertIn("*.fmap", open_filetypes("Any game (open anything)")[0][1])
        ascend = open_filetypes("Tribes: Ascend")
        self.assertEqual(ascend[0], ("Tribes: Ascend source", "*.fmap *.udk *.t3d"))
        self.assertEqual(ascend[1], ("Existing intake", "*.json *.zip"))
        text = steps_text("Tribes: Ascend").splitlines()
        self.assertTrue(text[0].startswith("1. Open the map: "))
        self.assertTrue(any(line.startswith("6. Sound pack (optional): ") for line in text))
        self.assertIn("→", text[-1])
        self.assertIn("Choose the game", steps_text("Any game (open anything)"))

    def test_window_titles_carry_the_step_number(self):
        self.assertEqual(window_title("settings", "Fallback", "Tribes: Ascend"),
                         "Step 3 of 9 · Choose import settings · Tribes: Ascend")
        self.assertEqual(window_title("review", "Fallback", "Tribes 2"), "Step 2 of 7 · Review the conversion · Tribes 2")
        self.assertEqual(window_title("settings", "Fallback", "Tribes 2"), "Fallback")  # no such window for T2
        self.assertEqual(window_title("open", "Fallback", "Any game (open anything)"), "Fallback")

    def test_step_instructions_quote_only_visible_labels(self):
        """Every quoted name in a step is a real button, label or menu entry (#8: refer to visible GUI)."""
        visible = {label for _, _, key, label, *_ in menu_entries() if label}
        for name in ("conversion_window.py", "native_import_window.py", "object_review_window.py"):
            visible |= set(re.findall(r'text="([^"]+)"', (TOOL / name).read_text(encoding="utf-8")))
        # Labels added by PRs this branch does not include yet: #5 (scale preset), #13 (remastered
        # textures). Remove each once its PR is merged into this branch's base.
        visible |= {"Scale preset", "Remastered textures…"}
        for _, profile, _, steps in GAMES:
            for _, title, instruction in steps:
                for quoted in re.findall(r'"([^"]+)"', instruction):
                    self.assertIn(quoted, visible, f"{NAMES[profile]} · {title}")

    def test_editor_install_copies_the_layout_module(self):
        import launch

        with TemporaryDirectory() as directory:
            project = Path(directory) / "Project" / "Map.uproject"
            project.parent.mkdir(parents=True)
            launch.install_editor_scripts(project, {}, include_seed=False)
            self.assertTrue((project.parent / "Content/Python/game_workflows.py").is_file())


if __name__ == "__main__":
    unittest.main()
