"""Test the shared SPSA lifecycle helper contracts."""

import json
import unittest
from math import isfinite

from fishtest.spsa_workflow import (
    CLASSIC_SPSA_ALGORITHM,
    apply_spsa_result_updates,
    build_spsa_chart_payload,
    build_spsa_form_values,
    build_spsa_state,
    build_spsa_worker_step,
)


class SpsaWorkflowTests(unittest.TestCase):
    def test_build_spsa_form_values_defaults_to_classic(self):
        values = build_spsa_form_values(None)

        self.assertEqual(values["algorithm"], CLASSIC_SPSA_ALGORITHM)
        self.assertEqual(values["A"], 0.1)
        self.assertEqual(values["alpha"], 0.602)
        self.assertEqual(values["gamma"], 0.101)
        self.assertEqual(values["raw_params"], "")

    def test_build_spsa_form_values_uses_ratio_without_mutating_state(self):
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
            "A": 25,
            "alpha": 0.602,
            "gamma": 0.101,
            "raw_params": "Tempo,1,0,2,0.5,0.1",
        }

        values = build_spsa_form_values(spsa, num_games=500)

        self.assertEqual(values["A"], 0.1)
        self.assertEqual(spsa["A"], 25)

    def test_build_spsa_state_sets_classic_algorithm_and_params(self):
        post = {
            "spsa_algorithm": CLASSIC_SPSA_ALGORITHM,
            "spsa_A": "0.1",
            "spsa_alpha": "0.602",
            "spsa_gamma": "0.101",
            "spsa_raw_params": "Tempo,1,0,2,0.5,0.1",
        }

        spsa = build_spsa_state(post, num_games=500)

        self.assertEqual(spsa["algorithm"], CLASSIC_SPSA_ALGORITHM)
        self.assertEqual(spsa["A"], 25)
        self.assertEqual(spsa["num_iter"], 250)
        self.assertEqual(spsa["params"][0]["theta"], 1.0)

    def test_build_spsa_state_rejects_unknown_algorithm(self):
        post = {
            "spsa_algorithm": "unknown",
            "spsa_A": "0.1",
            "spsa_alpha": "0.602",
            "spsa_gamma": "0.101",
            "spsa_raw_params": "Tempo,1,0,2,0.5,0.1",
        }

        with self.assertRaisesRegex(ValueError, "Unknown SPSA algorithm"):
            build_spsa_state(post, num_games=500)

    def test_build_spsa_state_rejects_invalid_hyperparameters(self):
        base_post = {
            "spsa_algorithm": CLASSIC_SPSA_ALGORITHM,
            "spsa_A": "0.1",
            "spsa_alpha": "0.602",
            "spsa_gamma": "0.101",
            "spsa_raw_params": "Tempo,1,0,2,0.5,0.1",
        }

        invalid_cases = [
            ("spsa_A", "nan", "A ratio"),
            ("spsa_alpha", "inf", "alpha"),
            ("spsa_gamma", "-1", ">= 0"),
        ]

        for field_name, raw_value, pattern in invalid_cases:
            post = dict(base_post)
            post[field_name] = raw_value

            with self.subTest(field_name=field_name, raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, pattern):
                    build_spsa_state(post, num_games=500)

    def test_build_spsa_worker_step_uses_classic_decay(self):
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
            "A": 25,
            "alpha": 0.602,
            "gamma": 0.101,
        }
        param = {"a": 0.2, "c": 1.6}

        worker_step = build_spsa_worker_step(spsa, param, iter_value=2, flip=1)

        expected_c = 1.6 / (3**0.101)
        expected_R = 0.2 / (28**0.602) / expected_c**2
        self.assertAlmostEqual(worker_step["c"], expected_c)
        self.assertAlmostEqual(worker_step["R"], expected_R)
        self.assertEqual(worker_step["flip"], 1)

    def test_apply_spsa_result_updates_preserves_classic_update_rule(self):
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
            "iter": 4,
            "params": [
                {
                    "theta": 10.0,
                    "min": 0.0,
                    "max": 20.0,
                },
            ],
        }
        w_params = [{"R": 0.5, "c": 2.0, "flip": 1}]

        result = apply_spsa_result_updates(
            spsa,
            w_params,
            result=3,
            game_pairs=10,
        )

        self.assertIsNone(result)
        self.assertEqual(spsa["params"][0]["theta"], 13.0)

    def test_apply_spsa_result_updates_rejects_length_mismatch(self):
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
            "iter": 4,
            "params": [
                {
                    "theta": 10.0,
                    "min": 0.0,
                    "max": 20.0,
                },
                {
                    "theta": 11.0,
                    "min": 0.0,
                    "max": 20.0,
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, "length mismatch"):
            apply_spsa_result_updates(
                spsa,
                [{"R": 0.5, "c": 2.0, "flip": 1}],
                result=3,
                game_pairs=10,
            )

    def test_build_spsa_chart_payload_returns_server_shaped_chart_rows(self):
        spsa = {
            "iter": 2,
            "num_iter": 10,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [[{"theta": 12.0, "iter": 1}]],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            set(payload),
            {
                "param_names",
                "chart_rows",
            },
        )
        self.assertEqual(payload["param_names"], ["ParamA"])
        self.assertEqual(
            payload["chart_rows"][0],
            {"iter_ratio": 0.0, "values": [10.0]},
        )
        self.assertEqual(payload["chart_rows"][1]["values"], [12.0])
        self.assertAlmostEqual(
            payload["chart_rows"][1]["c_values"][0],
            1.6 / (2**0.101),
        )
        self.assertEqual(payload["chart_rows"][2]["values"], [12.5])
        self.assertGreater(
            payload["chart_rows"][2]["iter_ratio"],
            payload["chart_rows"][1]["iter_ratio"],
        )
        self.assertAlmostEqual(
            payload["chart_rows"][2]["c_values"][0],
            1.6 / (3**0.101),
        )

    def test_build_spsa_chart_payload_deduplicates_matching_live_row(self):
        live_c = 1.6 / (21**0.101)
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [[{"theta": 12.5, "iter": 20}]],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(len(payload["chart_rows"]), 2)
        self.assertAlmostEqual(payload["chart_rows"][-1]["iter_ratio"], 20 / 250)
        self.assertEqual(payload["chart_rows"][-1]["values"], [12.5])
        self.assertEqual(payload["chart_rows"][-1]["c_values"], [live_c])

    def test_build_spsa_chart_payload_ignores_legacy_c_only_history_rows(self):
        gamma = 0.101
        base_c = 1.6
        sample_iter_1 = 20
        sample_iter_2 = 200
        sample_c_1 = base_c / ((sample_iter_1 + 1) ** gamma)
        sample_c_2 = base_c / ((sample_iter_2 + 1) ** gamma)
        spsa = {
            "iter": 201,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": gamma,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": base_c,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 11.5, "c": sample_c_1}],
                [{"theta": 12.0, "c": sample_c_2}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_accepts_estimated_float_iter_positions(self):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 11.0, "iter": 2.5}],
                [{"theta": 12.0, "iter": 5.0}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertAlmostEqual(payload["chart_rows"][1]["iter_ratio"], 2.5 / 250)
        self.assertAlmostEqual(payload["chart_rows"][2]["iter_ratio"], 5.0 / 250)
        self.assertAlmostEqual(
            payload["chart_rows"][1]["c_values"][0],
            1.6 / (3.5**0.101),
        )

    def test_build_spsa_chart_payload_drops_explicit_zero_iter_history_rows(self):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 12.0, "iter": 0}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_uses_explicit_iter_history_positions(
        self,
    ):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 11.0, "iter": 7}],
                [{"theta": 12.0, "iter": 13}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(len(payload["chart_rows"]), 4)
        self.assertAlmostEqual(payload["chart_rows"][1]["iter_ratio"], 7 / 250)
        self.assertAlmostEqual(
            payload["chart_rows"][2]["iter_ratio"],
            13 / 250,
        )
        self.assertAlmostEqual(payload["chart_rows"][3]["iter_ratio"], 20 / 250)

    def test_build_spsa_chart_payload_preserves_live_point_when_history_c_is_missing(
        self,
    ):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 12.5, "iter": 10}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(len(payload["chart_rows"]), 3)
        self.assertAlmostEqual(payload["chart_rows"][1]["iter_ratio"], 10 / 250)
        self.assertAlmostEqual(payload["chart_rows"][2]["iter_ratio"], 20 / 250)
        self.assertIsNotNone(payload["chart_rows"][2]["c_values"][0])
        self.assertNotEqual(
            payload["chart_rows"][1]["c_values"][0],
            payload["chart_rows"][2]["c_values"][0],
        )

    def test_build_spsa_chart_payload_ignores_legacy_history_rows_without_iter(self):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 11.0, "c": None}],
                [{"theta": 12.0, "c": 1.6 / (21**0.101)}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_ignores_constant_c_history_rows_without_iter(
        self,
    ):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.0,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 11.0, "c": 1.6}],
                [{"theta": 12.0, "c": 1.6}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_ignores_chart_only_constant_cr_rows(self):
        gamma = 0.101
        base_c = 7.59509630360077
        spsa = {
            "iter": 500,
            "num_iter": 500,
            "A": 1.0,
            "alpha": 0.602,
            "gamma": gamma,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": base_c,
                    "c_end": 0.1,
                    "a": 0.1,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [{"theta": 11.0, "c": base_c, "R": 0.0009177370584335204}],
                [{"theta": 12.0, "c": base_c, "R": 0.0009177370584335204}],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_ignores_legacy_r_history_rows_without_iter(
        self,
    ):
        base_c = 1.6
        base_a = 0.2
        A = 1.0
        alpha = 1.0
        sample_iter_1 = 5
        sample_iter_2 = 9
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": A,
            "alpha": alpha,
            "gamma": 0.0,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": base_c,
                    "c_end": 0.1,
                    "a": base_a,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [
                [
                    {
                        "theta": 11.0,
                        "c": base_c,
                        "R": base_a / (A + sample_iter_1 + 1) / base_c**2,
                    }
                ],
                [
                    {
                        "theta": 12.0,
                        "c": base_c,
                        "R": base_a / (A + sample_iter_2 + 1) / base_c**2,
                    }
                ],
            ],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_ignores_non_list_history_samples(self):
        spsa = {
            "iter": 20,
            "num_iter": 250,
            "A": 4,
            "alpha": 0.602,
            "gamma": 0.101,
            "params": [
                {
                    "name": "ParamA",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 1.6,
                    "c_end": 0.1,
                    "a": 0.2,
                    "r_end": 1.0e-03,
                },
            ],
            "param_history": [{"c": 0.8, "params": [{"theta": 12.0, "c": 0.8}]}],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_sanitizes_non_finite_numbers(self):
        spsa = {
            "iter": float("inf"),
            "num_iter": 10,
            "gamma": float("nan"),
            "params": [
                {
                    "name": "ParamA",
                    "theta": float("inf"),
                    "start": float("nan"),
                    "c": float("inf"),
                },
            ],
            "param_history": [[{"theta": float("nan"), "c": float("inf")}]],
        }

        payload = build_spsa_chart_payload(spsa)
        serialized = json.dumps(payload)

        self.assertNotIn("Infinity", serialized)
        self.assertNotIn("NaN", serialized)
        for row in payload["chart_rows"]:
            self.assertTrue(isfinite(row["iter_ratio"]))
            for value in row["values"]:
                self.assertTrue(isfinite(value))
            for c_value in row.get("c_values", []):
                self.assertTrue(c_value is None or isfinite(c_value))

    def test_build_spsa_chart_payload_rejects_unknown_algorithm(self):
        with self.assertRaisesRegex(ValueError, "Unknown SPSA algorithm"):
            build_spsa_chart_payload({"algorithm": "unknown"})
