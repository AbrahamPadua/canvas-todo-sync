# Canvas + Gradescope to Microsoft To Do

Pulls your UCSD Canvas and Gradescope work into Microsoft To Do and keeps it current. New assignments appear on their own, due date changes follow, and anything you submit gets checked off. An assignment that shows up on both Canvas and Gradescope becomes one task, and each course's tasks can land in the list that lives in that course's To Do group.

It runs on your own computer every couple of hours. Setup takes about 20 minutes, once.

## 1. Put the folder in place

On a Mac, keep it in your home folder (for example `~/canvas-todo-sync`), not Desktop, Documents or Downloads. macOS blocks background jobs from reading those three folders.

```bash
cd ~/canvas-todo-sync
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\python` wherever this guide says `.venv/bin/...`, and `copy` instead of `cp`.

## 2. Canvas access token

1. Go to canvas.ucsd.edu, then **Account > Settings**.
2. Under **Approved Integrations**, click **+ New Access Token**. Purpose: `To Do sync`. Leave the expiry blank or set it to the end of the year.
3. Copy the token (Canvas shows it only once) into `canvas_token` in `config.json`.

The token can read everything your Canvas login can, so treat `config.json` like a password file and never share it.

**No "New Access Token" button?** Some schools turn it off. Use the calendar feed instead: in Canvas open **Calendar**, click **Calendar Feed** in the right sidebar, copy the URL into `canvas_ics_url`, and leave `canvas_token` as it is. The feed works, but it has no submission status, so Canvas tasks won't check themselves off and only assignments come through.

## 3. Gradescope (optional)

Gradescope has no official API for students, so the sync signs in to gradescope.com as you and reads your course pages, the same pages you see. That needs an email and password.

1. If you normally sign in to Gradescope through UCSD single sign-on, give your account a password first: on gradescope.com click **Log In > Forgot your password?** and enter your @ucsd.edu email. The emailed link sets a password on the same account.
2. Put that email and password in `gradescope_email` and `gradescope_password` in `config.json`. Use a password you don't use anywhere else, since it sits in this file.

The sync reads only your current term's courses: one sign-in plus one page per course each run. Because this is unofficial, a Gradescope site redesign can break it. If that happens, Canvas keeps syncing and `sync.log` says Gradescope was skipped.

## 4. Check what comes in, and route courses to your lists

```bash
.venv/bin/python canvas_todo_sync.py --preview
```

This lists your upcoming work (C = Canvas, GS = Gradescope, C+GS = the same assignment on both, kept as one task), the list each item will go to, and the courses it found. Nothing goes to Microsoft yet.

**Routing into your To Do groups.** Microsoft's To Do API can see your lists but not the groups they sit in. So each course is routed to a list by name: the list inside that course's group. At the end of the preview there is a ready-made `"courses"` block. Paste it into `config.json` and set each `todo_list` to the exact name of that course's list:

```json
"courses": {
  "COGS 118B": {"todo_list": "COGS 118B"},
  "ECE 161A":  {"todo_list": "ECE 161A Homework"},
  "MATH 180A": {"todo_list": "MATH 180A"}
}
```

- List names must be unique across all your groups. If every group has a list called "Homework", rename them (for example "COGS 118B Homework"); the sync can't tell identically named lists apart and will skip those courses until you do.
- A course with no `todo_list` goes to the `list_name` list (default `Canvas`), with the course name in front of each title.
- Course names are matched between Canvas and Gradescope by course code, so "FA26 COGS 118B A00" on Canvas and "COGS 118B" on Gradescope are the same course. If a code can't be read, the preview shows the course twice; tie them together by adding the raw names: `"COGS 118B": {"todo_list": "...", "canvas": "<Canvas name>", "gradescope": "<Gradescope name>"}`.
- To ignore a course entirely: `"PHYS 2A": {"skip": true}`.

## 5. Microsoft app registration

Microsoft only lets apps write to To Do if they are registered, and registration needs a Microsoft "directory". Personal Microsoft accounts don't come with one, so you create a free one first.

**Get a directory (skip if you already have an Azure account):**

1. Go to azure.microsoft.com/free/students.
2. Sign in with your **personal** Microsoft account (Outlook, Hotmail, Live), not your UCSD login, and verify student status with your @ucsd.edu email. No credit card is needed.

Why personal: if you sign in with your UCSD account, the app lands inside UCSD's directory, where students usually aren't allowed to register apps.

**Register the app:**

1. Go to entra.microsoft.com, open **App registrations** (type it in the search bar if it isn't in the menu), and click **New registration**.
2. Name: `Canvas To Do Sync`.
3. Supported account types: the option for **any organizational directory and personal Microsoft accounts**.
4. Leave Redirect URI empty and click **Register**.
5. On the app's Overview page, copy **Application (client) ID** into `microsoft_client_id` in `config.json`.
6. Open **Authentication**, find **Allow public client flows** (under Advanced settings or the Settings tab), set it to **Yes**, and save.
7. Open **API permissions > Add a permission > Microsoft Graph > Delegated permissions**, tick **Tasks.ReadWrite**, and click **Add permissions**.

You don't need a client secret or admin consent.

## 6. Sign in, check routing, sync

```bash
.venv/bin/python canvas_todo_sync.py --login
```

It prints a short code. Open microsoft.com/devicelogin, enter the code, and sign in with the account your To Do app uses. Then check that every course points at a real list:

```bash
.venv/bin/python canvas_todo_sync.py --lists
```

It prints your lists, marks which course each one receives, and flags names that are used twice or `todo_list` names that don't exist. When that looks right:

```bash
.venv/bin/python canvas_todo_sync.py
```

## 7. Make it automatic

```bash
.venv/bin/python canvas_todo_sync.py --install-schedule
```

It now syncs every 2 hours while your computer is awake (a Mac LaunchAgent, a Windows scheduled task, or cron on Linux). Activity is logged to `sync.log` in this folder. To stop it: `--uninstall-schedule`.

## How it behaves

**What comes in.** From Canvas: assignments, quizzes, graded discussions, peer reviews, pages your instructor marked as to-dos, and your own Canvas planner notes. From Gradescope: every assignment with a due date in your current-term courses. Both cover 3 days ago through the next 120 days. Announcements and calendar events (lectures, office hours) are skipped. Anything already submitted, on either site, is not added.

**No duplicates.** A Gradescope assignment that matches a Canvas one in the same course becomes a single task with links to both. Matching compares titles ("Homework 3" = "HW3", "Problem Set 2" = "PS 2"), requires the numbers and part letters to agree ("HW 3 Part A" never matches "HW 3 Part B"), and checks that the due dates are within 36 hours. Submitting on Gradescope checks the task off even if Canvas doesn't know yet. If an instructor posts a Canvas copy of a Gradescope assignment later, the existing task is reused.

**What a task looks like.** The due date is the real due date, a reminder fires 24 hours before the deadline, and the note holds the exact due time and links to Canvas and/or Gradescope. Something due at 12:00 AM Thursday shows as due Wednesday, since that is when you actually have to finish it.

**On later runs**, new work is added, changed due dates and titles are updated, and work you submit is checked off.

**Your changes in To Do win:**

- A task you delete is never re-added.
- A task you check off stays checked off.
- If you write in a task's note, the sync stops updating that note.
- Moving a task into a list the sync writes to (a course's list or the default list) is fine; it keeps tracking it there. Moving it anywhere else ends tracking.
- The sync never deletes anything in To Do.

Changing a course's `todo_list` affects new tasks. Tasks already created stay where they are; drag them into the new list if you want, and they keep syncing.

## Settings (`config.json`)

| Setting | Default | What it does |
|---|---|---|
| `courses` | `{}` | Per course: `todo_list` (list to send it to), `canvas` / `gradescope` (raw course names, only if auto-matching fails), `skip`. |
| `list_name` | `Canvas` | Default list for courses without a `todo_list`. |
| `list_mode` | `single` | `per_course` creates a list per course (outside any group) for courses without a `todo_list`. |
| `gradescope_email` / `gradescope_password` | empty | Leave empty to skip Gradescope. |
| `skip_courses` | `[]` | Another way to ignore courses: names from `--preview`, or Canvas or Gradescope course IDs. |
| `reminder_hours_before` | `24` | Hours before the deadline for the To Do reminder. `0` turns reminders off. |
| `lookback_days` / `lookahead_days` | `3` / `120` | How far back and ahead to look. |
| `sync_every_hours` | `2` | Used by `--install-schedule`. Run it again after changing this. |
| `include_types` | see file | Which Canvas item types to bring in. Add `announcement` or `calendar_event` if you want those too. |
| `timezone` | `America/Los_Angeles` | Used for due dates and reminders. |

## Troubleshooting

- **"Canvas rejected the access token (401)"**: the token expired or was deleted. Make a new one (step 2).
- **"Gradescope login failed"**: wrong email or password, or the account has no password yet (step 3). Canvas still syncs in the meantime.
- **"Couldn't find Gradescope's login form"**: Gradescope changed its site. Canvas still syncs; Gradescope items pause until the script is updated.
- **The same assignment shows twice**: the titles or dates differ too much to match safely (for example "Homework 3" on Canvas and "Week 5 problems" on Gradescope). Delete the extra task; it won't come back.
- **"You have 2 To Do lists named ..."**: rename one of them so every list routed to is unique.
- **"No To Do list named ..."**: the `todo_list` spelling doesn't match. Run `--lists` to see the exact names (capitalization doesn't matter).
- **AADSTS7000218 during `--login`**: "Allow public client flows" is still off (step 5.6).
- **AADSTS700016 or "app not found"**: the client ID is wrong, or the app was registered for a single directory. Register again with the any-directory-plus-personal option.
- **"Need admin approval"**: you signed in with your UCSD account and UCSD blocks outside apps. Sign in with a personal Microsoft account instead (and use To Do on that account).
- **MailboxNotEnabledForRESTAPI**: open To Do once at to-do.office.com with that account, then try again.
- **"Microsoft sign-in has expired"**: run `--login` again. This happens if the sync hasn't run for about 90 days.
- **Mac: `sync.log` never updates**: the folder is in Desktop, Documents or Downloads. Move it to your home folder and run `--install-schedule` again. Startup errors land in `launchd_errors.log`.
- **Deleted `sync_state.json`?** No duplicates: the sync finds its own tasks through a hidden link stored on each one. It does forget which tasks you deleted on purpose, so those can come back once.

## Files

| File | What it is |
|---|---|
| `canvas_todo_sync.py` | The sync. |
| `config.json` | Your settings, Canvas token and Gradescope password. Private. |
| `ms_token_cache.json` | Your Microsoft sign-in. Private. Delete it to sign out. |
| `sync_state.json` | Which Canvas and Gradescope items map to which To Do tasks. |
| `sync.log` | What each run did. |
