#!/usr/bin/env python3
"""
canvas_todo_sync.py

Copies your Canvas assignments (and other Canvas to-dos) into Microsoft To Do
and keeps them in sync: new work shows up, due date changes follow, and things
you submit in Canvas get checked off.

Commands
  python3 canvas_todo_sync.py --preview             list what Canvas has (no Microsoft needed)
  python3 canvas_todo_sync.py --login               one-time Microsoft sign-in
  python3 canvas_todo_sync.py                       sync once
  python3 canvas_todo_sync.py --install-schedule    sync automatically every few hours
  python3 canvas_todo_sync.py --uninstall-schedule  stop the automatic sync

Settings live in config.json next to this file (copy config.example.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import logging.handlers
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "sync_state.json"
TOKEN_CACHE_PATH = HERE / "ms_token_cache.json"
LOG_PATH = HERE / "sync.log"

GRAPH = "https://graph.microsoft.com/v1.0"
MS_AUTHORITY = "https://login.microsoftonline.com/common"
MS_SCOPES = ["Tasks.ReadWrite"]
APP_NAME = "Canvas"              # linkedResource.applicationName that marks tasks this script owns
SCHEDULE_LABEL = "canvas-todo-sync"

DEFAULTS = {
    "canvas_base_url": "https://canvas.ucsd.edu",
    "canvas_token": "",
    "canvas_ics_url": "",
    "microsoft_client_id": "",
    "list_mode": "single",          # "single" = one list, "per_course" = one list per course
    "list_name": "Canvas",
    "timezone": "America/Los_Angeles",
    "lookback_days": 3,
    "lookahead_days": 120,
    "reminder_hours_before": 24,     # 0 turns reminders off
    "include_types": ["assignment", "quiz", "discussion_topic", "sub_assignment",
                      "assessment_request", "wiki_page", "planner_note"],
    "skip_courses": [],
    "course_aliases": {},
    "sync_every_hours": 2,
}

# Graph accepts Windows time zone names everywhere; IANA names only in some APIs.
WINDOWS_TZ = {
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Denver": "Mountain Standard Time",
    "America/Phoenix": "US Mountain Standard Time",
    "America/Chicago": "Central Standard Time",
    "America/New_York": "Eastern Standard Time",
    "America/Anchorage": "Alaskan Standard Time",
    "Pacific/Honolulu": "Hawaiian Standard Time",
    "UTC": "UTC",
}

log = logging.getLogger("canvas_todo_sync")


# --------------------------------------------------------------------------- setup

def setup_logging() -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=1, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    if sys.stdout is not None:  # pythonw on Windows has no stdout
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(sh)


def is_set(value) -> bool:
    return bool(value) and not str(value).startswith("PASTE_")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH.name}. Copy config.example.json to config.json and fill it in.")
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    cfg["canvas_token"] = os.environ.get("CANVAS_TOKEN") or cfg.get("canvas_token", "")
    cfg["canvas_base_url"] = cfg["canvas_base_url"].rstrip("/")
    if cfg["list_mode"] not in ("single", "per_course"):
        sys.exit('config.json: "list_mode" must be "single" or "per_course".')
    return cfg


def write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("sync_state.json was unreadable; rebuilding it from To Do.")
    return {"items": {}}


def save_state(state: dict) -> None:
    write_private(STATE_PATH, json.dumps(state, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- Canvas

@dataclass
class Item:
    key: str                  # stable id, e.g. "assignment:12345"
    kind: str
    title: str
    course: str
    due: Optional[datetime]   # timezone-aware
    url: str
    points: Optional[float]
    complete: bool


class SyncError(Exception):
    pass


def parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def course_label(raw: str, cfg: dict) -> str:
    return (cfg.get("course_aliases") or {}).get(raw, raw)


def canvas_get_all(session: requests.Session, url: str, params: Optional[dict] = None) -> list:
    out = []
    while url:
        r = session.get(url, params=params, timeout=30)
        if r.status_code == 401:
            raise SyncError("Canvas rejected the access token (401). It may have expired or been deleted. "
                            "Make a new one in Canvas > Account > Settings > Approved Integrations.")
        r.raise_for_status()
        data = r.json()
        out.extend(data if isinstance(data, list) else [data])
        url = r.links.get("next", {}).get("url")
        params = None  # the "next" link already carries them
    return out


def fetch_from_api(cfg: dict, now: datetime) -> list:
    base = cfg["canvas_base_url"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {cfg['canvas_token']}"

    courses = canvas_get_all(s, f"{base}/api/v1/courses", {"enrollment_state": "active", "per_page": 100})
    course_names = {c["id"]: (c.get("course_code") or c.get("name") or "")
                    for c in courses if isinstance(c, dict) and "id" in c}

    start = (now - timedelta(days=cfg["lookback_days"])).date().isoformat()
    end = (now + timedelta(days=cfg["lookahead_days"])).date().isoformat()
    raw = canvas_get_all(s, f"{base}/api/v1/planner/items",
                         {"start_date": start, "end_date": end, "per_page": 100})

    include, skip = set(cfg["include_types"]), set(cfg["skip_courses"])
    items = []
    for it in raw:
        kind = it.get("plannable_type") or ""
        if kind not in include:
            continue
        p = it.get("plannable") or {}
        pid = it.get("plannable_id") or p.get("id")
        if pid is None:
            continue
        course_id = it.get("course_id") or p.get("course_id")
        course_raw = course_names.get(course_id) or it.get("context_name") or ""
        if course_raw in skip or str(course_id) in skip:
            continue

        title = (p.get("title") or p.get("name") or "Untitled").strip()
        if kind == "assessment_request":
            title = f"Peer review: {title}"
        url = it.get("html_url") or ""
        if url.startswith("/"):
            url = base + url
        if kind == "planner_note" or "/api/" in url:
            url = base + "/"  # notes link to an API URL; send people to the dashboard instead

        subs = it.get("submissions")
        done = isinstance(subs, dict) and any(subs.get(k) for k in ("submitted", "excused", "graded"))
        if (it.get("planner_override") or {}).get("marked_complete"):
            done = True

        items.append(Item(
            key=f"{kind}:{pid}", kind=kind, title=title,
            course=course_label(course_raw, cfg),
            due=parse_iso(p.get("due_at") or p.get("todo_date") or it.get("plannable_date")),
            url=url, points=p.get("points_possible"), complete=bool(done),
        ))
    return items


def _ics_unescape(v: str) -> str:
    return (v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\"))


def parse_ics(text: str) -> list:
    """Minimal iCalendar reader: returns one {NAME: (value, params)} dict per VEVENT."""
    lines = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    events, cur = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            pdict = {}
            for prm in params:
                k, _, v = prm.partition("=")
                pdict[k.upper()] = v.strip('"')
            cur[name.upper()] = (_ics_unescape(value), pdict)
    return events


def ics_datetime(value: str, params: dict, tz: ZoneInfo) -> Optional[datetime]:
    try:
        if params.get("VALUE") == "DATE" or len(value) == 8:
            d = datetime.strptime(value, "%Y%m%d")
            return d.replace(hour=23, minute=59, tzinfo=tz)  # all-day = end of that day
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        zone = ZoneInfo(params["TZID"]) if "TZID" in params else tz
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
    except (ValueError, KeyError):
        return None


def fetch_from_ics(cfg: dict, now: datetime) -> list:
    r = requests.get(cfg["canvas_ics_url"], timeout=30)
    r.raise_for_status()
    tz = ZoneInfo(cfg["timezone"])
    lo = now - timedelta(days=cfg["lookback_days"])
    hi = now + timedelta(days=cfg["lookahead_days"])
    skip = set(cfg["skip_courses"])
    items = []
    for ev in parse_ics(r.text):
        uid = ev.get("UID", ("", {}))[0]
        if "assignment" not in uid:      # lectures, office hours and other calendar events
            continue
        summary = ev.get("SUMMARY", ("", {}))[0].strip()
        m = re.match(r"^(.*?)\s*\[([^\]]+)\]\s*$", summary)
        title, course_raw = (m.group(1), m.group(2)) if m else (summary, "")
        if course_raw in skip:
            continue
        due = ics_datetime(*ev["DTSTART"], tz) if "DTSTART" in ev else None
        if due and not (lo <= due <= hi):
            continue
        items.append(Item(
            key=f"ics:{uid}", kind="assignment", title=title or "Untitled",
            course=course_label(course_raw, cfg), due=due,
            url=ev.get("URL", ("", {}))[0], points=None, complete=False,
        ))
    return items


def fetch_items(cfg: dict, now: datetime) -> tuple:
    if is_set(cfg["canvas_token"]):
        return fetch_from_api(cfg, now), "Canvas API"
    if is_set(cfg["canvas_ics_url"]):
        return fetch_from_ics(cfg, now), "Canvas calendar feed"
    raise SyncError('config.json needs either "canvas_token" or "canvas_ics_url".')


# --------------------------------------------------------------------------- Microsoft

def ms_token(cfg: dict, interactive: bool) -> str:
    import msal  # imported here so --preview works before Microsoft is set up

    if not is_set(cfg["microsoft_client_id"]):
        raise SyncError('config.json is missing "microsoft_client_id" (see README, step 4).')
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(cfg["microsoft_client_id"], authority=MS_AUTHORITY, token_cache=cache)

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
    if not result:
        if not interactive:
            raise SyncError("Microsoft sign-in has expired. Run: python3 canvas_todo_sync.py --login")
        flow = app.initiate_device_flow(scopes=MS_SCOPES)
        if "user_code" not in flow:
            raise SyncError(f"Could not start Microsoft sign-in: {flow.get('error_description', flow)}")
        print("\n" + flow["message"] + "\n")
        result = app.acquire_token_by_device_flow(flow)

    if cache.has_state_changed:
        write_private(TOKEN_CACHE_PATH, cache.serialize())
    if "access_token" not in result:
        raise SyncError(f"Microsoft sign-in failed: {result.get('error_description') or result.get('error')}")
    return result["access_token"]


class GraphError(SyncError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Graph:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.expand_ok = True
        self.writes = 0

    def _req(self, method: str, url: str, **kw) -> requests.Response:
        if not url.startswith("http"):
            url = GRAPH + url
        r = None
        for attempt in range(5):
            r = self.s.request(method, url, timeout=30, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(int(r.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            break
        return r

    @staticmethod
    def _check(r: requests.Response) -> requests.Response:
        if r.status_code >= 400:
            raise GraphError(r.status_code, f"Microsoft Graph {r.request.method} failed "
                                            f"({r.status_code}): {r.text[:300]}")
        return r

    def get_all(self, url: str) -> list:
        out = []
        while url:
            j = self._check(self._req("GET", url)).json()
            out.extend(j.get("value", []))
            url = j.get("@odata.nextLink")
        return out

    def lists(self) -> dict:
        return {l["displayName"]: l["id"] for l in self.get_all("/me/todo/lists")}

    def create_list(self, name: str) -> str:
        self.writes += 1
        return self._check(self._req("POST", "/me/todo/lists", json={"displayName": name})).json()["id"]

    def tasks(self, list_id: str) -> list:
        base = f"/me/todo/lists/{list_id}/tasks"
        queries = ["?$expand=linkedResources&$top=100", "?$expand=linkedResources"] if self.expand_ok else []
        for q in queries:
            try:
                return self.get_all(base + q)
            except GraphError as e:
                if e.status != 400:
                    raise
        if self.expand_ok:
            self.expand_ok = False
            log.warning("Graph would not return linked Canvas info; relying on sync_state.json only.")
        return self.get_all(base + "?$top=100")

    def create_task(self, list_id: str, payload: dict) -> dict:
        self.writes += 1
        return self._check(self._req("POST", f"/me/todo/lists/{list_id}/tasks", json=payload)).json()

    def update_task(self, list_id: str, task_id: str, patch: dict) -> bool:
        """Returns False if the task no longer exists (deleted or moved by you)."""
        self.writes += 1
        r = self._req("PATCH", f"/me/todo/lists/{list_id}/tasks/{task_id}", json=patch)
        if r.status_code == 404:
            return False
        self._check(r)
        return True


# --------------------------------------------------------------------------- task building

def fmt_local(dt: datetime, tz: ZoneInfo) -> str:
    d = dt.astimezone(tz)
    hour = d.hour % 12 or 12
    return f"{d:%a %b} {d.day}, {hour}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"


def due_date_key(item: Item, tz: ZoneInfo) -> Optional[str]:
    if not item.due:
        return None
    d = item.due.astimezone(tz)
    if d.hour == 0 and d.minute == 0:   # "due 12:00 AM Thu" really means "by the end of Wed"
        d -= timedelta(minutes=1)
    return d.date().isoformat()


def task_title(item: Item, cfg: dict) -> str:
    if cfg["list_mode"] == "per_course" or not item.course:
        return item.title[:255]
    return f"{item.course}: {item.title}"[:255]


def body_text(item: Item, tz: ZoneInfo) -> str:
    lines = []
    if item.course:
        lines.append(f"Course: {item.course}")
    if item.due:
        lines.append(f"Due: {fmt_local(item.due, tz)}")
    if item.points:
        lines.append(f"Points: {item.points:g}")
    lines.append(f"Type: {item.kind.replace('_', ' ')}")
    if item.url:
        lines.append(f"Canvas: {item.url}")
    lines += ["", "Added by canvas_todo_sync. If you edit this note, the sync stops updating it."]
    return "\n".join(lines)


def norm_hash(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", text or "").strip().encode()).hexdigest()[:16]


def due_fields(item: Item, cfg: dict, tz: ZoneInfo, graph_tz: str, now: datetime) -> dict:
    key = due_date_key(item, tz)
    if not key:
        return {"dueDateTime": None, "isReminderOn": False}
    out = {"dueDateTime": {"dateTime": f"{key}T00:00:00", "timeZone": graph_tz}, "isReminderOn": False}
    hours = cfg.get("reminder_hours_before") or 0
    if hours > 0:
        remind_at = item.due - timedelta(hours=hours)
        if remind_at > now:
            out["isReminderOn"] = True
            out["reminderDateTime"] = {"dateTime": remind_at.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S"),
                                       "timeZone": graph_tz}
    return out


def new_task_payload(item: Item, cfg: dict, tz: ZoneInfo, graph_tz: str, now: datetime) -> dict:
    payload = {
        "title": task_title(item, cfg),
        "body": {"contentType": "text", "content": body_text(item, tz)},
        "linkedResources": [{
            "webUrl": item.url or cfg["canvas_base_url"] + "/",
            "applicationName": APP_NAME,
            "displayName": "Open in Canvas",
            "externalId": item.key,
        }],
    }
    payload.update({k: v for k, v in due_fields(item, cfg, tz, graph_tz, now).items() if v is not None})
    return payload


# --------------------------------------------------------------------------- sync

def sync(cfg: dict, graph: Graph, items: list, state: dict, now: datetime) -> Counter:
    tz = ZoneInfo(cfg["timezone"])
    graph_tz = WINDOWS_TZ.get(cfg["timezone"], cfg["timezone"])
    entries = state.setdefault("items", {})
    counts = Counter()

    def list_name_for(item: Item) -> str:
        if cfg["list_mode"] == "per_course":
            return item.course or "Canvas (no course)"
        return cfg["list_name"]

    # Lists we will write to, plus lists we wrote to before for these same items.
    wanted = {list_name_for(i) for i in items if not i.complete or i.key in entries}
    existing_lists = graph.lists()
    list_ids = {}
    for name in sorted(wanted):
        if name not in existing_lists:
            log.info(f"Creating To Do list '{name}'")
            existing_lists[name] = graph.create_list(name)
        list_ids[name] = existing_lists[name]
    known_list_ids = set(existing_lists.values())
    read_ids = set(list_ids.values()) | {
        entries[i.key]["list_id"] for i in items
        if i.key in entries and entries[i.key].get("list_id") in known_list_ids
    }

    # What is in To Do right now.
    by_key, by_id = {}, {}
    for lid in read_ids:
        for t in graph.tasks(lid):
            t["_list_id"] = lid
            by_id[t["id"]] = t
            for lr in t.get("linkedResources") or []:
                if lr.get("applicationName") == APP_NAME and lr.get("externalId"):
                    by_key[lr["externalId"]] = t

    for item in items:
        entry = entries.get(item.key)
        task = by_key.get(item.key) or (by_id.get(entry.get("task_id")) if entry else None)
        due_key = due_date_key(item, tz)
        body = body_text(item, tz)

        if task is None:
            if entry and (entry.get("deleted") or entry.get("task_id")):
                if not entry.get("deleted"):
                    entry["deleted"] = True
                    log.info(f"Leaving alone (you removed it from To Do): {task_title(item, cfg)}")
                counts["left alone"] += 1
                continue
            if item.complete:
                continue  # already done in Canvas; nothing to add
            lid = list_ids[list_name_for(item)]
            created = graph.create_task(lid, new_task_payload(item, cfg, tz, graph_tz, now))
            entries[item.key] = {"task_id": created["id"], "list_id": lid, "title": task_title(item, cfg),
                                 "due": due_key, "complete": False, "body_hash": norm_hash(body)}
            log.info(f"Added: {task_title(item, cfg)}" + (f" (due {due_key})" if due_key else ""))
            counts["added"] += 1
            continue

        lid = task["_list_id"]
        if entry is None:
            # Task exists but the state file lost track of it: adopt it without overwriting anything.
            entry = entries[item.key] = {
                "task_id": task["id"], "list_id": lid, "title": task_title(item, cfg), "due": due_key,
                "complete": task.get("status") == "completed",
                "body_hash": norm_hash(body),  # if the note differs, treat it as yours and leave it
            }
            counts["re-linked"] += 1
        entry["task_id"], entry["list_id"] = task["id"], lid
        entry.pop("deleted", None)  # it is back in a list we read

        patch, changes = {}, []
        title = task_title(item, cfg)
        if title != entry.get("title"):
            patch["title"] = title
            changes.append("title")
        if due_key != entry.get("due"):
            patch.update(due_fields(item, cfg, tz, graph_tz, now))
            changes.append(f"due {entry.get('due')} -> {due_key}")
        if norm_hash(body) != entry.get("body_hash") and \
                norm_hash((task.get("body") or {}).get("content", "")) == entry.get("body_hash"):
            patch["body"] = {"contentType": "text", "content": body}  # only if you haven't edited the note
        if item.complete and not entry.get("complete"):
            if task.get("status") != "completed":
                patch["status"] = "completed"
                changes.append("checked off (done in Canvas)")
            entry["complete"] = True

        if patch:
            if not graph.update_task(lid, task["id"], patch):
                entry["deleted"] = True
                counts["left alone"] += 1
                continue
            entry["title"], entry["due"] = title, due_key
            if "body" in patch:
                entry["body_hash"] = norm_hash(body)
            if changes:
                log.info(f"Updated: {title} ({', '.join(changes)})")
                counts["completed" if "status" in patch else "updated"] += 1
        else:
            counts["unchanged"] += 1
    return counts


# --------------------------------------------------------------------------- scheduling

def _notify(message: str) -> None:
    if platform.system() == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification "{message}" with title "Canvas to To Do"'],
                       capture_output=True)


def install_schedule(cfg: dict) -> None:
    hours = max(1, min(int(cfg.get("sync_every_hours", 2)), 23))
    py, script = sys.executable, str(Path(__file__).resolve())
    system = platform.system()

    if system == "Darwin":
        import plistlib
        label = f"local.{SCHEDULE_LABEL}"
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "wb") as f:
            plistlib.dump({
                "Label": label, "ProgramArguments": [py, script], "StartInterval": hours * 3600,
                "RunAtLoad": True, "WorkingDirectory": str(HERE),
                "StandardOutPath": "/dev/null", "StandardErrorPath": str(HERE / "launchd_errors.log"),
            }, f)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(plist_path)], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True)
        if any(part in ("Desktop", "Documents", "Downloads") for part in HERE.parts):
            print("Heads up: macOS blocks background jobs from reading Desktop, Documents and Downloads. "
                  "If sync.log stays empty, move this folder to your home folder and run --install-schedule again.")
    elif system == "Windows":
        pyw = Path(py).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else py)
        subprocess.run(["schtasks", "/Create", "/F", "/SC", "HOURLY", "/MO", str(hours),
                        "/TN", "CanvasTodoSync", "/TR", f'"{exe}" "{script}"'], check=True)
    else:
        line = (f"7 */{hours} * * * cd {shlex.quote(str(HERE))} && {shlex.quote(py)} "
                f"{shlex.quote(script)} >/dev/null 2>&1  # {SCHEDULE_LABEL}")
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        keep = [l for l in (cur.stdout if cur.returncode == 0 else "").splitlines() if SCHEDULE_LABEL not in l]
        subprocess.run(["crontab", "-"], input="\n".join(keep + [line]) + "\n", text=True, check=True)
    print(f"Scheduled: syncs every {hours} hour(s) while your computer is awake. Activity goes to {LOG_PATH.name}.")


def uninstall_schedule() -> None:
    system = platform.system()
    if system == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"local.{SCHEDULE_LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)], capture_output=True)
        plist_path.unlink(missing_ok=True)
    elif system == "Windows":
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", "CanvasTodoSync"], capture_output=True)
    else:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if cur.returncode == 0:
            keep = [l for l in cur.stdout.splitlines() if SCHEDULE_LABEL not in l]
            subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True, check=True)
    print("Automatic sync removed. Your To Do tasks are untouched.")


# --------------------------------------------------------------------------- main

def print_preview(items: list, source: str, cfg: dict) -> None:
    tz = ZoneInfo(cfg["timezone"])
    items = sorted(items, key=lambda i: i.due or datetime.max.replace(tzinfo=timezone.utc))
    print(f"\n{len(items)} item(s) from the {source}:\n")
    for i in items:
        due = fmt_local(i.due, tz) if i.due else "no due date"
        mark = "done" if i.complete else "todo"
        print(f"  [{mark}] {due:<22} {i.course[:28]:<28} {i.title}")
    courses = sorted({i.course for i in items if i.course})
    if courses:
        print("\nCourse names as they will appear (rename them with \"course_aliases\" in config.json):")
        for c in courses:
            print(f"  {json.dumps(c)}")
    print("\nNothing was sent to Microsoft To Do. Items marked [done] are skipped.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync Canvas assignments into Microsoft To Do.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="list Canvas items without touching To Do")
    g.add_argument("--login", action="store_true", help="sign in to Microsoft (one time)")
    g.add_argument("--install-schedule", action="store_true", help="run the sync automatically")
    g.add_argument("--uninstall-schedule", action="store_true", help="stop the automatic sync")
    args = ap.parse_args()
    setup_logging()

    if args.uninstall_schedule:
        uninstall_schedule()
        return 0
    cfg = load_config()
    if args.install_schedule:
        install_schedule(cfg)
        return 0

    now = datetime.now(timezone.utc)
    try:
        if args.login:
            ms_token(cfg, interactive=True)
            print("Signed in to Microsoft. Next: python3 canvas_todo_sync.py")
            return 0
        items, source = fetch_items(cfg, now)
        if args.preview:
            print_preview(items, source, cfg)
            return 0
        interactive = sys.stdin is not None and sys.stdin.isatty()
        graph = Graph(ms_token(cfg, interactive=interactive))
        state = load_state()
        counts = sync(cfg, graph, items, state, now)
        state["last_sync"] = now.isoformat()
        save_state(state)
        summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "nothing to do"
        log.info(f"Sync finished ({source}, {len(items)} Canvas items): {summary}")
        return 0
    except (SyncError, requests.RequestException) as e:
        log.error(str(e))
        _notify(str(e)[:120].replace('"', "'"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
