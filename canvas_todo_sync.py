#!/usr/bin/env python3
"""
canvas_todo_sync.py

Copies your Canvas and Gradescope assignments into Microsoft To Do and keeps
them in sync: new work shows up, due date changes follow, and things you submit
get checked off. An assignment that exists on both Canvas and Gradescope
becomes a single task. Each course can go to its own To Do list.

Commands
  python3 canvas_todo_sync.py --preview             list what would sync (no Microsoft needed)
  python3 canvas_todo_sync.py --preview --json      the same, as JSON (for scripts and AI agents)
  python3 canvas_todo_sync.py --status              last sync, schedule, recent log lines
  python3 canvas_todo_sync.py --login               one-time Microsoft sign-in
  python3 canvas_todo_sync.py --lists               show your To Do lists and where each course goes
  python3 canvas_todo_sync.py                       sync once
  python3 canvas_todo_sync.py --install-schedule    sync automatically every few hours
  python3 canvas_todo_sync.py --uninstall-schedule  stop the automatic sync

Settings and state live in the data folder: $CANVAS_TODO_HOME if set, else this
script's folder if it holds config.json, else ~/canvas-todo-sync.
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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

__version__ = "2.1.0"


def _data_dir() -> Path:
    """Where config, sign-in and state live, so any copy of this script (e.g. one bundled in a
    skill folder) finds the same data."""
    env = os.environ.get("CANVAS_TODO_HOME")
    if env:
        return Path(env).expanduser()
    script_dir = Path(__file__).resolve().parent
    if (script_dir / "config.json").exists():
        return script_dir
    return Path.home() / "canvas-todo-sync"


HERE = _data_dir()
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "sync_state.json"
TOKEN_CACHE_PATH = HERE / "ms_token_cache.json"
LOG_PATH = HERE / "sync.log"

GRAPH = "https://graph.microsoft.com/v1.0"
MS_AUTHORITY = "https://login.microsoftonline.com/common"
MS_SCOPES = ["Tasks.ReadWrite"]
OWNED_APPS = {"Canvas", "Gradescope"}   # linkedResource.applicationName values this script writes
SCHEDULE_LABEL = "canvas-todo-sync"
USER_AGENT = "Mozilla/5.0 (compatible; canvas_todo_sync)"

DEFAULTS = {
    "canvas_base_url": "https://canvas.ucsd.edu",
    "canvas_token": "",
    "canvas_ics_url": "",
    "gradescope_base_url": "https://www.gradescope.com",
    "gradescope_email": "",
    "gradescope_password": "",
    "microsoft_client_id": "",
    "list_mode": "single",          # "single" = one list, "per_course" = one list per course
    "list_name": "Canvas",          # default list for courses without a "todo_list"
    "courses": {},                  # {"COGS 118B": {"todo_list": "...", "canvas": "...", "gradescope": "...", "skip": false}}
    "timezone": "America/Los_Angeles",
    "lookback_days": 3,
    "lookahead_days": 120,
    "reminder_hours_before": 24,     # 0 turns reminders off
    "include_types": ["assignment", "quiz", "discussion_topic", "sub_assignment",
                      "assessment_request", "wiki_page", "planner_note"],
    "skip_courses": [],
    "course_aliases": {},           # older setting, still honored
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

def setup_logging(console=None) -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    if HERE.is_dir():
        fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=1, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    console = console or sys.stdout
    if console is not None:  # pythonw on Windows has no stdout
        sh = logging.StreamHandler(console)
        sh.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(sh)


def is_set(value) -> bool:
    return bool(value) and not str(value).startswith("PASTE_")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH}. Copy config.example.json to config.json and fill it in "
                 f"(or set CANVAS_TODO_HOME to the folder that has it).")
    cfg = dict(DEFAULTS)
    cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    cfg["canvas_token"] = os.environ.get("CANVAS_TOKEN") or cfg.get("canvas_token", "")
    cfg["gradescope_password"] = os.environ.get("GRADESCOPE_PASSWORD") or cfg.get("gradescope_password", "")
    cfg["canvas_base_url"] = cfg["canvas_base_url"].rstrip("/")
    cfg["gradescope_base_url"] = cfg["gradescope_base_url"].rstrip("/")
    cfg["courses"] = {k: v for k, v in (cfg.get("courses") or {}).items() if isinstance(v, dict)}
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


class SyncError(Exception):
    pass


# --------------------------------------------------------------------------- items and courses

@dataclass
class Item:
    key: str                  # stable id, e.g. "assignment:12345" or "gradescope:678:910"
    kind: str
    title: str
    course: str               # canonical course name, e.g. "COGS 118B"
    due: Optional[datetime]   # timezone-aware
    url: str
    points: Optional[float]
    complete: bool
    source: str = "Canvas"    # "Canvas" or "Gradescope"
    extra_links: list = field(default_factory=list)   # [(label, url)]
    alt_keys: list = field(default_factory=list)      # keys of merged duplicates


TERM_WORDS = {"FA", "WI", "SP", "SU", "SS", "FALL", "WIN", "WINTER", "SPR", "SPRING", "SUM", "SUMMER"}
CODE_RE = re.compile(r"\b([A-Z]{2,5})[\s\-]*(\d{1,3}[A-Z]{0,2})\b")
SEEN_COURSES = defaultdict(lambda: {"Canvas": set(), "Gradescope": set()})  # canonical -> raw names
RUN_INFO = {"gradescope_failed": False, "warnings": []}


def course_code(raw: str) -> str:
    """'FA26 COGS 118B A00' -> 'COGS 118B'. Empty string if no course code is found."""
    for subj, num in CODE_RE.findall((raw or "").upper().replace("_", " ")):
        if subj not in TERM_WORDS:
            return f"{subj} {num}"
    return ""


def resolve_course(raw: str, source: str, cfg: dict) -> str:
    """Map a course name as Canvas or Gradescope shows it to one shared name."""
    courses = cfg["courses"]
    name = None
    for key, spec in courses.items():
        if spec.get(source.lower()) == raw:
            name = key
            break
    if name is None and raw in (cfg.get("course_aliases") or {}):
        name = cfg["course_aliases"][raw]
    if name is None:
        code = course_code(raw)
        if code:
            name = next((k for k in courses if course_code(k) == code), code)
    name = name or raw
    SEEN_COURSES[name][source].add(raw)
    return name


def course_spec(cfg: dict, course: str) -> dict:
    return cfg["courses"].get(course) or {}


def is_skipped(cfg: dict, course: str, raw: str, course_id=None) -> bool:
    skip = set(cfg.get("skip_courses") or [])
    return (course in skip or raw in skip or (course_id is not None and str(course_id) in skip)
            or bool(course_spec(cfg, course).get("skip")))


def parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_loose_dt(value, tz: ZoneInfo) -> Optional[datetime]:
    """Handles '2026-10-15 23:59:00 -0700', '2026-10-15T23:59:00Z', '2026-10-15 23:59'."""
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(:\d{2})?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?",
                 str(value or ""))
    if not m:
        return None
    date, hm, sec, off = m.groups()
    text = f"{date}T{hm}{sec or ':00'}"
    if off:
        off = "+00:00" if off == "Z" else (off if ":" in off else f"{off[:3]}:{off[3:]}")
        return datetime.fromisoformat(text + off)
    return datetime.fromisoformat(text).replace(tzinfo=tz)


# --------------------------------------------------------------------------- Canvas

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

    include = set(cfg["include_types"])
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
        course = resolve_course(course_raw, "Canvas", cfg) if course_raw else ""
        if is_skipped(cfg, course, course_raw, course_id):
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
            key=f"{kind}:{pid}", kind=kind, title=title, course=course,
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
    items = []
    for ev in parse_ics(r.text):
        uid = ev.get("UID", ("", {}))[0]
        if "assignment" not in uid:      # lectures, office hours and other calendar events
            continue
        summary = ev.get("SUMMARY", ("", {}))[0].strip()
        m = re.match(r"^(.*?)\s*\[([^\]]+)\]\s*$", summary)
        title, course_raw = (m.group(1), m.group(2)) if m else (summary, "")
        course = resolve_course(course_raw, "Canvas", cfg) if course_raw else ""
        if is_skipped(cfg, course, course_raw):
            continue
        due = ics_datetime(*ev["DTSTART"], tz) if "DTSTART" in ev else None
        if due and not (lo <= due <= hi):
            continue
        items.append(Item(
            key=f"ics:{uid}", kind="assignment", title=title or "Untitled", course=course, due=due,
            url=ev.get("URL", ("", {}))[0], points=None, complete=False,
        ))
    return items


# --------------------------------------------------------------------------- Gradescope

def gradescope_enabled(cfg: dict) -> bool:
    return is_set(cfg.get("gradescope_email")) and is_set(cfg.get("gradescope_password"))


def _soup(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise SyncError("Gradescope support needs beautifulsoup4. Run: .venv/bin/pip install -r requirements.txt")
    return BeautifulSoup(html, "html.parser")


def parse_gradescope_courses(html: str) -> tuple:
    """Student courses from the newest term on /account. Returns (term_label, [(id, short, full)])."""
    soup = _soup(html)
    root = soup.select_one("div#account-show") or soup
    in_student = soup.select_one("button.js-createNewCourse") is None  # staff accounts list student courses second
    for el in root.find_all(["h2", "div"]):
        classes = el.get("class") or []
        if el.name == "h2" and "pageHeading" in classes:
            text = el.get_text(strip=True)
            if "Student" in text:
                in_student = True
            elif "Instructor" in text:
                in_student = False
        elif el.name == "div" and "courseList" in classes and in_student:
            terms = el.find_all("div", class_="courseList--term")
            if not terms:
                continue
            term = terms[0]  # Gradescope lists the newest term first
            out = []
            a = term.find_next("a")
            while a is not None:
                m = re.search(r"/courses/(\d+)", a.get("href") or "")
                if m:
                    short = a.find(class_="courseBox--shortname")
                    full = a.find(class_="courseBox--name")
                    out.append((m.group(1), short.get_text(strip=True) if short else "",
                                full.get_text(strip=True) if full else ""))
                a = a.find_next_sibling("a")
            return term.get_text(strip=True), out
    return "", []


def parse_gradescope_assignments(html: str, tz: ZoneInfo) -> list:
    soup = _soup(html)
    table = soup.select_one("table#assignments-student-table") or soup
    out = []
    for tr in table.find_all("tr"):
        if "dropzonePreview" in " ".join(tr.get("class") or []):
            continue
        th, tds = tr.find("th"), tr.find_all("td")
        if th is None or not tds:
            continue  # header row
        link = th.find("a", href=True)
        button = th.find(attrs={"data-assignment-id": True})
        name = (link or button or th).get_text(" ", strip=True)
        if not name:
            continue
        aid = None
        if link:
            m = re.search(r"/assignments/(\d+)", link["href"])
            aid = m.group(1) if m else None
        if not aid and button:
            aid = button["data-assignment-id"]
        status = tds[0].get_text(" ", strip=True)
        dues = tr.select(".submissionTimeChart--dueDate")
        out.append({
            "id": aid, "name": name, "status": status,
            "due": parse_loose_dt(dues[0].get("datetime"), tz) if dues else None,
            "late_due": parse_loose_dt(dues[1].get("datetime"), tz) if len(dues) > 1 else None,
            "complete": bool(re.search(r"submitted|\d\s*/\s*\d", status, re.I))
                        and not re.search(r"no submission", status, re.I),
        })
    return out


def gradescope_login(cfg: dict) -> requests.Session:
    base = cfg["gradescope_base_url"]
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    home = s.get(base + "/", timeout=30)
    token = _soup(home.text).select_one('form[action="/login"] input[name="authenticity_token"]')
    if token is None:
        raise SyncError("Couldn't find Gradescope's login form. Gradescope may have changed its site.")
    r = s.post(base + "/login", timeout=30, data={
        "utf8": "✓", "authenticity_token": token["value"],
        "session[email]": cfg["gradescope_email"], "session[password]": cfg["gradescope_password"],
        "session[remember_me]": "0", "session[remember_me_sso]": "0", "commit": "Log In",
    })
    if not r.history or "/login" in r.url:
        raise SyncError("Gradescope login failed. Check gradescope_email and gradescope_password "
                        "(setup guide, Gradescope section). If you only ever used UCSD single sign-on, set a password first.")
    return s


def fetch_from_gradescope(cfg: dict, now: datetime) -> list:
    base, tz = cfg["gradescope_base_url"], ZoneInfo(cfg["timezone"])
    s = gradescope_login(cfg)
    term, courses = parse_gradescope_courses(s.get(base + "/account", timeout=30).text)
    lo = now - timedelta(days=cfg["lookback_days"])
    hi = now + timedelta(days=cfg["lookahead_days"])
    items = []
    for cid, short, full in courses:
        raw = short or full
        course = resolve_course(raw, "Gradescope", cfg)
        if is_skipped(cfg, course, raw, cid):
            continue
        time.sleep(1)  # be gentle: one page per course
        page = s.get(f"{base}/courses/{cid}", timeout=30)
        if page.status_code != 200:
            log.warning(f"Gradescope course {raw} returned {page.status_code}; skipped this run.")
            continue
        for a in parse_gradescope_assignments(page.text, tz):
            if not a["due"] or not (lo <= a["due"] <= hi):
                continue
            slug = a["id"] or re.sub(r"[^a-z0-9]+", "-", a["name"].lower()).strip("-")
            url = f"{base}/courses/{cid}/assignments/{a['id']}" if a["id"] else f"{base}/courses/{cid}"
            items.append(Item(
                key=f"gradescope:{cid}:{slug}", kind="Gradescope assignment", title=a["name"],
                course=course, due=a["due"], url=url, points=None, complete=a["complete"],
                source="Gradescope",
            ))
    log.info(f"Gradescope: {len(courses)} course(s) in {term or 'the current term'}, {len(items)} upcoming item(s).")
    return items


# --------------------------------------------------------------------------- de-duplication

SYNONYMS = {"homework": "hw", "homeworks": "hw", "hws": "hw", "assignment": "hw", "assignments": "hw",
            "asg": "hw", "assgn": "hw", "pset": "ps", "psets": "ps", "quizzes": "quiz", "labs": "lab",
            "project": "proj", "projects": "proj", "worksheet": "ws", "worksheets": "ws",
            "discussion": "disc", "chapter": "ch", "exercise": "ex", "exercises": "ex", "problems": "problem"}
ID_PREFIXES = {"part", "section", "problem", "question", "q", "lab", "hw", "ps", "set", "version", "v",
               "quiz", "exam", "proj", "week", "ch", "module", "unit", "ws", "ex"}
STOPWORDS = {"the", "an", "of", "and", "for", "to", "in", "on", "due", "submission", "submit",
             "gradescope", "canvas", "pdf", "upload", "here"}


def title_signature(title: str) -> tuple:
    """('HW 3a: Regression') -> ({'3', 'a'}, {'hw', 'regression'}): identifiers must match exactly."""
    t = title.lower().replace("problem set", "ps").replace("p-set", "ps")
    toks = [SYNONYMS.get(x, x) for x in re.findall(r"[a-z]+|\d+", re.sub(r"(\d+)", r" \1 ", t))]
    ids, words = set(), set()
    for i, x in enumerate(toks):
        if x.isdigit():
            ids.add(str(int(x)))
        elif len(x) == 1:
            if x == "a" and not (i and (toks[i - 1].isdigit() or toks[i - 1] in ID_PREFIXES)):
                continue  # the article "a"
            ids.add(x)
        elif x not in STOPWORDS:
            words.add(x)
    return ids, words


def match_score(a: Item, b: Item) -> Optional[float]:
    ids_a, words_a = title_signature(a.title)
    ids_b, words_b = title_signature(b.title)
    if ids_a != ids_b:
        return None
    if words_a and words_b:
        overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
    else:
        overlap = 1.0 if words_a == words_b else 0.0
    gap = abs((a.due - b.due).total_seconds()) / 3600 if (a.due and b.due) else None
    if overlap == 1.0 and words_a == words_b:
        return 2.0 - (gap or 0) / 10_000        # same title: same assignment even if dates disagree
    if overlap >= 0.5 and (gap is None or gap <= 36):
        return overlap - (gap or 0) / 10_000
    return None


def merge_sources(canvas_items: list, gs_items: list) -> tuple:
    """Fold Gradescope items that duplicate a Canvas item into it. Returns (items, matches)."""
    by_course = defaultdict(list)
    for c in canvas_items:
        by_course[c.course].append(c)
    taken, matches, extra = set(), [], []
    for g in gs_items:
        scored = [(match_score(g, c), c) for c in by_course.get(g.course, []) if id(c) not in taken]
        scored = [(s, c) for s, c in scored if s is not None]
        if not scored:
            extra.append(g)
            continue
        _, c = max(scored, key=lambda x: x[0])
        taken.add(id(c))
        c.complete = c.complete or g.complete   # submitted on Gradescope counts
        c.extra_links.append(("Gradescope", g.url))
        c.alt_keys.append(g.key)
        matches.append((g, c))
    return canvas_items + extra, matches


def fetch_items(cfg: dict, now: datetime) -> tuple:
    if is_set(cfg["canvas_token"]):
        canvas, source = fetch_from_api(cfg, now), "Canvas"
    elif is_set(cfg["canvas_ics_url"]):
        canvas, source = fetch_from_ics(cfg, now), "Canvas calendar feed"
    else:
        raise SyncError('config.json needs either "canvas_token" or "canvas_ics_url".')
    gradescope = []
    RUN_INFO["gradescope_failed"] = False
    RUN_INFO["warnings"] = []
    if gradescope_enabled(cfg):
        try:
            gradescope = fetch_from_gradescope(cfg, now)
            source += " + Gradescope"
        except (SyncError, requests.RequestException) as e:
            # Canvas still syncs; Gradescope-only tasks simply don't update this run.
            RUN_INFO["gradescope_failed"] = True
            RUN_INFO["warnings"].append(f"Gradescope skipped: {e}")
            log.error(f"Gradescope skipped this run: {e}")
            _notify(f"Gradescope skipped: {str(e)[:90]}")
    items, matches = merge_sources(canvas, gradescope)
    return items, source, matches


# --------------------------------------------------------------------------- Microsoft

def ms_token(cfg: dict, interactive: bool) -> str:
    import msal  # imported here so --preview works before Microsoft is set up

    if not is_set(cfg["microsoft_client_id"]):
        raise SyncError('config.json is missing "microsoft_client_id" (setup guide: Microsoft app registration).')
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

    def lists(self) -> list:
        """Every list, including the ones inside groups (the API shows lists, never groups)."""
        return [(l["displayName"], l["id"]) for l in self.get_all("/me/todo/lists")]

    def create_list(self, name: str) -> str:
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
        return self._check(self._req("POST", f"/me/todo/lists/{list_id}/tasks", json=payload)).json()

    def update_task(self, list_id: str, task_id: str, patch: dict) -> bool:
        """Returns False if the task no longer exists (deleted or moved by you)."""
        r = self._req("PATCH", f"/me/todo/lists/{list_id}/tasks/{task_id}", json=patch)
        if r.status_code == 404:
            return False
        self._check(r)
        return True


# --------------------------------------------------------------------------- routing and task building

def target_list(item: Item, cfg: dict) -> tuple:
    """(list name, routed). 'routed' lists are per course, so titles skip the course prefix."""
    named = course_spec(cfg, item.course).get("todo_list")
    if named:
        return named, True
    if cfg["list_mode"] == "per_course" and item.course:
        return item.course, True
    return cfg["list_name"], False


def task_title(item: Item, cfg: dict) -> str:
    _, routed = target_list(item, cfg)
    if routed or not item.course:
        return item.title[:255]
    return f"{item.course}: {item.title}"[:255]


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
        lines.append(f"{item.source}: {item.url}")
    for label, url in item.extra_links:
        lines.append(f"{label}: {url}")
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
            "applicationName": item.source,
            "displayName": f"Open in {item.source}",
            "externalId": item.key,
        }],
    }
    payload.update({k: v for k, v in due_fields(item, cfg, tz, graph_tz, now).items() if v is not None})
    return payload


class ListResolver:
    """Finds To Do lists by name. Lists named in config must already exist (they live in your groups);
    the default list and per-course lists are created when missing."""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.by_name = defaultdict(list)
        for name, lid in graph.lists():
            self.by_name[name].append(lid)
        self.problems = {}

    def all_ids(self) -> set:
        return {lid for ids in self.by_name.values() for lid in ids}

    def find(self, name: str) -> list:
        """All list ids with this name (case-insensitive). Never creates or complains."""
        return [lid for n, lids in self.by_name.items()
                if n.strip().casefold() == name.strip().casefold() for lid in lids]

    def resolve(self, name: str, create_ok: bool) -> Optional[str]:
        if name in self.problems:
            return None
        ids = self.by_name.get(name) or [lid for n, lids in self.by_name.items()
                                         if n.strip().casefold() == name.strip().casefold() for lid in lids]
        if len(ids) > 1:
            self.problems[name] = (f'You have {len(ids)} To Do lists named "{name}". The To Do API can\'t see '
                                   f'groups, so it can\'t tell them apart. Give each a unique name.')
        elif ids:
            return ids[0]
        elif create_ok:
            log.info(f"Creating To Do list '{name}' (drag it into a group in To Do if you like)")
            lid = self.graph.create_list(name)
            self.by_name[name].append(lid)
            return lid
        else:
            self.problems[name] = (f'No To Do list named "{name}" (from "todo_list" in config.json). '
                                   f'Check the spelling, or run --lists to see your list names.')
        if name in self.problems:
            log.error(self.problems[name])
        return None


# --------------------------------------------------------------------------- sync

def sync(cfg: dict, graph: Graph, items: list, state: dict, now: datetime) -> Counter:
    tz = ZoneInfo(cfg["timezone"])
    graph_tz = WINDOWS_TZ.get(cfg["timezone"], cfg["timezone"])
    entries = state.setdefault("items", {})
    counts = Counter()

    # Carry state over when a Gradescope task now matches a Canvas assignment.
    for item in items:
        if item.key not in entries:
            for alt in item.alt_keys:
                if alt in entries:
                    entries[item.key] = entries.pop(alt)
                    break

    resolver = ListResolver(graph)
    targets = {}
    for item in items:
        entry = entries.get(item.key)
        if not (entry and entry.get("task_id")) and (item.complete or (entry and entry.get("done"))):
            continue  # finished work that never needed a task
        name, routed = target_list(item, cfg)
        explicit = bool(course_spec(cfg, item.course).get("todo_list"))
        targets[item.key] = resolver.resolve(name, create_ok=not explicit)

    known = resolver.all_ids()
    read_ids = {lid for lid in targets.values() if lid} | {
        entries[i.key]["list_id"] for i in items
        if i.key in entries and entries[i.key].get("list_id") in known
    }
    # Also look in every list the sync routes to, so tasks you dragged between them stay tracked.
    for name in [cfg["list_name"]] + [spec.get("todo_list") for spec in cfg["courses"].values()]:
        if name:
            read_ids.update(resolver.find(name))

    # What is in To Do right now.
    by_key, by_id = {}, {}
    for lid in read_ids:
        for t in graph.tasks(lid):
            t["_list_id"] = lid
            by_id[t["id"]] = t
            for lr in t.get("linkedResources") or []:
                if lr.get("applicationName") in OWNED_APPS and lr.get("externalId"):
                    by_key[lr["externalId"]] = t

    for item in items:
        entry = entries.get(item.key)
        task = next((by_key[k] for k in [item.key] + item.alt_keys if k in by_key), None)
        if task is None and entry:
            task = by_id.get(entry.get("task_id"))
        due_key = due_date_key(item, tz)
        body = body_text(item, tz)
        title = task_title(item, cfg)

        if task is None:
            if entry and (entry.get("deleted") or entry.get("task_id")):
                if not entry.get("deleted"):
                    entry["deleted"] = True
                    log.info(f"Leaving alone (you removed it from To Do): {title}")
                counts["left alone"] += 1
                continue
            if entry and entry.get("done"):
                continue  # was already finished (maybe only Gradescope knew); don't resurrect it
            if item.complete:
                entries[item.key] = {"done": True}
                continue
            lid = targets.get(item.key)
            if not lid:
                counts["waiting on a list fix"] += 1
                continue
            created = graph.create_task(lid, new_task_payload(item, cfg, tz, graph_tz, now))
            entries[item.key] = {"task_id": created["id"], "list_id": lid, "title": title,
                                 "due": due_key, "complete": False, "body_hash": norm_hash(body)}
            log.info(f"Added: {title}" + (f" (due {due_key})" if due_key else "") + f" -> {target_list(item, cfg)[0]}")
            counts["added"] += 1
            continue

        lid = task["_list_id"]
        if entry is None:
            # Task exists but the state file lost track of it: adopt it without overwriting anything.
            entry = entries[item.key] = {
                "task_id": task["id"], "list_id": lid, "title": title, "due": due_key,
                "complete": task.get("status") == "completed",
                "body_hash": norm_hash(body),  # if the note differs, treat it as yours and leave it
            }
            counts["re-linked"] += 1
        entry["task_id"], entry["list_id"] = task["id"], lid
        entry.pop("deleted", None)  # it is back in a list we read

        patch, changes = {}, []
        if title != entry.get("title"):
            patch["title"] = title
            changes.append("title")
        if due_key != entry.get("due"):
            patch.update(due_fields(item, cfg, tz, graph_tz, now))
            changes.append(f"due {entry.get('due')} -> {due_key}")
        if not RUN_INFO["gradescope_failed"] and norm_hash(body) != entry.get("body_hash") and \
                norm_hash((task.get("body") or {}).get("content", "")) == entry.get("body_hash"):
            patch["body"] = {"contentType": "text", "content": body}  # only if you haven't edited the note
        if item.complete and not entry.get("complete"):
            if task.get("status") != "completed":
                patch["status"] = "completed"
                changes.append("checked off (submitted)")
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
        message = message.replace('"', "'")
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


# --------------------------------------------------------------------------- commands

def print_preview(items: list, source: str, matches: list, cfg: dict) -> None:
    tz = ZoneInfo(cfg["timezone"])
    items = sorted(items, key=lambda i: i.due or datetime.max.replace(tzinfo=timezone.utc))
    print(f"\n{len(items)} item(s) from {source}:\n")
    for i in items:
        due = fmt_local(i.due, tz) if i.due else "no due date"
        mark = "done" if i.complete else "todo"
        src = "GS" if i.source == "Gradescope" else ("C+GS" if i.alt_keys else "C")
        print(f"  [{mark}] {src:<4} {due:<22} {i.course[:12]:<12} {i.title[:48]:<48} -> {target_list(i, cfg)[0]}")
    if matches:
        print("\nOn both Canvas and Gradescope (one task each):")
        for g, c in matches:
            print(f'  {c.course}: Gradescope "{g.title}" = Canvas "{c.title}"')

    print("\nCourses:")
    for name in sorted(SEEN_COURSES):
        seen = SEEN_COURSES[name]
        canvas = ", ".join(sorted(seen["Canvas"])) or "-"
        gs = ", ".join(sorted(seen["Gradescope"])) or "-"
        dest = course_spec(cfg, name).get("todo_list") or (name if cfg["list_mode"] == "per_course" else cfg["list_name"])
        print(f"  {name:<12} Canvas: {canvas[:30]:<30} Gradescope: {gs[:20]:<20} -> {dest}")
    if any(not course_spec(cfg, n).get("todo_list") for n in SEEN_COURSES):
        print("\nTo send each course to a list in your To Do groups, replace \"courses\" in config.json with this\n"
              "and set each todo_list to the exact name of that course's list (run --lists to see them):\n")
        names = sorted(set(SEEN_COURSES) | set(cfg["courses"]))
        rows = [f"    {json.dumps(n)}: {json.dumps(course_spec(cfg, n) or {'todo_list': n})}" for n in names]
        print('  "courses": {\n' + ",\n".join(rows) + "\n  }")
    print("\nNothing was sent to Microsoft To Do. C = Canvas, GS = Gradescope. Items marked [done] are skipped.")


def print_lists(cfg: dict, graph: Graph) -> None:
    names = Counter(name for name, _ in graph.lists())
    routed = defaultdict(list)
    for course, spec in cfg["courses"].items():
        if spec.get("todo_list"):
            routed[spec["todo_list"].strip().casefold()].append(course)
    print("\nYour To Do lists (the API shows lists, not the groups they sit in):\n")
    for name in sorted(names, key=str.casefold):
        notes = []
        if names[name] > 1:
            notes.append(f"NAME USED {names[name]} TIMES: rename so each is unique")
        if routed.get(name.strip().casefold()):
            notes.append("gets " + ", ".join(routed[name.strip().casefold()]))
        print(f"  {name}" + (f"   <- {'; '.join(notes)}" if notes else ""))
    lowered = {n.strip().casefold() for n in names}
    for key, courses in routed.items():
        if key not in lowered:
            print(f'\n  Not found: a list named "{key}" (for {", ".join(courses)}). Check config.json.')
    print()


def preview_json(items: list, source: str, matches: list, cfg: dict, now: datetime) -> dict:
    tz = ZoneInfo(cfg["timezone"])
    rows = []
    for i in sorted(items, key=lambda i: i.due or datetime.max.replace(tzinfo=timezone.utc)):
        rows.append({
            "course": i.course, "title": i.title, "kind": i.kind,
            "source": "Canvas+Gradescope" if i.alt_keys else i.source,
            "due": i.due.astimezone(tz).isoformat() if i.due else None,
            "due_local": fmt_local(i.due, tz) if i.due else None,
            "todo_due_date": due_date_key(i, tz),
            "complete": i.complete, "url": i.url,
            "links": [{"label": l, "url": u} for l, u in i.extra_links],
            "todo_list": target_list(i, cfg)[0],
        })
    courses = []
    for name in sorted(SEEN_COURSES):
        seen = SEEN_COURSES[name]
        spec = course_spec(cfg, name)
        courses.append({"name": name, "canvas_names": sorted(seen["Canvas"]),
                        "gradescope_names": sorted(seen["Gradescope"]),
                        "todo_list": target_list(Item("", "", "", name, None, "", None, False), cfg)[0],
                        "configured": bool(spec)})
    return {
        "generated_at": now.astimezone(tz).isoformat(), "timezone": cfg["timezone"], "source": source,
        "warnings": list(RUN_INFO.get("warnings") or []),
        "matched_across_sites": [{"course": c.course, "canvas": c.title, "gradescope": g.title} for g, c in matches],
        "items": rows, "courses": courses,
    }


def schedule_status() -> str:
    try:
        return _schedule_status()
    except (FileNotFoundError, OSError) as e:
        return f"unknown ({e})"


def _schedule_status() -> str:
    system = platform.system()
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"local.{SCHEDULE_LABEL}.plist"
        if not plist.exists():
            return "not installed"
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/local.{SCHEDULE_LABEL}"],
                           capture_output=True, text=True)
        return "installed and loaded" if r.returncode == 0 else "installed but not loaded (rerun --install-schedule)"
    if system == "Windows":
        r = subprocess.run(["schtasks", "/Query", "/TN", "CanvasTodoSync"], capture_output=True, text=True)
        return "installed" if r.returncode == 0 else "not installed"
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return "installed" if r.returncode == 0 and SCHEDULE_LABEL in r.stdout else "not installed"


def print_status() -> None:
    print(f"canvas_todo_sync {__version__}")
    print(f"Data folder: {HERE}")
    if not CONFIG_PATH.exists():
        print("Config: missing (not set up yet; see the setup guide)")
        return
    cfg = load_config()
    canvas = "API token" if is_set(cfg["canvas_token"]) else ("calendar feed" if is_set(cfg["canvas_ics_url"]) else "NOT SET")
    print(f"Canvas: {canvas}")
    print(f"Gradescope: {'on (' + cfg['gradescope_email'] + ')' if gradescope_enabled(cfg) else 'off'}")
    print(f"Microsoft app: {'set' if is_set(cfg['microsoft_client_id']) else 'NOT SET'}; "
          f"signed in: {'yes' if TOKEN_CACHE_PATH.exists() else 'no (run --login)'}")
    routed = {k: v.get("todo_list") for k, v in cfg["courses"].items() if v.get("todo_list")}
    print(f"Routing: {len(routed)} course(s) to their own lists; others to '{cfg['list_name']}'")
    state = load_state() if STATE_PATH.exists() else {}
    last = state.get("last_sync")
    if last:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        hours = age.total_seconds() / 3600
        print(f"Last successful sync: {last} ({hours:.1f} hours ago)")
    else:
        print("Last successful sync: never")
    print(f"Tracked tasks: {sum(1 for e in state.get('items', {}).values() if e.get('task_id'))}")
    print(f"Automatic sync: {schedule_status()}")
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        print("\nRecent log:")
        for line in lines:
            print("  " + line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync Canvas and Gradescope assignments into Microsoft To Do.")
    ap.add_argument("--version", action="version", version=f"canvas_todo_sync {__version__}")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--preview", action="store_true", help="list what would sync without touching To Do")
    g.add_argument("--status", action="store_true", help="show setup, last sync, schedule and recent log")
    g.add_argument("--login", action="store_true", help="sign in to Microsoft (one time)")
    g.add_argument("--lists", action="store_true", help="show your To Do lists and course routing")
    g.add_argument("--install-schedule", action="store_true", help="run the sync automatically")
    g.add_argument("--uninstall-schedule", action="store_true", help="stop the automatic sync")
    ap.add_argument("--json", action="store_true", help="with --preview: print JSON only (logs go to stderr)")
    args = ap.parse_args()
    if args.json and not args.preview:
        ap.error("--json only works with --preview")
    setup_logging(console=sys.stderr if args.json else None)

    if args.status:
        print_status()
        return 0
    if args.uninstall_schedule:
        uninstall_schedule()
        return 0
    cfg = load_config()
    if args.install_schedule:
        install_schedule(cfg)
        return 0

    now = datetime.now(timezone.utc)
    interactive = sys.stdin is not None and sys.stdin.isatty()
    try:
        if args.login:
            ms_token(cfg, interactive=True)
            print("Signed in to Microsoft. Next: python3 canvas_todo_sync.py --lists")
            return 0
        if args.lists:
            print_lists(cfg, Graph(ms_token(cfg, interactive=interactive)))
            return 0
        items, source, matches = fetch_items(cfg, now)
        if args.preview:
            if args.json:
                print(json.dumps(preview_json(items, source, matches, cfg, now), indent=1))
            else:
                print_preview(items, source, matches, cfg)
            return 0
        graph = Graph(ms_token(cfg, interactive=interactive))
        state = load_state()
        counts = sync(cfg, graph, items, state, now)
        state["last_sync"] = now.isoformat()
        save_state(state)
        # Offline snapshot of upcoming work so other tools (like a morning brief) can read it
        # without reaching Canvas or Gradescope. Holds no credentials.
        try:
            write_private(HERE / "due_cache.json",
                          json.dumps(preview_json(items, source, matches, cfg, now), indent=1))
        except OSError as e:
            log.warning(f"Could not write due_cache.json: {e}")
        summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "nothing to do"
        log.info(f"Sync finished ({source}, {len(items)} items, {len(matches)} matched across both): {summary}")
        return 0
    except (SyncError, requests.RequestException) as e:
        log.error(str(e))
        if args.json:
            print(json.dumps({"error": str(e)}))
        _notify(str(e)[:120])
        return 1


if __name__ == "__main__":
    sys.exit(main())
