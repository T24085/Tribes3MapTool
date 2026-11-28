"""Every tool module an editor script imports at load time is copied into the editor (No module named ...)."""

import ast
from pathlib import Path
import re
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"


def copied_scripts():
    source = (TOOL / "launch.py").read_text(encoding="utf-8")
    block = source[source.index("def install_editor_scripts"):source.index("shutil.copyfile(TOOL/name, folder/name)")]
    return set(re.findall(r"'([\w]+\.(?:py|json))'", block)) | {"editor.py"}


def imported_tool_modules(path, *, anywhere=False):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    # Module-level imports run as soon as Unreal loads the copy; ``anywhere`` adds imports inside functions.
    for node in (ast.walk(tree) if anywhere else tree.body):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return {name for name in names if (TOOL / f"{name}.py").is_file()}


class EditorScriptListTests(unittest.TestCase):
    def test_load_time_imports_of_the_editor_are_copied(self):
        # Follow module-level imports from the editor's entry script: every tool module they reach is
        # imported when Unreal loads the copy, so it must be copied too (terrain_smoothing was not).
        copied = copied_scripts()
        reached, missing = {"editor"}, {}
        queue = sorted(imported_tool_modules(TOOL / "editor.py", anywhere=True))
        while queue:
            name = queue.pop()
            if name in reached:
                continue
            reached.add(name)
            for module in imported_tool_modules(TOOL / f"{name}.py"):
                if module != "editor" and f"{module}.py" not in copied:
                    missing.setdefault(f"{name}.py", []).append(module)
                queue.append(module)
        self.assertEqual(missing, {}, "Add these modules to install_editor_scripts in launch.py")


if __name__ == "__main__":
    unittest.main()
