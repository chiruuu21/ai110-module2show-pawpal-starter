"""PawPal+ core domain model and scheduling engine.

Layers:
    Owner -> Pet -> CareTask        data
    Scheduler                       service (reads data, owns the algorithm)
    DailyPlan -> ScheduledTask      result
              -> DeferredTask

Time model: the scheduler reasons in whole minutes measured from
``DayConstraints.day_start``. Clock times are produced only at the boundary,
by ``DayConstraints.to_datetime``. A ``datetime`` carries its date, so a plan
running past midnight still sorts and compares correctly, which a bare
``time`` cannot do.

Placement model: tasks with a ``fixed_time`` are anchored first and split the
day into free intervals; flexible tasks are then fitted into those gaps. A
single advancing cursor would jump past every anchored task and strand the
whole morning.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import Enum
from uuid import uuid4

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Priority(Enum):
    """How important a task is. The value doubles as the sort weight."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def weight(self) -> int:
        """Numeric sort weight, highest priority first."""
        return self.value


class TaskCategory(Enum):
    """What kind of care a task is. Drives spacing rules such as the gap a
    walk needs after a meal."""

    WALK = "walk"
    FEEDING = "feeding"
    MEDS = "meds"
    GROOMING = "grooming"
    ENRICHMENT = "enrichment"
    OTHER = "other"


class Frequency(Enum):
    """How often a task repeats."""

    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"


class SkipReason(Enum):
    """Why a task did not make it into the plan."""

    NO_TIME_LEFT = "no time left in the day"
    NOT_DUE_TODAY = "not scheduled for today"
    EXCEEDS_DAY_BUDGET = "longer than the entire available window"
    TIME_CONFLICT = "overlaps a fixed time task"
    OUTSIDE_WINDOW = "fixed time falls outside the available window"
    ALREADY_COMPLETED = "already done today"


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------


@dataclass
class Recurrence:
    """When a task repeats.

    ``weekdays`` uses Python's convention: Monday is 0, Sunday is 6, and it is
    only meaningful when ``frequency`` is WEEKLY.

    ``due_date`` anchors a ONCE task to a specific day. Without it a one off
    task such as a vet visit would be "due" every single day forever.

    ``times_per_day`` lets one task produce several placements, which is how
    twice daily feeding is expressed.
    """

    frequency: Frequency = Frequency.DAILY
    weekdays: list[int] = field(default_factory=list)
    due_date: date | None = None
    times_per_day: int = 1

    def is_due_on(self, day: date) -> bool:
        """Return True if this recurrence lands on ``day``.

        A WEEKLY recurrence with an empty ``weekdays`` list is never due rather
        than silently meaning "every day", and a ONCE recurrence with no
        ``due_date`` is never due for the same reason: both are more likely to
        be a data entry mistake than an intent to run daily.
        """
        if self.frequency is Frequency.DAILY:
            return True
        if self.frequency is Frequency.WEEKLY:
            return day.weekday() in self.weekdays
        return self.due_date is not None and day == self.due_date

    def next_occurrence(self, after: date) -> date | None:
        """The first date strictly after ``after`` on which this is due.

        DAILY is simply the next day. WEEKLY checks the next seven days in
        turn; every weekday appears once in any seven, so the loop is bounded.
        ONCE has no next occurrence. A WEEKLY rule with no weekdays finds
        nothing and returns None, matching ``is_due_on`` treating it as never
        due.
        """
        if self.frequency is Frequency.DAILY:
            return after + timedelta(days=1)
        if self.frequency is Frequency.WEEKLY:
            for offset in range(1, 8):
                candidate = after + timedelta(days=offset)
                if candidate.weekday() in self.weekdays:
                    return candidate
        return None

    def describe(self) -> str:
        """Short human readable summary, used in deferral messages."""
        if self.frequency is Frequency.DAILY:
            return "daily"
        if self.frequency is Frequency.WEEKLY:
            if not self.weekdays:
                return "weekly, but no weekday was set"
            days = ", ".join(_WEEKDAY_NAMES[d] for d in sorted(self.weekdays))
            return f"weekly on {days}"
        if self.due_date is None:
            return "one off, but no date was set"
        return f"one off on {self.due_date.isoformat()}"


@dataclass
class CareTask:
    """A single care activity for one pet.

    ``completed_on`` records the dates this task was done rather than a single
    boolean. A daily walk marked done today must still be due tomorrow, which
    one shared flag cannot express.

    ``starts_on`` and ``ends_on`` bound the days this instance is live. They
    are None (open ended) for a task the owner created; ``Pet.complete_task``
    closes an instance at the completion day and hands the rest of the
    series to a fresh instance, linked back through ``follows_id``.
    """

    title: str
    duration_minutes: int
    priority: Priority = Priority.MEDIUM
    category: TaskCategory = TaskCategory.OTHER
    fixed_time: time | None = None
    recurrence: Recurrence = field(default_factory=Recurrence)
    completed_on: set[date] = field(default_factory=set)
    pet_id: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    starts_on: date | None = None
    ends_on: date | None = None
    follows_id: str | None = None

    def __post_init__(self) -> None:
        """Validate on construction so an invalid task can never exist."""
        self.validate()

    def validate(self) -> None:
        """Raise ValueError if this task could never be scheduled sensibly.

        Runs on construction so a task built directly and handed to the
        scheduler is checked too, not only one added through ``Pet.add_task``.
        """
        if not self.title or not self.title.strip():
            raise ValueError("task title must not be empty")
        if isinstance(self.duration_minutes, bool) or not isinstance(
            self.duration_minutes, int
        ):
            raise ValueError("duration_minutes must be a whole number of minutes")
        if self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")
        if self.recurrence.times_per_day < 1:
            raise ValueError("times_per_day must be at least 1")

    def is_done_on(self, day: date) -> bool:
        """Return True if this task was already completed on ``day``."""
        return day in self.completed_on

    def is_active_on(self, day: date) -> bool:
        """Return True if ``day`` falls inside this instance's live window."""
        if self.starts_on is not None and day < self.starts_on:
            return False
        return self.ends_on is None or day <= self.ends_on

    def is_due_on(self, day: date) -> bool:
        """Return True if the task should be considered for ``day``."""
        if not self.is_active_on(day) or self.is_done_on(day):
            return False
        return self.recurrence.is_due_on(day)

    def next_instance(self, completed: date) -> "CareTask | None":
        """Build the instance that carries this series on after ``completed``.

        The copy goes live the day after completion (``timedelta(days=1)``),
        so a daily task is due again tomorrow and a weekly one shows up as
        "not scheduled today" until its weekday comes round. It gets a new
        id and an empty completion history. A ONCE task has no next
        occurrence, so it returns None.
        """
        if self.recurrence.next_occurrence(completed) is None:
            return None
        return replace(
            self,
            id=uuid4().hex[:8],
            completed_on=set(),
            starts_on=completed + timedelta(days=1),
            ends_on=None,
            follows_id=self.id,
        )

    def mark_complete(self, day: date) -> date | None:
        """Record that this task was done on ``day`` and return when it is next due.

        Takes the date explicitly so completing today's instance never removes
        the task from tomorrow's plan. This only records the date; it does
        not create the next instance. ``Pet.complete_task`` does that, and is
        what the UI and demo call. The returned date tells the caller when the
        task is next due, or None for a one off task.
        """
        self.completed_on.add(day)
        return self.recurrence.next_occurrence(day)


@dataclass
class Pet:
    """A pet profile and the care tasks it needs."""

    name: str
    species: str
    age: int | None = None
    tasks: list[CareTask] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:8])

    def add_task(self, task: CareTask) -> None:
        """Attach a task to this pet and stamp ``task.pet_id`` with this id.

        The back reference is what lets a finished plan say whose walk it is
        once tasks from several pets have been pooled together.
        """
        task.validate()
        task.pet_id = self.id
        self.tasks.append(task)

    def remove_task(self, task_id: str) -> bool:
        """Remove a task by id. Return True if something was removed.

        Removal is by id, not list index, because indexes shift after a delete
        and the Streamlit UI would then edit the wrong row.
        """
        for index, task in enumerate(self.tasks):
            if task.id == task_id:
                del self.tasks[index]
                return True
        return False

    def find_task(self, task_id: str) -> CareTask | None:
        """Look up one of this pet's tasks by id."""
        return next((task for task in self.tasks if task.id == task_id), None)

    def active_tasks(self, day: date) -> list[CareTask]:
        """Tasks live on ``day``, leaving out instances already rolled over."""
        return [task for task in self.tasks if task.is_active_on(day)]

    def complete_task(self, task_id: str, day: date) -> CareTask | None:
        """Mark a task done on ``day`` and roll a recurring one forward.

        For a DAILY or WEEKLY task this closes the current instance at
        ``day`` and appends a new instance starting the next day, which is
        returned. A ONCE task is just marked done and None is returned.
        Completing a task that is not live on ``day``, or that is already
        done that day, changes nothing and returns None, so a double click
        cannot spawn two copies.
        """
        task = self.find_task(task_id)
        if task is None or not task.is_active_on(day) or task.is_done_on(day):
            return None
        task.mark_complete(day)
        follower = task.next_instance(day)
        if follower is None:
            return None
        task.ends_on = day
        follower.pet_id = self.id
        self.tasks.append(follower)
        return follower

    def reopen_task(self, task_id: str, day: date) -> bool:
        """Undo ``complete_task``: clear the completion and drop the rollover.

        The follower is removed only while it has no completions of its own,
        so undoing an old completion never throws away later history.
        Returns True if the task was done on ``day`` and is now reopened.
        """
        task = self.find_task(task_id)
        if task is None or not task.is_done_on(day):
            return False
        task.completed_on.discard(day)
        if task.ends_on == day:
            followers = [t for t in self.tasks if t.follows_id == task.id]
            if all(not follower.completed_on for follower in followers):
                for follower in followers:
                    self.remove_task(follower.id)
                task.ends_on = None
        return True


@dataclass
class Owner:
    """The person doing the caring.

    ``preferred_order`` is the owner's soft preference over task categories and
    is consumed by ``Scheduler._sort`` as a tiebreak. It replaces an untyped
    preferences dict, which had no reader and no schema.
    """

    name: str
    pets: list[Pet] = field(default_factory=list)
    preferred_order: list[TaskCategory] = field(default_factory=list)

    def add_pet(self, pet: Pet) -> None:
        """Attach a pet to this owner, ignoring a pet already present."""
        if any(existing.id == pet.id for existing in self.pets):
            return
        self.pets.append(pet)

    def all_tasks(self) -> list[CareTask]:
        """Return every task across every pet, flattened."""
        return [task for pet in self.pets for task in pet.tasks]

    def filter_tasks(
        self,
        pet_name: str | None = None,
        completed: bool | None = None,
        day: date | None = None,
    ) -> list[CareTask]:
        """Return tasks matching every filter given; None means "any".

        ``pet_name`` matches case insensitively. ``completed`` is judged
        against ``day`` (today if omitted), because completion is recorded per
        date: a daily walk is done today and pending tomorrow. Only instances
        live on ``day`` are considered, so a rolled over task appears once.
        """
        day = day or date.today()
        wanted = pet_name.strip().lower() if pet_name else None
        matches: list[CareTask] = []
        for pet in self.pets:
            if wanted is not None and pet.name.lower() != wanted:
                continue
            for task in pet.active_tasks(day):
                if completed is None or task.is_done_on(day) == completed:
                    matches.append(task)
        return matches

    def find_pet(self, pet_id: str) -> Pet | None:
        """Look up a pet by id so a scheduled task can be traced back to it."""
        for pet in self.pets:
            if pet.id == pet_id:
                return pet
        return None

    def pet_name_for(self, task: CareTask) -> str:
        """Resolve the display name of the pet a task belongs to."""
        if task.pet_id is None:
            return ""
        pet = self.find_pet(task.pet_id)
        return pet.name if pet else ""


@dataclass
class DayConstraints:
    """The owner's limits for one day."""

    day: date
    day_start: time
    available_minutes: int
    min_gap_after_meal: int = 0

    def __post_init__(self) -> None:
        """Validate on construction so an impossible day can never exist."""
        self.validate()

    def validate(self) -> None:
        """Raise ValueError on a negative budget.

        Zero is legal and must yield an empty plan rather than an error.
        """
        if self.available_minutes < 0:
            raise ValueError("available_minutes must not be negative")
        if self.min_gap_after_meal < 0:
            raise ValueError("min_gap_after_meal must not be negative")

    def start_datetime(self) -> datetime:
        """The moment the available window opens."""
        return datetime.combine(self.day, self.day_start)

    def end_datetime(self) -> datetime:
        """The moment the window closes, correct even when it crosses midnight."""
        return self.start_datetime() + timedelta(minutes=self.available_minutes)

    def to_datetime(self, offset_minutes: int) -> datetime:
        """Convert an offset in minutes from ``day_start`` into a real moment."""
        return self.start_datetime() + timedelta(minutes=offset_minutes)

    def offset_of(self, clock: time) -> int:
        """Convert a wall clock time into minutes from ``day_start``.

        A fixed time earlier in the clock than ``day_start`` is treated as
        falling on the next day, so a window running 22:00 to 02:00 can still
        anchor a task at 01:00.
        """
        moment = datetime.combine(self.day, clock)
        if moment < self.start_datetime():
            moment += timedelta(days=1)
        return int((moment - self.start_datetime()).total_seconds() // 60)


# ---------------------------------------------------------------------------
# Result layer
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTask:
    """A task placed at a concrete moment, with the reason it earned that slot.

    Stores ``datetime`` rather than ``time`` so overlap checks stay correct for
    a task that runs across midnight.
    """

    task: CareTask
    start: datetime
    end: datetime
    reason: str = ""

    @property
    def duration_minutes(self) -> int:
        """How long this placement actually occupies, in whole minutes."""
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class DeferredTask:
    """A task that was left out, and why."""

    task: CareTask
    reason: SkipReason
    detail: str = ""


@dataclass
class DailyPlan:
    """The output of a scheduling run: what happens, what got cut, and why.

    Carries the ``constraints`` it was built under so ``explain`` can report
    the budget as well as the usage.
    """

    day: date
    constraints: DayConstraints
    entries: list[ScheduledTask] = field(default_factory=list)
    deferred: list[DeferredTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def minutes_used(self) -> int:
        """Derived from ``entries`` so it can never drift out of sync."""
        return sum(entry.duration_minutes for entry in self.entries)

    @property
    def minutes_available(self) -> int:
        """The day's total budget, read back from the constraints."""
        return self.constraints.available_minutes

    @property
    def minutes_free(self) -> int:
        """Budget left over once every scheduled entry is counted."""
        return self.minutes_available - self.minutes_used

    def explain(self) -> str:
        """Return a human readable justification for the whole plan.

        Covers both halves: why each entry sits where it does, and why each
        deferred task was cut. Built from the same records the scheduler
        produced, so the explanation cannot drift from the actual plan.
        """
        window = (
            f"{self.constraints.start_datetime():%H:%M} to "
            f"{self.constraints.end_datetime():%H:%M}"
        )
        lines = [
            f"Daily plan for {self.day.isoformat()}",
            f"Window {window}. "
            f"{self.minutes_available} min available, "
            f"{self.minutes_used} used, {self.minutes_free} free.",
        ]

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"  {warning}" for warning in self.warnings)

        if self.entries:
            lines.append("")
            lines.append("Scheduled:")
            for entry in self.entries:
                lines.append(
                    f"  {entry.start:%H:%M} to {entry.end:%H:%M}  "
                    f"{entry.task.title} ({entry.duration_minutes} min) "
                    f"because {entry.reason}"
                )
        else:
            lines.append("")
            lines.append("Nothing could be scheduled.")

        if self.deferred:
            lines.append("")
            lines.append("Left out:")
            for item in self.deferred:
                detail = f" ({item.detail})" if item.detail else ""
                lines.append(
                    f"  {item.task.title} ({item.task.duration_minutes} min): "
                    f"{item.reason.value}{detail}"
                )
        return "\n".join(lines)

    def to_rows(self) -> list[dict]:
        """Flatten the scheduled entries into dicts for a Streamlit table."""
        return [
            {
                "start": f"{entry.start:%H:%M}",
                "end": f"{entry.end:%H:%M}",
                "task": entry.task.title,
                "minutes": entry.duration_minutes,
                "priority": entry.task.priority.name.lower(),
                "category": entry.task.category.value,
                "pet_id": entry.task.pet_id or "",
                "why": entry.reason,
            }
            for entry in self.entries
        ]

    def deferred_rows(self) -> list[dict]:
        """Flatten the deferred tasks into dicts for display.

        The cut tasks are half the point of the plan, so they get a display
        path of their own rather than being formatted ad hoc in the UI.
        """
        return [
            {
                "task": item.task.title,
                "minutes": item.task.duration_minutes,
                "priority": item.task.priority.name.lower(),
                "pet_id": item.task.pet_id or "",
                "reason": item.reason.value,
                "detail": item.detail,
            }
            for item in self.deferred
        ]


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


class Scheduler:
    """Builds a daily plan from tasks plus constraints.

    Holds no data of its own, so ``build_plan`` is a pure function of its
    inputs and tests can call it with a hand built list and no Streamlit
    session involved.
    """

    def build_plan(
        self,
        tasks: list[CareTask],
        constraints: DayConstraints,
        preferred_order: list[TaskCategory] | None = None,
        name_for=None,
    ) -> DailyPlan:
        """Produce a DailyPlan for ``constraints.day``.

        Pipeline: drop instances not live on the day (history left behind by
        ``Pet.complete_task``), check fixed times for clashes, filter to what
        is due today, expand repeats, sort, anchor the fixed time tasks, then
        fit the flexible ones into the remaining gaps. Every task that is not
        placed lands in ``deferred`` with a SkipReason, so nothing is dropped
        silently. Clashes are recorded in ``plan.warnings`` up front, so the
        owner sees them even though planning carries on. ``name_for`` is
        passed through to ``detect_conflicts`` for pet names in warnings.
        """
        day = constraints.day
        plan = DailyPlan(day=day, constraints=constraints)

        # Instances from before or after a rollover are history, not part of
        # this day, so they are dropped without a deferral entry.
        tasks = [task for task in tasks if task.is_active_on(day)]
        plan.warnings = self.detect_conflicts(tasks, day, name_for)
        due: list[CareTask] = []
        for task in tasks:
            if task.is_due_on(day):
                due.append(task)
            elif task.is_done_on(day):
                plan.deferred.append(DeferredTask(task, SkipReason.ALREADY_COMPLETED))
            else:
                plan.deferred.append(
                    DeferredTask(
                        task, SkipReason.NOT_DUE_TODAY, task.recurrence.describe()
                    )
                )

        if constraints.available_minutes == 0:
            for task in due:
                plan.deferred.append(
                    DeferredTask(task, SkipReason.NO_TIME_LEFT, "no time was available")
                )
            return plan

        candidates = self._sort(self._expand(due), preferred_order)
        anchored, rejected = self._anchor_fixed(
            [task for task in candidates if task.fixed_time is not None], constraints
        )
        plan.entries.extend(anchored)
        plan.deferred.extend(rejected)

        for task in (task for task in candidates if task.fixed_time is None):
            if task.duration_minutes > constraints.available_minutes:
                plan.deferred.append(
                    DeferredTask(
                        task,
                        SkipReason.EXCEEDS_DAY_BUDGET,
                        f"needs {task.duration_minutes} min of a "
                        f"{constraints.available_minutes} min day",
                    )
                )
                continue
            free = self._free_intervals(plan.entries, constraints, task)
            entry = self._fit(task, free, constraints)
            if entry is None:
                plan.deferred.append(
                    DeferredTask(
                        task,
                        SkipReason.NO_TIME_LEFT,
                        f"no free gap of {task.duration_minutes} min remained",
                    )
                )
            else:
                plan.entries.append(entry)

        plan.entries.sort(key=lambda entry: entry.start)
        return plan

    def sort_by_time(self, tasks: list[CareTask]) -> list[CareTask]:
        """Order tasks by their fixed clock time, earliest first.

        Flexible tasks have no time yet, so they go last, in their original
        order. Comparing ``time`` objects directly is safe here because this is
        a same day view; ``build_plan`` uses offsets for windows that cross
        midnight.
        """
        return sorted(
            tasks,
            key=lambda task: (task.fixed_time is None, task.fixed_time or time.min),
        )

    def detect_conflicts(
        self,
        tasks: list[CareTask],
        day: date,
        name_for=None,
    ) -> list[str]:
        """Warn about fixed time tasks due on ``day`` that overlap.

        Returns readable warnings instead of raising, so the caller can show
        them and still build a plan. ``name_for`` optionally maps a task to its
        pet's name (``Owner.pet_name_for`` fits) to make the warning clearer.

        Each warning is labelled "Same time" when both tasks start together or
        "Overlap" when one runs into the other, and "same pet" or "different
        pets" by comparing ``pet_id``. A fixed time task with
        ``times_per_day`` above 1 is flagged too, since every repeat would land
        on the same minute. Flexible tasks are skipped: they have no time until
        ``build_plan`` fits them into a free gap. Times are combined with
        ``day`` directly, so this check assumes a window that does not cross
        midnight.

        Sweep after sorting by start: once a later task starts at or after an
        earlier one's end, nothing after it can overlap that earlier task, so
        the inner loop stops early. O(n log n) plus the number of clashes,
        rather than comparing every pair.
        """
        label = (lambda task: task.title) if name_for is None else (
            lambda task: f"{task.title} ({name_for(task) or 'no pet'})"
        )
        warnings: list[str] = []
        timed: list[tuple[datetime, datetime, CareTask]] = []
        for task in self._due_today(tasks, day):
            if task.fixed_time is None:
                continue
            if task.recurrence.times_per_day > 1:
                warnings.append(
                    f"{label(task)} repeats {task.recurrence.times_per_day} times "
                    f"but every repeat is fixed at {task.fixed_time:%H:%M}"
                )
            start = datetime.combine(day, task.fixed_time)
            timed.append((start, start + timedelta(minutes=task.duration_minutes), task))

        timed.sort(key=lambda item: item[0])
        for index, (start, end, task) in enumerate(timed):
            for other_start, other_end, other in timed[index + 1:]:
                if other_start >= end:
                    break
                who = "same pet" if task.pet_id == other.pet_id else "different pets"
                if start == other_start:
                    warnings.append(
                        f"Same time, {who}: {label(task)} and {label(other)} "
                        f"both start at {start:%H:%M}"
                    )
                else:
                    warnings.append(
                        f"Overlap, {who}: {label(task)} {start:%H:%M}-{end:%H:%M} "
                        f"runs into {label(other)} {other_start:%H:%M}-{other_end:%H:%M}"
                    )
        return warnings

    def _due_today(self, tasks: list[CareTask], day: date) -> list[CareTask]:
        """Filter to tasks due on ``day``.

        Tasks already done on ``day`` and tasks off schedule drop out.
        """
        return [task for task in tasks if task.is_due_on(day)]

    def _expand(self, tasks: list[CareTask]) -> list[CareTask]:
        """Expand each task into ``recurrence.times_per_day`` instances.

        Twice daily feeding is one task that needs two slots. Each instance is
        a distinct object with its own id, so placing one never aliases another.
        """
        expanded: list[CareTask] = []
        for task in tasks:
            count = task.recurrence.times_per_day
            if count == 1:
                expanded.append(task)
                continue
            for number in range(1, count + 1):
                expanded.append(
                    replace(
                        task,
                        title=f"{task.title} ({number} of {count})",
                        completed_on=set(task.completed_on),
                        id=uuid4().hex[:8],
                    )
                )
        return expanded

    def _sort(
        self,
        tasks: list[CareTask],
        preferred_order: list[TaskCategory] | None = None,
    ) -> list[CareTask]:
        """Order tasks for placement.

        Key: priority descending, then the owner's ``preferred_order`` over
        categories, then shorter duration first. ``sorted`` is stable, so the
        original insertion order survives as the final tiebreak. That is what
        makes repeated runs identical; without it two equal tasks could swap
        between Streamlit reruns and the tests would be flaky.
        """
        ranking = {category: index for index, category in enumerate(preferred_order or [])}
        unranked = len(ranking)
        return sorted(
            tasks,
            key=lambda task: (
                -task.priority.weight,
                ranking.get(task.category, unranked),
                task.duration_minutes,
            ),
        )

    def _anchor_fixed(
        self, tasks: list[CareTask], constraints: DayConstraints
    ) -> tuple[list[ScheduledTask], list[DeferredTask]]:
        """Pin every task with a ``fixed_time`` to its required moment.

        A fixed task running past the end of the window is deferred as
        OUTSIDE_WINDOW; one colliding with an already anchored task is deferred
        as TIME_CONFLICT. Earlier times are anchored first so the conflict is
        reported against the task that got there first.
        """
        placed: list[ScheduledTask] = []
        rejected: list[DeferredTask] = []
        for task in sorted(tasks, key=lambda t: constraints.offset_of(t.fixed_time)):
            start = constraints.offset_of(task.fixed_time)
            if start + task.duration_minutes > constraints.available_minutes:
                rejected.append(
                    DeferredTask(
                        task,
                        SkipReason.OUTSIDE_WINDOW,
                        f"{task.fixed_time:%H:%M} plus {task.duration_minutes} min "
                        f"runs past {constraints.end_datetime():%H:%M}",
                    )
                )
                continue
            entry = self._place(task, start, constraints)
            blocker = self._find_conflict(entry, placed)
            if blocker is not None:
                rejected.append(
                    DeferredTask(
                        task,
                        SkipReason.TIME_CONFLICT,
                        f"{task.fixed_time:%H:%M} clashes with {blocker.task.title} "
                        f"at {blocker.start:%H:%M}-{blocker.end:%H:%M}",
                    )
                )
                continue
            placed.append(entry)
        return placed, rejected

    def _free_intervals(
        self,
        placed: list[ScheduledTask],
        constraints: DayConstraints,
        task: CareTask | None = None,
    ) -> list[tuple[int, int]]:
        """Return the gaps left by ``placed``, as (start, end) minute offsets.

        With nothing anchored this is a single interval covering the whole
        budget. Each anchored task splits the interval it falls inside.

        When ``task`` is a walk, the ``min_gap_after_meal`` minutes after every
        feeding count as busy too. Blocking each meal's window, not just the
        latest one, means a short unrelated task slotted after breakfast
        cannot open a gap that lets a walk start minutes after eating.
        """
        start_of_day = constraints.start_datetime()
        busy = [
            (self._offset(entry.start, start_of_day), self._offset(entry.end, start_of_day))
            for entry in placed
        ]
        if (
            task is not None
            and task.category is TaskCategory.WALK
            and constraints.min_gap_after_meal > 0
        ):
            for entry in placed:
                if entry.task.category is TaskCategory.FEEDING:
                    meal_end = self._offset(entry.end, start_of_day)
                    busy.append((meal_end, meal_end + constraints.min_gap_after_meal))

        free: list[tuple[int, int]] = []
        cursor = 0
        for busy_start, busy_end in sorted(busy):
            if busy_start > cursor:
                free.append((cursor, busy_start))
            cursor = max(cursor, busy_end)
        if cursor < constraints.available_minutes:
            free.append((cursor, constraints.available_minutes))
        return free

    def _fit(
        self,
        task: CareTask,
        free: list[tuple[int, int]],
        constraints: DayConstraints,
    ) -> ScheduledTask | None:
        """Place ``task`` at the start of the first gap long enough, or None.

        Returning None is how a failure to place is reported, so the caller
        owns the decision of which SkipReason applies. Spacing rules such as
        the gap after a meal are already folded into ``free``.
        """
        for gap_start, gap_end in free:
            if gap_start + task.duration_minutes <= gap_end:
                return self._place(task, gap_start, constraints)
        return None

    def _offset(self, moment: datetime, start_of_day: datetime) -> int:
        """Whole minutes from ``start_of_day`` to ``moment``."""
        return int((moment - start_of_day).total_seconds() // 60)

    def _place(
        self, task: CareTask, offset_minutes: int, constraints: DayConstraints
    ) -> ScheduledTask:
        """Build a ScheduledTask starting ``offset_minutes`` into the day.

        Pure construction: it assumes the slot was already checked, and fills
        in the per task ``reason`` string explaining the placement.
        """
        start = constraints.to_datetime(offset_minutes)
        end = constraints.to_datetime(offset_minutes + task.duration_minutes)
        if task.fixed_time is not None:
            reason = f"it is fixed at {task.fixed_time:%H:%M}"
        else:
            reason = (
                f"{task.priority.name.lower()} priority and "
                f"{task.duration_minutes} min fitted the first free gap"
            )
        return ScheduledTask(task=task, start=start, end=end, reason=reason)

    def _find_conflict(
        self, candidate: ScheduledTask, placed: list[ScheduledTask]
    ) -> ScheduledTask | None:
        """Return the first placed entry ``candidate`` overlaps, or None.

        Intervals are half open: a task ending at 09:00 and one starting at
        09:00 do not conflict. Returning the entry lets the deferral say what
        the slot was lost to.
        """
        for entry in placed:
            if candidate.start < entry.end and entry.start < candidate.end:
                return entry
        return None


# The course brief calls this class ``Task``; ``CareTask`` is the name used
# throughout the code and the UML. This alias lets either name be imported.
Task = CareTask
