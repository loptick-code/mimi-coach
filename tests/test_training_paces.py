from __future__ import annotations

import unittest

from app.services.training_paces import (
    calculate_pace_targets,
    pace_to_seconds,
    scale_ef_range,
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
        marathon_targets = calculate_pace_targets("marathon", 300, 330)
        self.assertEqual(marathon_targets["s60"], 231)
        self.assertEqual(marathon_targets["race_half"], 228)
        self.assertEqual(marathon_targets["race_marathon"], 266)

    def test_feedback_moves_current_ef_and_keeps_30_second_gap(self):
        faster = scale_ef_range(300, 330, 0.98)
        slower = scale_ef_range(300, 330, 1.02)
        self.assertEqual(faster[1] - faster[0], 30)
        self.assertEqual(slower[1] - slower[0], 30)
        self.assertLess(faster[0], 300)
        self.assertGreater(slower[0], 300)


if __name__ == "__main__":
    unittest.main()
