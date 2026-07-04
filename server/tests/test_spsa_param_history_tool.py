"""Regression coverage for local SPSA param_history migration helpers."""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fishtest.spsa_workflow import build_spsa_worker_step

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


class _FakeDatabase:
    def __getitem__(self, name):
        del name
        return object()


class _FakeMongoClient:
    def __getitem__(self, name):
        del name
        return _FakeDatabase()


class _FakeClientContextFor:
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, exc_type, exc, tb):
        return False


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

    def test_write_commands_require_explicit_db(self):
        # A write-capable parser must not fall back to a baked-in database name.
        write_parser = argparse.ArgumentParser()
        SPSA_PARAM_HISTORY_TOOL._add_connection_args(write_parser, require_db=True)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            write_parser.parse_args([])
        self.assertEqual(write_parser.parse_args(["--db", "fishtest"]).db, "fishtest")

        read_parser = argparse.ArgumentParser()
        SPSA_PARAM_HISTORY_TOOL._add_connection_args(read_parser)
        self.assertEqual(
            read_parser.parse_args([]).db,
            SPSA_PARAM_HISTORY_TOOL.DEFAULT_DB,
        )

    def test_stage_new_scans_snapshot_with_unfiltered_query(self):
        # stage-new reads the spsa_orig snapshot, whose docs carry no
        # finished/deleted fields, so it must use the unfiltered stage query;
        # stage-orig reads runs and must keep the finished/non-deleted filter.
        captured: dict[str, object] = {}

        def fake_find_runs(collection, query, *, projection=None, limit=None):
            del collection, projection, limit
            captured["query"] = query
            return iter(())

        def run(main):
            with (
                patch.object(
                    SPSA_PARAM_HISTORY_TOOL,
                    "_connect",
                    return_value=_FakeClientContextFor(_FakeMongoClient()),
                ),
                patch.object(
                    SPSA_PARAM_HISTORY_TOOL,
                    "_runs_collection",
                    return_value=object(),
                ),
                patch.object(
                    SPSA_PARAM_HISTORY_TOOL,
                    "_find_runs",
                    side_effect=fake_find_runs,
                ),
                redirect_stdout(io.StringIO()),
            ):
                main(["--db", "fishtest"])

        run(SPSA_PARAM_HISTORY_TOOL.main_stage_converted_history)
        self.assertNotIn("finished", captured["query"])

        run(SPSA_PARAM_HISTORY_TOOL.main_stage_original_history)
        self.assertEqual(captured["query"].get("finished"), True)

    def test_runs_query_filters_finished_non_deleted_but_stage_query_does_not(self):
        args = SimpleNamespace(run_id=None)
        self.assertEqual(
            SPSA_PARAM_HISTORY_TOOL._build_spsa_query(args),
            {
                "args.spsa": {"$exists": True},
                "finished": True,
                "deleted": {"$ne": True},
            },
        )
        self.assertEqual(
            SPSA_PARAM_HISTORY_TOOL._build_stage_query(args),
            {"args.spsa": {"$exists": True}},
        )

    def test_execute_bulk_write_fails_closed_on_partial_error(self):
        class _BulkErrorCollection:
            def bulk_write(self, operations, ordered=False):
                del operations, ordered
                raise SPSA_PARAM_HISTORY_TOOL.BulkWriteError(
                    {"writeErrors": [{"errmsg": "duplicate key"}]}
                )

        operation = SPSA_PARAM_HISTORY_TOOL.UpdateOne({"_id": 1}, {"$set": {"x": 1}})
        with self.assertRaises(RuntimeError) as raised:
            SPSA_PARAM_HISTORY_TOOL._execute_bulk_write(
                _BulkErrorCollection(), [operation]
            )
        self.assertIn("bulk write failed", str(raised.exception))
        # No operations means no write is attempted.
        self.assertEqual(
            SPSA_PARAM_HISTORY_TOOL._execute_bulk_write(_BulkErrorCollection(), []),
            0,
        )

    def test_stage_apply_drift_guard_detects_changed_target(self):
        legacy_history = [
            [{"theta": 11.0, "c": 1.5}],
            [{"theta": 12.0, "c": 1.4}],
        ]
        target_doc = {
            "_id": "run-1",
            "finished": True,
            "args": {"spsa": {"param_history": legacy_history}},
        }
        stage_doc = {
            "_id": "run-1",
            "stage": {
                "kind": SPSA_PARAM_HISTORY_TOOL.DEFAULT_NEW_COLLECTION,
                "source_history_shape": SPSA_PARAM_HISTORY_TOOL._history_shape(
                    target_doc
                ),
                "source_history_len": len(legacy_history),
            },
        }

        # Unchanged finished target with the staged snapshot shape: no drift.
        self.assertIsNone(
            SPSA_PARAM_HISTORY_TOOL._stage_apply_drift_reason(
                _FakeFindOneCollection(target_doc), stage_doc
            )
        )
        # Missing target.
        self.assertIsNotNone(
            SPSA_PARAM_HISTORY_TOOL._stage_apply_drift_reason(
                _FakeFindOneCollection(None), stage_doc
            )
        )
        # No longer finished.
        unfinished = {**target_doc, "finished": False}
        self.assertIsNotNone(
            SPSA_PARAM_HISTORY_TOOL._stage_apply_drift_reason(
                _FakeFindOneCollection(unfinished), stage_doc
            )
        )
        # History changed since staging (length differs).
        changed = {
            "_id": "run-1",
            "finished": True,
            "args": {"spsa": {"param_history": legacy_history[:1]}},
        }
        reason = SPSA_PARAM_HISTORY_TOOL._stage_apply_drift_reason(
            _FakeFindOneCollection(changed), stage_doc
        )
        self.assertIsNotNone(reason)
        self.assertIn("length changed", reason)

    def test_migration_recovers_true_iters_from_genuine_worker_history(self):
        # Forward-truth: build legacy {theta, R, c} rows with the production
        # worker formula at known iterations, then assert the migration recovers
        # exactly those iterations -- not a self-consistent but wrong answer.
        spsa = {
            "iter": 50,
            "num_iter": 1000,
            "A": 5000,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.0,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "a": 0.2,
                },
            ],
        }
        param = spsa["params"][0]
        # Forward-truth iterations are the real sampler boundaries for a run
        # created under the 2025 regime with num_iter = num_games // 2 = 1000 and
        # one param: period = 1000 / 100 = 10 (inclusive-le), so a run stopped at
        # iter 50 stored samples at 10, 20, 30, 40, 50. The migration must recover
        # exactly those from the historical algorithm plus the stored c/R.
        true_iters = [10, 20, 30, 40, 50]
        history = [
            [
                {
                    "theta": 10.0 + sample_iter / 1000.0,
                    "R": build_spsa_worker_step(
                        spsa, param, iter_value=sample_iter, flip=1
                    )["R"],
                    "c": build_spsa_worker_step(
                        spsa, param, iter_value=sample_iter, flip=1
                    )["c"],
                }
            ]
            for sample_iter in true_iters
        ]
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {"num_games": 2000, "spsa": {**spsa, "param_history": history}},
        }

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(report.errors, [])
        self.assertEqual(report.recovery_errors, [])
        recovered = [row[0]["iter"] for row in report.converted_history]
        self.assertEqual(recovered, true_iters)

    def test_convert_history_c_to_iter_stores_chart_faithful_positions_off_regime(self):
        # When the observed sample count is inconsistent with the reconstructed
        # regime (here 4 samples for a run whose 2025-regime period of 10 predicts
        # ~100 by iter 1000), the sampler windows are rejected. The recovery then
        # stores the c/R-inverted chart positions the runtime renders (not even
        # spacing), so the migrated chart matches the legacy chart -- here the c
        # was built at iters 200/400/600/800, so those exact positions come back.
        gamma = 0.101
        base_c = 1.6
        doc = {
            "start_time": datetime(2025, 6, 1, tzinfo=UTC),
            "args": {
                "num_games": 2000,
                "spsa": {
                    "iter": 1000,
                    "num_iter": 1000,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": base_c / ((sample_iter + 1) ** gamma)}]
                        for sample_iter in (200, 400, 600, 800)
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        self.assertEqual(
            [row[0]["iter"] for row in converted], [200.0, 400.0, 600.0, 800.0]
        )

        # The run is still flagged: its stored iters are chart-faithful positions,
        # not true optimizer iterations.
        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )
        self.assertEqual(report.errors, [])
        self.assertTrue(
            any(
                "chart-faithful c/R-inverted positions" in warning
                for warning in report.warnings
            ),
            report.warnings,
        )

    def test_convert_history_c_to_iter_admits_no_iter_beyond_terminal(self):
        # More stored samples than the regime can place (3 for a period-100,
        # terminal-250 run whose regime predicts 2) must not push an iteration
        # past the terminal via the monotonic clamp: the windows are rejected and
        # the run falls back to strictly-increasing even spacing bounded by V.
        gamma = 0.101
        base_c = 1.6
        doc = {
            "start_time": datetime(2025, 6, 1, tzinfo=UTC),
            "args": {
                "num_games": 20000,
                "spsa": {
                    "iter": 250,
                    "num_iter": 10000,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": base_c / ((sample_iter + 1) ** gamma)}]
                        for sample_iter in (80, 180, 250)
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )
        iters = [row[0]["iter"] for row in converted]
        self.assertTrue(all(1 <= value <= 250 for value in iters), iters)
        self.assertTrue(
            all(iters[i] < iters[i + 1] for i in range(len(iters) - 1)), iters
        )

    def test_conversion_is_idempotent_on_iter_only_history(self):
        # Re-running the migration on an already-migrated history is a no-op:
        # the stored iterations are read back unchanged and nothing is flagged.
        iter_history = [
            [{"theta": 11.0, "iter": 100}],
            [{"theta": 12.0, "iter": 200}],
        ]
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 2000,
                "spsa": {
                    "iter": 200,
                    "num_iter": 1000,
                    "gamma": 0.101,
                    "params": [{"theta": 12.0, "c": 1.6}],
                    "param_history": iter_history,
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
        self.assertEqual(report.recovery_errors, [])
        self.assertEqual(report.converted_history, iter_history)

    def test_orig_stage_round_trips_history_byte_equal(self):
        # Rollback safety: the orig snapshot restores the original history
        # byte-equal, and as an independent copy so staging cannot mutate the run.
        original_history = [
            [{"theta": 11.0, "R": 0.09, "c": 1.5}],
            [{"theta": 12.0, "R": 0.08, "c": 1.4}],
        ]
        doc = {
            "_id": "run-rollback",
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "username": "tester",
                "tc": "10+0.1",
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": 0.101,
                    "params": [{"theta": 12.5, "c": 1.6}],
                    "param_history": original_history,
                },
            },
        }

        result = SPSA_PARAM_HISTORY_TOOL._build_spsa_orig_stage(
            doc,
            source_collection="runs",
        )
        restored = SPSA_PARAM_HISTORY_TOOL._read_stage_history_for_apply(
            result.stage_doc,
            allow_validation_errors=False,
        )

        self.assertEqual(restored, original_history)
        self.assertIsNot(restored, original_history)
        self.assertIsNot(restored[0], original_history[0])

    def test_build_history_conversion_report_respaces_mixed_history_without_recovery_error(
        self,
    ):
        # A partially migrated run (one iter-only row, one legacy row) cannot
        # occur in production -- migration is per-document atomic. Its regime is
        # inconsistent, so the chart-faithful fallback stores the positions the
        # runtime renders: the iter-only row keeps its stored 50, and the legacy c
        # (built at iter 100) inverts back to 100. No recovery error is raised.
        gamma = 0.101
        base_c = 1.6
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 500,
                "spsa": {
                    "iter": 200,
                    "num_iter": 250,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "iter": 50}],
                        [{"theta": 12.0, "c": base_c / (101**gamma)}],
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

        self.assertEqual(report.recovery_errors, [])
        self.assertEqual(
            report.converted_history,
            [[{"theta": 11.0, "iter": 50.0}], [{"theta": 12.0, "iter": 100.0}]],
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

    def test_convert_history_c_to_iter_anchors_missing_c_sample_to_its_window(self):
        # A sample with no usable c/R cannot be pinned inside its window, so it
        # anchors to that window's historical boundary while its neighbours are
        # refined from c. Under the 2025 regime (period 10) a run stopped at iter
        # 40 has windows at 10/20/30/40; the third sample (c = None) still lands
        # on its own boundary (30) instead of being dropped or displaced.
        gamma = 0.101
        base_c = 1.6

        def sample_c(sample_iter):
            return base_c / ((sample_iter + 1) ** gamma)

        doc = {
            "start_time": datetime(2025, 6, 1, tzinfo=UTC),
            "args": {
                "num_games": 2000,
                "spsa": {
                    "iter": 40,
                    "num_iter": 1000,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": sample_c(10)}],
                        [{"theta": 12.0, "c": sample_c(20)}],
                        [{"theta": 13.0, "c": None}],
                        [{"theta": 14.0, "c": sample_c(40)}],
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        assert converted is not None
        self.assertEqual([row[0]["iter"] for row in converted], [10, 20, 30, 40])

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

    def test_build_history_conversion_report_respaces_constant_c_and_r_history(
        self,
    ):
        # Constant c and constant R now convert via index spacing: when c is
        # constant, any monotonic x-axis is a correct rendering, so the migration
        # spaces the rows evenly by index instead of falling back to a chart
        # heuristic or refusing the run.
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

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        assert converted is not None
        # V = 120, n = 3 -> k / 4 * 120 = [30, 60, 90].
        self.assertEqual(converted[0][0]["iter"], 30)
        self.assertEqual(converted[1][0]["iter"], 60)
        self.assertEqual(converted[2][0]["iter"], 90)

        report = SPSA_PARAM_HISTORY_TOOL._build_history_conversion_report(
            doc,
            iter_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
            c_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_C_TOLERANCE,
            r_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_R_TOLERANCE,
            chart_tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )

        self.assertEqual(report.recovery_errors, [])
        self.assertEqual(report.errors, [])

    def test_build_history_conversion_report_respaces_non_monotonic_c_monotonically(
        self,
    ):
        # The c values decode backward (iter 20 then 10). The regime is
        # inconsistent, so the chart-faithful fallback stores the runtime's
        # (monotone-anchored) c positions -- strictly increasing, never refused.
        gamma = 0.101
        base_c = 1.6
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
                        [{"theta": 11.0, "c": base_c / (21**gamma)}],
                        [{"theta": 12.0, "c": base_c / (11**gamma)}],
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

        self.assertEqual(report.recovery_errors, [])
        recovered = [row[0]["iter"] for row in report.converted_history]
        self.assertEqual(recovered, [20.0, 50.0])
        self.assertLess(recovered[0], recovered[1])

    def test_build_history_conversion_report_accepts_recoverable_monotonic_history(
        self,
    ):
        # Two c-invertible samples (c built at iters 100 and 200) on an
        # off-regime run: the chart-faithful fallback inverts the stored c and
        # recovers the true positions 100/200 exactly, not an even-spacing guess.
        gamma = 0.101
        base_c = 1.6
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 800,
                "spsa": {
                    "iter": 400,
                    "num_iter": 400,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": base_c / (101**gamma)}],
                        [{"theta": 12.0, "c": base_c / (201**gamma)}],
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

        self.assertEqual(report.recovery_errors, [])
        self.assertEqual(report.errors, [])
        self.assertEqual(report.converted_history[0][0]["iter"], 100.0)
        self.assertEqual(report.converted_history[1][0]["iter"], 200.0)

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

    def test_build_history_conversion_report_respaces_unrecoverable_constant_c_and_r_history(
        self,
    ):
        # Constant c and constant R carry no per-sample information and the
        # regime is inconsistent, so neither the sampler nor c/R inversion can
        # place the samples: the chart-faithful fallback stores the runtime's
        # fractional even spacing, a correct rendering whenever c is constant.
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

        # V = 500, n = 2 -> fractional even spacing 500/3, 1000/3.
        self.assertEqual(report.errors, [])
        self.assertEqual(report.recovery_errors, [])
        recovered = [row[0]["iter"] for row in report.converted_history]
        self.assertAlmostEqual(recovered[0], 500 / 3)
        self.assertAlmostEqual(recovered[1], 1000 / 3)
        self.assertLess(recovered[0], recovered[1])

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
        # Established iter is now the index-spacing recovery: V = 20, single
        # sample -> round(20/2) = 10.
        self.assertIn("Established iter: 10", output)
        self.assertIn("Best iter in window: 9", output)
        self.assertIn("Stored R targets: 1", output)

    def test_convert_history_c_to_iter_anchors_constant_c_to_sampler_boundaries(self):
        # gamma = 0 makes c constant, so it cannot pin the iter within a window;
        # the recovery anchors each sample to its historical boundary. Under the
        # 2025 regime with num_iter = num_games // 2 = 1000 and one param the
        # period is 10, so a run stopped at iter 30 recovers 10, 20, 30.
        doc = {
            "start_time": datetime(2025, 6, 1, tzinfo=UTC),
            "args": {
                "num_games": 2000,
                "spsa": {
                    "iter": 30,
                    "num_iter": 1000,
                    "gamma": 0,
                    "params": [{"c": 1.0}],
                    "param_history": [
                        [{"theta": 12.0, "c": 1.0}],
                        [{"theta": 13.0, "c": 1.0}],
                        [{"theta": 14.0, "c": 1.0}],
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        self.assertEqual([row[0]["theta"] for row in converted], [12.0, 13.0, 14.0])
        self.assertEqual([row[0]["iter"] for row in converted], [10, 20, 30])

    def test_convert_history_c_to_iter_recovers_strict_lt_samples_from_c(self):
        # Pre-2025 runs used the strict-lt append rule, so the first sample was
        # stored on the first update (iter ~ 0), not near one period. A run
        # created 2020 with num_iter = num_games // 2 = 1000 and one param uses
        # period = max(100, 25) = 100, giving windows [1,100], [101,200],
        # [201,250]. The stored c pins each sample inside its window, so a first
        # sample whose c decodes to iter 30 is recovered at 30 -- not the window
        # anchor (1) and not an even-spacing guess.
        gamma = 0.101
        base_c = 1.6
        true_iters = [30, 130, 230]
        doc = {
            "start_time": datetime(2020, 6, 1, tzinfo=UTC),
            "args": {
                "num_games": 2000,
                "spsa": {
                    "iter": 250,
                    "num_iter": 1000,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": base_c / ((sample_iter + 1) ** gamma)}]
                        for sample_iter in true_iters
                    ],
                },
            },
        }

        converted = SPSA_PARAM_HISTORY_TOOL._convert_history_c_to_iter(
            doc,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_ITER_TOLERANCE,
        )

        self.assertEqual([row[0]["iter"] for row in converted], true_iters)

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

        # The reference chart is now built through the same shared historical
        # sampler recovery as the stored iters (both resolve the run's dated
        # regime windows via `created`), so a faithfully converted partial-legacy
        # history reproduces the reference exactly -- no spurious row mismatch.
        check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
            doc,
            converted,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_SANITY_TOLERANCE,
        )

        self.assertEqual(check.checked_rows, 4)
        self.assertEqual(check.mismatched_rows, 0)
        self.assertIsNone(check.first_mismatch)

    def test_inspect_chart_roundtrip_rejects_wrong_stored_iter(self):
        # The round-trip is non-circular: the reference comes from the legacy c
        # values, the converted chart from the stored iters through the runtime
        # reader. A stored iter that does not reproduce the legacy-derived chart
        # must be rejected, even though c is invertible. The correct iters for
        # this history are 100 and 200.
        gamma = 0.101
        base_c = 1.6
        doc = {
            "start_time": datetime(2025, 4, 20, tzinfo=UTC),
            "args": {
                "num_games": 800,
                "spsa": {
                    "iter": 400,
                    "num_iter": 400,
                    "gamma": gamma,
                    "params": [{"theta": 12.5, "start": 10, "c": base_c}],
                    "param_history": [
                        [{"theta": 11.0, "c": base_c / (101**gamma)}],
                        [{"theta": 12.0, "c": base_c / (201**gamma)}],
                    ],
                },
            },
        }

        correct_history = [
            [{"theta": 11.0, "iter": 100}],
            [{"theta": 12.0, "iter": 200}],
        ]
        wrong_history = [
            [{"theta": 11.0, "iter": 100}],
            [{"theta": 12.0, "iter": 250}],
        ]

        correct_check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
            doc,
            correct_history,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )
        self.assertEqual(correct_check.mismatched_rows, 0)
        self.assertIsNone(correct_check.first_mismatch)

        wrong_check = SPSA_PARAM_HISTORY_TOOL._inspect_chart_roundtrip(
            doc,
            wrong_history,
            tolerance=SPSA_PARAM_HISTORY_TOOL.DEFAULT_CHART_TOLERANCE,
        )
        self.assertGreater(wrong_check.mismatched_rows, 0)
        self.assertIsNotNone(wrong_check.first_mismatch)

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
                "_build_legacy_reference_chart_payload",
                return_value=original_payload,
            ),
            patch.object(
                SPSA_PARAM_HISTORY_TOOL,
                "_render_iter_history_chart_payload",
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
                    # V = 40, n = 1 -> recovered iter = round(40/2) = 20 =
                    # sample_iter, so c and R still round-trip exactly.
                    "iter": 40,
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

    def test_build_spsa_new_stage_warns_when_nonempty_history_has_invalid_base_c(
        self,
    ):
        # An invalid base_c no longer blocks conversion: index spacing does not
        # need base_c, so the non-empty history is still converted and the bad
        # base_c is only surfaced as a warning while the stage stays ready.
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

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.stage_doc["stage"]["status"], "ready")
        self.assertEqual(result.errors, [])
        # V = 20, n = 1 -> round(20/2) = 10.
        self.assertEqual(
            result.stage_doc["args"]["spsa"]["param_history"],
            [[{"theta": 12.0, "iter": 10}]],
        )
        self.assertTrue(result.warnings)
        self.assertIn(
            "invalid args.spsa.params[0].c: expected a finite number > 0",
            result.warnings[0],
        )

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
                    # V = 40, n = 1 -> recovered iter = round(40/2) = 20.
                    "iter": 40,
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
