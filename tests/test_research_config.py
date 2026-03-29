from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autoresearch_agent.core.spec.research_config import (  # noqa: E402
    ResearchSpecError,
    default_research_spec,
    dump_research_yaml,
    load_research_spec,
    validate_research_spec,
)


class ResearchConfigTests(unittest.TestCase):
    def test_round_trip_through_yaml_loader(self) -> None:
        spec = default_research_spec(
            project_name="alpha-research",
            pack_id="prediction_market",
            data_source="./datasets/markets.json",
        )
        rendered = dump_research_yaml(spec)

        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "research.yaml"
            path.write_text(rendered, encoding="utf-8")
            loaded = load_research_spec(path)

        self.assertEqual(loaded["project"]["name"], "alpha-research")
        self.assertEqual(loaded["pack"]["id"], "prediction_market")
        self.assertEqual(loaded["data"]["source"], "./datasets/markets.json")
        self.assertEqual(loaded["search"]["editable_targets"], ["workspace/strategy.py"])

    def test_validate_rejects_empty_search_axes(self) -> None:
        with self.assertRaises(ResearchSpecError):
            validate_research_spec(
                {
                    "search": {"allowed_axes": []},
                }
            )


if __name__ == "__main__":
    unittest.main()
