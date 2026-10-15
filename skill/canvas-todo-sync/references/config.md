# config.json reference

`config.json` lives in the data folder (`~/canvas-todo-sync` by default). Keep it valid JSON; `--status` and `--preview` will complain if it isn't.

## Routing courses to To Do lists

Microsoft's To Do API can see lists but not the groups they sit in. A course is routed by naming the list inside its group:

```json
"courses": {
  "COGS 118B": {"todo_list": "COGS 118B"},
  "ECE 161A":  {"todo_list": "ECE 161A Homework"},
  "MATH 180A": {"todo_list": "MATH 180A"},
  "PHYS 2A":   {"skip": true}
}
```

- Keys are canonical course names as `--preview` shows them. The tool derives them from course codes, so "FA26 COGS 118B A00" on Canvas and "COGS 118B" on Gradescope both become `COGS 118B`.
- `todo_list` must match an existing list (capitalization doesn't matter). The tool does not create lists named here, so a typo can't scatter tasks into a stray list; those items wait, and the log says why.
- List names must be unique across all groups. If several groups each have a list called "Homework", rename them first; the tool skips ambiguous names.
- Courses without a `todo_list` go to `list_name` (default `Canvas`, created if missing) with the course name in front of each title. Routed tasks drop that prefix.
- If a course's code can't be read and it appears twice, tie the two names together: `"COGS 118B": {"todo_list": "COGS 118B", "canvas": "<Canvas name>", "gradescope": "<Gradescope name>"}`. Raw names are in `--preview --json` under `courses[]`.
- Changing a route affects new tasks. Existing ones stay where they are; dragging them into the new list in To Do is fine, they keep syncing.

## All settings

| Setting | Default | What it does |
|---|---|---|
| `canvas_base_url` | `https://canvas.ucsd.edu` | Canvas site. |
| `canvas_token` | | Canvas access token. Can also come from the `CANVAS_TOKEN` environment variable. |
| `canvas_ics_url` | | Calendar feed URL, used only when there is no token. |
| `gradescope_email` / `gradescope_password` | | Leave empty to skip Gradescope. Password can also come from `GRADESCOPE_PASSWORD`. |
| `microsoft_client_id` | | Application (client) ID of the user's app registration. |
| `courses` | `{}` | Per course: `todo_list`, `canvas`, `gradescope`, `skip`. |
| `list_name` | `Canvas` | Default list for courses without a `todo_list`. |
| `list_mode` | `single` | `per_course` auto-creates one list per unrouted course (outside any group). |
| `skip_courses` | `[]` | Another way to ignore courses: canonical names, raw names, or Canvas/Gradescope course IDs. |
| `reminder_hours_before` | `24` | To Do reminder this many hours before the deadline. `0` turns reminders off. |
| `lookback_days` / `lookahead_days` | `3` / `120` | Window of due dates considered. |
| `sync_every_hours` | `2` | Used by `--install-schedule`; rerun it after changing this. |
| `include_types` | assignment, quiz, discussion_topic, sub_assignment, assessment_request, wiki_page, planner_note | Canvas item types. Add `announcement` or `calendar_event` to include those. |
| `timezone` | `America/Los_Angeles` | Used for due dates and reminders. |

## How syncing decides things

- **De-duplication:** a Gradescope assignment that matches a Canvas one in the same course becomes one task with both links. Titles are compared loosely ("Homework 3" = "HW3", "Problem Set 2" = "PS 2"), numbers and part letters must agree ("HW 3 Part A" never matches "Part B"), and due dates must be within 36 hours unless the titles are identical. Submitting on either site checks the task off.
- **What the user changes in To Do wins:** deleted tasks are never re-added, checked-off tasks stay checked, an edited note stops being updated, and moving a task into any list the tool routes to keeps it tracked. Moving it elsewhere ends tracking.
- **Due dates:** To Do shows dates only. Something due at 12:00 AM Thursday is dated Wednesday. The exact time is in the task note and in the reminder.
