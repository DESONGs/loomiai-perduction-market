from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autoresearch_agent.core.packs.loader import PACKS_ROOT, PackLoader, discover_pack_manifests, load_pack_manifest
from autoresearch_agent.core.packs.project import create_project_scaffold, default_research_spec


class PackLoaderTests(unittest.TestCase):
    def test_load_prediction_market_manifest(self) -> None:
        manifest_path = ROOT / "src" / "autoresearch_agent" / "packs" / "prediction_market" / "pack.yaml"
        manifest = load_pack_manifest(manifest_path)

        self.assertEqual(manifest.pack_id, "prediction_market")
        self.assertEqual(manifest.default_adapter, "canonical_json")
        self.assertIn("confidence_threshold", manifest.allowed_axes)
        self.assertEqual(manifest.entrypoints["strategy_template"], "templates/strategy.py")

    def test_discover_pack_manifests_includes_prediction_market(self) -> None:
        manifests = discover_pack_manifests(ROOT / "src" / "autoresearch_agent" / "packs")
        self.assertTrue(any(item.pack_id == "prediction_market" for item in manifests))

    def test_default_pack_root_and_loader_work(self) -> None:
        self.assertTrue(PACKS_ROOT.exists())
        manifest = PackLoader().load("prediction_market")
        self.assertEqual(manifest.pack_id, "prediction_market")

    def test_create_project_scaffold_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project_root = Path(tempdir) / "research-project"
            created = create_project_scaffold(
                project_root,
                project_name="demo-project",
                pack_id="prediction_market",
                data_source="./datasets/eval_markets.json",
            )

            research_path = project_root / "research.yaml"
            strategy_path = project_root / "workspace" / "strategy.py"
            manifest_path = project_root / ".autoresearch" / "state" / "pack_manifest.json"

            self.assertTrue(research_path.exists())
            self.assertTrue(strategy_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertIn("research.yaml", created)
            self.assertIn("workspace/strategy.py", created)
            self.assertIn(".autoresearch/state/pack_manifest.json", created)
            research_text = research_path.read_text(encoding="utf-8")
            strategy_text = strategy_path.read_text(encoding="utf-8")
            self.assertIn("demo-project", research_text)
            self.assertIn("prediction_market", research_text)
            self.assertIn("./datasets/eval_markets.json", research_text)
            self.assertIn("def strategy", strategy_text)

    def test_default_research_spec_uses_pack_axes(self) -> None:
        spec = default_research_spec(
            project_name="demo-project",
            pack_id="prediction_market",
            data_source="./datasets/eval_markets.json",
            allowed_axes=["confidence_threshold", "bet_sizing"],
        )
        self.assertEqual(spec.pack["id"], "prediction_market")
        self.assertEqual(spec.search["allowed_axes"], ["confidence_threshold", "bet_sizing"])
        self.assertEqual(spec.constraints["retention_hours"], 168)


if __name__ == "__main__":
    unittest.main()
