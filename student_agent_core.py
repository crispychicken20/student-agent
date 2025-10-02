
import os, io, re, csv, uuid
from datetime import datetime, timedelta, time
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field
from dateutil import tz
import dateparser
from PyPDF2 import PdfReader

# ---------- Config (can be tweaked by the UI by setting these module vars) ----------
APP_TZ = tz.gettz("America/Los_Angeles")  # default locale
DEFAULT_DAILY_HOURS = 2.0
WORK_START = time(9, 0)
WORK_END = time(21, 0)
BLOCK_MINUTES = 50  # length of each planned block
OUTPUT_DIR = "exports"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------- Optional OpenAI ----------
USE_LLM = False
try:
    import os as _os
    from openai import OpenAI
    if _os.environ.get("OPENAI_API_KEY"):
        client = OpenAI()
        USE_LLM = True
except Exception:
    USE_LLM = False

# ---------- Data Models ----------
class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str
    due: Optional[datetime] = None
    est_minutes: Optional[int] = 60
    tag: Optional[str] = None # course/project
    priority: int = 3         # 1=high, 5=low
    source: Optional[str] = None

# ---------- Utilities ----------
def read_pdf_bytes(file_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        text = []
        for page in reader.pages:
            text.append(page.extract_text() or "")
        return "\n".join(text)
    except Exception:
        return ""

def parse_possible_date(s: str) -> Optional[datetime]:
    dt = dateparser.parse(
        s,
        settings={"TIMEZONE":"US/Pacific","RETURN_AS_TIMEZONE_AWARE":True}
    )
    if dt:
        return dt.astimezone(APP_TZ)
    return None

DUE_PAT = re.compile(
    r"(?:(?:due|deadline|submit|by)\s*:?|\b)\s*(?:on\s+)?"
    r"((?:\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b)?\s*[A-Z]?[a-z]{2,9}\s+\d{1,2}(?:,\s*\d{4})?"
    r"|(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
    r"|(?:tomorrow|today|next\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)))",
    re.IGNORECASE
)

EST_PAT = re.compile(r"(?:~?\s*)(\d+(?:\.\d+)?)\s*(h(?:ours?)?|m(?:in(?:s|utes)?)?)", re.IGNORECASE)
TAG_PAT = re.compile(r"\b(CS\d{1,3}|Calc\s*3|Linear\s*Algebra|Physics|Project|Work|Personal)\b", re.IGNORECASE)

def rule_based_extract(text: str, source_name: str = "input") -> List[Task]:
    tasks: List[Task] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        # Heuristic: lines with verbs/keywords become candidate tasks
        if re.search(r"\b(assign|finish|read|solve|submit|implement|study|review|fix|email|apply|prepare|meet|write)\b", ln, re.IGNORECASE):
            title = ln
            # Due date
            due = None
            m = DUE_PAT.search(ln)
            if m:
                due = parse_possible_date(m.group(1))
            # Estimate
            est = 60
            e = EST_PAT.search(ln)
            if e:
                val, unit = e.group(1), e.group(2).lower()
                est = int(round(float(val) * (60 if unit.startswith('h') else 1)))
            # Tag
            tg = None
            t = TAG_PAT.search(ln)
            if t:
                tg = t.group(0)
            # Priority heuristic (earlier due = higher)
            prio = 3
            if due:
                days = (due - datetime.now(APP_TZ)).total_seconds() / 86400
                if days <= 1: prio = 1
                elif days <= 3: prio = 2
                elif days >= 14: prio = 4
            tasks.append(Task(title=title, due=due, est_minutes=est, tag=tg, priority=prio, source=source_name))
    # If nothing found, create one generic task out of the blob
    if not tasks and text.strip():
        tasks.append(Task(title="Review: " + (text.strip()[:60].replace("\n"," ")+("..." if len(text)>60 else "")),
                          est_minutes=60, source=source_name))
    return tasks

LLM_SYS = """You extract structured tasks for a busy CS student named Chris.
Return a JSON list with objects: {title, due (ISO if present), est_minutes (int, default 60), tag, priority (1-5)}.
Prefer short, actionable titles. Infer reasonable estimates. If no due date, omit it.
"""

def llm_extract(text: str, source_name: str) -> List[Task]:
    try:
        msg = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {"role":"system","content":LLM_SYS},
                {"role":"user","content":text}
            ],
            response_format={"type":"json_object"}
        )
        content = msg.choices[0].message.content
        import json
        data = json.loads(content)
        raw = data.get("tasks") if "tasks" in data else data
        tasks: List[Task] = []
        for t in raw:
            due = None
            if t.get("due"):
                try:
                    due = dateparser.parse(t["due"])
                    if due and due.tzinfo is None:
                        due = due.replace(tzinfo=APP_TZ)
                    if due:
                        due = due.astimezone(APP_TZ)
                except Exception:
                    due = parse_possible_date(str(t["due"]))
            tasks.append(Task(
                title=t.get("title","Untitled task"),
                due=due,
                est_minutes=int(t.get("est_minutes",60) or 60),
                tag=t.get("tag"),
                priority=int(t.get("priority",3) or 3),
                source=source_name
            ))
        return tasks or rule_based_extract(text, source_name)
    except Exception:
        return rule_based_extract(text, source_name)

def extract_tasks_from_inputs(raw_texts: List[Tuple[str,str]]) -> List[Task]:
    all_tasks: List[Task] = []
    for source_name, text in raw_texts:
        if USE_LLM:
            tasks = llm_extract(text, source_name)
        else:
            tasks = rule_based_extract(text, source_name)
        all_tasks.extend(tasks)
    # De-duplicate by normalized title
    seen = set()
    deduped = []
    for t in all_tasks:
        key = re.sub(r"\s+"," ", t.title.strip().lower())
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return deduped

# ---------- Scheduling ----------
def next_work_start(after: datetime) -> datetime:
    # Move to within work hours
    local = after.astimezone(APP_TZ)
    if local.time() < WORK_START:
        local = local.replace(hour=WORK_START.hour, minute=WORK_START.minute, second=0, microsecond=0)
    elif local.time() >= WORK_END:
        local = (local + timedelta(days=1)).replace(hour=WORK_START.hour, minute=WORK_START.minute, second=0, microsecond=0)
    return local

def plan_blocks(tasks: List[Task], daily_hours: float = DEFAULT_DAILY_HOURS) -> List[dict]:
    """Greedy: fill up to daily_hours per day per task priority, stopping by due date."""
    now = datetime.now(APP_TZ)
    by_priority = sorted(tasks, key=lambda t: (t.priority, (t.due or (now+timedelta(days=30)))))
    blocks = []
    # Track used minutes per day
    day_budget = {}
    for t in by_priority:
        remaining = max(30, int(t.est_minutes or 60))  # min 30 min
        cursor = next_work_start(now)
        last_allowed = (t.due - timedelta(hours=1)) if t.due else now + timedelta(days=14)
        while remaining > 0 and cursor <= last_allowed:
            day_key = cursor.date().isoformat()
            used = day_budget.get(day_key, 0)
            cap = int(daily_hours * 60)
            if used >= cap:
                # advance to next day start
                cursor = next_work_start(cursor.replace(hour=WORK_END.hour, minute=WORK_END.minute) + timedelta(minutes=1))
                continue
            # determine block length within hour window and day cap
            block_len = min(BLOCK_MINUTES, remaining, cap - used)
            block_end = cursor + timedelta(minutes=block_len)
            # avoid crossing WORK_END
            if block_end.time() > WORK_END:
                cursor = next_work_start(cursor.replace(hour=WORK_END.hour, minute=WORK_END.minute) + timedelta(minutes=1))
                continue
            blocks.append({
                "task_id": t.id,
                "title": f"[{t.tag}] {t.title}" if t.tag else t.title,
                "start": cursor,
                "end": block_end,
                "due": t.due,
                "source": t.source
            })
            remaining -= block_len
            day_budget[day_key] = day_budget.get(day_key, 0) + block_len
            cursor = block_end + timedelta(minutes=10)  # short break
    return blocks

# ---------- Exports ----------
def to_ics(blocks: List[dict]) -> str:
    from dateutil import tz as _tz
    def fmt(dt: datetime):
        return dt.astimezone(_tz.UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//StudentAgent//EN"]
    for b in blocks:
        uid = str(uuid.uuid4())
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{fmt(datetime.now(APP_TZ))}",
            f"DTSTART:{fmt(b['start'])}",
            f"DTEND:{fmt(b['end'])}",
            f"SUMMARY:{b['title']}",
            f"DESCRIPTION:{'Auto-planned block'}",
            "END:VEVENT"
        ]
    lines.append("END:VCALENDAR")
    return "\n".join(lines)

def tasks_to_csv(tasks: List[Task]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","title","due","est_minutes","tag","priority","source"])
    for t in tasks:
        writer.writerow([t.id, t.title, t.due.isoformat() if t.due else "", t.est_minutes, t.tag or "", t.priority, t.source or ""])
    return output.getvalue()

FAR_FUTURE = datetime(9999, 12, 31, tzinfo=APP_TZ)  # tz-aware fallback

def tasks_to_notion_md(tasks: List[Task]) -> str:
    def sort_key(x: Task):
        # normalize due to tz-aware for consistent comparisons
        due = x.due
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=APP_TZ)
        return (x.priority, due or FAR_FUTURE)

    lines = ["# Tasks", ""]
    for t in sorted(tasks, key=sort_key):
        due_str = t.due.astimezone(APP_TZ).strftime("%a %b %d, %I:%M %p") if t.due else "—"
        lines.append(
            f"- **P{t.priority}** {t.title}  \n"
            f"  • Tag: `{t.tag or '-'}`  \n"
            f"  • Est: {t.est_minutes}m  \n"
            f"  • Due: {due_str}"
        )
    return "\n".join(lines)

def as_bytes(data):
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    return str(data or "").encode("utf-8")

__all__ = [
    "Task",
    "APP_TZ","DEFAULT_DAILY_HOURS","WORK_START","WORK_END","BLOCK_MINUTES",
    "USE_LLM",
    "read_pdf_bytes","parse_possible_date","rule_based_extract","llm_extract","extract_tasks_from_inputs",
    "next_work_start","plan_blocks",
    "to_ics","tasks_to_csv","tasks_to_notion_md","as_bytes"
]
