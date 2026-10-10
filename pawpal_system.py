"""PawPal+ core domain model and scheduling engine.

Structure only -- every behavior method raises NotImplementedError for now.
Each docstring states the contract and the edge cases that behavior must handle,
so the implementation step has a spec to work against.

Layers:
    Owner -> Pet -> CareTask        data
    Scheduler                        service (reads data, owns the algorithm)
    DailyPlan -> ScheduledTask       result
              -> DeferredTask
"""

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from uuid import uuid4


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
        return self.value


class TaskCategory(Enum):
    """What kind of care a task is. Drives spacing rules (e.g. walk after feeding)."""

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
    TIME_CONFLICT = "overlaps a fixed-time task"
    ALREADY_COMPLETED = "already marked complete"


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------


@dataclass
class Recurrence:
    """When a task repeats.

    ``weekdays`` uses Python's convention: Monday is 0, Sunday is 6.
    It is only meaningful when ``frequency`` is WEEKLY.
    """

    frequency: Frequency = Frequency.DAILY
    weekdays: list[int] = field(default_factory=list)

    def is_due_on(self, day: date) -> bool:
        """Return True if this recurrence lands on ``day``.

        Edge cases:
            - DAILY is always due.
            - ONCE is treated as due; the caller decides whether it was used.
            - WEEKLY with an empty ``weekdays`` list must not silently mean
              "every day" -- treat it as not due and let the caller report it.
        """
        raise NotImplementedError


@dataclass
class CareTask:
    """A single care activity for one pet."""

    title: str
    duration_minutes: int
    priority: Priority = Priority.MEDIUM
    category: TaskCategory = TaskCategory.OTHER
    fixed_time: time | None = None
    recurrence: Recurrence = field(default_factory=Recurrence)
    is_completed: bool = False
    id: str = field(default_factory=lambda: uuid4().hex[:8])

    def validate(self) -> None:
        """Raise ValueError if this task could never be scheduled sensibly.

        Checks:
            - title is non-empty after stripping whitespace
            - duration_minutes is a positive integer
        """
        raise NotImplementedError

    def is_due_on(self, day: date) -> bool:
        """Return True if the task should be considered for ``day``.

        A completed task is not due. Otherwise defers to ``recurrence``.
        """
        raise NotImplementedError

    def mark_complete(self) -> None:
        """Flag the task as done so it drops out of future plans."""
        raise NotImplementedError


@dataclass
class Pet:
    """A pet profile and the care tasks it needs."""

    name: str
    species: str
    age: int | None = None
    tasks: list[CareTask] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:8])

    def add_task(self, task: CareTask) -> None:
        """Validate and attach a task to this pet."""
        raise NotImplementedError

    def remove_task(self, task_id: str) -> bool:
        """Remove a task by id. Return True if something was removed.

        Removal is by id, not list index -- indexes shift after a delete and
        the Streamlit UI would then edit the wrong row.
        """
        raise NotImplementedError


@dataclass
class Owner:
    """The person doing the caring."""

    name: str
    preferences: dict = field(default_factory=dict)
    pets: list[Pet] = field(default_factory=list)

    def add_pet(self, pet: Pet) -> None:
        """Attach a pet to this owner."""
        raise NotImplementedError

    def all_tasks(self) -> list[CareTask]:
        """Return every task across every pet, flattened."""
        raise NotImplementedError


@dataclass
class DayConstraints:
    """The owner's limits for one day."""

    day: date
    day_start: time
    available_minutes: int
    min_gap_after_meal: int = 0

    def validate(self) -> None:
        """Raise ValueError if available_minutes is negative.

        Zero is legal and must yield an empty plan rather than an error.
        """
        raise NotImplementedError

    def window_end(self) -> time:
        """Return the clock time at which the available window closes.

        Must handle a window that runs past midnight without crashing.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Result layer
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTask:
    """A task placed at a concrete time, with the reason it earned that slot."""

    task: CareTask
    start: time
    end: time
    reason: str = ""


@dataclass
class DeferredTask:
    """A task that was left out, and why."""

    task: CareTask
    reason: SkipReason
    detail: str = ""


@dataclass
class DailyPlan:
    """The output of a scheduling run: what happens, what got cut, and why."""

    day: date
    entries: list[ScheduledTask] = field(default_factory=list)
    deferred: list[DeferredTask] = field(default_factory=list)
    minutes_used: int = 0

    def explain(self) -> str:
        """Return a human-readable justification for the whole plan.

        Must cover both halves: why each entry is placed where it is, and why
        each deferred task was cut. Built from the same data the scheduler
        produced, so the explanation cannot drift from the actual plan.
        """
        raise NotImplementedError

    def to_rows(self) -> list[dict]:
        """Flatten entries into dicts for display in a Streamlit table."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


class Scheduler:
    """Builds a daily plan from tasks plus constraints.

    Holds no data of its own -- tasks are passed in. That keeps ``build_plan``
    a pure function of its inputs, so tests can call it with a hand-built list
    and no Streamlit session involved.
    """

    def build_plan(
        self, tasks: list[CareTask], constraints: DayConstraints
    ) -> DailyPlan:
        """Produce a DailyPlan for ``constraints.day``.

        Pipeline: filter to what is due today, sort, then place each task in
        turn until the time budget runs out. Every task that is not placed
        lands in ``deferred`` with a SkipReason -- nothing is dropped silently.

        Edge cases:
            - empty task list -> empty plan, no error
            - available_minutes == 0 -> everything deferred as NO_TIME_LEFT
            - a task longer than the whole window -> EXCEEDS_DAY_BUDGET, which
              is a different problem from merely running out of room
            - a task that exactly fills the remaining time must still fit
        """
        raise NotImplementedError

    def _due_today(self, tasks: list[CareTask], day: date) -> list[CareTask]:
        """Filter to tasks due on ``day``. Completed and off-schedule tasks drop out."""
        raise NotImplementedError

    def _sort(self, tasks: list[CareTask]) -> list[CareTask]:
        """Order tasks for placement.

        Key: fixed-time tasks first (they cannot move), then priority
        descending, then shorter duration first, then original insertion order
        as the final tiebreak. That last key is what makes repeated runs
        identical -- without it two equal tasks could swap between reruns and
        the tests would be flaky.
        """
        raise NotImplementedError

    def _place(
        self, task: CareTask, cursor: time, constraints: DayConstraints
    ) -> ScheduledTask:
        """Build a ScheduledTask starting at ``cursor`` (or at its fixed_time).

        Also fills in the per-task ``reason`` string explaining the placement.
        """
        raise NotImplementedError

    def _has_conflict(
        self, candidate: ScheduledTask, placed: list[ScheduledTask]
    ) -> bool:
        """Return True if ``candidate`` overlaps anything already placed.

        Intervals are half-open: a task ending at 09:00 and one starting at
        09:00 do not conflict.
        """
        raise NotImplementedError
