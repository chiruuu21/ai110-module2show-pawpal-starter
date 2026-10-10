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


# ---------------------------------------------------------------------------
# Smarter scheduling: sorting, filtering, recurrence, conflicts
# ---------------------------------------------------------------------------


class TestSortByTime:
    def test_fixed_tasks_come_out_in_clock_order(self, scheduler):
        tasks = [
            CareTask("Dinner", 10, fixed_time=time(18, 0)),
            CareTask("Breakfast", 10, fixed_time=time(7, 0)),
            CareTask("Lunch", 10, fixed_time=time(12, 0)),
        ]
        ordered = [task.title for task in scheduler.sort_by_time(tasks)]
        assert ordered == ["Breakfast", "Lunch", "Dinner"]

    def test_flexible_tasks_go_last_in_original_order(self, scheduler):
        tasks = [
            CareTask("Brush", 10),
            CareTask("Meds", 5, fixed_time=time(9, 0)),
            CareTask("Play", 10),
        ]
        ordered = [task.title for task in scheduler.sort_by_time(tasks)]
        assert ordered == ["Meds", "Brush", "Play"]


class TestFilterTasks:
    @pytest.fixture
    def owner(self):
        owner = Owner(name="Jordan")
        mochi, biscuit = Pet("Mochi", "dog"), Pet("Biscuit", "cat")
        mochi.add_task(CareTask("Walk", 30))
        biscuit.add_task(CareTask("Litter", 10))
        biscuit.add_task(CareTask("Brush", 10))
        owner.add_pet(mochi)
        owner.add_pet(biscuit)
        return owner

    def test_filter_by_pet_name_ignores_case(self, owner):
        assert [t.title for t in owner.filter_tasks(pet_name="biscuit")] == [
            "Litter",
            "Brush",
        ]

    def test_filter_by_status_is_judged_per_day(self, owner):
        owner.filter_tasks(pet_name="Mochi")[0].mark_complete(FRIDAY)
        assert [t.title for t in owner.filter_tasks(completed=True, day=FRIDAY)] == ["Walk"]
        assert owner.filter_tasks(completed=True, day=SATURDAY) == []

    def test_filters_combine(self, owner):
        owner.filter_tasks(pet_name="Biscuit")[0].mark_complete(FRIDAY)
        pending = owner.filter_tasks(pet_name="Biscuit", completed=False, day=FRIDAY)
        assert [t.title for t in pending] == ["Brush"]

    def test_unknown_pet_returns_nothing(self, owner):
        assert owner.filter_tasks(pet_name="Rex") == []


class TestNextOccurrence:
    def test_completing_a_daily_task_returns_tomorrow(self):
        task = CareTask("Walk", 30)
        assert task.mark_complete(FRIDAY) == SATURDAY
        assert task.is_due_on(SATURDAY)

    def test_weekly_steps_to_the_next_listed_weekday(self):
        rule = Recurrence(Frequency.WEEKLY, weekdays=[0, 4])  # Mon, Fri
        assert rule.next_occurrence(FRIDAY) == MONDAY

    def test_weekly_on_one_day_is_a_full_week_later(self):
        rule = Recurrence(Frequency.WEEKLY, weekdays=[FRIDAY.weekday()])
        assert rule.next_occurrence(FRIDAY) == date(2026, 10, 16)

    def test_once_has_no_next_occurrence(self):
        task = CareTask("Vet", 60, recurrence=Recurrence(Frequency.ONCE, due_date=FRIDAY))
        assert task.mark_complete(FRIDAY) is None

    def test_weekly_without_weekdays_has_no_next_occurrence(self):
        assert Recurrence(Frequency.WEEKLY).next_occurrence(FRIDAY) is None


class TestDetectConflicts:
    def test_overlap_across_pets_is_reported_with_names(self, scheduler):
        owner = Owner(name="Jordan")
        mochi, biscuit = Pet("Mochi", "dog"), Pet("Biscuit", "cat")
        mochi.add_task(CareTask("Walk", 30, fixed_time=time(8, 30)))
        biscuit.add_task(CareTask("Vet call", 20, fixed_time=time(8, 45)))
        owner.add_pet(mochi)
        owner.add_pet(biscuit)
        warnings = scheduler.detect_conflicts(owner.all_tasks(), FRIDAY, owner.pet_name_for)
        assert len(warnings) == 1
        assert "Walk (Mochi)" in warnings[0] and "Vet call (Biscuit)" in warnings[0]

    def test_back_to_back_is_not_a_conflict(self, scheduler):
        tasks = [
            CareTask("A", 30, fixed_time=time(9, 0)),
            CareTask("B", 30, fixed_time=time(9, 30)),
        ]
        assert scheduler.detect_conflicts(tasks, FRIDAY) == []

    def test_one_long_task_overlapping_two_reports_both(self, scheduler):
        tasks = [
            CareTask("Long", 120, fixed_time=time(9, 0)),
            CareTask("B", 10, fixed_time=time(9, 30)),
            CareTask("C", 10, fixed_time=time(10, 30)),
        ]
        assert len(scheduler.detect_conflicts(tasks, FRIDAY)) == 2

    def test_tasks_not_due_that_day_are_ignored(self, scheduler):
        tasks = [
            CareTask("A", 30, fixed_time=time(9, 0)),
            CareTask(
                "B", 30, fixed_time=time(9, 0),
                recurrence=Recurrence(Frequency.WEEKLY, weekdays=[MONDAY.weekday()]),
            ),
        ]
        assert scheduler.detect_conflicts(tasks, FRIDAY) == []

    def test_fixed_task_repeated_per_day_is_flagged(self, scheduler):
        task = CareTask(
            "Feed", 10, fixed_time=time(8, 0),
            recurrence=Recurrence(times_per_day=2),
        )
        assert "repeats 2 times" in scheduler.detect_conflicts([task], FRIDAY)[0]

    def test_plan_deferral_names_the_blocking_task(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Meds A", 30, fixed_time=time(9, 0)),
                CareTask("Meds B", 30, fixed_time=time(9, 15)),
            ],
            constraints(),
        )
        assert "Meds A" in plan.deferred[0].detail


class TestRecurringRollover:
    @pytest.fixture
    def pet(self):
        return Pet("Mochi", "dog")

    def test_completing_a_daily_task_creates_tomorrows_instance(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        follower = pet.complete_task(walk.id, FRIDAY)
        assert follower is not None and follower.id != walk.id
        assert follower.starts_on == SATURDAY
        assert follower.follows_id == walk.id
        assert follower.pet_id == pet.id
        assert follower.completed_on == set()

    def test_each_day_sees_exactly_one_live_instance(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        follower = pet.complete_task(walk.id, FRIDAY)
        assert pet.active_tasks(FRIDAY) == [walk]
        assert pet.active_tasks(SATURDAY) == [follower]

    def test_weekly_instance_waits_for_its_weekday(self, pet):
        trim = CareTask(
            "Trim", 20, recurrence=Recurrence(Frequency.WEEKLY, weekdays=[FRIDAY.weekday()])
        )
        pet.add_task(trim)
        follower = pet.complete_task(trim.id, FRIDAY)
        assert not follower.is_due_on(SATURDAY)
        assert follower.is_due_on(date(2026, 10, 16))

    def test_once_task_does_not_roll_over(self, pet):
        vet = CareTask("Vet", 60, recurrence=Recurrence(Frequency.ONCE, due_date=FRIDAY))
        pet.add_task(vet)
        assert pet.complete_task(vet.id, FRIDAY) is None
        assert len(pet.tasks) == 1 and vet.is_done_on(FRIDAY)

    def test_completing_twice_does_not_spawn_two_copies(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        pet.complete_task(walk.id, FRIDAY)
        assert pet.complete_task(walk.id, FRIDAY) is None
        assert len(pet.tasks) == 2

    def test_reopen_removes_the_rollover(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        pet.complete_task(walk.id, FRIDAY)
        assert pet.reopen_task(walk.id, FRIDAY)
        assert pet.tasks == [walk]
        assert walk.ends_on is None and not walk.is_done_on(FRIDAY)

    def test_reopen_keeps_a_follower_that_has_its_own_history(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        saturday = pet.complete_task(walk.id, FRIDAY)
        pet.complete_task(saturday.id, SATURDAY)
        pet.reopen_task(walk.id, FRIDAY)
        assert saturday in pet.tasks and walk.ends_on == FRIDAY

    def test_plan_tomorrow_schedules_the_new_instance_once(self, scheduler, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        pet.complete_task(walk.id, FRIDAY)
        plan = scheduler.build_plan(pet.tasks, constraints(day=SATURDAY))
        assert titles(plan) == ["Walk"]
        assert plan.deferred == []

    def test_plan_today_reports_the_completed_instance_only(self, scheduler, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        pet.complete_task(walk.id, FRIDAY)
        plan = scheduler.build_plan(pet.tasks, constraints())
        assert plan.entries == []
        assert reasons(plan) == {"Walk": SkipReason.ALREADY_COMPLETED}


class TestConflictWarnings:
    @pytest.fixture
    def owner(self):
        owner = Owner(name="Jordan")
        owner.add_pet(Pet("Mochi", "dog"))
        owner.add_pet(Pet("Biscuit", "cat"))
        return owner

    def test_same_time_same_pet_is_labelled(self, scheduler, owner):
        mochi = owner.pets[0]
        mochi.add_task(CareTask("Breakfast", 10, fixed_time=time(7, 0)))
        mochi.add_task(CareTask("Ear drops", 5, fixed_time=time(7, 0)))
        [warning] = scheduler.detect_conflicts(owner.all_tasks(), FRIDAY, owner.pet_name_for)
        assert warning.startswith("Same time, same pet")
        assert "both start at 07:00" in warning

    def test_same_time_different_pets_is_labelled(self, scheduler, owner):
        owner.pets[0].add_task(CareTask("Walk", 30, fixed_time=time(17, 30)))
        owner.pets[1].add_task(CareTask("Dinner", 10, fixed_time=time(17, 30)))
        [warning] = scheduler.detect_conflicts(owner.all_tasks(), FRIDAY, owner.pet_name_for)
        assert warning.startswith("Same time, different pets")

    def test_build_plan_records_warnings_and_still_plans(self, scheduler, owner):
        owner.pets[0].add_task(CareTask("Walk", 30, fixed_time=time(9, 0)))
        owner.pets[1].add_task(CareTask("Dinner", 10, fixed_time=time(9, 0)))
        plan = scheduler.build_plan(
            owner.all_tasks(), constraints(), name_for=owner.pet_name_for
        )
        assert len(plan.warnings) == 1
        assert len(plan.entries) == 1
        assert "Warnings:" in plan.explain()

    def test_no_conflicts_means_no_warnings_section(self, scheduler):
        plan = scheduler.build_plan([CareTask("Walk", 30)], constraints())
        assert plan.warnings == []
        assert "Warnings:" not in plan.explain()

    def test_completed_task_no_longer_conflicts(self, scheduler, owner):
        mochi = owner.pets[0]
        meds = CareTask("Meds", 5, fixed_time=time(7, 0))
        mochi.add_task(CareTask("Breakfast", 10, fixed_time=time(7, 0)))
        mochi.add_task(meds)
        mochi.complete_task(meds.id, FRIDAY)
        assert scheduler.detect_conflicts(owner.all_tasks(), FRIDAY) == []


# ---------------------------------------------------------------------------
# Phase 4: sorting correctness, recurrence logic, conflict detection
# ---------------------------------------------------------------------------


class TestSortingCorrectness:
    def test_empty_list_sorts_to_empty(self, scheduler):
        assert scheduler.sort_by_time([]) == []

    def test_sort_returns_a_new_list_and_leaves_input_alone(self, scheduler):
        tasks = [
            CareTask("Dinner", 10, fixed_time=time(18, 0)),
            CareTask("Breakfast", 10, fixed_time=time(7, 0)),
        ]
        original = list(tasks)
        scheduler.sort_by_time(tasks)
        assert tasks == original

    def test_equal_times_keep_insertion_order(self, scheduler):
        tasks = [
            CareTask("Meds", 5, fixed_time=time(9, 0)),
            CareTask("Breakfast", 10, fixed_time=time(9, 0)),
            CareTask("Early walk", 20, fixed_time=time(7, 0)),
        ]
        ordered = [task.title for task in scheduler.sort_by_time(tasks)]
        assert ordered == ["Early walk", "Meds", "Breakfast"]

    def test_plan_entries_are_chronological_and_never_overlap(self, scheduler):
        tasks = [
            CareTask("Dinner", 15, fixed_time=time(17, 0)),
            CareTask("Brush", 20, priority=Priority.LOW),
            CareTask("Meds", 5, fixed_time=time(9, 0)),
            CareTask("Walk", 45, priority=Priority.HIGH),
            CareTask("Lunch", 10, fixed_time=time(12, 0)),
        ]
        plan = scheduler.build_plan(tasks, constraints())
        starts = [entry.start for entry in plan.entries]
        assert starts == sorted(starts)
        for earlier, later in zip(plan.entries, plan.entries[1:]):
            assert earlier.end <= later.start
        assert len(plan.entries) == 5

    def test_overnight_window_puts_after_midnight_task_last(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Late meds", 10, fixed_time=time(1, 0)),
                CareTask("Night walk", 20, fixed_time=time(23, 0)),
            ],
            constraints(day_start=time(22, 0), available_minutes=240),
        )
        assert titles(plan) == ["Night walk", "Late meds"]
        assert plan.entries[1].start.date() == SATURDAY


class TestRecurrenceLogic:
    @pytest.fixture
    def pet(self):
        return Pet("Mochi", "dog")

    def test_follower_copies_the_task_details(self, pet):
        walk = CareTask(
            "Walk", 30,
            priority=Priority.HIGH,
            category=TaskCategory.WALK,
            fixed_time=time(7, 30),
        )
        pet.add_task(walk)
        follower = pet.complete_task(walk.id, FRIDAY)
        assert (follower.title, follower.duration_minutes) == ("Walk", 30)
        assert follower.priority is Priority.HIGH
        assert follower.category is TaskCategory.WALK
        assert follower.fixed_time == time(7, 30)
        assert follower.recurrence == walk.recurrence

    def test_follower_is_due_tomorrow_but_not_today(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        follower = pet.complete_task(walk.id, FRIDAY)
        assert not follower.is_due_on(FRIDAY)
        assert follower.is_due_on(SATURDAY)
        assert not walk.is_due_on(SATURDAY)

    def test_completing_on_consecutive_days_builds_a_chain(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        saturday = pet.complete_task(walk.id, FRIDAY)
        sunday = pet.complete_task(saturday.id, SATURDAY)
        assert sunday.starts_on == date(2026, 10, 11)
        assert sunday.follows_id == saturday.id
        assert pet.active_tasks(date(2026, 10, 11)) == [sunday]
        assert len(pet.tasks) == 3

    def test_follower_cannot_be_completed_before_it_starts(self, pet):
        walk = CareTask("Walk", 30)
        pet.add_task(walk)
        follower = pet.complete_task(walk.id, FRIDAY)
        assert pet.complete_task(follower.id, FRIDAY) is None
        assert len(pet.tasks) == 2


class TestDuplicateTimeConflicts:
    def test_three_tasks_at_one_time_report_every_pair(self, scheduler):
        tasks = [
            CareTask("Meds", 5, fixed_time=time(9, 0)),
            CareTask("Breakfast", 10, fixed_time=time(9, 0)),
            CareTask("Walk", 30, fixed_time=time(9, 0)),
        ]
        warnings = scheduler.detect_conflicts(tasks, FRIDAY)
        assert len(warnings) == 3
        assert all(warning.startswith("Same time") for warning in warnings)

    def test_duplicate_start_is_same_time_even_with_different_lengths(self, scheduler):
        tasks = [
            CareTask("Meds", 5, fixed_time=time(9, 0)),
            CareTask("Walk", 60, fixed_time=time(9, 0)),
        ]
        [warning] = scheduler.detect_conflicts(tasks, FRIDAY)
        assert "both start at 09:00" in warning

    def test_partial_overlap_is_labelled_overlap_not_same_time(self, scheduler):
        tasks = [
            CareTask("Walk", 30, fixed_time=time(9, 0)),
            CareTask("Meds", 5, fixed_time=time(9, 10)),
        ]
        [warning] = scheduler.detect_conflicts(tasks, FRIDAY)
        assert warning.startswith("Overlap, same pet")
        assert "09:00-09:30" in warning

    def test_flexible_tasks_never_conflict(self, scheduler):
        tasks = [CareTask("Brush", 10), CareTask("Play", 10)]
        assert scheduler.detect_conflicts(tasks, FRIDAY) == []

    def test_build_plan_keeps_one_duplicate_and_defers_the_other(self, scheduler):
        plan = scheduler.build_plan(
            [
                CareTask("Breakfast", 10, priority=Priority.LOW, fixed_time=time(9, 0)),
                CareTask("Meds", 5, priority=Priority.HIGH, fixed_time=time(9, 0)),
            ],
            constraints(),
        )
        assert titles(plan) == ["Meds"]
        assert reasons(plan) == {"Breakfast": SkipReason.TIME_CONFLICT}
        assert len(plan.warnings) == 1
