# PawPal+ (Module 2 Project)

You are building **PawPal+**, a Streamlit app that helps a pet owner plan care tasks for their pet.

## Scenario

A busy pet owner needs help staying consistent with pet care. They want an assistant that can:

- Track pet care tasks (walks, feeding, meds, enrichment, grooming, etc.)
- Consider constraints (time available, priority, owner preferences)
- Produce a daily plan and explain why it chose that plan

Your job is to design the system first (UML), then implement the logic in Python, then connect it to the Streamlit UI.

## What you will build

Your final app should:

- Let a user enter basic owner + pet info
- Let a user add/edit tasks (duration + priority at minimum)
- Generate a daily schedule/plan based on constraints and priorities
- Display the plan clearly (and ideally explain the reasoning)
- Include tests for the most important scheduling behaviors

## Getting started

### Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Suggested workflow

1. Read the scenario carefully and identify requirements and edge cases.
2. Draft a UML diagram (classes, attributes, methods, relationships).
3. Convert UML into Python class stubs (no logic yet).
4. Implement scheduling logic in small increments.
5. Add tests to verify key behaviors.
6. Connect your logic to the Streamlit UI in `app.py`.
7. Refine UML so it matches what you actually built.

## 🖥️ Sample Output

Paste a sample of your app's CLI or Streamlit output here so a reader can see what a generated plan looks like:

```
# e.g.:
# Daily plan for Biscuit (Golden Retriever):
#   08:00 — Morning walk (30 min) [priority: high]
#   09:00 — Feeding (10 min) [priority: high]
#   ...
```

## 🧪 Testing PawPal+

```bash
# Run the full test suite:
pytest

# Run with coverage:
pytest --cov
```

Sample test output:

```
# Paste your pytest output here
```

## 📐 Smarter Scheduling

PawPal+ goes beyond a plain to-do list with four small algorithms. All of them live in `pawpal_system.py`, and `python main.py` shows each one in the terminal.

| Feature | Method(s) | One-line summary |
|---------|-----------|------------------|
| Sorting | `Scheduler.sort_by_time()`, `Scheduler._sort()` | Clock-order view for the owner, and priority order for placement |
| Filtering | `Owner.filter_tasks()`, `Pet.active_tasks()` | Filter by pet name and/or completion status on a given day |
| Conflict detection | `Scheduler.detect_conflicts()`, `DailyPlan.warnings` | Warns about clashing fixed times without crashing |
| Recurring tasks | `Pet.complete_task()`, `CareTask.next_instance()`, `Recurrence.next_occurrence()` | Completing a daily or weekly task creates its next instance |

### Sorting

- **`Scheduler.sort_by_time(tasks)`** returns tasks in clock order. Tasks with a `fixed_time` come first, earliest to latest, and flexible tasks follow in the order they were added. The sort key is `lambda task: (task.fixed_time is None, task.fixed_time or time.min)`. `False` sorts before `True`, so tasks with a time lead. `time.min` stands in for flexible tasks, because comparing `None` with a `time` would raise an error.
- **`Scheduler._sort(tasks, preferred_order)`** is the order used when building a plan: priority (high first), then the owner's preferred category order, then shorter duration. Python's `sorted()` is stable, so tasks that tie keep their insertion order and the plan is identical on every run.

### Filtering

- **`Owner.filter_tasks(pet_name=None, completed=None, day=None)`** returns the tasks that match every filter you give it. Any filter left as `None` is ignored.
  - `pet_name` matches regardless of capitalisation (`"biscuit"` finds Biscuit's tasks).
  - `completed=True` / `False` is checked against `day` (today if omitted), because completion is stored per date. A daily walk can be done today and still pending tomorrow.
  - Filters combine, e.g. `filter_tasks(pet_name="Biscuit", completed=False)`.
- **`Pet.active_tasks(day)`** returns only the task instances live on `day`. That way, a recurring task that has rolled over appears once, not once per past completion.
- **`Scheduler._due_today(tasks, day)`** is the filter used inside `build_plan`. It drops tasks that aren't due that day or are already done. Those tasks are listed under "Left out" with a reason, never dropped silently.

### Conflict detection

- **`Scheduler.detect_conflicts(tasks, day, name_for=None)`** checks every fixed-time task due on `day` and returns a list of warning strings. It never raises, so the app can show the warnings and still build a plan.
  - Each warning says whether it involves the **same pet** or **different pets**, and whether the tasks start at the **same time** or **overlap**:
    ```
    Same time, same pet: Breakfast (Mochi) and Ear drops (Mochi) both start at 07:00
    Overlap, different pets: Morning walk (Mochi) 08:30-09:00 runs into Vet phone call (Biscuit) 08:45-09:05
    ```
  - It also flags a fixed-time task set to repeat more than once a day, since every repeat would land on the same minute.
  - **Algorithm:** sort the tasks by start time, then compare each one only with the tasks that start before it ends. The inner loop stops at the first task that starts after it, so this costs O(n log n) plus the number of clashes, instead of checking every pair. Intervals are half-open: a task ending at 09:00 does not clash with one starting at 09:00.
- **`Scheduler.build_plan()`** runs this check first and stores the results in **`DailyPlan.warnings`**. `plan.explain()` lists them, and the Streamlit app shows each one as a yellow `st.warning` banner.
- During placement, **`Scheduler._anchor_fixed()`** keeps one task from each clash and defers the other as `TIME_CONFLICT`, with a detail naming the task that kept the slot (e.g. `08:45 clashes with Morning walk at 08:30-09:00`). For tasks that start at the same time, the higher-priority one wins. For overlaps, the one that starts first wins.
- **Limits:** only fixed-time tasks are checked, because flexible tasks are fitted into free gaps and so can never clash. `detect_conflicts` also treats every fixed time as being on the same calendar day, so it would miss a clash in a window that crosses midnight. `build_plan` itself does handle overnight windows.

### Recurring tasks

- **`Pet.complete_task(task_id, day)`** marks a task done. For a daily or weekly task it:
  1. records the completion and closes the current instance at `day` (`ends_on = day`),
  2. creates the next instance with **`CareTask.next_instance(day)`**: a copy with a new id, an empty completion history, `starts_on = day + timedelta(days=1)`, and `follows_id` pointing back to the original,
  3. adds that instance to the pet and returns it.

  A one-off task is just marked done, and the method returns `None`. Completing the same task twice on the same day does nothing the second time, so a double-click can't create two copies.
- **`Pet.reopen_task(task_id, day)`** undoes a completion and removes the instance it created, unless that instance already has completions of its own. The ↺ button in the app uses it.
- **`Recurrence.next_occurrence(after)`** works out the next due date: `after + timedelta(days=1)` for daily, the first of the next seven days that falls on a listed weekday for weekly, and `None` for one-off tasks. `CareTask.mark_complete(day)` returns this date.
- **`CareTask.is_active_on(day)`** together with `starts_on` / `ends_on` makes sure each day sees exactly one live instance of a task. `build_plan` and `filter_tasks` skip instances that aren't live, so old completed instances never show up as duplicates.

## 📸 Demo Walkthrough

Describe your app in numbered steps so a reader can follow along without watching a video:

1. <!-- Describe this step -->
2. <!-- Describe this step -->
3. <!-- Describe this step -->
4. <!-- Describe this step -->
5. <!-- Add more steps as needed -->

**Screenshot or video** *(optional)*: <!-- Insert a screenshot or link to a demo video here -->
