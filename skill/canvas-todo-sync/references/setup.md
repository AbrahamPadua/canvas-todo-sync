# Setup guide

One-time setup, about 20 minutes. The browser steps (Canvas token, Gradescope password, Microsoft app registration, sign-in) have to be done by the user; an agent can run the commands and check each step.

All commands run from the data folder:

```bash
cd ~/canvas-todo-sync          # or $CANVAS_TODO_HOME
```

On Windows use `.venv\Scripts\python` where this guide says `.venv/bin/python`.

## 1. Install

From the skill folder: `python3 scripts/install.py`. It creates `~/canvas-todo-sync` with the tool, a Python environment and a `config.json` to fill in, and makes the skill visible to Claude Code, Codex and Gemini CLI. Running it again later upgrades the tool without touching your settings.

On a Mac, keep the data folder out of Desktop, Documents and Downloads: macOS blocks background jobs from reading those.

## 2. Canvas access token

1. Go to canvas.ucsd.edu, then **Account > Settings**.
2. Under **Approved Integrations**, click **+ New Access Token**. Purpose: `To Do sync`. Leave the expiry blank or set it to the end of the year.
3. Paste the token (Canvas shows it only once) into `canvas_token` in `config.json`.

The token can read everything your Canvas login can, so treat `config.json` like a password file.

**No "New Access Token" button?** Some schools turn it off. Use the calendar feed instead: in Canvas open **Calendar**, click **Calendar Feed** in the right sidebar, paste the URL into `canvas_ics_url`, and leave `canvas_token` as it is. The feed has no submission status, so Canvas tasks won't check themselves off, and only assignments come through.

## 3. Gradescope (optional)

Gradescope has no official API for students, so the tool signs in to gradescope.com as you and reads your current-term course pages: one sign-in plus one page per course per run.

1. If you normally sign in through UCSD single sign-on, give your account a password first: on gradescope.com click **Log In > Forgot your password?** and enter your @ucsd.edu email. The emailed link sets a password on the same account.
2. Put the email and password in `gradescope_email` and `gradescope_password`. Use a password you don't use anywhere else.

Because this is unofficial, a Gradescope redesign can break it. If that happens, Canvas keeps syncing and the log says Gradescope was skipped.

## 4. Check what comes in, and route courses

```bash
.venv/bin/python canvas_todo_sync.py --preview
```

This lists upcoming work (C = Canvas, GS = Gradescope, C+GS = the same assignment on both, kept as one task), the list each item goes to, and the courses found. Nothing is sent to Microsoft.

To send each course to the list inside its To Do group, copy the `"courses"` block printed at the end into `config.json` and set each `todo_list` to that list's exact name. See `config.md` for details. List names must be unique across groups.

## 5. Microsoft app registration

Microsoft only lets apps write to To Do if they're registered, and registration needs a Microsoft "directory". Personal Microsoft accounts don't come with one, so create a free one first.

**Get a directory (skip if you already have an Azure account):**

1. Go to azure.microsoft.com/free/students.
2. Sign in with your **personal** Microsoft account (Outlook, Hotmail, Live), not your UCSD login, and verify student status with your @ucsd.edu email. No credit card is needed.

Signing in with the UCSD account would put the app inside UCSD's directory, where students usually aren't allowed to register apps.

**Register the app:**

1. Go to entra.microsoft.com, open **App registrations** (use the search bar if it isn't in the menu), and click **New registration**.
2. Name: `Canvas To Do Sync`.
3. Supported account types: the option for **any organizational directory and personal Microsoft accounts**.
4. Leave Redirect URI empty and click **Register**.
5. On the Overview page, copy **Application (client) ID** into `microsoft_client_id` in `config.json`.
6. Open **Authentication**, find **Allow public client flows** (under Advanced settings or the Settings tab), set it to **Yes**, and save.
7. Open **API permissions > Add a permission > Microsoft Graph > Delegated permissions**, tick **Tasks.ReadWrite**, and click **Add permissions**.

No client secret or admin consent is needed.

## 6. Sign in, check routing, sync

```bash
.venv/bin/python canvas_todo_sync.py --login
```

It prints a short code. Open microsoft.com/devicelogin, enter the code, and sign in with the account your To Do app uses. Then:

```bash
.venv/bin/python canvas_todo_sync.py --lists     # every course points at a real, uniquely named list?
.venv/bin/python canvas_todo_sync.py             # first sync
```

## 7. Make it automatic

```bash
.venv/bin/python canvas_todo_sync.py --install-schedule
```

It syncs every 2 hours while the computer is awake (a Mac LaunchAgent, a Windows scheduled task, or cron on Linux) and logs to `sync.log`. `--status` shows whether it's running; `--uninstall-schedule` stops it.
