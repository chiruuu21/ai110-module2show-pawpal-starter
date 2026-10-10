"""Terminal testing ground for PawPal+.

Builds a small household, schedules today, and prints the result. Running this
is the quickest way to check the engine end to end without Streamlit:

    python main.py
"""

from datetime import date, time

from pawpal_system import (
    CareTask,
    DayConstraints,
    Frequency,
    Owner,
    Pet,
    Priority,
    Recurrence,
    Scheduler,
    TaskCategory,
)

LINE = "=" * 72
THIN = "-" * 72


def build_household(today: date) -> Owner:
    """One owner, two pets, and a spread of tasks at different times."""
    owner = Owner(
        name="Jordan",
        preferred_order=[TaskCategory.MEDS, TaskCategory.FEEDING, TaskCategory.WALK],
    )

    mochi = Pet(name="Mochi", species="dog", age=4)
    mochi.add_task(
        CareTask("Breakfast", 10, Priority.HIGH, TaskCategory.FEEDING,
                 fixed_time=time(7, 0))
    )
    mochi.add_task(
        CareTask("Morning walk", 30, Priority.HIGH, TaskCategory.WALK,
                 fixed_time=time(8, 30))
    )
    mochi.add_task(
        CareTask("Evening walk", 30, Priority.MEDIUM, TaskCategory.WALK,
                 fixed_time=time(17, 30))
    )
    # Deliberately set to a different weekday so the deferral is always visible.
    mochi.add_task(
        CareTask("Nail trim", 45, Priority.LOW, TaskCategory.GROOMING,
                 recurrence=Recurrence(Frequency.WEEKLY,
                                       weekdays=[(today.weekday() + 2) % 7]))
    )

    biscuit = Pet(name="Biscuit", species="cat", age=7)
    biscuit.add_task(
        CareTask("Thyroid meds", 5, Priority.HIGH, TaskCategory.MEDS,
                 fixed_time=time(12, 0))
    )
    biscuit.add_task(
        CareTask("Litter box", 10, Priority.MEDIUM, TaskCategory.OTHER)
    )
    biscuit.add_task(
        CareTask("Play session", 15, Priority.LOW, TaskCategory.ENRICHMENT,
                 recurrence=Recurrence(Frequency.DAILY, times_per_day=2))
    )

    owner.add_pet(mochi)
    owner.add_pet(biscuit)
    return owner


def print_schedule(owner: Owner, plan) -> None:
    """The schedule table, formatted from plan.to_rows()."""
    print(f"TODAY'S SCHEDULE  {plan.day:%A %d %B %Y}")
    print(THIN)
    if not plan.entries:
        print("  Nothing could be scheduled today.")
        return

    print(f"  {'TIME':<15}{'TASK':<22}{'PET':<10}{'MINS':>5}  PRIORITY")
    for entry, row in zip(plan.entries, plan.to_rows()):
        pet = owner.pet_name_for(entry.task) or "-"
        slot = f"{row['start']} - {row['end']}"
        print(
            f"  {slot:<15}{row['task']:<22}{pet:<10}"
            f"{row['minutes']:>5}  {row['priority']}"
        )
    print(THIN)
    print(
        f"  Used {plan.minutes_used} of {plan.minutes_available} minutes, "
        f"{plan.minutes_free} free."
    )


def print_left_out(owner: Owner, plan) -> None:
    """The cut tasks, formatted from plan.deferred_rows()."""
    if not plan.deferred:
        return
    print()
    print("LEFT OUT")
    print(THIN)
    for item, row in zip(plan.deferred, plan.deferred_rows()):
        pet = owner.pet_name_for(item.task) or "-"
        detail = f" ({row['detail']})" if row["detail"] else ""
        print(f"  {row['task']:<22}{pet:<10}{row['reason']}{detail}")


def main() -> None:
    today = date.today()
    owner = build_household(today)

    constraints = DayConstraints(
        day=today,
        day_start=time(7, 0),
        available_minutes=12 * 60,
        min_gap_after_meal=60,
    )

    plan = Scheduler().build_plan(
        owner.all_tasks(), constraints, owner.preferred_order
    )

    print(LINE)
    print(f" PawPal+ : daily plan for {owner.name}")
    print(LINE)
    pets = ", ".join(f"{pet.name} ({pet.species}, {pet.age})" for pet in owner.pets)
    print(f"Pets:   {pets}")
    print(
        f"Window: {constraints.start_datetime():%H:%M} to "
        f"{constraints.end_datetime():%H:%M}  "
        f"({constraints.available_minutes} minutes available)"
    )
    print(f"Tasks:  {len(owner.all_tasks())} across {len(owner.pets)} pets")
    print()

    print_schedule(owner, plan)
    print_left_out(owner, plan)

    print()
    print("REASONING  (straight from plan.explain())")
    print(THIN)
    print(plan.explain())
    print(LINE)


if __name__ == "__main__":
    main()
