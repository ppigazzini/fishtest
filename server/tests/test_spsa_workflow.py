"""Test the shared SPSA lifecycle helper contracts."""

import unittest

from fishtest.spsa_workflow import (
    CLASSIC_SPSA_ALGORITHM,
    DEFAULT_SPSA_SF_BETA1,
    DEFAULT_SPSA_SF_BETA2,
    DEFAULT_SPSA_SF_EPS,
    DEFAULT_SPSA_SF_LR,
    MIN_SPSA_SF_LR,
    SF_ADAM_SPSA_ALGORITHM,
    apply_spsa_result_updates,
    build_spsa_chart_payload,
    build_spsa_form_values,
    build_spsa_state,
    build_spsa_worker_step,
)


class SpsaWorkflowTests(unittest.TestCase):
    def test_build_spsa_form_values_defaults_to_sf_adam(self):
        values = build_spsa_form_values(None)

        self.assertEqual(values["algorithm"], SF_ADAM_SPSA_ALGORITHM)
        self.assertEqual(values["sf_lr"], DEFAULT_SPSA_SF_LR)
        self.assertEqual(values["sf_beta1"], DEFAULT_SPSA_SF_BETA1)
        self.assertEqual(values["sf_beta2"], DEFAULT_SPSA_SF_BETA2)
        self.assertEqual(values["sf_eps"], DEFAULT_SPSA_SF_EPS)
        self.assertEqual(values["raw_params"], "")

    def test_build_spsa_form_values_uses_existing_sf_adam_state(self):
        spsa = {
            "algorithm": SF_ADAM_SPSA_ALGORITHM,
            "raw_params": "Tempo,1,0,2,0.5",
            "sf_lr": 0.01,
            "sf_beta1": 0.8,
            "sf_beta2": 0.95,
            "sf_eps": 1e-6,
        }

        values = build_spsa_form_values(spsa, num_games=500)

        self.assertEqual(values["algorithm"], SF_ADAM_SPSA_ALGORITHM)
        self.assertEqual(values["raw_params"], "Tempo,1,0,2,0.5")
        self.assertEqual(values["sf_lr"], 0.01)
        self.assertEqual(values["sf_beta1"], 0.8)
        self.assertEqual(values["sf_beta2"], 0.95)
        self.assertEqual(values["sf_eps"], 1e-6)

    def test_build_spsa_state_sets_sf_adam_algorithm_and_params(self):
        post = {
            "spsa_algorithm": SF_ADAM_SPSA_ALGORITHM,
            "spsa_sf_lr": "0.0025",
            "spsa_sf_beta1": "0.9",
            "spsa_sf_beta2": "0.999",
            "spsa_sf_eps": "1e-8",
            "spsa_raw_params": "Tempo,1,0,2,0.5",
        }

        spsa = build_spsa_state(post, num_games=500)

        self.assertEqual(spsa["algorithm"], SF_ADAM_SPSA_ALGORITHM)
        self.assertEqual(spsa["num_iter"], 250)
        self.assertEqual(spsa["sf_lr"], 0.0025)
        self.assertEqual(spsa["params"][0]["theta"], 1.0)
        self.assertEqual(spsa["params"][0]["z"], 1.0)
        self.assertEqual(spsa["params"][0]["v"], 0.0)

    def test_build_spsa_state_accepts_minimum_sf_learning_rate(self):
        post = {
            "spsa_algorithm": SF_ADAM_SPSA_ALGORITHM,
            "spsa_sf_lr": "1e-8",
            "spsa_sf_beta1": "0.9",
            "spsa_sf_beta2": "0.999",
            "spsa_sf_eps": "1e-8",
            "spsa_raw_params": "Tempo,1,0,2,0.5",
        }

        spsa = build_spsa_state(post, num_games=500)

        self.assertEqual(spsa["sf_lr"], MIN_SPSA_SF_LR)

    def test_build_spsa_state_rejects_too_small_sf_learning_rate(self):
        post = {
            "spsa_algorithm": SF_ADAM_SPSA_ALGORITHM,
            "spsa_sf_lr": "9e-9",
            "spsa_sf_beta1": "0.9",
            "spsa_sf_beta2": "0.999",
            "spsa_sf_eps": "1e-8",
            "spsa_raw_params": "Tempo,1,0,2,0.5",
        }

        with self.assertRaisesRegex(
            ValueError,
            "SPSA learning rate must be between 1e-8 and 1",
        ):
            build_spsa_state(post, num_games=500)

    def test_build_spsa_state_supports_classic_when_requested(self):
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

    def test_build_spsa_state_rejects_invalid_classic_hyperparameters(self):
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

    def test_build_spsa_state_rejects_unknown_algorithm(self):
        post = {
            "spsa_algorithm": "unknown",
            "spsa_raw_params": "Tempo,1,0,2,0.5",
        }

        with self.assertRaisesRegex(ValueError, "Unknown SPSA algorithm"):
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

    def test_build_spsa_worker_step_uses_constant_c_for_sf_adam(self):
        worker_step = build_spsa_worker_step(
            {"algorithm": SF_ADAM_SPSA_ALGORITHM},
            {"c": 0.5},
            iter_value=2,
            flip=-1,
        )

        self.assertEqual(worker_step, {"c": 0.5, "flip": -1})

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

        self.assertEqual(result, [13.0])
        self.assertEqual(spsa["params"][0]["theta"], 13.0)

    def test_apply_spsa_result_updates_updates_sf_adam_state(self):
        spsa = {
            "algorithm": SF_ADAM_SPSA_ALGORITHM,
            "iter": 10,
            "sf_lr": 0.0025,
            "sf_beta1": 0.9,
            "sf_beta2": 0.999,
            "sf_eps": 1e-8,
            "sf_weight_sum": 0.0,
            "mu2_init": 0.8,
            "mu2_reports": 5.0,
            "mu2_sum_N": 82.5,
            "mu2_sum_s": 0.0,
            "mu2_sum_s2_over_N": 4.0,
            "params": [
                {
                    "theta": 10.0,
                    "z": 10.0,
                    "v": 0.0,
                    "min": 0.0,
                    "max": 20.0,
                    "c": 0.5,
                },
            ],
        }
        w_params = [{"c": 0.5, "flip": 1}]

        show_values = apply_spsa_result_updates(
            spsa,
            w_params,
            result=3,
            game_pairs=10,
        )

        self.assertEqual(len(show_values), 1)
        self.assertAlmostEqual(spsa["sf_weight_sum"], 0.025)
        self.assertEqual(spsa["mu2_reports"], 6.0)
        self.assertAlmostEqual(spsa["mu2_sum_N"], 92.5)
        self.assertAlmostEqual(spsa["mu2_sum_s"], 3.0)
        self.assertAlmostEqual(spsa["mu2_sum_s2_over_N"], 4.9)
        self.assertGreater(spsa["params"][0]["theta"], 10.0)
        self.assertGreater(spsa["params"][0]["z"], 10.0)
        self.assertGreaterEqual(spsa["params"][0]["v"], 0.0)

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

    def test_build_spsa_chart_payload_returns_server_shaped_chart_rows_for_classic(
        self,
    ):
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
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
            "param_history": [[{"theta": 12.0, "c": 1.5}]],
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
        self.assertEqual(payload["chart_rows"][1]["c_values"], [1.5])
        self.assertEqual(payload["chart_rows"][2]["values"], [12.5])
        self.assertGreater(
            payload["chart_rows"][2]["iter_ratio"],
            payload["chart_rows"][1]["iter_ratio"],
        )
        self.assertAlmostEqual(
            payload["chart_rows"][2]["c_values"][0],
            1.6 / (3**0.101),
        )

    def test_build_spsa_chart_payload_deduplicates_matching_live_row_for_classic(self):
        live_c = 1.6 / (21**0.101)
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
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
            "param_history": [[{"theta": 12.5, "R": 0.08, "c": live_c}]],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(len(payload["chart_rows"]), 2)
        self.assertAlmostEqual(payload["chart_rows"][-1]["iter_ratio"], 20 / 250)
        self.assertEqual(payload["chart_rows"][-1]["values"], [12.5])
        self.assertEqual(payload["chart_rows"][-1]["c_values"], [live_c])

    def test_build_spsa_chart_payload_reconstructs_sf_adam_chart_rows(self):
        spsa = {
            "algorithm": SF_ADAM_SPSA_ALGORITHM,
            "iter": 2,
            "num_iter": 10,
            "sf_beta1": 0.9,
            "params": [
                {
                    "name": "ParamSF",
                    "theta": 12.5,
                    "start": 10,
                    "min": 0,
                    "max": 20,
                    "c": 0.5,
                    "z": 12.0,
                },
            ],
            "param_history": [[{"theta": 12.0, "c": 0.5}]],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            set(payload),
            {
                "param_names",
                "chart_rows",
            },
        )
        self.assertEqual(payload["param_names"], ["ParamSF"])
        self.assertEqual(len(payload["chart_rows"]), 3)
        self.assertEqual(
            payload["chart_rows"][0],
            {"iter_ratio": 0.0, "values": [10.0]},
        )
        self.assertEqual(payload["chart_rows"][1]["values"], [12.0])
        self.assertAlmostEqual(
            payload["chart_rows"][2]["values"][0],
            (12.5 - (1 - 0.9) * 12.0) / 0.9,
        )
        self.assertEqual(payload["chart_rows"][1]["c_values"], [0.5])
        self.assertEqual(payload["chart_rows"][2]["c_values"], [0.5])
        self.assertLess(
            payload["chart_rows"][1]["iter_ratio"],
            payload["chart_rows"][2]["iter_ratio"],
        )
        self.assertAlmostEqual(payload["chart_rows"][2]["iter_ratio"], 0.2)

    def test_build_spsa_chart_payload_ignores_non_list_history_samples(self):
        spsa = {
            "algorithm": CLASSIC_SPSA_ALGORITHM,
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
            "param_history": [{"iter": 2, "params": [{"theta": 12.0, "c": 1.5}]}],
        }

        payload = build_spsa_chart_payload(spsa)

        self.assertEqual(
            payload["chart_rows"],
            [{"iter_ratio": 0.0, "values": [10.0]}],
        )

    def test_build_spsa_chart_payload_rejects_unknown_algorithm(self):
        with self.assertRaisesRegex(ValueError, "Unknown SPSA algorithm"):
            build_spsa_chart_payload({"algorithm": "unknown"})
