"""Regression coverage for local SPSA param_history migration helpers."""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "utils"
    / "spsa"
    / "_spsa_param_history_tool.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location(
    "_spsa_param_history_tool_for_tests",
    MODULE_PATH,
)
assert MODULE_SPEC is not None
assert MODULE_SPEC.loader is not None
SPSA_PARAM_HISTORY_TOOL = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = SPSA_PARAM_HISTORY_TOOL
MODULE_SPEC.loader.exec_module(SPSA_PARAM_HISTORY_TOOL)


class _FakeClientContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeFindOneCollection:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, query, projection=None):
        del query, projection
        return self._doc


class SpsaParamHistoryToolTests(unittest.TestCase):
    def test_command_registry_keeps_staged_workflow_and_resample(self):
        self.assertEqual(
            set(SPSA_PARAM_HISTORY_TOOL._COMMANDS),
            {
                "inspect-iter-window",
                "list-constant-history",
                "stage-orig",
                "stage-new",
                "apply-stage",
                "resample-dense-histories",
            },
        )

    def test_history_field_is_constant_detects_constant_c_vectors(self):
        doc = {
            "args": {
                "spsa": {
                    "param_history": [
                        [{"theta": 12.0, "c": 1.0, "R": 2.0}],
                        [{"theta": 13.0, "c": 1.0, "R": 3.0}],
                    ]
                }
            }
        }

        self.assertTrue(
            SPSA_PARAM_HISTORY_TOOL._history_field_is_constant(
                doc,
                field_name="c",
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            )
        )
        self.assertFalse(
            SPSA_PARAM_HISTORY_TOOL._history_field_is_constant(
                doc,
                field_name="R",
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            )
        )

    def test_inspect_c_to_iter_roundtrip_accepts_exact_recovery(self):
        gamma = 0.101
        base_c = 1.6
        sample_iter = 20
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [{"theta": 12.0, "c": base_c / ((sample_iter + 1) ** gamma)}]
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )
        assert converted is not None

        check = SPSA_PARAM_HISTORY_TOOL._inspect_c_to_iter_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
        )

        self.assertEqual(check.checked_values, 1)
        self.assertEqual(check.mismatched_values, 0)
        self.assertIsNone(check.first_mismatch)

    def test_convert_history_c_to_iter_promotes_exact_base_c_sample_to_positive_iter(
        self,
    ):
        gamma = 0.101
        base_c = 26.09780750329488
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 60000,
                "spsa": {
                    "iter": 500,
                    "num_iter": 30000,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c}],
                    "param_history": [[{"theta": 12.0, "c": base_c}]],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 1)

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(len(report.errors), 1)
        self.assertIn("c-to-iter round-trip assertion failed", report.errors[0])

    def test_convert_history_c_to_iter_does_not_tail_align_sparse_exact_sample(self):
        gamma = 0.101
        base_c = 1.6
        sample_iter = 20
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 60000,
                "spsa": {
                    "iter": 500,
                    "num_iter": 30000,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c}],
                    "param_history": [
                        [
                            {
                                "theta": 12.0,
                                "c": base_c / ((sample_iter + 1) ** gamma),
                            }
                        ]
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], sample_iter)

        check = SPSA_PARAM_HISTORY_TOOL._inspect_c_to_iter_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
        )

        self.assertEqual(check.checked_values, 1)
        self.assertEqual(check.mismatched_values, 0)

    def test_convert_history_c_to_iter_keeps_recoverable_samples_when_one_legacy_sample_is_missing_c(
        self,
    ):
        gamma = 0.101
        base_c = 1.6
        sample_iter = 20
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": None}],
                        [{"theta": 12.0, "c": base_c / ((sample_iter + 1) ** gamma)}],
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 10)
        self.assertEqual(converted[1][0]["iter"], sample_iter)

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(report.c_check.checked_values, 1)
        self.assertEqual(report.c_check.mismatched_values, 0)
        self.assertEqual(report.chart_check.mismatched_rows, 0)

    def test_convert_history_c_to_iter_checks_neighbor_integer_when_roundtrip_is_off_by_one(
        self,
    ):
        gamma = 0.101
        base_c = 1.6
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 21,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [[{"theta": 12.0, "c": base_c / (21**gamma)}]],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[20.6],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 20)

        check = SPSA_PARAM_HISTORY_TOOL._inspect_c_to_iter_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
        )

        self.assertEqual(check.checked_values, 1)
        self.assertEqual(check.mismatched_values, 0)

    def test_convert_history_c_to_iter_checks_neighbor_integer_using_stored_r(self):
        doc = {
            "_id": "run-r-neighbor",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 6,
                    "num_iter": 250,
                    "A": 1,
                    "alpha": 1.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "c": 1.0, "a": 4.0}],
                    "param_history": [[{"theta": 12.0, "c": 1.0, "R": 4.0 / 7.0}]],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[5.6],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 5)

        check = SPSA_PARAM_HISTORY_TOOL._inspect_r_to_iter_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
        )

        self.assertEqual(check.checked_values, 1)
        self.assertEqual(check.mismatched_values, 0)

    def test_convert_history_c_to_iter_searches_iter_window_using_stored_r(self):
        doc = {
            "_id": "run-r-window",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "A": 1,
                    "alpha": 1.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "c": 1.0, "a": 4.0}],
                    "param_history": [[{"theta": 12.0, "R": 4.0 / 11.0}]],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[12.4],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 9)

    def test_estimate_history_sample_iter_from_r_exact_for_gamma_zero(self):
        target = SPSA_PARAM_HISTORY_TOOL._HistorySampleValidationTarget(
            stored_c=1.0,
            base_c=1.0,
            stored_r=4.0 / 11.0,
            base_a=4.0,
        )

        estimate = SPSA_PARAM_HISTORY_TOOL._estimate_history_sample_iter_from_r(
            [target],
            A=1.0,
            alpha=1.0,
            gamma=0.0,
            seed=12.4,
        )

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertAlmostEqual(estimate, 9.0)

    def test_convert_history_c_to_iter_uses_r_estimate_for_constant_c_history(self):
        exact_iter = 80
        doc = {
            "_id": "run-r-constant-c",
            "start_time": datetime(2026, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 1000,
                "spsa": {
                    "iter": 120,
                    "num_iter": 500,
                    "A": 5.0,
                    "alpha": 1.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "c": 1.0, "a": 4.0}],
                    "param_history": [
                        [
                            {
                                "theta": 12.0,
                                "c": 1.0,
                                "R": 4.0 / (5.0 + exact_iter + 1.0),
                            }
                        ]
                    ],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[40.2],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], exact_iter)

    def test_convert_history_c_to_iter_uses_r_estimate_when_r_only_sample_is_far_from_chart_guess(
        self,
    ):
        exact_iter = 20
        doc = {
            "_id": "run-r-only",
            "start_time": datetime(2026, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 1000,
                "spsa": {
                    "iter": 120,
                    "num_iter": 500,
                    "A": 1.0,
                    "alpha": 1.0,
                    "gamma": 0.1,
                    "params": [{"theta": 12.5, "c": 2.0, "a": 8.0}],
                    "param_history": [
                        [
                            {
                                "theta": 12.0,
                                "R": SPSA_PARAM_HISTORY_TOOL._recompute_sample_r_from_iter(
                                    base_a=8.0,
                                    base_c=2.0,
                                    A=1.0,
                                    alpha=1.0,
                                    gamma=0.1,
                                    sample_iter=float(exact_iter),
                                ),
                            }
                        ]
                    ],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[100.0],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], exact_iter)

    def test_convert_history_c_to_iter_uses_both_direct_signals_before_chart_heuristic(
        self,
    ):
        exact_iter = 20
        doc = {
            "_id": "run-c-and-r",
            "start_time": datetime(2026, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 1000,
                "spsa": {
                    "iter": 120,
                    "num_iter": 500,
                    "A": 1.0,
                    "alpha": 1.0,
                    "gamma": 0.1,
                    "params": [{"theta": 12.5, "c": 2.0, "a": 8.0}],
                    "param_history": [
                        [
                            {
                                "theta": 12.0,
                                "c": SPSA_PARAM_HISTORY_TOOL._recompute_sample_c_from_iter(
                                    base_c=2.0,
                                    gamma=0.1,
                                    sample_iter=float(exact_iter),
                                ),
                                "R": SPSA_PARAM_HISTORY_TOOL._recompute_sample_r_from_iter(
                                    base_a=8.0,
                                    base_c=2.0,
                                    A=1.0,
                                    alpha=1.0,
                                    gamma=0.1,
                                    sample_iter=float(exact_iter),
                                ),
                            }
                        ]
                    ],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[100.0],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], exact_iter)

    def test_convert_history_c_to_iter_uses_master_chart_when_c_and_r_are_constant(
        self,
    ):
        doc = {
            "_id": "run-constant-c-r",
            "start_time": datetime(2026, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 1000,
                "spsa": {
                    "iter": 120,
                    "num_iter": 500,
                    "A": 1.0,
                    "alpha": 0.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "c": 1.0, "a": 4.0}],
                    "param_history": [
                        [{"theta": 10.0, "c": 1.0, "R": 4.0}],
                        [{"theta": 11.0, "c": 1.0, "R": 4.0}],
                        [{"theta": 12.0, "c": 1.0, "R": 4.0}],
                    ],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[11.6, 23.4, 34.7],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 12)
        self.assertEqual(converted[1][0]["iter"], 23)
        self.assertEqual(converted[2][0]["iter"], 35)

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(report.errors, [])
        self.assertEqual(report.c_check.checked_values, 0)
        self.assertEqual(report.r_check.checked_values, 0)
        self.assertEqual(report.chart_check.mismatched_rows, 0)

    def test_build_history_conversion_report_uses_r_roundtrip_when_c_is_constant(self):
        exact_iter = 80
        doc = {
            "_id": "run-r-constant-c",
            "start_time": datetime(2026, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 1000,
                "spsa": {
                    "iter": 120,
                    "num_iter": 500,
                    "A": 5.0,
                    "alpha": 1.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "c": 1.0, "a": 4.0}],
                    "param_history": [
                        [
                            {
                                "theta": 12.0,
                                "c": 1.0,
                                "R": 4.0 / (5.0 + exact_iter + 1.0),
                            }
                        ]
                    ],
                },
            },
        }

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(report.errors, [])
        self.assertEqual(report.c_check.checked_values, 0)
        self.assertEqual(report.r_check.checked_values, 1)
        self.assertEqual(report.r_check.mismatched_values, 0)

    def test_build_history_conversion_report_accepts_chart_only_constant_c_and_r_history(
        self,
    ):
        doc = {
            "_id": "run-non-invertible-constant-c-r",
            "start_time": datetime(2024, 5, 15, tzinfo=UTC),
            "args": {
                "num_games": 1000,
                "spsa": {
                    "iter": 500,
                    "num_iter": 500,
                    "A": 1.0,
                    "alpha": 0.602,
                    "gamma": 0.101,
                    "params": [
                        {
                            "theta": 12.5,
                            "c": 7.59509630360077,
                            "a": 0.1,
                        }
                    ],
                    "param_history": [
                        [
                            {
                                "theta": 11.0,
                                "c": 7.59509630360077,
                                "R": 0.0009177370584335204,
                            }
                        ],
                        [
                            {
                                "theta": 12.0,
                                "c": 7.59509630360077,
                                "R": 0.0009177370584335204,
                            }
                        ],
                    ],
                },
            },
        }

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(report.c_check.checked_values, 0)
        self.assertEqual(report.r_check.checked_values, 0)
        self.assertEqual(report.chart_check.mismatched_rows, 0)
        self.assertEqual(report.errors, [])

    def test_main_inspect_iter_window_reports_best_nearby_iter(self):
        doc = {
            "_id": "run-r-window",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "A": 1,
                    "alpha": 1.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "c": 1.0, "a": 4.0}],
                    "param_history": [[{"theta": 12.0, "R": 4.0 / 11.0}]],
                },
            },
        }

        stdout = io.StringIO()
        with (
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_connect",
                return_value=_FakeClientContext(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_runs_collection",
                return_value=_FakeFindOneCollection(doc),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_resolve_history_sample_iters",
                return_value=[12.4],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = SPSA_PARAM_HISTORY_TOOL.main_inspect_iter_window(
                [
                    "--run-id",
                    "0123456789abcdef01234567",
                    "--sample-index",
                    "1",
                    "--radius",
                    "5",
                    "--top",
                    "3",
                ]
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Resolved estimate: 12.4", output)
        self.assertIn("Established iter: 9", output)
        self.assertIn("Best iter in window: 9", output)
        self.assertIn("Stored R targets: 1", output)

    def test_convert_history_c_to_iter_prefers_direct_c_estimate_over_chart_resolution(
        self,
    ):
        gamma = 0.101
        base_c = 1.6
        sample_iter = 20
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 120,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [
                            {
                                "theta": 12.0,
                                "c": base_c / ((sample_iter + 1) ** gamma),
                            }
                        ]
                    ],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[83.4],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], sample_iter)

        check = SPSA_PARAM_HISTORY_TOOL._inspect_c_to_iter_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
        )

        self.assertEqual(check.checked_values, 1)
        self.assertEqual(check.mismatched_values, 0)

    def test_convert_history_c_to_iter_keeps_exact_c_estimates_when_rows_run_backward(
        self,
    ):
        gamma = 0.101
        base_c = 1.6
        first_iter = 20
        second_iter = 10
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 120,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [
                            {
                                "theta": 11.0,
                                "c": base_c / ((first_iter + 1) ** gamma),
                            }
                        ],
                        [
                            {
                                "theta": 12.0,
                                "c": base_c / ((second_iter + 1) ** gamma),
                            }
                        ],
                    ],
                },
            },
        }

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_resolve_history_sample_iters",
            return_value=[first_iter, second_iter],
        ):
            converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
                doc,
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            )

        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], first_iter)
        self.assertEqual(converted[1][0]["iter"], second_iter)

        check = SPSA_PARAM_HISTORY_TOOL._inspect_c_to_iter_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
        )

        self.assertEqual(check.checked_values, 2)
        self.assertEqual(check.mismatched_values, 0)

    def test_convert_history_c_to_iter_estimates_gamma_zero_samples(self):
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 5,
                    "num_iter": 250,
                    "gamma": 0,
                    "params": [{"c": 1.0}],
                    "param_history": [
                        [{"theta": 12.0, "c": 1.0}],
                        [{"theta": 13.0, "c": 1.0}],
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        self.assertEqual(len(converted), 2)
        self.assertEqual(converted[0][0]["theta"], 12.0)
        self.assertEqual(converted[0][0]["iter"], 2)
        self.assertEqual(converted[1][0]["theta"], 13.0)
        self.assertEqual(converted[1][0]["iter"], 3)

    def test_convert_history_c_to_iter_interpolates_unrecoverable_legacy_samples(
        self,
    ):
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "c": 1.6}],
                    "param_history": [
                        [{"theta": 11.0, "c": None}],
                        [{"theta": 12.0, "c": 1.6 / (21**0.101)}],
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        self.assertEqual(len(converted), 2)
        self.assertEqual(converted[0][0]["theta"], 11.0)
        self.assertEqual(converted[0][0]["iter"], 10)
        self.assertEqual(converted[1][0]["theta"], 12.0)
        self.assertEqual(converted[1][0]["iter"], 20)

    def test_convert_history_c_to_iter_transform_accepts_partial_legacy_recovery(self):
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "c": 1.6}],
                    "param_history": [
                        [{"theta": 11.0, "c": None}],
                        [{"theta": 12.0, "c": 1.6 / (21**0.101)}],
                    ],
                },
            },
        }

        transform = SPSA_PARAM_HISTORY_TOOL._ConvertHistoryCToIterTransform(
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        converted = transform(doc)

        self.assertEqual(transform.roundtrip_stats.checked_values, 1)
        self.assertEqual(transform.roundtrip_stats.mismatched_values, 0)
        self.assertEqual(transform.roundtrip_stats.mismatch_runs, 0)
        self.assertEqual(transform.roundtrip_stats.previews, [])
        assert converted is not None
        self.assertEqual(converted[0][0]["iter"], 10)
        self.assertEqual(converted[1][0]["iter"], 20)

    def test_inspect_chart_roundtrip_detects_chart_mismatch(self):
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "start": 10, "c": 1.6}],
                    "param_history": [
                        [{"theta": 11.0, "c": None}],
                        [{"theta": 12.0, "c": 1.6 / (21**0.101)}],
                    ],
                },
            },
        }

        bad_history = [
            [{"theta": 11.0, "iter": 1}],
            [{"theta": 12.0, "iter": 20}],
        ]

        check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
            doc,
            bad_history,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(check.checked_rows, 4)
        self.assertGreater(check.mismatched_rows, 0)
        self.assertIn("iter_ratio differs", check.first_mismatch)

    def test_inspect_chart_roundtrip_accepts_partial_legacy_recovery_conversion(self):
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "start": 10, "c": 1.6}],
                    "param_history": [
                        [{"theta": 11.0, "c": None}],
                        [{"theta": 12.0, "c": 1.6 / (21**0.101)}],
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )
        assert converted is not None

        check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(check.checked_rows, 4)
        self.assertEqual(check.mismatched_rows, 0)
        self.assertIsNone(check.first_mismatch)

    def test_inspect_chart_roundtrip_accepts_exact_recovery_conversion(self):
        gamma = 0.101
        base_c = 1.6
        sample_iter = 20
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c}],
                    "param_history": [
                        [{"theta": 12.0, "c": base_c / ((sample_iter + 1) ** gamma)}]
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )
        assert converted is not None

        check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(check.checked_rows, 3)
        self.assertEqual(check.mismatched_rows, 0)
        self.assertIsNone(check.first_mismatch)

    def test_inspect_chart_roundtrip_accepts_one_step_drift_and_terminal_dedupe(
        self,
    ):
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 60000,
                "spsa": {
                    "iter": 15000,
                    "num_iter": 30000,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "start": 10, "c": 1.6}],
                    "param_history": [],
                },
            },
        }
        original_payload = {
            "param_names": ["Tempo"],
            "chart_rows": [
                {"iter_ratio": 0.0, "values": [10.0], "c_values": [1.6]},
                {
                    "iter_ratio": 0.2,
                    "values": [11.0],
                    "c_values": [1.3],
                },
                {
                    "iter_ratio": 0.5,
                    "values": [12.0],
                    "c_values": [1.1],
                },
                {
                    "iter_ratio": 0.5000333333333333,
                    "values": [12.0],
                    "c_values": [1.1],
                },
            ],
        }
        converted_payload = {
            "param_names": ["Tempo"],
            "chart_rows": [
                {"iter_ratio": 0.0, "values": [10.0], "c_values": [1.6]},
                {
                    "iter_ratio": 0.20003333333333334,
                    "values": [11.0],
                    "c_values": [1.3],
                },
                {
                    "iter_ratio": 0.5000333333333333,
                    "values": [12.0],
                    "c_values": [1.1],
                },
            ],
        }

        with (
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "build_spsa_chart_payload",
                return_value=original_payload,
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_build_chart_payload_for_history",
                return_value=converted_payload,
            ),
        ):
            check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
                doc,
                [],
                tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
            )

        self.assertEqual(check.checked_rows, 3)
        self.assertEqual(check.mismatched_rows, 0)
        self.assertIsNone(check.first_mismatch)

    def test_convert_history_c_to_iter_transform_records_chart_mismatch_without_failing(
        self,
    ):
        doc = {
            "start_time": datetime(2020, 4, 2, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "start": 10, "c": 1.6}],
                    "param_history": [
                        [{"theta": 11.0, "c": None}],
                        [{"theta": 12.0, "c": 1.6 / (21**0.101)}],
                    ],
                },
            },
        }
        bad_history = [
            [{"theta": 11.0, "iter": 1}],
            [{"theta": 12.0, "iter": 20}],
        ]

        transform = SPSA_PARAM_HISTORY_TOOL._ConvertHistoryCToIterTransform(
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        with patch.object(
            SPSA_PARAM_HISTORY_TOOL,
            "_convert_history_c_to_iter",
            return_value=bad_history,
        ):
            converted = transform(doc)

        self.assertEqual(converted, bad_history)
        self.assertEqual(transform.roundtrip_stats.checked_values, 1)
        self.assertEqual(transform.roundtrip_stats.mismatched_values, 0)
        self.assertEqual(transform.chart_stats.checked_rows, 4)
        self.assertGreater(transform.chart_stats.mismatched_rows, 0)
        self.assertEqual(transform.chart_stats.mismatch_runs, 1)

    def test_run_history_mutation_dry_run_prints_c_roundtrip_summary(self):
        transform = SPSA_PARAM_HISTORY_TOOL._ConvertHistoryCToIterTransform(
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )
        transform.roundtrip_stats.checked_values = 7
        transform.roundtrip_stats.mismatched_values = 2
        transform.roundtrip_stats.mismatch_runs = 1
        transform.roundtrip_stats.max_abs_error = 0.125
        transform.roundtrip_stats.max_rel_error = 0.25
        transform.roundtrip_stats.previews.append("run-0: 2/7 stored c values differ")
        transform.chart_stats.checked_rows = 12
        transform.chart_stats.mismatched_rows = 3
        transform.chart_stats.mismatch_runs = 1
        transform.chart_stats.max_iter_ratio_error = 0.003
        transform.chart_stats.max_value_error = 0.125
        transform.chart_stats.previews.append("run-0: 3/12 chart rows differ")

        stats = SPSA_PARAM_HISTORY_TOOL.MutationStats(
            scanned=1,
            changed=1,
            unchanged=0,
        )
        args = SimpleNamespace(
            uri="mongodb://localhost:27017/",
            db="fishtest_new",
            collection="runs",
            run_id=None,
            limit=None,
            batch_size=10,
            write=False,
        )

        stdout = io.StringIO()
        with (
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_connect",
                return_value=_FakeClientContext(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_runs_collection",
                return_value=object(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_collect_mutation_stats",
                return_value=stats,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = SPSA_PARAM_HISTORY_TOOL._run_history_mutation(
                args,
                action="replace c with iter in param_history",
                transform=transform,
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("c(iter) round-trip validation:", output)
        self.assertIn("Checked stored c values: 7", output)
        self.assertIn("Mismatched stored c values: 2", output)
        self.assertIn("run-0: 2/7 stored c values differ", output)
        self.assertIn("legacy chart equivalence validation:", output)
        self.assertIn("Compared chart rows: 12", output)
        self.assertIn("Mismatched chart rows: 3", output)
        self.assertIn("run-0: 3/12 chart rows differ", output)

    def test_print_stage_build_stats_includes_run_date(self):
        stats = SPSA_PARAM_HISTORY_TOOL.StageBuildStats(
            scanned=2,
            staged=2,
            ready=1,
            validation_failed=1,
            errors=[
                "run-1 (2020-01-05): synthetic assertion failure",
            ],
            previews=[
                ("run-1", "2020-01-05", "validation-failed", 53, 53),
            ],
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            SPSA_PARAM_HISTORY_TOOL._print_stage_build_stats(
                "stage converted SPSA history in spsa_new",
                stats,
                show_all_errors=True,
            )

        output = stdout.getvalue()
        self.assertIn("date", output)
        self.assertIn("2020-01-05", output)
        self.assertIn("run-1 (2020-01-05): synthetic assertion failure", output)

    def test_resample_dense_history_skips_safe_2022_checkpoint_regime(self):
        doc = {
            "start_time": datetime(2022, 4, 1, tzinfo=UTC),
            "args": {
                "num_games": 200000,
                "spsa": {
                    "params": [{"name": "Tempo"}] * 64,
                    "param_history": [
                        [{"theta": float(index), "iter": index}]
                        for index in range(1, 102)
                    ],
                },
            },
        }

        self.assertIsNone(SPSA_PARAM_HISTORY_TOOL._resample_dense_history(doc))

    def test_resample_dense_history_handles_pre_2022_early_stop_runs(self):
        doc = {
            "start_time": datetime(2021, 12, 7, tzinfo=UTC),
            "args": {
                "num_games": 505000,
                "spsa": {
                    "params": [{"name": "Tempo"}] * 6,
                    "param_history": [
                        [{"theta": float(index), "iter": index}]
                        for index in range(1, 51)
                    ],
                },
            },
        }

        self.assertEqual(SPSA_PARAM_HISTORY_TOOL._resample_dense_history(doc), [])

    def test_run_history_mutation_dry_run_reports_all_errors_and_returns_success(self):
        stats = SPSA_PARAM_HISTORY_TOOL.MutationStats(
            scanned=3,
            changed=2,
            unchanged=1,
            errors=[
                f"run-{index}: synthetic error {index}"
                for index in range(SPSA_PARAM_HISTORY_TOOL.DEFAULT_PREVIEW_COUNT + 2)
            ],
            previews=[("run-0", 7, 7)],
        )
        args = SimpleNamespace(
            uri="mongodb://localhost:27017/",
            db="fishtest_new",
            collection="runs",
            run_id=None,
            limit=None,
            batch_size=10,
            write=False,
        )

        stdout = io.StringIO()
        with (
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_connect",
                return_value=_FakeClientContext(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_runs_collection",
                return_value=object(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_collect_mutation_stats",
                return_value=stats,
            ),
            redirect_stdout(stdout),
        ):
            exit_code = SPSA_PARAM_HISTORY_TOOL._run_history_mutation(
                args,
                action="replace c with iter in param_history",
                transform=lambda doc: None,
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Dry run only. No writes applied.", output)
        self.assertIn(
            "Fix or filter the listed runs before re-running with --write.",
            output,
        )
        self.assertNotIn("... and", output)
        for error in stats.errors:
            self.assertIn(error, output)

    def test_run_history_mutation_write_mode_refuses_errors_before_writing(self):
        stats = SPSA_PARAM_HISTORY_TOOL.MutationStats(
            scanned=1,
            changed=0,
            unchanged=1,
            errors=[f"run-{index}: synthetic error" for index in range(12)],
        )
        args = SimpleNamespace(
            uri="mongodb://localhost:27017/",
            db="fishtest_new",
            collection="runs",
            run_id=None,
            limit=None,
            batch_size=10,
            write=True,
        )

        stdout = io.StringIO()
        with (
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_connect",
                return_value=_FakeClientContext(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_runs_collection",
                return_value=object(),
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_collect_mutation_stats",
                return_value=stats,
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL, "_apply_history_mutation"
            ) as apply_mock,
            redirect_stdout(stdout),
        ):
            exit_code = SPSA_PARAM_HISTORY_TOOL._run_history_mutation(
                args,
                action="replace c with iter in param_history",
                transform=lambda doc: None,
            )

        output = stdout.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn(
            "Refusing to apply mutation while validation errors are present.",
            output,
        )
        self.assertIn("... and 2 more", output)
        apply_mock.assert_not_called()

    def test_build_spsa_orig_stage_preserves_spsa_snapshot(self):
        doc = {
            "_id": "run-orig",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 10,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "c": 1.6}],
                    "param_history": [[{"theta": 12.0, "c": 1.5, "R": 0.25}]],
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_orig_stage(
            doc,
            source_collection="runs",
        )

        self.assertEqual(result.status, "snapshot")
        self.assertEqual(result.stage_doc["_id"], "run-orig")
        self.assertEqual(result.stage_doc["stage"]["kind"], "spsa_orig")
        self.assertEqual(result.stage_doc["stage"]["status"], "snapshot")
        self.assertEqual(result.stage_doc["stage"]["source_collection"], "runs")
        self.assertEqual(result.stage_doc["stage"]["source_history_shape"], "theta-R-c")
        self.assertEqual(
            result.stage_doc["args"]["spsa"]["param_history"],
            doc["args"]["spsa"]["param_history"],
        )

    def test_build_spsa_new_stage_records_iter_only_history_and_validation(self):
        gamma = 0.101
        base_c = 1.6
        base_a = 4.0
        sample_iter = 20
        sample_c = base_c / ((sample_iter + 1) ** gamma)
        sample_r = base_a / (10 + sample_iter + 1) / sample_c**2
        doc = {
            "_id": "run-new",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "A": 10,
                    "alpha": 1.0,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c, "a": base_a}],
                    "param_history": [[{"theta": 12.0, "c": sample_c, "R": sample_r}]],
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_new_stage(
            doc,
            source_collection="spsa_orig",
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.stage_doc["stage"]["kind"], "spsa_new")
        self.assertEqual(result.stage_doc["stage"]["status"], "ready")
        self.assertEqual(result.stage_doc["stage"]["source_collection"], "spsa_orig")
        self.assertEqual(
            result.stage_doc["args"]["spsa"]["param_history"],
            [[{"theta": 12.0, "iter": 20}]],
        )
        self.assertEqual(
            result.stage_doc["stage"]["validation"]["c"]["mismatched_values"],
            0,
        )
        self.assertEqual(
            result.stage_doc["stage"]["validation"]["r"]["mismatched_values"],
            0,
        )

    def test_build_spsa_new_stage_keeps_ready_status_when_only_r_roundtrip_mismatches(
        self,
    ):
        doc = {
            "_id": "run-new-r-warning",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 5,
                    "num_iter": 250,
                    "A": 10,
                    "alpha": 1.0,
                    "gamma": 0.0,
                    "params": [{"theta": 12.5, "start": 10, "c": 1.0, "a": 4.0}],
                    "param_history": [[{"theta": 12.0, "c": 1.0, "R": 999.0}]],
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_new_stage(
            doc,
            source_collection="spsa_orig",
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.stage_doc["stage"]["status"], "ready")
        self.assertEqual(
            result.stage_doc["stage"]["validation"]["c"]["mismatched_values"],
            0,
        )
        self.assertGreater(
            result.stage_doc["stage"]["validation"]["r"]["mismatched_values"],
            0,
        )
        self.assertTrue(result.warnings)
        self.assertIn("R-to-iter round-trip assertion failed", result.warnings[0])
        self.assertEqual(result.stage_doc["stage"]["errors"], [])
        self.assertEqual(result.stage_doc["stage"]["warnings"], result.warnings)

    def test_build_spsa_new_stage_warns_when_empty_history_has_invalid_base_c(self):
        doc = {
            "_id": "run-new-empty-history-invalid-c",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 0,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "start": 10, "c": 0.0}],
                    "param_history": [],
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_new_stage(
            doc,
            source_collection="spsa_orig",
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.stage_doc["stage"]["status"], "ready")
        self.assertEqual(result.stage_doc["args"]["spsa"]["param_history"], [])
        self.assertTrue(result.warnings)
        self.assertIn(
            "invalid args.spsa.params[0].c: expected a finite number > 0",
            result.warnings[0],
        )
        self.assertIn("args.spsa.param_history is empty", result.warnings[0])
        self.assertEqual(result.stage_doc["stage"]["errors"], [])
        self.assertEqual(result.stage_doc["stage"]["warnings"], result.warnings)

    def test_build_spsa_new_stage_keeps_conversion_error_for_nonempty_history_with_invalid_base_c(
        self,
    ):
        doc = {
            "_id": "run-new-invalid-c",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "start": 10, "c": 0.0}],
                    "param_history": [[{"theta": 12.0, "c": 0.5}]],
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_new_stage(
            doc,
            source_collection="spsa_orig",
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(result.status, "conversion-error")
        self.assertEqual(result.stage_doc["stage"]["status"], "conversion-error")
        self.assertIn(
            "invalid args.spsa.params[0].c: expected a finite number > 0",
            result.errors[0],
        )
        self.assertNotIn("param_history", result.stage_doc["args"]["spsa"])
        self.assertEqual(result.warnings, [])

    def test_build_spsa_new_stage_warns_when_nonempty_history_has_partial_invalid_base_c(
        self,
    ):
        gamma = 0.101
        valid_base_c = 1.6
        sample_iter = 20
        doc = {
            "_id": "run-new-partial-invalid-c",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 20,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [
                        {"theta": 12.5, "start": 10, "c": 0.0},
                        {"theta": 13.5, "start": 10, "c": valid_base_c},
                    ],
                    "param_history": [
                        [
                            {"theta": 12.0, "c": 0.5},
                            {
                                "theta": 13.0,
                                "c": valid_base_c / ((sample_iter + 1) ** gamma),
                            },
                        ]
                    ],
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_new_stage(
            doc,
            source_collection="spsa_orig",
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.stage_doc["stage"]["status"], "ready")
        self.assertEqual(
            result.stage_doc["args"]["spsa"]["param_history"],
            [[{"theta": 12.0, "iter": 20}, {"theta": 13.0, "iter": 20}]],
        )
        self.assertTrue(result.warnings)
        self.assertIn(
            "invalid args.spsa.params[0].c: expected a finite number > 0",
            result.warnings[0],
        )
        self.assertIn(
            "non-empty args.spsa.param_history was converted using other recoverable entries",
            result.warnings[0],
        )

    def test_read_stage_history_for_apply_rejects_validation_failed_doc(self):
        doc = {
            "_id": "run-stage-error",
            "args": {"spsa": {"param_history": [[{"theta": 12.0, "iter": 20}]]}},
            "stage": {
                "status": "validation-failed",
                "errors": ["synthetic validation error"],
            },
        }

        with self.assertRaisesRegex(ValueError, "synthetic validation error"):
            SPSA_PARAM_HISTORY_TOOL._read_stage_history_for_apply(
                doc,
                allow_validation_errors=False,
            )

        self.assertEqual(
            SPSA_PARAM_HISTORY_TOOL._read_stage_history_for_apply(
                doc,
                allow_validation_errors=True,
            ),
            [[{"theta": 12.0, "iter": 20}]],
        )
