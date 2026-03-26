from __future__ import annotations

import unittest

from app.schemas import build_run_spec, validate_run_spec


class RunSpecTests(unittest.TestCase):
    def test_build_run_spec_normalizes_dataset_and_retention_aliases(self) -> None:
        spec = build_run_spec(
            {
                "data": {"adapter": "canonical_csv", "input_format": "csv"},
                "runtime": {"retention_hours": 24, "preserve_run": True},
                "constraints": {"allowed_axes": ["CONFIDENCE_THRESHOLD"]},
            }
        )
        self.assertEqual(spec["dataset"]["adapter"], "canonical_csv")
        self.assertEqual(spec["retention_policy"]["retention_hours"], 24)
        self.assertTrue(spec["retention_policy"]["preserve_run"])
        self.assertEqual(spec["harness_policy"]["allowed_axes"], ["CONFIDENCE_THRESHOLD"])

    def test_validate_run_spec_rejects_mismatched_policy_axes(self) -> None:
        with self.assertRaises(ValueError):
            validate_run_spec(
                {
                    "constraints": {"allowed_axes": ["CONFIDENCE_THRESHOLD"]},
                    "harness_policy": {"allowed_axes": ["BET_SIZING"]},
                }
            )


if __name__ == "__main__":
    unittest.main()
