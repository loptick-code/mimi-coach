from __future__ import annotations

import unittest

from app.services.training_paces import (
    calculate_pace_targets,
    pace_to_seconds,
    seconds_to_pace,
    validate_ef_range,
)


class TrainingPaceTests(unittest.TestCase):
    def test_pace_format_round_trip(self):
        self.assertEqual(pace_to_seconds("5:15/km"), 315)
        self.assertEqual(seconds_to_pace(315), "5:15")

    def test_ef_band_must_be_30_seconds(self):
        validate_ef_range(300, 330)
        with self.assertRaises(ValueError):
            validate_ef_range(300, 331)

    def test_paces_scale_from_ef_midpoint(self):
        targets = calculate_pace_targets("10k", 300, 330)
        self.assertEqual(targets["s60"], 231)
        self.assertEqual(targets["race_10k"], 216)
        self.assertEqual(calculate_pace_targets("marathon", 300, 330), {})


if __name__ == "__main__":
    unittest.main()
