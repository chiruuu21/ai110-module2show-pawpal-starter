# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

- Briefly describe your initial UML design.
  My UML design consists of 4 major objects: owner, pet, tasks, and scheduler. 
- What classes did you include, and what responsibilities did you assign to each?
  I built thirteen classes across three layers. The data layer is Owner, Pet, and CareTask, which record what exists, plus two helpers: Recurrence, which answers whether a task is due on a given date, and DayConstraints, which holds the owner's limits for one day. The service layer is a single class, Scheduler, and it is the only place where decisions get made: it filters tasks to what is due today, sorts them by priority and duration, places each one at a concrete time, and checks for overlaps. I gave Scheduler no data of its own so that building a plan is a pure function of its inputs and I can test it without running Streamlit. The result layer is DailyPlan, which holds ScheduledTask entries for what made it in and DeferredTask entries for what got cut, each tagged with a SkipReason. Separating the result from the scheduler mattered most, because explain() reads back the same records the plan was built from and so can never contradict the actual plan. Four enums supply the fixed vocabularies, and I used dataclasses for every data holding type. In short: the data classes know what exists, Scheduler decides, and DailyPlan records what was decided.

**b. Design changes**

- Did your design change during implementation?
- If yes, describe at least one change and why you made it.
Written for: your course instructor, as reflection prose in your own voice.

Yes. My first skeleton tracked completion with a single `is_completed` boolean and placed tasks by advancing one clock cursor from the start of the day, and both broke once I worked through real cases. The boolean meant that marking today's walk as done removed it from every future plan, so I replaced it with a `completed_on` set of dates, which lets a daily task be finished today and still be due tomorrow. The cursor was the bigger problem: because tasks with a fixed time were sorted first, anchoring medication at 18:00 pushed the cursor to 18:20 and every flexible task landed after it, leaving the whole morning empty. I switched to a free interval model where fixed tasks are anchored first and split the day into gaps, and flexible tasks are fitted into whatever gaps remain. I also moved from time to datetime internally after finding that a time object cannot be added to a timedelta and that 23:30 compares as less than 00:30, which quietly defeated my overlap check for anything running past midnight. Writing the edge cases into my docstrings before implementing anything is what surfaced these flaws while they were still cheap to fix.



---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
My scheduler works against a time budget first: each day has a start time and a number of available minutes, and a task is only placed if its duration fits in a remaining gap. Within that budget it considers priority as the main ordering rule, then the owner's preferred order over task categories, then shorter duration, with insertion order as a final tiebreak so the plan never changes between runs. It also respects hard constraints that override preference: a task with a fixed time must sit at that time or be deferred, a task is only considered if its recurrence makes it due today and it is not already done, and a walk cannot begin until the owner's required gap after a meal has passed.

- How did you decide which constraints mattered most?

**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.
- I sorted constraints by asking what would actually harm the pet if the scheduler got it wrong. Fixed times (like medication), the gap between a meal and a walk, and the day's available minutes became hard rules, so the scheduler must obey them or defer the task with a reason. Priority, the owner's preferred category order, and task length only decide the order of everything else. A badly ordered day is just inconvenient, but a missed dose or a walk right after eating can cause real problems.

- Why is that tradeoff reasonable for this scenario?
- When two fixed-time tasks overlap, my scheduler doesn't move either one to a new time. It keeps the task that claimed the slot first, defers the other, and shows a warning naming both tasks. This means a higher-priority task can lose its slot if it starts later, and the owner has to fix the clash by hand. I think that is reasonable because fixed times are usually set on purpose, like a vet call or a medication time. Quietly moving them would be worse than clearly telling the owner there is a conflict.

---

## 3. AI Collaboration

**a. How you used AI**

- How did you use AI tools during this project (for example: design brainstorming, debugging, refactoring)?
- What kinds of prompts or questions were most helpful?

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
- How did you evaluate or verify what the AI suggested?

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
- Why were these tests important?

**b. Confidence**

- How confident are you that your scheduler works correctly?
- What edge cases would you test next if you had more time?

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?
