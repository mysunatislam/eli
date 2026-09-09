"""Small natural-language time parser for reminders and recurring jobs.

parse_when("in 20 minutes")            -> {"kind": "once",  "at": <ts>}
parse_when("at 5pm")                   -> {"kind": "once",  "at": <today/tomorrow 17:00>}
parse_when("tomorrow at 9")            -> {"kind": "once",  "at": ...}
parse_when("every 30 minutes")         -> {"kind": "every", "seconds": 1800, "at": now+1800}
parse_when("every day at 9am")         -> {"kind": "every", "seconds": 86400, "at": next 09:00}
parse_when("hourly") / ("daily")       -> every 3600 / 86400
strip_when(text) removes the matched time phrase so the rest is the job text.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional

UNITS = {"second": 1, "sec": 1, "s": 1, "minute": 60, "min": 60, "m": 60, "hour": 3600, "hr": 3600, "h": 3600,
         "day": 86400, "d": 86400, "week": 604800}
NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
             "half an": 0.5, "half a": 0.5, "quarter of an": 0.25}
_num = r"(\d+(?:\.\d+)?|half an|half a|quarter of an|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|fifty|sixty)"
_unit = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
IN_RE = re.compile(rf"\bin\s+{_num}\s*{_unit}\b", re.I)
EVERY_RE = re.compile(rf"\bevery\s+(?:{_num}\s*)?{_unit}\b", re.I)
EVERY_WORD_RE = re.compile(r"\b(hourly|daily|weekly|every morning|every evening|every night|every day|every hour|every week)\b", re.I)
AT_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b(?!\s*(?:mm|cm|%))", re.I)
TOMORROW_RE = re.compile(r"\btomorrow\b", re.I)
TONIGHT_RE = re.compile(r"\btonight\b", re.I)


def _n(s: str) -> float:
    s = s.lower().strip()
    if s in NUM_WORDS:
        return float(NUM_WORDS[s])
    try:
        return float(s)
    except ValueError:
        return 1.0


def _unit_seconds(u: str) -> int:
    u = u.lower().rstrip("s") if not u.lower().endswith("ss") else u.lower()
    for k, v in UNITS.items():
        if u.startswith(k):
            return v
    return 60


def _clock(hour: int, minute: int, ampm: Optional[str], base: datetime) -> datetime:
    if ampm:
        ap = ampm.lower().replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
    elif hour <= 7:          # "at 5" with no am/pm during the day means 17:00
        if base.hour >= hour:
            hour += 12
    t = base.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if t <= base:
        t += timedelta(days=1)
    return t


def parse_when(text: str, now: Optional[float] = None) -> Optional[dict]:
    base = datetime.fromtimestamp(now or time.time())
    m = EVERY_RE.search(text)
    if m:
        n = _n(m.group(1)) if m.group(1) else 1.0
        secs = max(60, int(n * _unit_seconds(m.group(2))))
        first = base + timedelta(seconds=secs)
        at = AT_RE.search(text)
        if at and secs >= 86400:
            first = _clock(int(at.group(1)), int(at.group(2) or 0), at.group(3), base)
        return {"kind": "every", "seconds": secs, "at": first.timestamp()}
    m = EVERY_WORD_RE.search(text)
    if m:
        w = m.group(1).lower()
        secs = 3600 if "hour" in w else (604800 if "week" in w else 86400)
        first = base + timedelta(seconds=secs)
        at = AT_RE.search(text)
        if at:
            first = _clock(int(at.group(1)), int(at.group(2) or 0), at.group(3), base)
        elif "morning" in w:
            first = _clock(9, 0, "am", base)
        elif "evening" in w or "night" in w:
            first = _clock(19, 0, None, base)
        return {"kind": "every", "seconds": secs, "at": first.timestamp()}
    m = IN_RE.search(text)
    if m:
        secs = max(5, int(_n(m.group(1)) * _unit_seconds(m.group(2))))
        return {"kind": "once", "at": (base + timedelta(seconds=secs)).timestamp()}
    day_shift = 1 if TOMORROW_RE.search(text) else 0
    m = AT_RE.search(text)
    if m:
        t = _clock(int(m.group(1)), int(m.group(2) or 0), m.group(3), base)
        if day_shift and t.date() == base.date():
            t += timedelta(days=1)
        return {"kind": "once", "at": t.timestamp()}
    if TONIGHT_RE.search(text):
        return {"kind": "once", "at": _clock(20, 0, None, base).timestamp()}
    if day_shift:
        t = (base + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return {"kind": "once", "at": t.timestamp()}
    return None


def strip_when(text: str) -> str:
    out = text
    for rx in (EVERY_RE, EVERY_WORD_RE, IN_RE, AT_RE, TOMORROW_RE, TONIGHT_RE):
        out = rx.sub(" ", out)
    out = re.sub(r"^\s*(?:to|and|then|,)\s+", "", out.strip(), flags=re.I)
    out = re.sub(r"\s+", " ", out).strip(" ,.")
    return out


def describe_when(when: dict) -> str:
    if when["kind"] == "every":
        s = when["seconds"]
        if s % 86400 == 0:
            return f"every {s // 86400} day{'s' if s // 86400 != 1 else ''} at {datetime.fromtimestamp(when['at']).strftime('%H:%M')}"
        if s % 3600 == 0:
            return f"every {s // 3600} hour{'s' if s // 3600 != 1 else ''}"
        return f"every {max(1, s // 60)} min"
    dt = datetime.fromtimestamp(when["at"])
    delta = when["at"] - time.time()
    if delta < 3600 * 12 and dt.date() == datetime.now().date():
        return "at " + dt.strftime("%H:%M")
    return dt.strftime("%a %d %b %H:%M")
