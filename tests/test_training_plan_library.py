from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.services.training_plan_library import generate_schedule, get_plan


class TrainingPlanLibraryTests(unittest.TestCase):
    def test_10k_workouts_keep_interval_repeats_and_ef_steps(self):
        from app.services.training_plan_library import _load_10k

        plan = _load_10k()
        workout = next(item for item in plan.workouts if item.week_number == 2 and item.session_number == 2)

        self.assertEqual(workout.steps[0].label, "EF")
        self.assertEqual(workout.steps[0].step_type, "other")
        self.assertEqual(workout.steps[1].repeat_count, 6)
        self.assertEqual(workout.steps[1].children[0].label, "Seuil 60")
        self.assertEqual(workout.steps[1].children[0].step_type, "interval")
        self.assertEqual(workout.steps[1].children[1].label, "Récupération")
        self.assertEqual(workout.steps[1].children[1].step_type, "recovery")

    def test_objectives_have_library_templates(self):
        self.assertEqual(get_plan("10k").weeks, 13)
        self.assertEqual(get_plan("half").weeks, 15)

    def test_source_plans_expose_the_race_session(self):
        for goal, race_name in (("10k", "Course 10 km"), ("half", "Course semi-marathon")):
            plan = get_plan(goal)
            race = next(
                item for item in plan.workouts
                if item.week_number == plan.weeks and item.session_number == 4
            )
            self.assertEqual(race.name, race_name)
            self.assertEqual(race.steps[0].label, "Course")

    def test_schedule_is_anchored_on_race_date(self):
        race_date = date(2027, 3, 14)  # dimanche
        _, schedule = generate_schedule("10k", race_date, 300, 330, 4, today=date(2026, 11, 22))
        self.assertEqual(schedule[-1].date, race_date)
        self.assertGreater(schedule[-1].plan_week, 13)
        self.assertEqual(schedule[-1].session_number, 4)
        source_start = race_date - timedelta(days=6) - timedelta(weeks=12)
        source_first = next(item for item in schedule if item.date == source_start and item.session_number == 1)
        self.assertEqual(source_first.date.weekday(), 0)

    def test_source_easy_long_run_preserves_summary_metadata_and_ef_step(self):
        from app.services.training_plan_library import _load_semi

        plan = _load_semi()
        workout = next(item for item in plan.workouts if item.week_number == 4 and item.session_number == 4)

        self.assertEqual(workout.difficulty, 1)
        self.assertEqual(workout.estimated_duration_min, 60)
        self.assertAlmostEqual(workout.estimated_distance_km, 12.33, places=2)
        self.assertEqual(len(workout.steps), 1)
        self.assertEqual(workout.steps[0].label, "EF")
        self.assertEqual(workout.steps[0].duration_seconds, 3600)

    def test_non_sunday_race_keeps_each_program_week_monday_to_sunday(self):
        race_date = date(2027, 4, 17)  # samedi
        _, schedule = generate_schedule("half", race_date, 300, 330, 4, today=date(2026, 11, 22))

        source_start = race_date - timedelta(days=race_date.weekday()) - timedelta(weeks=14)
        source_first = next(item for item in schedule if item.date == source_start and item.session_number == 1)
        self.assertEqual(source_first.date.weekday(), 0)
        self.assertEqual(schedule[-1].date, race_date)
        self.assertEqual(schedule[-1].session_number, 4)
        self.assertTrue(all(item.date.weekday() == 0 for item in schedule if item.session_number == 1))

        for item in schedule:
            week_start = item.date - timedelta(days=item.date.weekday())
            self.assertGreaterEqual(item.date, week_start)
            self.assertLessEqual(item.date, week_start + timedelta(days=6))

    def test_long_lead_time_adds_general_preparation_before_source_plan(self):
        race_date = date(2027, 3, 14)
        _, schedule = generate_schedule("10k", race_date, 300, 330, 4, today=date(2026, 11, 22))
        source_start = race_date - timedelta(days=6) - timedelta(weeks=12)
        source_first = next(item for item in schedule if item.date == source_start and item.session_number == 1)
        self.assertEqual(source_first.plan_week, 5)
        self.assertTrue(any(item.plan_week == 1 for item in schedule))
        self.assertTrue(any("Préparation générale" in item.description for item in schedule))

    def test_three_sessions_remove_session_three_every_week(self):
        race_date = date(2027, 3, 14)
        _, schedule = generate_schedule("half", race_date, 300, 330, 3, today=date(2026, 11, 22))
        self.assertTrue(schedule)
        self.assertTrue(all(item.session_number != 3 for item in schedule))
        self.assertEqual(schedule[-1].date, race_date)

    def test_ef_is_applied_to_easy_steps(self):
        plan = get_plan("10k")
        easy = next(item for item in plan.workouts if item.week_number == 1 and item.session_number == 1)
        self.assertEqual(easy.steps[0].target_key, "ef")
        _, schedule = generate_schedule("10k", date(2027, 3, 14), 300, 330, 4, today=date(2026, 11, 22))
        first_easy = next(item for item in schedule if item.session_number == 1)
        self.assertEqual(first_easy.steps[0].source_pace_fast, 300)
        self.assertEqual(first_easy.steps[0].source_pace_slow, 330)


if __name__ == "__main__":
    unittest.main()
