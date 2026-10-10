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
Yes. My first skeleton tracked completion with a single `is_completed` boolean and placed tasks by advancing one clock cursor from the start of the day, and both broke once I worked through real cases. The boolean meant that marking today's walk as done removed it from every future plan, so I replaced it with a `completed_on` set of dates, which lets a daily task be finished today and still be due tomorrow. The cursor was the bigger problem: because tasks with a fixed time were sorted first, anchoring medication at 18:00 pushed the cursor to 18:20 and every flexible task landed after it, leaving the whole morning empty. I switched to a free interval model where fixed tasks are anchored first and split the day into gaps, and flexible tasks are fitted into whatever gaps remain. I also moved from time to datetime internally after finding that a time object cannot be added to a timedelta and that 23:30 compares as less than 00:30, which quietly defeated my overlap check for anything running past midnight. Writing the edge cases into my docstrings before implementing anything is what surfaced these flaws while they were still cheap to fix.



---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
My scheduler works against a time budget first: each day has a start time and a number of available minutes, and a task is only placed if its duration fits in a remaining gap. Within that budget it considers priority as the main ordering rule, then the owner's preferred order over task categories, then shorter duration, with insertion order as a final tiebreak so the plan never changes between runs. It also respects hard constraints that override preference: a task with a fixed time must sit at that time or be deferred, a task is only considered if its recurrence makes it due today and it is not already done, and a walk cannot begin until the owner's required gap after a meal has passed.

- How did you decide which constraints mattered most?
I sorted constraints by asking what would actually harm the pet if the scheduler got it wrong. Fixed times (like medication), the gap between a meal and a walk, and the day's available minutes became hard rules, so the scheduler must obey them or defer the task with a reason. Priority, the owner's preferred category order, and task length only decide the order of everything else. A badly ordered day is just inconvenient, but a missed dose or a walk right after eating can cause real problems.

**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.
When two fixed-time tasks overlap, my scheduler doesn't move either one to a new time. It keeps the task that claimed the slot first, defers the other, and shows a warning naming both tasks. This means a higher-priority task can lose its slot if it starts later, and the owner has to fix the clash by hand.

- Why is that tradeoff reasonable for this scenario?
I think that is reasonable because fixed times are usually set on purpose, like a vet call or a medication time. Quietly moving them would be worse than clearly telling the owner there is a conflict. The warning appears as soon as the task is saved, so the owner can fix it before relying on the plan.

---

## 3. AI Collaboration

**a. How you used AI**

- How did you use AI tools during this project (for example: design brainstorming, debugging, refactoring)?
I used Claude Code inside VS Code as a working partner through every phase of the project. Early on it helped me turn my UML into class skeletons and talk through edge cases like tasks that run past midnight. Later I used it to plan and draft tests, add sorting, filtering and conflict warnings to the Streamlit UI, update my UML diagram to match the final code, and write the README. I stayed in charge of the decisions and used the AI mostly to move faster and to catch cases I had not thought of.

- What kinds of prompts or questions were most helpful?
The most helpful prompts were specific and tied to my actual code, such as "review pawpal_system.py and list 3 to 5 core behaviors to verify" or "draft tests for sorting, recurrence and conflict detection." Asking it to first explain what it planned to test, before writing anything, helped me check that the plan matched what my scheduler really does. Reporting a concrete bug, like "I added a task at the same time as Breakfast and saw no warning," got a much better fix than a vague request would have. Vague prompts tended to produce longer answers that I then had to trim down.

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
After the AI updated my UI to show conflict warnings, I tested it myself by adding a task for Mochi at 07:00, the same time as Breakfast, and nothing warned me. It turned out the warnings only appeared on the Schedule view and Daily plan tabs, and the success message was being erased when the page reloaded. I reported this, and the fix now shows a red message when a clashing task is saved and keeps the warnings visible on the Tasks tab. I also rejected the first Demo Walkthrough it wrote for my README, because it was far too long for a reader, and had it cut it down to a short five-step workflow.

- How did you evaluate or verify what the AI suggested?
I ran `python -m pytest` after every change to make sure all 100 tests still passed, and I ran `python main.py` to check the output by eye. For UI changes I opened the app with `streamlit run app.py` and clicked through the feature myself, since passing tests do not prove the screen shows the right thing. I also read the new tests to make sure they checked real behavior and did not just repeat what the code already does. When something did not match what I expected, I described exactly what I saw instead of accepting the AI's explanation.

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
My suite has 100 tests covering five main areas. Sorting: tasks come back in clock order with flexible tasks last, ties keep their insertion order, and finished plans never overlap. Recurrence: daily, weekly and one-off rules are due on the right days, and completing a daily task creates exactly one new instance for the next day. Conflict detection: same-time and overlapping tasks are flagged and labelled correctly, while back-to-back tasks are not. I also tested the time budget (a zero-minute day, a task longer than the day, fixed times outside the window) and input validation.

- Why were these tests important?
These are the behaviors that would silently produce a wrong plan without crashing. A bug in recurrence could hide tomorrow's medication, and a bug in conflict detection could double-book the owner without any warning. Because the Scheduler has no data of its own, I could test it with small hand-built task lists, which made each test quick to write and easy to understand.

**b. Confidence**

- How confident are you that your scheduler works correctly?
I would rate my confidence 4 out of 5. All 100 tests pass, and I tested the main features by hand in the app. I held back the last star because the Streamlit UI has no automated tests, and the conflict check assumes a day does not cross midnight.

- What edge cases would you test next if you had more time?
I would test conflict warnings for a day that runs past midnight, for example 23:30 and 00:15 tasks in a 22:00 to 02:00 window. I would also test two pets with the same name, since the task form finds pets by name, and editing a task that has already been completed. Finally, I would add automated UI tests with Streamlit's testing tools so the warnings and filters are checked on every change.

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?
I am most satisfied with how the plan explains itself. Every scheduled task says why it got its slot, and every task that was left out has a reason, so nothing disappears silently. I am also happy with the free-interval placement model, which fixed the bug where one fixed task pushed every flexible task to the end of the day.

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?
I would save the owner's data to a file, because right now everything resets when the browser is refreshed. I would also add a way to delete a pet and have the task form use pet IDs instead of names so duplicate names cannot cause mistakes. On the scheduling side, I would like the app to suggest the nearest free time when two tasks clash instead of only warning about it.

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?
I learned that AI can write code and tests very quickly, but I still have to check its work myself, because passing tests did not catch the missing warning on the Tasks tab. I also learned that good design up front makes everything after it easier. Keeping all the decisions in one Scheduler class with no data of its own made the logic simple to test, simple to reuse in both the app and main.py, and simple to explain to the AI.
