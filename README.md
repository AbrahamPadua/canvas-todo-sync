# Canvas to Microsoft To Do

Pulls your UCSD Canvas homework into Microsoft To Do and keeps it current. New assignments appear on their own, due date changes follow, and anything you submit in Canvas gets checked off.

It runs on your own computer every couple of hours. Two credentials make it work: a Canvas access token (read your assignments) and a small Microsoft app registration (write to your To Do). Setup takes about 15 minutes, once.

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

**No "New Access Token" button?** Some schools turn it off. Use the calendar feed instead: in Canvas open **Calend  ar**, click **Calendar Feed** in the right sidebar, copy the URL into `canvas_ics_url`, and leave `canvas_token` as it is. The feed works, but it has no submission status, so tasks won't check themselves off and only assignments come through.

## 3. Check what Canvas returns

```bash
.venv/bin/python canvas_todo_sync.py --preview
```

This lists your upcoming work and the course names as they will appear. Nothing goes to Microsoft yet. If a course name is clunky (say `COGS 118B FA26 A00`), shorten it in `config.json`:

```json
"course_aliases": {"COGS 118B FA26 A00": "COGS 118B"}
```

## 4. Microsoft app registration

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

## 5. Sign in and sync

```bash
.venv/bin/python canvas_todo_sync.py --login
```

It prints a short code. Open microsoft.com/devicelogin, enter the code, and sign in with the account your To Do app uses. Then:

```bash
.venv/bin/python canvas_todo_sync.py
```

Open To Do. You should see a **Canvas** list with tasks like `COGS 118B: Problem Set 3`.

## 6. Make it automatic

```bash
.venv/bin/python canvas_todo_sync.py --install-schedule
```

It now syncs every 2 hours while your computer is awake (a Mac LaunchAgent, a Windows scheduled task, or cron on Linux). Activity is logged to `sync.log` in this folder. To stop it: `--uninstall-schedule`.

## How it behaves

What comes in: assignments, quizzes, graded discussions, peer reviews, Canvas pages your instructor marked as to-dos, and your own Canvas planner notes, from 3 days ago through the next 120 days. Announcements and calendar events (lectures, office hours) are skipped. Anything already submitted or marked done in Canvas is not added.

What a task looks like: the title is `Course: Assignment`, the To Do due date is the Canvas due date, a reminder fires 24 hours before the deadline, and the note holds the exact due time, points and a link back to Canvas. Something due at 12:00 AM Thursday shows as due Wednesday, since that is when you actually have to finish it.

On later runs: new Canvas work is added, changed due dates and titles are updated, and work you submit in Canvas is checked off.

Your changes in To Do win:

- A task you delete is never re-added.
- A task you check off stays checked off.
- If you write in a task's note, the sync stops updating that note.
- Moving a task to a different list counts as deleting it; the sync stops tracking it.
- The sync never deletes anything in To Do.

## Settings (`config.json`)

| Setting | Default | What it does |
|---|---|---|
| `list_mode` | `single` | `single` puts everything in one list. `per_course` makes one list per course and drops the course prefix from titles. |
| `list_name` | `Canvas` | Name of the list in `single` mode. |
| `course_aliases` | `{}` | Rename courses. Keys are the names shown by `--preview`. |
| `skip_courses` | `[]` | Course names (as shown by `--preview`) or Canvas course IDs to ignore. |
| `reminder_hours_before` | `24` | Hours before the deadline for the To Do reminder. `0` turns reminders off. |
| `lookback_days` / `lookahead_days` | `3` / `120` | How far back and ahead to look. |
| `sync_every_hours` | `2` | Used by `--install-schedule`. Run it again after changing this. |
| `include_types` | see file | Which Canvas item types to bring in. Add `announcement` or `calendar_event` if you want those too. |
| `timezone` | `America/Los_Angeles` | Used for due dates and reminders. |

## Troubleshooting

- **"Canvas rejected the access token (401)"**: the token expired or was deleted. Make a new one (step 2).
- **AADSTS7000218 during `--login`**: "Allow public client flows" is still off (step 4.6).
- **AADSTS700016 or "app not found"**: the client ID is wrong, or the app was registered for a single directory. Register again with the any-directory-plus-personal option.
- **"Need admin approval"**: you signed in with your UCSD account and UCSD blocks outside apps. Sign in with a personal Microsoft account instead (and use To Do on that account).
- **MailboxNotEnabledForRESTAPI**: open To Do once at to-do.office.com with that account, then try again.
- **"Microsoft sign-in has expired"**: run `--login` again. This happens if the sync hasn't run for about 90 days.
- **Mac: `sync.log` never updates**: the folder is in Desktop, Documents or Downloads. Move it to your home folder and run `--install-schedule` again. Startup errors land in `launchd_errors.log`.
- **Duplicates after deleting `sync_state.json`**: shouldn't happen. The sync finds its own tasks through the hidden Canvas link on each one. The one thing it forgets is which tasks you deleted on purpose, so those can come back once.

## Files

| File | What it is |
|---|---|
| `canvas_todo_sync.py` | The sync. |
| `config.json` | Your settings and Canvas token. Private. |
| `ms_token_cache.json` | Your Microsoft sign-in. Private. Delete it to sign out. |
| `sync_state.json` | Which Canvas items map to which To Do tasks. |
| `sync.log` | What each run did. |
