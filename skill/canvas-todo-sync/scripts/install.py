#!/usr/bin/env python3
"""
Install or upgrade the canvas_todo_sync tool, and make this skill visible to
Claude Code, OpenAI Codex and Gemini CLI.

    python3 scripts/install.py              install/upgrade + register the skill
    python3 scripts/install.py --no-link    install/upgrade only

What it does (safe to run again any time):
  1. Creates the data folder: $CANVAS_TODO_HOME, or ~/canvas-todo-sync.
  2. Copies the latest canvas_todo_sync.py, requirements.txt and config.example.json there.
     Your config.json, Microsoft sign-in and sync state are never overwritten.
  3. Creates config.json from the template if you don't have one yet (private permissions).
  4. Creates a Python virtual environment (.venv) there and installs the dependencies.
  5. Links this skill folder into ~/.claude/skills (Claude Code) and ~/.agents/skills
     (Codex and Gemini CLI). Existing folders with that name are left alone.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "canvas-todo-sync"
SKILL_DIR = Path(__file__).resolve().parent.parent
BUNDLED = SKILL_DIR / "scripts"
COPY_ALWAYS = ["canvas_todo_sync.py", "requirements.txt", "config.example.json"]


def data_dir() -> Path:
    env = os.environ.get("CANVAS_TODO_HOME")
    return Path(env).expanduser() if env else Path.home() / "canvas-todo-sync"


def venv_python(home: Path) -> Path:
    return home / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def tool_version(path: Path) -> tuple:
    try:
        m = re.search(r'__version__ = "([\d.]+)"', path.read_text(encoding="utf-8"))
    except OSError:
        return ()
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def install_tool(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    installed, bundled = tool_version(home / "canvas_todo_sync.py"), tool_version(BUNDLED / "canvas_todo_sync.py")
    if installed and bundled and installed > bundled:
        print(f"  kept your newer tool ({'.'.join(map(str, installed))}); this skill bundles "
              f"{'.'.join(map(str, bundled))}. Update the skill folder instead.")
        skip = set(COPY_ALWAYS)
    else:
        skip = set()
    for name in COPY_ALWAYS:
        src, dst = BUNDLED / name, home / name
        if name in skip or (dst.exists() and filecmp.cmp(src, dst, shallow=False)):
            continue
        if dst.exists() and name == "canvas_todo_sync.py":
            shutil.copy2(dst, home / (name + ".previous"))
        shutil.copy2(src, dst)
        print(f"  updated {dst}")

    config = home / "config.json"
    if not config.exists():
        shutil.copy2(BUNDLED / "config.example.json", config)
        if os.name != "nt":
            os.chmod(config, 0o600)
        print(f"  created {config} (fill it in: see references/setup.md)")
    else:
        print(f"  kept your {config}")

    py = venv_python(home)
    if not py.exists():
        print("  creating Python environment (.venv)...")
        subprocess.run([sys.executable, "-m", "venv", str(home / ".venv")], check=True)
    print("  installing dependencies...")
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                    "-r", str(home / "requirements.txt")], check=True)


def register_skill() -> None:
    targets = [Path.home() / ".claude" / "skills" / SKILL_NAME,   # Claude Code
               Path.home() / ".agents" / "skills" / SKILL_NAME]   # Codex, Gemini CLI
    for target in targets:
        try:
            if target.exists() and target.resolve() == SKILL_DIR:
                print(f"  skill already visible at {target}")
                continue
            if target.exists() or target.is_symlink():
                print(f"  left alone: {target} already exists (remove it and rerun to replace it)")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.symlink_to(SKILL_DIR, target_is_directory=True)
                print(f"  linked {target} -> {SKILL_DIR}")
            except OSError:  # Windows without symlink rights: copy instead
                shutil.copytree(SKILL_DIR, target, ignore=shutil.ignore_patterns("__pycache__"))
                print(f"  copied skill to {target} (rerun install.py after updating the skill)")
        except OSError as e:
            print(f"  could not register at {target}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-link", action="store_true", help="don't link the skill into agent folders")
    args = ap.parse_args()
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.")
        return 1

    home = data_dir()
    print(f"Tool and data folder: {home}")
    install_tool(home)
    if not args.no_link:
        print("Registering the skill:")
        register_skill()

    py = venv_python(home)
    print(f"\nDone. Run the tool with:\n  {py} {home / 'canvas_todo_sync.py'} --status")
    if sys.platform == "darwin" and any(p in ("Desktop", "Documents", "Downloads") for p in home.parts):
        print("Note: macOS blocks background jobs in Desktop, Documents and Downloads; "
              "set CANVAS_TODO_HOME to a folder outside them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
