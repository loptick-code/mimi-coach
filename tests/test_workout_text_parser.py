from __future__ import annotations

import unittest
from datetime import date

from app.services.workout_text_parser import WorkoutTextError, parse_workout_text


class WorkoutTextParserTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2026, 11, 9)
        self.targets = {"s60": 216, "tempo": 255, "race_10k": 202}
        self.ef = (279, 309)

    def test_parses_repeats_and_profile_paces(self):
        result = parse_workout_text(
            "Semaine 1\nMercredi : 15 min échauffement + 6 x (3 min S60 + 1 min 30 récupération) + 1 h 30 EF",
            start_date=self.start,
            pace_targets=self.targets,
            ef_range=self.ef,
        )
        self.assertEqual(len(result.workouts), 1)
        workout = result.workouts[0]
        self.assertEqual(workout.date, date(2026, 11, 11))
        repeat = workout.steps[1]
        self.assertEqual(repeat.kind, "repeat")
        self.assertEqual(repeat.repeat_count, 6)
        self.assertEqual(workout.steps[2].duration_value, 180)
        self.assertEqual(workout.steps[3].duration_value, 90)
        self.assertEqual(workout.steps[4].duration_value, 5400)
        self.assertEqual(workout.steps[2].pace_fast_seconds_per_km, 216)

    def test_three_sessions_per_week_omits_the_third_of_four(self):
        result = parse_workout_text(
            "Semaine 1\nLundi : 40 min EF\nMardi : 30 min EF\nJeudi : 45 min tempo à 4:10/km\nDimanche : 60 min EF",
            start_date=self.start,
            ef_range=self.ef,
            sessions_per_week=3,
        )
        self.assertEqual(len(result.workouts), 3)
        self.assertEqual([item.weekday_label for item in result.omitted_workouts], ["jeudi"])

    def test_three_explicit_sessions_are_kept(self):
        result = parse_workout_text(
            "Lundi : 40 min EF\nMercredi : 30 min EF\nDimanche : 60 min EF",
            start_date=self.start,
            ef_range=self.ef,
            sessions_per_week=3,
        )
        self.assertEqual(len(result.workouts), 3)
        self.assertEqual(result.omitted_workouts, [])

    def test_race_week_removes_explicit_session_three(self):
        result = parse_workout_text(
            "Semaine 13\nSéance 2 (mardi) : 20 min EF\nSéance 3 (jeudi) : 20 min EF\nSéance 4 (dimanche) : 10 km à allure 10 km",
            start_date=self.start,
            pace_targets={"race_10k": 202},
            ef_range=self.ef,
            sessions_per_week=3,
        )
        self.assertEqual(len(result.workouts), 2)
        self.assertEqual([item.session_number for item in result.omitted_workouts], [3])

    def test_unknown_sentence_is_rejected(self):
        with self.assertRaises(WorkoutTextError):
            parse_workout_text(
                "Semaine 1\nFais une sortie tranquille comme tu le sens",
                start_date=self.start,
            )

    def test_week_heading_and_session_day_required(self):
        with self.assertRaises(WorkoutTextError):
            parse_workout_text("40 min EF", start_date=self.start, ef_range=self.ef)


if __name__ == "__main__":
    unittest.main()
