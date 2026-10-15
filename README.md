# Canvas + Gradescope to Microsoft To Do

This tool copies your UCSD Canvas and Gradescope assignments into Microsoft To Do and keeps them current. It adds new work as it's posted, follows due date changes, and checks off what you submit. An assignment that exists on both Canvas and Gradescope becomes one task, and each course can land in the list inside that course's To Do group.

The sync runs on your computer on a schedule, with no AI involved. An AI agent can also drive the tool to answer "what's due?" or to read Canvas announcements and update tasks (see [Using it with an AI agent](#using-it-with-an-ai-agent)). Setup takes about 20 minutes, once.

## Setup

### 1. Put the folder in place

Keep this folder at `~/canvas-todo-sync` (on Windows, `C:\Users\<you>\canvas-todo-sync`), which is where the agent skill looks for it. If you keep it somewhere else, set the `CANVAS_TODO_HOME` environment variable to its path.

Avoid OneDrive, iCloud Drive, Dropbox and other synced folders: `config.json` and your Microsoft sign-in would get uploaded with everything else. On a Mac, also avoid Desktop, Documents and Downloads, because macOS blocks background jobs from reading them.

```bash
cd ~/canvas-todo-sync
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\python` wherever this guide says `.venv/bin/...`, and `copy` instead of `cp`.

### 2. Canvas access token

1. Go to canvas.ucsd.edu, then **Account > Settings**.
2. Under **Approved Integrations**, click **+ New Access Token**. Purpose: `To Do sync`. Leave the expiry blank or set it to the end of the year.
3. Copy the token into `canvas_token` in `config.json`. Canvas shows it only once.

The token can read everything your Canvas login can, so treat `config.json` like a password file and never share it.

Some schools turn off the **New Access Token** button. In that case, use the calendar feed: in Canvas open **Calendar**, click **Calendar Feed** in the right sidebar, copy the URL into `canvas_ics_url`, and leave `canvas_token` as it is. The feed lists only assignments and leaves out submission status and announcements, so Canvas tasks won't check themselves off and `--announcements` won't work.

### 3. Gradescope (optional)

Gradescope has no official API for students, so the tool signs in to gradescope.com as you and reads the same course pages you see. That takes an email and password.

1. If you normally sign in to Gradescope through UCSD single sign-on, give your account a password first: on gradescope.com click **Log In > Forgot your password?** and enter your @ucsd.edu email. The emailed link sets a password on the same account.
2. Put that email and password in `gradescope_email` and `gradescope_password` in `config.json`. Use a password you don't use anywhere else, since it sits in this file.

Each run reads only your current term, with one sign-in plus one page per course. A Gradescope redesign can break this part. If that happens, Canvas keeps syncing and `sync.log` says Gradescope was skipped.

### 4. Check what comes in

```bash
.venv/bin/python canvas_todo_sync.py --preview
```

This prints your upcoming work, the list each item will go to, and the courses it found. Nothing is sent to Microsoft. In the source column, C is Canvas, GS is Gradescope, and C+GS is one assignment found on both sites, which becomes a single task.

### 5. Route courses to your To Do lists

Microsoft's To Do API can see your lists but not the groups they sit in, so each course is routed to a list by name: the list inside that course's group. The preview ends with a ready-made `"courses"` block. Paste it into `config.json` and set each `todo_list` to the exact name of that course's list:

```json
"courses": {
  "COGS 118B": {"todo_list": "COGS 118B"},
  "ECE 161A":  {"todo_list": "ECE 161A Homework"},
  "MATH 180A": {"todo_list": "MATH 180A"}
}
```

- List names have to be unique across all your groups. If every group has a list called "Homework", rename them (say, "COGS 118B Homework"). The tool can't tell identically named lists apart, so it skips those courses until you do.
- A course without a `todo_list` goes to the `list_name` list (`Canvas` by default), with the course name in front of each title.
- Canvas and Gradescope courses are matched by course code, so "FA26 COGS 118B A00" on Canvas and "COGS 118B" on Gradescope count as one course. When a code can't be read, the preview lists the course twice. Tie the two names together like this: `"COGS 118B": {"todo_list": "...", "canvas": "<Canvas name>", "gradescope": "<Gradescope name>"}`.
- To ignore a course entirely, add `"PHYS 2A": {"skip": true}`.

### 6. Microsoft app registration

Microsoft only lets registered apps write to To Do, and registering an app needs a Microsoft "directory". Personal Microsoft accounts don't come with one, so you create a free one first.

#### Get a directory

Skip this if you already have an Azure account.

1. Go to azure.microsoft.com/free/students.
2. Sign in with your personal Microsoft account (Outlook, Hotmail, Live) and verify student status with your @ucsd.edu email. No credit card is needed.

Use the personal account here. If you sign in with your UCSD account, the app lands inside UCSD's directory, where students usually aren't allowed to register apps.

#### Register the app

1. Go to entra.microsoft.com, open **App registrations** (type it in the search bar if it isn't in the menu), and click **New registration**.
2. Name: `Canvas To Do Sync`.
3. Supported account types: the option for **any organizational directory and personal Microsoft accounts**.
4. Leave Redirect URI empty and click **Register**.
5. On the app's Overview page, copy **Application (client) ID** into `microsoft_client_id` in `config.json`.
6. Open **Authentication**, find **Allow public client flows** (under Advanced settings or the Settings tab), set it to **Yes**, and save.
7. Open **API permissions > Add a permission > Microsoft Graph > Delegated permissions**, tick **Tasks.ReadWrite**, and click **Add permissions**.

You don't need a client secret or admin consent.

### 7. Sign in, check routing, sync

```bash
.venv/bin/python canvas_todo_sync.py --login
```

It prints a short code. Open microsoft.com/devicelogin, enter the code, and sign in with the account your To Do app uses. Then check that every course points at a real list:

```bash
.venv/bin/python canvas_todo_sync.py --lists
```

This prints your lists, marks which course each one receives, and flags names used twice and `todo_list` names that don't exist. When it looks right, run the first sync:

```bash
.venv/bin/python canvas_todo_sync.py
```

### 8. Make it automatic

```bash
.venv/bin/python canvas_todo_sync.py --install-schedule
.venv/bin/python canvas_todo_sync.py --status
```

The tool now syncs every `sync_every_hours` hours (2 by default) while your computer is awake. It uses a LaunchAgent on a Mac, a scheduled task on Windows, and cron on Linux. `--status` shows the last successful sync, whether the schedule is installed, and the last few log lines. To stop syncing, run `--uninstall-schedule`.

The scheduled job remembers the folder's path. If you move the folder, run `--uninstall-schedule` before the move and `--install-schedule` from the new location.

## How it behaves

### What comes in

From Canvas, the tool takes assignments, quizzes, graded discussions, peer reviews, pages your instructor marked as to-dos, and your own Canvas planner notes. From Gradescope, it takes every assignment with a due date in your current-term courses. Both cover due dates from 3 days ago through the next 120 days. Announcements and calendar events such as lectures and office hours don't become tasks, and anything already submitted on either site is left out.

### Assignments on both sites

A Gradescope assignment that matches a Canvas one in the same course becomes a single task with links to both. Titles are compared loosely ("Homework 3" matches "HW3", "Problem Set 2" matches "PS 2"), but numbers and part letters have to agree, so "HW 3 Part A" never matches "HW 3 Part B". The due dates also have to be within 36 hours of each other. Submitting on Gradescope checks the task off even before Canvas knows. If an instructor later posts a Canvas copy of a Gradescope assignment, the existing task is reused.

### What a task looks like

The task's due date is the assignment's due date, a reminder fires 24 hours before the deadline, and the note holds the exact due time plus links to Canvas, Gradescope or both. To Do only shows dates, so something due at 12:00 AM Thursday is dated Wednesday, the day you actually have to finish it.

### Your changes in To Do win

- A task you delete is never re-added.
- A task you check off stays checked off.
- If you write in a task's note, the sync stops updating that note.
- You can drag a task into any list the sync writes to (a course's list or the default list), and it keeps syncing there. Moving it anywhere else ends tracking.
- The sync never deletes anything in To Do.

Changing a course's `todo_list` affects new tasks only. Existing tasks stay where they are until you drag them.

## Announcements

Instructors often post things in announcements that never show up as a due date, like an extension or a regrade window. The scheduled sync doesn't read announcements. You, or an AI agent using the skill, read them and add or change tasks by hand:

```bash
.venv/bin/python canvas_todo_sync.py --announcements                 # last 14 days, new ones marked NEW
.venv/bin/python canvas_todo_sync.py --announcements --json --days 30
.venv/bin/python canvas_todo_sync.py --add-task "HW 1 regrade requests" --course "ECE 161A" --due "2026-10-16 23:59" --note "Submit on Gradescope."
.venv/bin/python canvas_todo_sync.py --update-task "HW 2" --course "ECE 161A" --due 2026-10-19 --note "Extended per announcement"
.venv/bin/python canvas_todo_sync.py --update-task "Quiz 6" --course "COGS 107A" --done --note "Cancelled"
```

- `--announcements` covers your active courses, minus skipped ones. An announcement is marked new until the first time it's listed. This needs a Canvas token, since the calendar feed has no announcements.
- `--due` takes `YYYY-MM-DD` (meaning 11:59 PM that day) or `"YYYY-MM-DD HH:MM"`, in your time zone. The reminder moves with it.
- `--add-task` puts the task in the course's list, or in the default list with the course name in front. It won't add a second task with the same title, and the scheduled sync never changes tasks added this way.
- `--update-task` matches against task titles. If the text matches more than one open task, it lists them and changes nothing; use more of the title or add `--course`. A due date set this way holds until Canvas or Gradescope changes that assignment's own date. Adding a note makes the note yours, so the sync stops rewriting it.

## All commands

| Command | What it does |
|---|---|
| `--preview` | Lists upcoming work and where each item will go. Read-only. |
| `--preview --json` | The same as JSON, for scripts and agents. Log lines go to stderr. |
| (no flags) | Syncs once. |
| `--status` | Shows the setup, the last successful sync, the schedule and recent log lines. Works offline. |
| `--login` | Signs in to Microsoft. |
| `--lists` | Shows your To Do lists and which course each one receives. |
| `--install-schedule`, `--uninstall-schedule` | Turns the automatic sync on or off. |
| `--announcements [--json] [--days N]` | Lists recent Canvas announcements. |
| `--add-task TITLE --course C [--due D] [--note N] [--link URL] [--json]` | Adds a task. |
| `--update-task MATCH [--course C] [--due D] [--note N] [--done] [--json]` | Changes the task whose title contains MATCH. |
| `--version` | Prints the version. |

## Using it with an AI agent

The `skill/canvas-todo-sync` folder is an Agent Skill in the `SKILL.md` format, which Claude, OpenAI Codex and Gemini CLI all read. With it installed, you can ask an agent "what's due this week?" or "check my announcements" and it runs this tool for you. Install it with:

```bash
python3 skill/canvas-todo-sync/scripts/install.py
```

The installer sets up or upgrades the tool in the data folder (it never touches `config.json`, the sign-in or sync history) and links the skill into `~/.claude/skills` for Claude Code and `~/.agents/skills` for Codex and Gemini CLI. For the Claude apps, zip the `skill/canvas-todo-sync` folder and upload it under Customize > Skills.

The agent needs a shell on this computer that can reach the internet. Web and mobile chat apps run in cloud sandboxes that can't see your computer, so they can't run it.

The skill bundles its own copy of the tool in `skill/canvas-todo-sync/scripts/`, and the installer installs that copy. When you change `canvas_todo_sync.py`, copy it there too and bump `__version__` and the `version` in `SKILL.md`. The installer refuses to replace a newer installed version with an older bundled one.

## Settings (`config.json`)

| Setting | Default | What it does |
|---|---|---|
| `courses` | `{}` | Per course: `todo_list` (the list to send it to), `canvas` and `gradescope` (raw course names, only needed if auto-matching fails), and `skip`. |
| `list_name` | `Canvas` | Default list for courses without a `todo_list`. |
| `list_mode` | `single` | Set to `per_course` to create a separate list (outside any group) for each course without a `todo_list`. |
| `gradescope_email`, `gradescope_password` | empty | Leave empty to skip Gradescope. |
| `skip_courses` | `[]` | Another way to ignore courses: names from `--preview`, or Canvas or Gradescope course IDs. |
| `reminder_hours_before` | `24` | Hours before the deadline for the To Do reminder. `0` turns reminders off. |
| `lookback_days`, `lookahead_days` | `3`, `120` | How far back and ahead to look for due dates. |
| `sync_every_hours` | `2` | Used by `--install-schedule`. Run it again after changing this. |
| `include_types` | see file | Which Canvas item types to bring in. Add `announcement` or `calendar_event` to turn those into tasks too. |
| `timezone` | `America/Los_Angeles` | Used for due dates and reminders. |

## Troubleshooting

Start with `--status`, then look for the message below.

| Message or symptom | Fix |
|---|---|
| `Canvas rejected the access token (401)` | The token expired or was deleted. Make a new one (step 2). |
| `Gradescope login failed` | The email or password is wrong, or the account has no password yet (step 3). Canvas keeps syncing in the meantime. |
| `Couldn't find Gradescope's login form` | Gradescope changed its site. Canvas keeps syncing, and Gradescope items pause until the script is updated. |
| The same assignment shows up twice | The titles or dates differ too much to match safely, for example "Homework 3" on Canvas and "Week 5 problems" on Gradescope. Delete the extra task; it won't come back. |
| `You have 2 To Do lists named ...` | Rename one of the lists so every routed name is unique. |
| `No To Do list named ...` | The `todo_list` spelling doesn't match a list. Run `--lists` to see the exact names (capitalization doesn't matter). |
| `Reading announcements needs a Canvas access token` | You set up with the calendar feed. Create a Canvas token (step 2). |
| `N tasks match ...` from `--update-task` | Use more of the title, or add `--course`. |
| `AADSTS7000218` during `--login` | "Allow public client flows" is still off (step 6, item 6). |
| `AADSTS700016` or "app not found" | The client ID is wrong, or the app was registered for a single directory. Register it again with the any-directory-plus-personal option. |
| "Need admin approval" at sign-in | You signed in with your UCSD account, and UCSD blocks outside apps. Sign in with a personal Microsoft account instead, and use To Do on that account. |
| `MailboxNotEnabledForRESTAPI` | Open To Do once at to-do.office.com with that account, then try again. |
| `Microsoft sign-in has expired` | Run `--login` again. This happens when the sync hasn't run for about 90 days. |
| `--status` shows no recent sync after moving the folder | The schedule still points at the old path. Run `--uninstall-schedule` and `--install-schedule` from the new folder. |
| Mac: `sync.log` never updates | The folder is in Desktop, Documents or Downloads. Move it to your home folder and run `--install-schedule` again. Startup errors land in `launchd_errors.log`. |
| You deleted `sync_state.json` | Nothing gets duplicated, because each task carries a hidden link back to its assignment. The sync does forget which tasks you deleted on purpose, so those can come back once. |

## Files

| File | What it is |
|---|---|
| `canvas_todo_sync.py` | The tool. |
| `config.json` | Your settings, Canvas token and Gradescope password. Private. |
| `ms_token_cache.json` | Your Microsoft sign-in. Private. Delete it to sign out. |
| `sync_state.json` | Which Canvas and Gradescope items map to which To Do tasks, and which tasks you deleted. |
| `due_cache.json` | A snapshot of upcoming work, written after each sync so other tools (a morning brief, for example) can read it offline. No credentials. |
| `announcements_seen.json` | Which announcements have already been listed, so new ones can be marked. |
| `sync.log` | What each run did. |
| `canvas_todo_sync.py.previous` | The version the skill's installer replaced during its last upgrade. |
| `skill/` | The agent skill. |

`.gitignore` keeps the private and generated files out of git.
