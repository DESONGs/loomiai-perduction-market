from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autoresearch_agent.core.spec.research_config import load_research_spec  # noqa: E402
from autoresearch_agent.project.scaffold import build_project_scaffold  # noqa: E402


class ProjectScaffoldTests(unittest.TestCase):
    def test_scaffold_creates_project_layout_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "demo-project"
            result = build_project_scaffold(
                root,
                project_name="demo-project",
                pack_id="prediction_market",
                data_source="./datasets/input.json",
            )

            config_path = result["config_path"]
            self.assertTrue(config_path.exists())
            self.assertTrue((root / "workspace" / "strategy.py").exists())
            self.assertTrue((root / "datasets").exists())
            self.assertTrue((root / ".autoresearch" / "runs").exists())
            self.assertTrue((root / ".autoresearch" / "cache").exists())
            self.assertTrue((root / ".autoresearch" / "state").exists())

            loaded = load_research_spec(config_path)
            self.assertEqual(loaded["project"]["name"], "demo-project")
            self.assertEqual(loaded["pack"]["id"], "prediction_market")
            self.assertEqual(loaded["data"]["source"], "./datasets/input.json")
            self.assertEqual(loaded["search"]["editable_targets"], ["workspace/strategy.py"])


if __name__ == "__main__":
    unittest.main()
