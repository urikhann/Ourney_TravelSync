"""Opening-hours rules from the data teammate's operating-info report (app/hours.py + the mock checker)."""
import json
from datetime import date

from app.hours import parse_closed_days, parse_row, time_range


def row(**kw):
    base = {"collection_status": "FOUND", "hours_scope": "PUBLISHED_SCHEDULE", "opening_time": "NULL",
            "closing_time": "NULL", "closed_day": "NULL", "operating_hours_raw": "NULL", "closing_next_day": "NULL",
            "hours_valid_from": "NULL", "hours_valid_until": "NULL", "festival": "0", "category_name": ""}
    return {**base, **kw}


def test_time_range_midnight_and_split_shifts():
    assert time_range("11:00 ~ 21:30") == ("11:00", "21:30")
    assert time_range("17:00 ~ 24:00") == ("17:00", "24:00")
    assert time_range("12:00 ~ 00:00") == ("12:00", "24:00")       # midnight
    assert time_range("17:00 ~ 02:00") == ("17:00", "02:00")       # next day, the checker adds 24h
    assert time_range("11:00 ~ 15:00, 17:00 ~ 22:00") == ("11:00", "22:00")
    assert time_range("휴무") is None


def test_closed_days():
    assert parse_closed_days("NULL") == (set(), False, "UNKNOWN")  # unknown, NOT "open every day"
    assert parse_closed_days("연중무휴") == (set(), False, "KNOWN")
    assert parse_closed_days("매주 월요일, 공휴일") == ({1}, True, "KNOWN")
    assert parse_closed_days("매주 월요일, 화요일") == ({1, 2}, False, "KNOWN")
    assert parse_closed_days("매주 일요일, 설날 당일, 추석 당일") == ({0}, False, "PARTIAL")
    assert parse_closed_days("매주 월요일(월요일이 공휴일인 경우 다음날 휴관), 1월1일")[2] == "PARTIAL"


def test_published_text_hours_minus_closed_days():
    h = parse_row(row(opening_time="09:00", closing_time="18:00", closed_day="매주 월요일"))
    assert h.hours == "KNOWN" and h.closed_days == "KNOWN"
    assert 1 not in h.weekly and h.weekly[3] == ("09:00", "18:00") and len(h.weekly) == 6


def test_published_with_unknown_closed_days_keeps_unknown_flag():
    h = parse_row(row(opening_time="10:00", closing_time="17:00"))
    assert h.hours == "KNOWN" and len(h.weekly) == 7 and h.closed_days == "UNKNOWN"


def test_published_json_day_list_and_overnight():
    raw = {"all": {"periods": [{"period_title": "기본 영업시간", "days": [
        {"day_of_the_week": d, "on_days": {"start_end_time_desc": "18:00 ~ 02:00"}} for d in "화수목금토일"]}]}}
    h = parse_row(row(operating_hours_raw=json.dumps(raw, ensure_ascii=False)))
    assert h.hours == "KNOWN" and 1 not in h.weekly and h.weekly[5] == ("18:00", "02:00")
    assert h.closed_days == "KNOWN"  # Monday is not in the published list


def test_seasonal_json_stays_unknown():
    raw = {"all": {"periods": [
        {"period_title": "매년 3월 ~ 10월", "days": [{"day_of_the_week": "매일", "on_days": {"start_end_time_desc": "09:00 ~ 18:00"}}]},
        {"period_title": "매년 11월 ~ 2월", "days": [{"day_of_the_week": "매일", "on_days": {"start_end_time_desc": "09:00 ~ 17:00"}}]}]}}
    h = parse_row(row(collection_status="VARIABLE_HOURS", operating_hours_raw=json.dumps(raw, ensure_ascii=False)))
    assert h.hours == "UNKNOWN" and not h.weekly


def test_always_open_text():
    h = parse_row(row(operating_hours_raw="상시 개방"))
    assert h.hours == "KNOWN" and h.weekly[0] == ("00:00", "24:00") and len(h.weekly) == 7


def test_festival_and_bad_status_and_temporary_closure_stay_unknown():
    assert parse_row(row(opening_time="10:00", closing_time="18:00", festival="1")).hours == "UNKNOWN"
    assert parse_row(row(opening_time="10:00", closing_time="18:00", collection_status="AMBIGUOUS")).hours == "UNKNOWN"
    assert parse_row(row(opening_time="10:00", closing_time="18:00", hours_scope="NULL")).hours == "UNKNOWN"
    assert parse_row(row(opening_time="07:00", closing_time="22:00", closed_day="10/26(월), 10/27(화)")).hours == "UNKNOWN"


def test_snapshot_week_is_dated_only():
    raw = {"week_from_today": {"week_periods": [{"days": [
        {"day_of_the_week_desc": "월(10/26)", "on_days": {"start_end_time_desc": "11:00 ~ 21:30"}},
        {"day_of_the_week_desc": "화(10/27)", "off_days_desc": "휴무일"},
        {"day_of_the_week_desc": "월(11/2)", "on_days": {"start_end_time_desc": "11:00 ~ 21:30"}}]}]}}
    h = parse_row(row(hours_scope="SNAPSHOT_WEEK", opening_time="11:00", closing_time="21:30",
                      hours_valid_from="2026-10-26", hours_valid_until="2026-11-01",
                      operating_hours_raw=json.dumps(raw, ensure_ascii=False)))
    assert h.hours == "SNAPSHOT" and not h.weekly  # never turned into weekly hours
    assert h.dated == [(date(2026, 10, 26), "11:00", "21:30"), (date(2026, 10, 27), None, None)]  # 11/2 is outside


def test_checker_says_unknown_not_closed():
    from tests.conftest import mocks
    from app.db import SessionLocal
    from app.models import PoiHours
    with SessionLocal() as s:
        saved = [(h.dow, h.open, h.close) for h in s.query(PoiHours).filter_by(poi_id="gamcheon")]
        s.query(PoiHours).filter_by(poi_id="gamcheon").delete()
        s.commit()
    try:
        r = mocks.check_one("gamcheon", date(2026, 10, 10), "14:00", 90)
        assert r["available"] is None and "unknown" in r["reason"].lower()
    finally:
        with SessionLocal() as s:
            s.add_all(PoiHours(poi_id="gamcheon", dow=d, open=o, close=c) for d, o, c in saved)
            s.commit()
