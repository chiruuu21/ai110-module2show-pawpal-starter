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

    def is_due_on(self, day: date) -> bool:
        """Return True if the task should be considered for ``day``."""
        if self.is_done_on(day):
            return False
        return self.recurrence.is_due_on(day)

    def mark_complete(self, day: date) -> None:
        """Record that this task was done on ``day``.

        Takes the date explicitly so completing today's instance never removes
        the task from tomorrow's plan.
        """
        self.completed_on.add(day)


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
    ) -> DailyPlan:
        """Produce a DailyPlan for ``constraints.day``.

        Pipeline: filter to what is due today, expand repeats, sort, anchor the
        fixed time tasks, then fit the flexible ones into the remaining gaps.
        Every task that is not placed lands in ``deferred`` with a SkipReason,
        so nothing is dropped silently.
        """
        day = constraints.day
        plan = DailyPlan(day=day, constraints=constraints)

        due = self._due_today(tasks, day)
        due_identities = {id(task) for task in due}
        for task in tasks:
            if id(task) in due_identities:
                continue
            if task.is_done_on(day):
                plan.deferred.append(
                    DeferredTask(task, SkipReason.ALREADY_COMPLETED)
                )
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
            free = self._free_intervals(plan.entries, constraints)
            entry = self._fit(task, free, plan.entries, constraints)
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
            if self._has_conflict(entry, placed):
                rejected.append(
                    DeferredTask(
                        task,
                        SkipReason.TIME_CONFLICT,
                        f"{task.fixed_time:%H:%M} is already taken",
                    )
                )
                continue
            placed.append(entry)
        return placed, rejected

    def _free_intervals(
        self, placed: list[ScheduledTask], constraints: DayConstraints
    ) -> list[tuple[int, int]]:
        """Return the gaps left by ``placed``, as (start, end) minute offsets.

        With nothing anchored this is a single interval covering the whole
        budget. Each anchored task splits the interval it falls inside.
        """
        start_of_day = constraints.start_datetime()
        busy = sorted(
            (self._offset(entry.start, start_of_day), self._offset(entry.end, start_of_day))
            for entry in placed
        )
        free: list[tuple[int, int]] = []
        cursor = 0
        for busy_start, busy_end in busy:
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
        placed: list[ScheduledTask],
        constraints: DayConstraints,
    ) -> ScheduledTask | None:
        """Find a gap for ``task`` and return the placement, or None.

        Returning None is how a failure to place is reported, so the caller
        owns the decision of which SkipReason applies and the fitting rule
        lives in exactly one place.

        Respects ``min_gap_after_meal``: a walk cannot start until that many
        minutes after a feeding ends.
        """
        start_of_day = constraints.start_datetime()
        for gap_start, gap_end in free:
            start = gap_start
            # Pushing past one meal can land inside the window of a later one,
            # so repeat until the start stops moving. Each pass only moves it
            # forward, so this terminates.
            for _ in range(len(placed) + 1):
                meal = self._last_meal_before(placed, start, start_of_day)
                if meal is None:
                    break
                earliest = self._offset(meal.end, start_of_day) + self._required_gap(
                    task, meal, constraints
                )
                if start >= earliest:
                    break
                start = earliest
            if start + task.duration_minutes <= gap_end:
                return self._place(task, start, constraints)
        return None

    def _last_meal_before(
        self,
        placed: list[ScheduledTask],
        offset_minutes: int,
        start_of_day: datetime,
    ) -> ScheduledTask | None:
        """The latest feeding finishing at or before ``offset_minutes``.

        Looking only at the immediately preceding entry is not enough: a short
        unrelated task slotted after a meal would otherwise reset the gap and
        let a walk start minutes after breakfast.
        """
        best: ScheduledTask | None = None
        best_end = -1
        for entry in placed:
            if entry.task.category is not TaskCategory.FEEDING:
                continue
            end = self._offset(entry.end, start_of_day)
            if best_end < end <= offset_minutes:
                best, best_end = entry, end
        return best

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

    def _required_gap(
        self,
        task: CareTask,
        previous: ScheduledTask | None,
        constraints: DayConstraints,
    ) -> int:
        """Minutes that must separate ``task`` from ``previous``.

        Returns ``min_gap_after_meal`` when a walk follows a feeding, else 0.
        """
        if previous is None or task.category is not TaskCategory.WALK:
            return 0
        if previous.task.category is not TaskCategory.FEEDING:
            return 0
        return constraints.min_gap_after_meal

    def _has_conflict(
        self, candidate: ScheduledTask, placed: list[ScheduledTask]
    ) -> bool:
        """Return True if ``candidate`` overlaps anything already placed.

        Intervals are half open: a task ending at 09:00 and one starting at
        09:00 do not conflict.
        """
        return any(
            candidate.start < entry.end and entry.start < candidate.end
            for entry in placed
        )


# The course brief calls this class ``Task``; ``CareTask`` is the name used
# throughout the code and the UML. This alias lets either name be imported.
Task = CareTask
