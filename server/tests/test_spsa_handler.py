"""Test SPSA handler history sampling behavior."""

import unittest

from fishtest.spsa_handler import _add_to_history


class SpsaHandlerHistoryTests(unittest.TestCase):
    def test_add_to_history_stores_theta_and_iter_only(self):
        spsa = {
            "iter": 20,
            "params": [
                {
                    "theta": 12.5,
                }
            ],
        }

        _add_to_history(spsa, num_games=500)

        self.assertEqual(spsa["param_history"], [[{"theta": 12.5, "iter": 20}]])
