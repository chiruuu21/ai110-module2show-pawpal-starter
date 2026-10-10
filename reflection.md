# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

- Briefly describe your initial UML design.
  My UML design consists of 4 major objects: owner, pet, tasks, and scheduler. 
- What classes did you include, and what responsibilities did you assign to each?

**b. Design changes**

- Did your design change during implementation?
- If yes, describe at least one change and why you made it.

Yes. My first skeleton tracked completion with a single `is_completed` boolean and placed tasks by advancing one clock cursor from the start of the day, and both broke once I worked through real cases. The boolean meant that marking today's walk as done removed it from every future plan, so I replaced it with a `completed_on` set of dates and an `is_done_on(day)` check, which lets a daily task be finished today and still be due tomorrow. The cursor was the bigger problem: because tasks with a fixed time were sorted first, anchoring medication at 18:00 pushed the cursor to 18:20 and every flexible task landed after it, leaving the whole morning empty. I switched to a free interval model where fixed tasks are anchored first and split the day into gaps, and the flexible tasks are then fitted into whatever gaps remain. I also moved from `time` to `datetime` internally after discovering that a `time` object cannot be added to a `timedelta` at all, and that 23:30 compares as less than 00:30, which silently defeated my overlap check for anything running past midnight. What I took from this is that writing the edge cases into the docstrings before implementing anything surfaced these flaws while they were still cheap to fix.

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.
- Why is that tradeoff reasonable for this scenario?

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
