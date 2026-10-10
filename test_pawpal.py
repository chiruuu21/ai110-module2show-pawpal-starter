"""Tests for the PawPal+ scheduling engine.

Focus is on the behaviors that would silently produce a wrong plan: recurrence
filtering, the free interval placement model, the time budget boundaries, and
determinism across repeated runs.
"""

from datetime import date, time

import pytest

from pawpal_system import (
    CareTask,
    DayConstraints,
    Frequency,
    Owner,
    Pet,
    Priority,
    Recurrence,
    Scheduler,
    SkipReason,
    TaskCategory,
)

FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)
MONDAY = date(2026, 10, 12)


@pytest.fixture
def scheduler():
    return Scheduler()


def constraints(**overrides):
    """A 10 hour day starting at 08:00 unless a test says otherwise."""
    settings = dict(day=FRIDAY, day_start=time(8, 0), available_minutes=600)
    settings.update(overrides)
    return DayConstraints(**settings)


def titles(plan):
    return [entry.task.title for entry in plan.entries]


def reasons(plan):
    return {item.task.title: item.reason for item in plan.deferred}


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------


class TestRecurrence:
    def test_daily_is_always_due(self):
        assert Recurrence(Frequency.DAILY).is_due_on(FRIDAY)

    def test_weekly_matches_only_its_weekdays(self):
        weekly = Recurrence(Frequency.WEEKLY, weekdays=[0])  # Monday
        assert weekly.is_due_on(MONDAY)
        assert not weekly.is_due_on(FRIDAY)

    def test_weekly_without_weekdays_is_never_due(self):
        """An empty list must not silently mean 'every day'."""
        assert not Recurrence(Frequency.WEEKLY).is_due_on(FRIDAY)

    def test_once_is_due_only_on_its_date(self):
        once = Recurrence(Frequency.ONCE, due_date=FRIDAY)
        assert once.is_due_on(FRIDAY)
        assert not once.is_due_on(SATURDAY)

    def test_once_without_a_date_is_never_due(self):
        """Otherwise a one off vet visit reappears every day forever."""
        assert not Recurrence(Frequency.ONCE).is_due_on(FRIDAY)


# ---------------------------------------------------------------------------
# CareTask
# ---------------------------------------------------------------------------


class TestCareTaskValidation:
    @pytest.mark.parametrize("title", ["", "   "])
    def test_blank_title_rejected(self, title):
        with pytest.raises(ValueError, match="title"):
            CareTask(title, 30)

    @pytest.mark.parametrize("duration", [0, -5])
    def test_non_positive_duration_rejected(self, duration):
        with pytest.raises(ValueError, match="positive"):
            CareTask("Walk", duration)

    @pytest.mark.parametrize("duration", [10.5, "30", True])
    def test_non_integer_duration_rejected(self, duration):
        with pytest.raises(ValueError, match="whole number"):
            CareTask("Walk", duration)

    def test_validation_runs_on_direct_construction(self):
        """Not only through Pet.add_task, or the scheduler could be handed junk."""
        with pytest.raises(ValueError):
            CareTask("Walk", -1)


class TestCareTaskCompletion:
    def test_mark_complete_changes_the_task_status(self):
        """Task completion: the status must actually flip."""
        task = CareTask("Morning walk", 30)
        assert task.is_done_on(FRIDAY) is False
        task.mark_complete(FRIDAY)
        assert task.is_done_on(FRIDAY) is True

    def test_completion_is_recorded_per_date(self):
        task = CareTask("Walk", 30)
        task.mark_complete(FRIDAY)
        assert task.is_done_on(FRIDAY)
        assert not task.is_done_on(SATURDAY)

    def test_daily_task_done_today_is_still_due_tomorrow(self):
        """The bug a single is_completed boolean would cause."""
        task = CareTask("Walk", 30)
        task.mark_complete(FRIDAY)
        assert not task.is_due_on(FRIDAY)
        assert task.is_due_on(SATURDAY)


# ---------------------------------------------------------------------------
# Pet and Owner
# ---------------------------------------------------------------------------


class TestPet:
    def test_adding_a_task_increases_the_pet_task_count(self):
        """Task addition: the pet's task list must grow by one."""
        pet = Pet("Mochi", "dog")
        assert len(pet.tasks) == 0
        pet.add_task(CareTask("Morning walk", 30))
        assert len(pet.tasks) == 1
        pet.add_task(CareTask("Dinner", 10))
        assert len(pet.tasks) == 2

    def test_add_task_stamps_the_back_reference(self):
        pet = Pet("Mochi", "dog")
        task = CareTask("Walk", 30)
        pet.add_task(task)
        assert task.pet_id == pet.id

    def test_remove_task_by_id_returns_true(self):
        pet = Pet("Mochi", "dog")
        task = CareTask("Walk", 30)
        pet.add_task(task)
        assert pet.remove_task(task.id) is True
        assert pet.tasks == []

    def test_remove_unknown_task_returns_false(self):
        assert Pet("Mochi", "dog").remove_task("nope") is False

    def test_removal_targets_the_right_task_after_indexes_shift(self):
        pet = Pet("Mochi", "dog")
        first, second, third = (CareTask(name, 10) for name in ("A", "B", "C"))
        for task in (first, second, third):
            pet.add_task(task)
        pet.remove_task(first.id)
        pet.remove_task(third.id)
        assert [task.title for task in pet.tasks] == ["B"]


class TestOwner:
    def test_all_tasks_flattens_across_pets(self):
        owner = Owner("Jordan")
        for name in ("Mochi", "Biscuit"):
            pet = Pet(name, "dog")
            pet.add_task(CareTask(f"{name} walk", 30))
            owner.add_pet(pet)
        assert len(owner.all_tasks()) == 2

    def test_scheduled_task_traces_back_to_its_pet(self):
        owner = Owner("Jordan")
        pet = Pet("Mochi", "dog")
        task = CareTask("Walk", 30)
        pet.add_task(task)
        owner.add_pet(pet)
        assert owner.pet_name_for(task) == "Mochi"

    def test_adding_the_same_pet_twice_is_ignored(self):
        owner = Owner("Jordan")
        pet = Pet("Mochi", "dog")
        owner.add_pet(pet)
        owner.add_pet(pet)
        assert len(owner.pets) == 1


# ---------------------------------------------------------------------------
# DayConstraints
# ---------------------------------------------------------------------------


class TestDayConstraints:
    def test_negative_budget_rejected(self):
        with pytest.raises(ValueError, match="negative"):
            constraints(available_minutes=-1)

    def test_zero_budget_is_legal(self):
        assert constraints(available_minutes=0).available_minutes == 0

    def test_window_crossing_midnight(self):
        night = constraints(day_start=time(22, 0), available_minutes=240)
        assert night.end_datetime().day == SATURDAY.day
        assert night.end_datetime().hour == 2

    def test_offset_of_wraps_past_midnight(self):
        night = constraints(day_start=time(22, 0), available_minutes=240)
        assert night.offset_of(time(1, 0)) == 180


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_task_not_due_today_is_deferred_with_a_reason(self, scheduler):
        weekly = CareTask(
            "Nail trim", 45, recurrence=Recurrence(Frequency.WEEKLY, weekdays=[0])
        )
        plan = scheduler.build_plan([weekly], constraints())
        assert plan.entries == []
        assert reasons(plan)["Nail trim"] is SkipReason.NOT_DUE_TODAY

    def test_task_already_done_today_is_deferred(self, scheduler):
        task = CareTask("Walk", 30)
        task.mark_complete(FRIDAY)
        plan = scheduler.build_plan([task], constraints())
        assert reasons(plan)["Walk"] is SkipReason.ALREADY_COMPLETED

    def test_empty_task_list_produces_an_empty_plan(self, scheduler):
        plan = scheduler.build_plan([], constraints())
        assert plan.entries == [] and plan.deferred == []


class TestTimeBudget:
    def test_task_that_exactly_fills_the_day_still_fits(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("Exactly 60", 60)], constraints(available_minutes=60)
        )
        assert titles(plan) == ["Exactly 60"]

    def test_one_minute_too_long_is_not_merely_out_of_time(self, scheduler):
        """A task that can never fit is a different problem from a full day."""
        plan = scheduler.build_plan(
            [CareTask("61 min", 61)], constraints(available_minutes=60)
        )
        assert reasons(plan)["61 min"] is SkipReason.EXCEEDS_DAY_BUDGET

    def test_zero_budget_defers_everything_without_crashing(self, scheduler):
        plan = scheduler.build_plan([CareTask("Walk", 30)], constraints(available_minutes=0))
        assert plan.entries == []
        assert reasons(plan)["Walk"] is SkipReason.NO_TIME_LEFT

    def test_lower_priority_task_is_cut_when_time_runs_out(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Important", 50, Priority.HIGH),
                CareTask("Optional", 50, Priority.LOW),
            ],
            constraints(available_minutes=60),
        )
        assert titles(plan) == ["Important"]
        assert reasons(plan)["Optional"] is SkipReason.NO_TIME_LEFT


class TestOrdering:
    def test_higher_priority_is_scheduled_first(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("Low", 30, Priority.LOW), CareTask("High", 30, Priority.HIGH)],
            constraints(),
        )
        assert titles(plan) == ["High", "Low"]

    def test_shorter_task_wins_a_priority_tie(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("Long", 60, Priority.HIGH), CareTask("Short", 10, Priority.HIGH)],
            constraints(),
        )
        assert titles(plan) == ["Short", "Long"]

    def test_owner_preference_breaks_a_tie_before_duration(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Walk", 30, Priority.HIGH, TaskCategory.WALK),
                CareTask("Meds", 30, Priority.HIGH, TaskCategory.MEDS),
            ],
            constraints(),
            preferred_order=[TaskCategory.MEDS, TaskCategory.WALK],
        )
        assert titles(plan) == ["Meds", "Walk"]

    def test_plan_is_identical_across_repeated_runs(self, scheduler):
        """Streamlit reruns the script constantly; a flickering plan is a bug."""
        tasks = [CareTask(name, 20, Priority.HIGH) for name in ("A", "B", "C")]
        results = {
            tuple(titles(scheduler.build_plan(tasks, constraints()))) for _ in range(50)
        }
        assert len(results) == 1


class TestFixedTimePlacement:
    def test_morning_is_not_stranded_by_an_afternoon_anchor(self, scheduler):
        """The failure the old single cursor model produced."""
        plan = scheduler.build_plan(
            [
                CareTask("Meds", 5, fixed_time=time(12, 0)),
                CareTask("Walk", 30, Priority.HIGH),
            ],
            constraints(),
        )
        walk = next(e for e in plan.entries if e.task.title == "Walk")
        assert walk.start.hour == 8

    def test_fixed_task_lands_on_its_own_time(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("Meds", 5, fixed_time=time(12, 0))], constraints()
        )
        assert plan.entries[0].start.hour == 12

    def test_two_fixed_tasks_at_the_same_time_conflict(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Meds A", 30, fixed_time=time(9, 0)),
                CareTask("Meds B", 30, fixed_time=time(9, 15)),
            ],
            constraints(),
        )
        assert titles(plan) == ["Meds A"]
        assert reasons(plan)["Meds B"] is SkipReason.TIME_CONFLICT

    def test_back_to_back_fixed_tasks_do_not_conflict(self, scheduler):
        """Intervals are half open: ending at 09:30 and starting at 09:30 is fine."""
        plan = scheduler.build_plan(
            [
                CareTask("A", 30, fixed_time=time(9, 0)),
                CareTask("B", 30, fixed_time=time(9, 30)),
            ],
            constraints(),
        )
        assert titles(plan) == ["A", "B"]
        assert plan.deferred == []

    def test_fixed_task_past_the_window_is_deferred(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("Evening meds", 30, fixed_time=time(19, 0))],
            constraints(available_minutes=600),  # 08:00 to 18:00
        )
        assert reasons(plan)["Evening meds"] is SkipReason.OUTSIDE_WINDOW

    def test_plan_running_past_midnight(self, scheduler):
        night = constraints(day_start=time(22, 0), available_minutes=240)
        plan = scheduler.build_plan(
            [CareTask("Late meds", 15, fixed_time=time(1, 0))], night
        )
        assert plan.entries[0].start.day == SATURDAY.day


class TestSpacingAndRepeats:
    def test_walk_waits_the_required_gap_after_a_meal(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Breakfast", 10, Priority.HIGH, TaskCategory.FEEDING),
                CareTask("Walk", 30, Priority.MEDIUM, TaskCategory.WALK),
            ],
            constraints(min_gap_after_meal=60),
        )
        breakfast, walk = plan.entries
        assert (walk.start - breakfast.end).total_seconds() / 60 == 60

    def test_no_gap_is_applied_between_unrelated_categories(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Brush", 10, Priority.HIGH, TaskCategory.GROOMING),
                CareTask("Walk", 30, Priority.MEDIUM, TaskCategory.WALK),
            ],
            constraints(min_gap_after_meal=60),
        )
        brush, walk = plan.entries
        assert walk.start == brush.end

    def test_meal_gap_survives_an_unrelated_task_in_between(self, scheduler):
        """A short task slotted after breakfast must not reset the gap."""
        plan = scheduler.build_plan(
            [
                CareTask("Breakfast", 10, Priority.HIGH, TaskCategory.FEEDING),
                CareTask("Litter box", 10, Priority.MEDIUM, TaskCategory.OTHER),
                CareTask("Walk", 30, Priority.LOW, TaskCategory.WALK),
            ],
            constraints(min_gap_after_meal=60),
        )
        breakfast = next(e for e in plan.entries if e.task.title == "Breakfast")
        walk = next(e for e in plan.entries if e.task.title == "Walk")
        assert (walk.start - breakfast.end).total_seconds() / 60 >= 60

    def test_twice_daily_task_produces_two_slots(self, scheduler):
        task = CareTask(
            "Feeding",
            10,
            recurrence=Recurrence(Frequency.DAILY, times_per_day=2),
        )
        plan = scheduler.build_plan([task], constraints())
        assert len(plan.entries) == 2

    def test_times_per_day_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="times_per_day"):
            CareTask("Feeding", 10, recurrence=Recurrence(times_per_day=0))


# ---------------------------------------------------------------------------
# Plan output
# ---------------------------------------------------------------------------


class TestPlanOutput:
    def test_minutes_used_is_derived_from_the_entries(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("A", 20), CareTask("B", 35)], constraints()
        )
        assert plan.minutes_used == 55
        assert plan.minutes_free == plan.minutes_available - 55

    def test_explain_reports_both_the_plan_and_the_cuts(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Walk", 30, Priority.HIGH),
                CareTask("Nail trim", 45, recurrence=Recurrence(Frequency.WEEKLY)),
            ],
            constraints(),
        )
        text = plan.explain()
        assert "Walk" in text
        assert "Nail trim" in text
        assert SkipReason.NOT_DUE_TODAY.value in text

    def test_explain_reports_the_budget(self, scheduler):
        plan = scheduler.build_plan([CareTask("Walk", 30)], constraints())
        assert "600 min available" in plan.explain()

    def test_rows_carry_the_fields_the_ui_needs(self, scheduler):
        plan = scheduler.build_plan([CareTask("Walk", 30)], constraints())
        row = plan.to_rows()[0]
        assert set(row) >= {"start", "end", "task", "minutes", "priority", "why"}

    def test_deferred_rows_are_available_for_display(self, scheduler):
        plan = scheduler.build_plan(
            [CareTask("Huge", 999)], constraints(available_minutes=60)
        )
        assert plan.deferred_rows()[0]["reason"] == SkipReason.EXCEEDS_DAY_BUDGET.value
