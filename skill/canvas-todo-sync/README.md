# canvas-todo-sync skill

An Agent Skill (the open SKILL.md format) that lets Claude, Codex/ChatGPT and Gemini work with your Canvas + Gradescope to Microsoft To Do sync: "what's due this week?", "sync my To Do", "send ECE 161A to my ECE list", "why didn't my quiz show up?".

The sync itself is a Python tool that runs on your computer on a schedule. This skill bundles that tool, installs it, and teaches the agents how to use it.

## Install (one time, all three agents)

```bash
mkdir -p ~/.agents/skills
unzip canvas-todo-sync-skill.zip -d ~/.agents/skills/
python3 ~/.agents/skills/canvas-todo-sync/scripts/install.py
```

`install.py` sets up the tool in `~/canvas-todo-sync` and links the skill where each agent looks:

| Agent | Skill folder |
|---|---|
| Codex CLI / IDE extension (and ChatGPT desktop standalone skills) | `~/.agents/skills/canvas-todo-sync` |
| Gemini CLI | `~/.agents/skills/canvas-todo-sync` (alias of `~/.gemini/skills`) |
| Claude Code (terminal and desktop app) | `~/.claude/skills/canvas-todo-sync` (linked) |

Then follow `references/setup.md` for the credentials, or open any of the agents and say "help me set up canvas-todo-sync".

Agents that sandbox their commands (Codex does by default) will ask before running the tool, since it needs the internet and writes to `~/canvas-todo-sync`. Approve those requests.

## Where it can't run

Web and mobile chat apps (claude.ai on the web, ChatGPT web, the Gemini app) run in cloud sandboxes that can't reach your computer, your credentials, or usually Canvas. You can still upload the skill there, but it will only be able to tell you to use a local agent.

## Updating

Replace the folder with a newer version and run `scripts/install.py` again. Your `config.json`, Microsoft sign-in and sync history in `~/canvas-todo-sync` are kept.
