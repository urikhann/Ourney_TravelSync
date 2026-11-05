"""Turn one row of the operating-info CSV into opening hours we can store.

Rules from the data teammate (travelsync_operating_info_report):
  - hours_scope PUBLISHED_SCHEDULE -> the place's regular weekly hours            -> PoiHours rows
  - hours_scope SNAPSHOT_WEEK      -> only true for hours_valid_from..until dates -> PoiException rows (dated)
  - anything else (NULL scope, NOT_FOUND, AMBIGUOUS, RAW_ONLY, FETCH_ERROR)       -> unknown, store nothing
  - closed_day NULL means "not known", never "open every day"
  - last order and break times are not the closing time (we only read start_end_time_desc)
  - 24:00 / closing after midnight: a close time at or before the open time means "the next day"
  - festivals never get recurring hours; a temporary closure on given dates (e.g. "10/26(월)") hides the
    real weekly pattern, so those places stay unknown too

Pure functions only (no database), so they are easy to test.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date

DOW_KO = {"일": 0, "월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6}  # same numbering as PoiHours: 0 = Sunday
ALL_WEEK = tuple(range(7))
TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})")
WEEKDAY = re.compile(r"^(?:매주\s*)?([월화수목금토일])요일$")
ALWAYS_OPEN = ("상시 개방", "상시개방", "24시간")
USABLE = {"FOUND", "VARIABLE_HOURS"}  # collection_status values we trust enough to parse


@dataclass
class ParsedHours:
    hours: str = "UNKNOWN"                # KNOWN (weekly) | SNAPSHOT (dated, one week) | UNKNOWN
    closed_days: str = "UNKNOWN"          # KNOWN | PARTIAL (some rules we can't store) | UNKNOWN
    weekly: dict[int, tuple[str, str]] = field(default_factory=dict)            # dow -> (open, close)
    dated: list[tuple[date, str | None, str | None]] = field(default_factory=list)  # (day, open, close); None = closed
    closed_on_public_holidays: bool = False


def _missing(v) -> bool:
    return v is None or str(v).strip() in ("", "NULL", "null", "None")


def _hhmm(h: str, m: str) -> str:
    return f"{int(h):02d}:{m}"


def time_range(text: str | None) -> tuple[str, str] | None:
    """'11:00 ~ 21:30' -> ('11:00', '21:30'). If there are two ranges (lunch + dinner) we take the first open
    and the last close. A close of 00:00 becomes 24:00 (midnight); 02:00 stays 02:00 (= next day)."""
    found = TIME_RANGE.findall(text or "")
    if not found:
        return None
    o = _hhmm(found[0][0], found[0][1])
    c = _hhmm(found[-1][2], found[-1][3])
    if c == "00:00":
        c = "24:00"
    return o, c


def parse_closed_days(text: str | None) -> tuple[set[int], bool, str]:
    """'매주 월요일, 공휴일' -> ({1}, True, 'KNOWN').
    Returns (weekly closed days, closed on public holidays, status). Rules we cannot store as weekly
    (설날/추석, 1월1일, '매월 첫째 일요일', different floors…) make the status PARTIAL."""
    if _missing(text):
        return set(), False, "UNKNOWN"
    t = text.strip()
    if "|" in t:  # e.g. different rules per floor
        return set(), False, "PARTIAL"
    if t.startswith("연중무휴"):
        return set(), False, "KNOWN" if t == "연중무휴" else "PARTIAL"
    status = "KNOWN"
    if re.search(r"\(.*?\)", t):  # e.g. "(월요일이 공휴일인 경우 다음날 휴관)" — we keep the main rule only
        t, status = re.sub(r"\(.*?\)", "", t), "PARTIAL"
    days, holidays = set(), False
    for piece in (p.strip() for p in t.split(",")):
        if not piece:
            continue
        m = WEEKDAY.match(piece)
        if m:
            days.add(DOW_KO[m.group(1)])
        elif piece == "공휴일":
            holidays = True
        else:
            status = "PARTIAL"
    return days, holidays, status


def _published_json(raw: dict) -> dict[int, tuple[str, str]] | None:
    """Kakao 'all' block: one '기본 영업시간' (regular hours) period with a day list.
    Seasonal or date-specific periods (e.g. '매년 3월 ~ 10월') -> None (too complex, stays unknown)."""
    periods = (raw.get("all") or {}).get("periods") or []
    basic = [p for p in periods if p.get("period_title") == "기본 영업시간"]
    other = [p for p in periods if p.get("period_title") not in ("기본 영업시간", "공휴일")]
    if len(basic) != 1 or other:
        return None
    weekly = {}
    for d in basic[0].get("days") or []:
        rng = time_range((d.get("on_days") or {}).get("start_end_time_desc"))
        name = d.get("day_of_the_week")
        if not rng:
            continue
        if name == "매일":
            weekly.update({k: rng for k in ALL_WEEK})
        elif name in DOW_KO:
            weekly[DOW_KO[name]] = rng
        else:
            return None
    return weekly or None


def _snapshot_days(raw: dict, valid_from: date, valid_until: date) -> list[tuple[date, str | None, str | None]]:
    """Kakao 'week_from_today': '월(10/26)' + times or '휴무일'. Only days inside the valid period are kept."""
    out = []
    for period in (raw.get("week_from_today") or {}).get("week_periods") or []:
        for d in period.get("days") or []:
            m = re.search(r"\((\d{1,2})/(\d{1,2})\)", d.get("day_of_the_week_desc") or "")
            if not m:
                continue
            month, day = int(m.group(1)), int(m.group(2))
            year = valid_from.year if month >= valid_from.month else valid_until.year  # week across New Year
            try:
                when = date(year, month, day)
            except ValueError:
                continue
            if not valid_from <= when <= valid_until:
                continue
            if d.get("off_days_desc"):
                out.append((when, None, None))
            else:
                rng = time_range((d.get("on_days") or {}).get("start_end_time_desc"))
                if rng:
                    out.append((when, *rng))
    return out


def _json(text: str | None) -> dict | None:
    if _missing(text) or not text.lstrip().startswith("{"):
        return None
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def parse_row(row: dict) -> ParsedHours:
    """One operating-info CSV row -> what we store. Unknown stays unknown."""
    out = ParsedHours()
    if (row.get("collection_status") or "").strip() not in USABLE:
        return out
    if str(row.get("festival", "")).strip() == "1" or "축제" in (row.get("category_name") or ""):
        return out  # festivals run on their own dates, never on a weekly schedule
    scope = (row.get("hours_scope") or "").strip()
    raw_text = row.get("operating_hours_raw")
    raw = _json(raw_text)

    if scope == "PUBLISHED_SCHEDULE":
        if re.search(r"\d{1,2}/\d{1,2}", row.get("closed_day") or ""):
            return out  # temporary closure on specific dates: the listed days are not the normal week
        closed, holidays, closed_status = parse_closed_days(row.get("closed_day"))
        weekly = _published_json(raw) if raw else None
        listed_days = weekly is not None
        if weekly is None and not _missing(row.get("opening_time")) and not _missing(row.get("closing_time")):
            o, c = row["opening_time"].strip()[:5], row["closing_time"].strip()[:5]
            if c == "00:00" and str(row.get("closing_next_day", "")).lower() == "true":
                c = "24:00"
            weekly = {k: (o, c) for k in ALL_WEEK}
        if weekly is None and not raw and any(s in (raw_text or "") for s in ALWAYS_OPEN):
            weekly, closed, closed_status = {k: ("00:00", "24:00") for k in ALL_WEEK}, set(), "KNOWN"
        if not weekly:
            return out
        out.weekly = {k: v for k, v in weekly.items() if k not in closed}
        out.hours = "KNOWN"
        # A day list that skips a weekday already tells us it is closed that day.
        out.closed_days = "KNOWN" if listed_days and closed_status == "UNKNOWN" and len(weekly) < 7 else closed_status
        out.closed_on_public_holidays = holidays
        return out

    if scope == "SNAPSHOT_WEEK" and raw:
        try:
            start, end = date.fromisoformat(row["hours_valid_from"]), date.fromisoformat(row["hours_valid_until"])
        except (KeyError, TypeError, ValueError):
            return out
        out.dated = _snapshot_days(raw, start, end)
        if out.dated:
            out.hours = "SNAPSHOT"
    return out
