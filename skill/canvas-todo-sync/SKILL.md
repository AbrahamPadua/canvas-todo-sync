---
name: canvas-todo-sync
description: Syncs UCSD Canvas and Gradescope assignments into Microsoft To Do, reads Canvas announcements, and answers questions about upcoming coursework, using the canvas_todo_sync tool on the user's computer. Use this whenever the user asks what homework, assignments, quizzes or deadlines are coming up, asks about class announcements or whether anything changed in a course (extensions, moved exams, regrade windows), wants their To Do refreshed or updated from Canvas or Gradescope, wants to set up, check on or fix the Canvas to To Do sync, route a course into a particular To Do list or group, skip a course, or deal with duplicate or missing tasks, even if they don't name the tool.
compatibility: Runs on the user's own computer (macOS, Windows or Linux) with Python 3.9+ and network access to Canvas, Gradescope and Microsoft Graph. A cloud sandbox that can't reach that computer can't run it.
metadata:
  version: "2.2.0"
---

# Canvas + Gradescope to Microsoft To Do

This skill drives a small Python tool that lives on the user's computer. Once set up, a scheduled job syncs every couple of hours with no AI involved. You are here for everything around that: answering "what's due?", checking announcements, syncing on demand, changing settings, setting it up, and troubleshooting.

## Find the tool first

- **Data folder (HOME):** `$CANVAS_TODO_HOME` if set, otherwise `~/canvas-todo-sync` (Windows: `%USERPROFILE%\canvas-todo-sync`). It holds the tool, `config.json`, the Microsoft sign-in, `sync_state.json` and `sync.log`.
- **Python (PY):** `HOME/.venv/bin/python` (Windows: `HOME\.venv\Scripts\python.exe`). Use this interpreter, not the system one, because the dependencies live in that environment.
- **Run:** `PY HOME/canvas_todo_sync.py <flags>`

If `HOME/canvas_todo_sync.py` is missing, ask the user whether they installed it somewhere else before installing it again: a second copy would run a second scheduled sync. If it lives elsewhere, use that folder as HOME (or suggest setting `CANVAS_TODO_HOME`). If it was never installed, run `python3 <this skill folder>/scripts/install.py`, then guide the user through `references/setup.md`. If `--version` reports something older than this skill's `metadata.version`, run install.py again: it upgrades the tool and never touches config, sign-in or state.

The tool needs the network and writes to HOME, which is usually outside the current project. Agents that sandbox commands (Codex does by default) will block that and report connection or permission errors; when that happens, rerun the same command with the user's approval to run outside the sandbox rather than trying workarounds. If your shell can see HOME but has no route to Canvas (for example a VM with a restricted network, which also can't run a Windows `.venv`), give the user the exact command to run in their own terminal and read the output they paste back.

If you cannot run commands on the user's computer at all (for example you are a web chat whose sandbox can't reach it), say so plainly and suggest asking from Claude Code, Codex or Gemini CLI on that computer. Installing into a cloud sandbox doesn't help: it would have no credentials and usually can't reach Canvas.

## Commands

| User wants | Run | Notes |
|---|---|---|
| What's due / what's left this week | `--preview --json` | Read-only. Never changes To Do. |
| Sync now | (no flags) | Last log line summarizes added / updated / completed. |
| Is it working? When did it last run? | `--status` | No network needed. Start every troubleshooting session here. |
| Which list does each course go to | `--lists` | Needs Microsoft sign-in. |
| First-time Microsoft sign-in | `--login` | Interactive, see below. |
| Turn the automatic sync on or off | `--install-schedule` / `--uninstall-schedule` | Only when the user asks. |
| Check announcements / did anything change | `--announcements --json` | Read-only. Follow `references/announcements.md`. |
| Add a deadline or change a task from an announcement | `--add-task ...` / `--update-task ...` | Changes To Do. See `references/announcements.md`. |

## Answering "what's due"

Run `--preview --json`. It prints one JSON object on stdout (log lines go to stderr):

- `items[]`: `course`, `title`, `due` (ISO, local time zone), `due_local` (e.g. "Wed Oct 14, 11:59 PM"), `complete`, `source` (`Canvas`, `Gradescope`, or `Canvas+Gradescope` when both sites have it), `url`, `todo_list`.
- `warnings[]`: for example Gradescope being skipped. Mention these, since the answer may be missing Gradescope work.
- `courses[]`: the canonical course names and where each routes.

Answer the question asked. Default to the next 7 days if no range is given, leave out `complete` items unless asked, group by day, and give the exact due time: the To Do app shows only dates, so the time is the detail people can't see there. Don't run a full sync just to answer a question; the preview is current and has no side effects.

## Announcements

When the user asks about announcements or whether something changed in a class, read `references/announcements.md` and follow it: read recent announcements, work out which ones change a deadline or add one, propose the To Do changes, and apply them with `--add-task` / `--update-task` once the user agrees.

When answering "what's due" for the coming week, it's worth a quick `--announcements --json --days 7` too, since an extension posted only in an announcement changes the answer. Mention such changes; don't apply them unasked.

## Syncing on demand

Run with no flags. Report the summary line in one sentence (for example "Added 2, checked off 1"). Exit code 1 means it stopped on an error: read the error line and match it in `references/troubleshooting.md`.

## Changing settings

Settings live in `HOME/config.json`. Read it, change only the keys involved, keep it valid JSON, then verify: `--lists` after routing changes, `--preview` after anything that changes which items come in. `references/config.md` documents every setting. The common ones:

- **Route a course to a list in one of the user's To Do groups:** set `courses["<course>"].todo_list` to the list's exact name. Microsoft's To Do API sees lists, not groups, so the list name is the handle, and it must be unique across all groups. Course names are the canonical ones `--preview` shows (for example `COGS 118B`).
- **Ignore a course:** `courses["<course>"].skip = true`.
- **Same course shows up twice** (Canvas and Gradescope names didn't auto-match): add `"canvas": "<Canvas name>"` and `"gradescope": "<Gradescope name>"` to that course's entry, using the raw names from `--preview --json` `courses[]`.

Routing changes apply to new tasks. Existing tasks stay put; the user can drag them into the new list in To Do and they keep syncing there.

## Secrets and side effects

- `config.json` holds the Canvas token and the Gradescope password, and `ms_token_cache.json` holds the Microsoft sign-in. Don't print, quote, copy, commit or upload them, and don't copy them into this skill folder (skill folders get shared and synced). When a credential needs entering or replacing, have the user paste it into `config.json` themselves rather than into the chat, since conversations are stored and a local file is not.
- `--login` prints a URL and a short code, then waits while the user signs in with a browser. Relay both and wait for the command to finish. If your shell can't keep an interactive command running, ask the user to run `--login` in their own terminal.
- Leave `sync_state.json` alone unless the user asks for a reset. It remembers which tasks they deleted on purpose; without it, those come back once.
- `--install-schedule` and `--uninstall-schedule` change the computer's scheduled jobs. Run them only when the user asks.
- `--add-task` and `--update-task` change the user's To Do. Propose first and apply once they agree, unless they already asked you to update things.
- The tool never deletes To Do tasks. Keep it that way.

## Setup and troubleshooting

- New setup: `references/setup.md` (Canvas token, Gradescope password, Microsoft app registration, routing, schedule). The user has to do the browser steps themselves; walk them through one step at a time and check each with `--status` or `--preview` before moving on.
- Errors: run `--status`, then find the message in `references/troubleshooting.md`.
