"""Control-plane integrity tests: the orchestration artifacts must stay valid."""

from __future__ import annotations

import json
import py_compile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CP = ROOT / ".orchestrator"


class TestControlPlane(unittest.TestCase):
    def test_tasks_json_is_valid(self) -> None:
        data = json.loads((CP / "TASKS.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, list)

    def test_project_state_lists_all_gates(self) -> None:
        text = (CP / "PROJECT_STATE.md").read_text(encoding="utf-8")
        for gate in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"):
            self.assertIn(gate, text)

    def test_verification_records_baseline(self) -> None:
        text = (CP / "VERIFICATION.md").read_text(encoding="utf-8")
        self.assertIn("rubberband", text)
        self.assertIn("librosa", text)

    def test_decisions_are_recorded(self) -> None:
        text = (CP / "DECISIONS.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"D0")


class TestProjectLayout(unittest.TestCase):
    def test_no_venv_or_cache_tracked_at_root(self) -> None:
        for banned in (".venv", "__pycache__", ".cache"):
            self.assertFalse((ROOT / banned).exists(), f"{banned} must not live at repo root")

    def test_python_sources_compile(self) -> None:
        sources = sorted((ROOT / "ai_dj").rglob("*.py"))
        sources += sorted((ROOT / "scripts").rglob("*.py"))
        for py in sources:
            py_compile.compile(str(py), doraise=True)


if __name__ == "__main__":
    unittest.main()
