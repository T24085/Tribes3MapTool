"""Progress feedback for slow steps (#8): the Tk progress window and pipeline step reporting."""

from pathlib import Path
import sys
import threading
import unittest

TOOL = Path(__file__).resolve().parent / "tools/community_map_editor"
sys.path.insert(0, str(TOOL))
from busy import elapsed_text, run_busy
from map_pipeline import Outcome, PipelineRun


class BusyWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"No display for Tk: {error}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_returns_the_result_from_a_worker_thread_and_closes_its_window(self):
        seen = {}

        def work():
            seen["thread"] = threading.current_thread()
            return 42

        self.assertEqual(run_busy(self.root, "Reading…", work), 42)
        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertEqual(self.root.winfo_children(), [])
        self.assertEqual(self.root.cget("cursor"), "")

    def test_reraises_the_work_error_in_the_caller(self):
        def work():
            raise ValueError("bad package")

        with self.assertRaisesRegex(ValueError, "bad package"):
            run_busy(self.root, "Reading…", work)
        self.assertEqual(self.root.winfo_children(), [])

    def test_elapsed_text(self):
        self.assertEqual(elapsed_text(7.9), "7 s elapsed")
        self.assertEqual(elapsed_text(125), "2 min 05 s elapsed")


class PipelineProgressTests(unittest.TestCase):
    def test_on_step_is_told_each_step_before_it_runs(self):
        document = {"reader": [{"operation": "a", "label": "Read"}, {"operation": "b", "label": "Plan"}]}
        run = PipelineRun(document, "reader")
        order = []
        operators = {"a": lambda _: order.append("ran a"), "b": lambda _: order.append("ran b")}
        self.assertTrue(run.execute(operators, on_step=lambda step: order.append(step["label"])))
        self.assertEqual(order, ["Read", "ran a", "Plan", "ran b"])

    def test_cancelled_step_stops_the_run(self):
        run = PipelineRun({"reader": [{"operation": "a"}, {"operation": "b"}]}, "reader")
        labels = []
        self.assertFalse(run.execute({"a": lambda _: Outcome("cancelled", ""), "b": lambda _: None},
                                     on_step=lambda step: labels.append(step["operation"])))
        self.assertEqual(labels, ["a"])

    def test_unreal_draft_build_shows_a_progress_dialog_per_step(self):
        source = (TOOL / "import_intake.py").read_text(encoding="utf-8")
        self.assertIn("unreal.ScopedSlowTask(len(steps)", source)
        self.assertIn("on_step=lambda step: progress.enter_progress_frame(", source)


if __name__ == "__main__":
    unittest.main()
