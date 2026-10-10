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

## ✨ Features

All scheduling logic lives in `pawpal_system.py`. The Streamlit app (`app.py`) and the terminal demo (`main.py`) only collect input and display results.

- **Sorting by time:** `Scheduler.sort_by_time()` lists tasks in clock order. Fixed-time tasks come first, earliest to latest, and flexible tasks follow in the order they were added.
- **Priority-based planning:** `build_plan` places tasks by priority (high first), then by the owner's preferred category order, then by shortest duration. The sort is stable, so the same input always gives the same plan.
- **Fixed times first, then gap filling:** tasks with a fixed time are placed first and split the day into free gaps. Flexible tasks then go into the first gap long enough to hold them.
- **Conflict warnings:** `Scheduler.detect_conflicts()` flags fixed-time tasks that start at the same time or overlap. Each warning says whether the clash is between tasks for the same pet or different pets. The app shows these as warnings and never crashes, and the plan keeps the higher-priority (or earlier) task.
- **Daily and weekly recurrence:** marking a daily or weekly task done creates its next instance, starting the next day, with `Pet.complete_task()`. Today's copy stays marked done, and tomorrow's copy is pending. One-off tasks don't repeat. Completing a task can be undone.
- **Repeats within a day:** a task with `times_per_day = 2` (for example, twice-daily play) is scheduled as two separate slots.
- **Meal-to-walk spacing:** walks are kept a set number of minutes away from the end of any meal.
- **Filtering:** `Owner.filter_tasks()` filters by pet name, by done or pending for a given day, or both.
- **Time budget:** the plan never goes past the hours available. Fixed tasks that run outside the window, and tasks longer than the whole day, are caught.
- **Explained plans:** every scheduled task says why it got its slot. Every task left out is listed with a reason (`TIME_CONFLICT`, `NO_TIME_LEFT`, `NOT_DUE_TODAY`, `ALREADY_COMPLETED`, …), so nothing disappears silently.
- **Overnight windows:** a day that starts at 22:00 and runs past midnight is planned correctly.

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

Run the full test suite from the project root:

```bash
python -m pytest
```

### What the tests cover

`test_pawpal.py` has 100 tests for the scheduling engine in `pawpal_system.py`. They call the classes directly, so no Streamlit session is needed.

- **Sorting:** `sort_by_time` returns fixed-time tasks in clock order with flexible tasks last, keeps tasks that share a time in insertion order, and leaves the input list unchanged. Finished plans come out in order of start time with no overlapping entries, including a window that runs past midnight. Placement follows priority, then the preferred category order, then shorter duration.
- **Recurrence:** daily, weekly and one-off rules are due on the right days. Completing a daily task creates a new instance that starts the next day, copies the task's details and links back to the original. Each day sees exactly one live instance. Completing twice doesn't create two copies, and reopening a task undoes the rollover.
- **Conflict detection:** tasks that start at the same time are flagged as "Same time", partial overlaps as "Overlap", and each warning says whether it's the same pet or different pets. Back-to-back tasks and flexible tasks are never flagged. `build_plan` keeps the higher-priority task and defers the other as `TIME_CONFLICT`.
- **Time budget and placement:** a zero-minute day, a task longer than the whole day, fixed times outside the window, the required gap between a meal and a walk, and tasks repeated more than once a day. A task that isn't placed always appears in the plan's deferred list with a reason.
- **Validation and filtering:** invalid tasks and day settings are rejected, and `Owner.filter_tasks` filters by pet name and per-day completion status.

### Sample test output

```
============================= test session starts ==============================
platform darwin -- Python 3.12.2, pytest-7.4.4, pluggy-1.0.0
rootdir: /Users/chiragdhungana/Documents/8th_Semester/ai110-module2show-pawpal-starter
plugins: anyio-4.6.2
collected 100 items

test_pawpal.py ......................................................... [ 57%]
...........................................                              [100%]

============================= 100 passed in 0.12s ==============================
```

### Confidence level

**★★★★☆ (4/5)**

All 100 tests pass, and they cover the main behaviors and their edge cases. That includes the bugs that would quietly produce a wrong plan: duplicate rollovers, tasks missing without a reason, and overlapping placements. It isn't 5 stars for two reasons:

- `detect_conflicts` and `sort_by_time` assume the day doesn't cross midnight. `build_plan` handles overnight windows correctly, so a clash after midnight can still be resolved in the plan without a warning being shown.
- The Streamlit UI in `app.py` isn't covered by automated tests. It has only been checked by hand.

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

Run `streamlit run app.py`. The app opens with two sample pets, Mochi and Biscuit, so you can try it right away.

### What you can do

- **Sidebar:** add pets, pick the day, and set the hours you have.
- **Tasks tab:** add, edit, complete (✓), undo (↺) or delete (✕) tasks. You get a warning if a new task clashes with another one.
- **Schedule view tab:** see the day's tasks sorted by time, filtered by pet or by done/pending.
- **Daily plan tab:** see the generated plan, why each task was placed where it is, and what was left out.

### Example workflow

1. **Add a pet:** in the sidebar, add "Luna" the cat.
2. **Add a task:** on the Tasks tab, give Luna a "Brush" task at 07:00. You'll see a warning, because Mochi's Breakfast is also at 07:00.
3. **View the schedule:** open Schedule view to see every task in time order.
4. **Check the plan:** open Daily plan. The higher-priority task gets the 07:00 slot, and the other is listed under "Left out" with the reason.
5. **Complete a task:** click ✓ on "Litter box". It's marked done today and comes back tomorrow.

### Scheduler behaviors on show

- **Sorting:** tasks are listed by time, and flexible tasks go last.
- **Conflict warnings:** tasks at the same or overlapping times are flagged instead of crashing the app.
- **Daily recurrence:** completing a daily task creates tomorrow's copy.
- **Explained plan:** every task left out comes with a reason.

### Sample CLI output

`python main.py` runs the same scheduler in the terminal (trimmed):

```
CONFLICT CHECK
------------------------------------------------------------------------
  WARNING: Same time, same pet: Breakfast (Mochi) and Ear drops (Mochi) both start at 07:00
  WARNING: Overlap, different pets: Morning walk (Mochi) 08:30-09:00 runs into Vet phone call (Biscuit) 08:45-09:05
  WARNING: Same time, different pets: Evening walk (Mochi) and Dinner (Biscuit) both start at 17:30

ALL TASKS BY TIME
------------------------------------------------------------------------
  07:00  Breakfast             Mochi
  07:00  Ear drops             Mochi
  08:30  Morning walk          Mochi
  08:45  Vet phone call        Biscuit
  12:00  Thyroid meds          Biscuit
  17:30  Evening walk          Mochi
  17:30  Dinner                Biscuit
  flex   Nail trim             Mochi
  flex   Litter box            Biscuit
  flex   Play session          Biscuit

RECURRING: marking tasks complete
------------------------------------------------------------------------
  Thyroid meds          done today [261dffe8], new instance [ec680741] starts Sun 11 Oct
  Litter box            done today [1d47aa6a], new instance [2b6d0db1] starts Sun 11 Oct

TODAY'S SCHEDULE  Saturday 10 October 2026
------------------------------------------------------------------------
  TIME           TASK                  PET        MINS  PRIORITY
  07:00 - 07:05  Ear drops             Mochi         5  high
  07:05 - 07:20  Play session (1 of 2) Biscuit      15  low
  07:20 - 07:35  Play session (2 of 2) Biscuit      15  low
  08:30 - 09:00  Morning walk          Mochi        30  high
  17:30 - 17:40  Dinner                Biscuit      10  high
------------------------------------------------------------------------
  Used 75 of 720 minutes, 645 free.

LEFT OUT
------------------------------------------------------------------------
  Nail trim             Mochi     not scheduled for today (weekly on Mon)
  Thyroid meds          Biscuit   already done today
  Litter box            Biscuit   already done today
  Breakfast             Mochi     overlaps a fixed time task (07:00 clashes with Ear drops at 07:00-07:05)
  Vet phone call        Biscuit   overlaps a fixed time task (08:45 clashes with Morning walk at 08:30-09:00)
  Evening walk          Mochi     overlaps a fixed time task (17:30 clashes with Dinner at 17:30-17:40)
```
