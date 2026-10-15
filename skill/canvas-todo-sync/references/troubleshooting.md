# Troubleshooting

Start with `--status` (setup summary, last successful sync, whether the schedule is loaded, last log lines), then match the message below.

| Message or symptom | Cause | Fix |
|---|---|---|
| `Missing .../config.json` | Not set up, or the agent is looking in the wrong folder | Run `scripts/install.py`; or set `CANVAS_TODO_HOME` to the folder that has `config.json`. |
| `Canvas rejected the access token (401)` | Token expired or deleted | User makes a new token (setup step 2) and pastes it into `config.json`. |
| `Gradescope login failed` | Wrong email/password, or the account only has UCSD single sign-on | User sets a Gradescope password via "Forgot your password?" (setup step 3). Canvas keeps syncing meanwhile. |
| `Couldn't find Gradescope's login form` | Gradescope changed its site | Canvas keeps syncing; Gradescope-only tasks pause. The page-reading code in `canvas_todo_sync.py` (`parse_gradescope_*`) needs updating. |
| `config.json is missing "microsoft_client_id"` | App registration not done | Setup step 5. |
| `AADSTS7000218` during `--login` | "Allow public client flows" is off | Setup step 5.6. |
| `AADSTS700016` / app not found | Wrong client ID, or the app was registered for one directory only | Register again with the any-directory-plus-personal option. |
| "Need admin approval" at sign-in | Signed in with the UCSD account; UCSD blocks outside apps | Use a personal Microsoft account (and To Do on that account). |
| `MailboxNotEnabledForRESTAPI` | Personal account's To Do not initialized for the API | Open to-do.office.com once with that account, then retry. |
| `Microsoft sign-in has expired` | No successful run for about 90 days, or the sign-in was revoked | Run `--login`. |
| `You have N To Do lists named "X"` | The To Do API can't tell same-named lists in different groups apart | Rename the lists so each routed name is unique. |
| `No To Do list named "X"` | `todo_list` typo or list renamed | Run `--lists` to see exact names; fix `config.json`. |
| Same assignment appears twice | Titles or dates differ too much to merge safely (e.g. "Homework 3" vs "Week 5 problems") | Delete the extra task in To Do; it won't come back. |
| A course appears twice in `--preview` | Its Canvas and Gradescope names didn't auto-match | Add `canvas` and `gradescope` names to that course's entry (see `config.md`). |
| Mac: `sync.log` never updates, `--status` says loaded | Data folder is in Desktop, Documents or Downloads | Move it (or set `CANVAS_TODO_HOME`), rerun `--install-schedule`. Startup errors are in `launchd_errors.log`. |
| `Reading announcements needs a Canvas access token` | Set up with the calendar feed, which has no announcements | Create a Canvas token (setup step 2). |
| `N tasks match "..."` from `--update-task` | The text matches several task titles | Use more of the title, or add `--course`. |
| Deleted `sync_state.json` | | No duplicates (each task carries a hidden link back to its assignment), but tasks the user deleted on purpose come back once. |
