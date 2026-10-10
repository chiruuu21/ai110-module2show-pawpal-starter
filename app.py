"""PawPal+ Streamlit UI.

All scheduling logic lives in pawpal_system.py. This file only collects input,
calls Scheduler.build_plan, and renders the result.
"""

from datetime import date, time

import streamlit as st

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

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="wide")

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def seed_owner() -> Owner:
    """A small starting household so the app shows something useful at once."""
    owner = Owner(name="Jordan", preferred_order=[TaskCategory.MEDS, TaskCategory.FEEDING])

    mochi = Pet(name="Mochi", species="dog", age=4)
    mochi.add_task(
        CareTask("Breakfast", 10, Priority.HIGH, TaskCategory.FEEDING, fixed_time=time(7, 0))
    )
    mochi.add_task(
        CareTask("Morning walk", 30, Priority.HIGH, TaskCategory.WALK, fixed_time=time(8, 30))
    )
    mochi.add_task(CareTask("Evening walk", 30, Priority.MEDIUM, TaskCategory.WALK))

    biscuit = Pet(name="Biscuit", species="cat", age=7)
    biscuit.add_task(
        CareTask("Thyroid meds", 5, Priority.HIGH, TaskCategory.MEDS, fixed_time=time(12, 0))
    )
    biscuit.add_task(CareTask("Litter box", 10, Priority.MEDIUM, TaskCategory.OTHER))

    owner.add_pet(mochi)
    owner.add_pet(biscuit)
    return owner


if "owner" not in st.session_state:
    st.session_state.owner = seed_owner()

owner: Owner = st.session_state.owner


def find_task(task_id: str) -> tuple[Pet, CareTask] | tuple[None, None]:
    for pet in owner.pets:
        for task in pet.tasks:
            if task.id == task_id:
                return pet, task
    return None, None


# ---------------------------------------------------------------------------
# Sidebar: owner, pets, and the day's constraints
# ---------------------------------------------------------------------------

st.sidebar.header("Owner")
owner.name = st.sidebar.text_input("Owner name", value=owner.name)

st.sidebar.header("Pets")
for pet in owner.pets:
    st.sidebar.write(f"**{pet.name}** ({pet.species}, {pet.age}) — {len(pet.tasks)} tasks")

with st.sidebar.form("add_pet", clear_on_submit=True):
    st.caption("Add a pet")
    new_name = st.text_input("Name")
    new_species = st.selectbox("Species", ["dog", "cat", "other"])
    new_age = st.number_input("Age", min_value=0, max_value=40, value=1)
    if st.form_submit_button("Add pet") and new_name.strip():
        owner.add_pet(Pet(name=new_name.strip(), species=new_species, age=int(new_age)))
        st.rerun()

st.sidebar.header("Today's constraints")
plan_day = st.sidebar.date_input("Day", value=date.today())
day_start = st.sidebar.time_input("Start of day", value=time(7, 0))
hours_available = st.sidebar.slider("Hours available", 0.0, 16.0, 12.0, step=0.5)
meal_gap = st.sidebar.slider("Minutes between a meal and a walk", 0, 120, 60, step=15)

st.sidebar.header("Preferences")
preferred_order = st.sidebar.multiselect(
    "Category priority order",
    options=list(TaskCategory),
    default=owner.preferred_order,
    format_func=lambda category: category.value,
    help="Breaks ties between tasks of equal priority.",
)
owner.preferred_order = preferred_order


# ---------------------------------------------------------------------------
# Main: tasks
# ---------------------------------------------------------------------------

st.title("🐾 PawPal+")
st.caption("A daily care plan that explains itself.")

tasks_tab, plan_tab = st.tabs(["Tasks", "Daily plan"])

with tasks_tab:
    if not owner.pets:
        st.info("Add a pet in the sidebar to get started.")
    else:
        left, right = st.columns([3, 2])

        with left:
            st.subheader("Current tasks")
            for pet in owner.pets:
                st.markdown(f"**{pet.name}**")
                if not pet.tasks:
                    st.caption("No tasks yet.")
                for task in list(pet.tasks):
                    cols = st.columns([4, 2, 2, 1, 1])
                    when = f"{task.fixed_time:%H:%M}" if task.fixed_time else "flexible"
                    cols[0].write(task.title)
                    cols[1].caption(f"{task.duration_minutes} min · {when}")
                    cols[2].caption(
                        f"{task.priority.name.lower()} · {task.category.value}"
                    )
                    done = task.is_done_on(plan_day)
                    if cols[3].button(
                        "✓" if not done else "↺", key=f"done_{task.id}",
                        help="Mark done today" if not done else "Undo",
                    ):
                        if done:
                            task.completed_on.discard(plan_day)
                        else:
                            task.mark_complete(plan_day)
                        st.rerun()
                    if cols[4].button("✕", key=f"del_{task.id}", help="Delete"):
                        pet.remove_task(task.id)
                        st.rerun()

        with right:
            st.subheader("Add or edit a task")
            all_tasks = [(pet, task) for pet in owner.pets for task in pet.tasks]
            options = ["(new task)"] + [f"{p.name}: {t.title}" for p, t in all_tasks]
            choice = st.selectbox("Task", options, key="task_choice")

            editing = choice != "(new task)"
            target_pet, target = (
                all_tasks[options.index(choice) - 1] if editing else (None, None)
            )

            with st.form("task_form"):
                pet_names = [pet.name for pet in owner.pets]
                pet_index = (
                    pet_names.index(target_pet.name) if editing else 0
                )
                pet_name = st.selectbox("Pet", pet_names, index=pet_index)

                title = st.text_input("Title", value=target.title if editing else "")
                duration = st.number_input(
                    "Duration (minutes)",
                    min_value=1,
                    max_value=480,
                    value=target.duration_minutes if editing else 20,
                )
                priority = st.selectbox(
                    "Priority",
                    list(Priority),
                    index=list(Priority).index(target.priority) if editing else 1,
                    format_func=lambda p: p.name.lower(),
                )
                category = st.selectbox(
                    "Category",
                    list(TaskCategory),
                    index=list(TaskCategory).index(target.category) if editing else 5,
                    format_func=lambda c: c.value,
                )

                has_fixed = st.checkbox(
                    "Must happen at a fixed time",
                    value=bool(target.fixed_time) if editing else False,
                )
                fixed_at = st.time_input(
                    "Fixed time",
                    value=target.fixed_time if editing and target.fixed_time else time(8, 0),
                )

                frequency = st.selectbox(
                    "Repeats",
                    list(Frequency),
                    index=list(Frequency).index(target.recurrence.frequency)
                    if editing
                    else 1,
                    format_func=lambda f: f.value,
                )
                weekday_names = st.multiselect(
                    "On which days (weekly only)",
                    WEEKDAYS,
                    default=[WEEKDAYS[d] for d in target.recurrence.weekdays]
                    if editing
                    else [],
                )
                due_date = st.date_input(
                    "Date (one off only)",
                    value=target.recurrence.due_date
                    if editing and target.recurrence.due_date
                    else plan_day,
                )
                times_per_day = st.number_input(
                    "Times per day",
                    min_value=1,
                    max_value=6,
                    value=target.recurrence.times_per_day if editing else 1,
                )

                submitted = st.form_submit_button("Save task" if editing else "Add task")

            if submitted:
                recurrence = Recurrence(
                    frequency=frequency,
                    weekdays=[WEEKDAYS.index(name) for name in weekday_names],
                    due_date=due_date if frequency is Frequency.ONCE else None,
                    times_per_day=int(times_per_day),
                )
                try:
                    task = CareTask(
                        title=title,
                        duration_minutes=int(duration),
                        priority=priority,
                        category=category,
                        fixed_time=fixed_at if has_fixed else None,
                        recurrence=recurrence,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    destination = next(p for p in owner.pets if p.name == pet_name)
                    if editing:
                        task.completed_on = set(target.completed_on)
                        target_pet.remove_task(target.id)
                    destination.add_task(task)
                    st.success(f"{'Updated' if editing else 'Added'} {task.title}.")
                    st.rerun()


# ---------------------------------------------------------------------------
# Main: the plan
# ---------------------------------------------------------------------------

with plan_tab:
    constraints = DayConstraints(
        day=plan_day,
        day_start=day_start,
        available_minutes=int(hours_available * 60),
        min_gap_after_meal=int(meal_gap),
    )
    plan = Scheduler().build_plan(owner.all_tasks(), constraints, owner.preferred_order)

    st.subheader(f"Plan for {plan.day:%A %d %B %Y}")
    window = (
        f"{constraints.start_datetime():%H:%M} to {constraints.end_datetime():%H:%M}"
    )
    used, available, free = st.columns(3)
    used.metric("Minutes used", plan.minutes_used)
    available.metric("Minutes available", plan.minutes_available)
    free.metric("Minutes free", plan.minutes_free)
    st.caption(f"Window {window}")

    rows = plan.to_rows()
    for row, entry in zip(rows, plan.entries):
        row["pet"] = owner.pet_name_for(entry.task) or "—"

    if rows:
        st.dataframe(
            rows,
            hide_index=True,
            column_order=["start", "end", "task", "pet", "minutes", "priority", "why"],
        )
    else:
        st.warning("Nothing could be scheduled with these constraints.")

    if plan.deferred:
        st.subheader("Left out")
        deferred = plan.deferred_rows()
        for row, item in zip(deferred, plan.deferred):
            row["pet"] = owner.pet_name_for(item.task) or "—"
        st.dataframe(
            deferred,
            hide_index=True,
            column_order=["task", "pet", "minutes", "reason", "detail"],
        )

    with st.expander("Why this plan?", expanded=True):
        st.text(plan.explain())
