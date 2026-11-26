"""TC-REPLAN fixtures are synthetic; no facts describe real Busan venues."""
from datetime import date

import pytest

pytest.importorskip("pydantic")
from ourney_travelsync.optimizer.contracts import DayPlan, Lock, PlanRequest, Visit
from ourney_travelsync.optimizer.repair import rebuild_day, repair, same_day_request
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.replacement_intent import extract_replacement, resolve_target
from optimizer_fixtures import fact, request_data


def scenario(days=1):
    raw = request_data(count=6, days=days)
    req = PlanRequest.model_validate(raw)
    output = []
    for k, day in enumerate(req.days):
        shell = DayPlan(date=day.date, start_point="hotel", end_point="hotel", start_min=540,
                        items=(), end_arrival_min=900, return_travel_minutes=30,
                        total_cost=0, travel_minutes=0, walk_minutes=0)
        visits = [(Visit(poi_id=pid, arrival_min=570, start_min=start, departure_min=start+60,
                         travel_minutes=30, stay_minutes=60, visit_cost=200), start)
                  for pid, start in zip(("A", "B") if k == 0 else ("D", "E"), (600, 750))]
        output.append(rebuild_day(req, shell, visits))
    assert validate_final(req, tuple(output)).passed
    return req, tuple(output)


@pytest.mark.parametrize("text,name,category", [
    ("경성대학교 콘서트홀 가기 싫어 다른 곳으로 치환해줘.", "경성대학교 콘서트홀", None),
    ("경성대학교 콘서트홀 대신 다른 곳 추천해줘.", "경성대학교 콘서트홀", None),
    ("경성대학교 콘서트홀 빼고 다른 장소 넣어줘.", "경성대학교 콘서트홀", None),
    ("콘서트홀을 카페로 바꿔줘.", "콘서트홀", "cafe"),
    ("@TravelSync replace Concert Hall with a cafe", "Concert Hall", "cafe"),
])
def test_tc001_intent(text, name, category):
    intent = extract_replacement(text)
    assert intent["target_poi_name"] == name
    assert intent["replacement_constraints"].get("category") == category


def test_named_destination_is_preserved_as_a_constraint():
    assert extract_replacement("please replace Concert Hall with Gallery Z")["replacement_constraints"] == {
        "replacement_poi_name": "Gallery Z"}
    assert extract_replacement("콘서트홀 대신 갤러리 지 넣어줘")["replacement_constraints"] == {
        "replacement_poi_name": "갤러리 지"}


@pytest.mark.parametrize("index", [0, 1])
def test_tc002_003_004_025_fixed_with_boundary_nodes(index):
    req, days = scenario()
    out = repair(req, days, days[0].date, index, "C")
    assert out.strategy == "fixed" and validate_final(req, out.days).passed
    retained = 1-index
    assert out.days[0].items[retained].start_min == days[0].items[retained].start_min
    assert out.days[0].start_point == "hotel" and out.days[0].end_point == "hotel"


@pytest.mark.parametrize("field,reason", [("closed", "candidate_closed"), ("unknown", "opening_hours_unknown")])
def test_tc005_006_operating_rejected(field, reason):
    req, days = scenario()
    raw = req.model_dump(mode="json")
    op = raw["candidates"][2]["operations"][str(days[0].date)]
    op.update({"closed": fact(True)} if field == "closed" else {"openings": {}})
    out = repair(PlanRequest.model_validate(raw), days, days[0].date, 0, "C")
    assert not out.days and out.reason == reason


def test_tc007_too_far():
    req, days = scenario()
    raw = req.model_dump(mode="json")
    for e in raw["edges"]:
        if e["origin"] == "C" or e["destination"] == "C":
            e["duration_minutes"] = 900
    assert not repair(PlanRequest.model_validate(raw), days, days[0].date, 0, "C").days


def test_tc009_local_shift():
    req, days = scenario()
    raw = req.model_dump(mode="json")
    raw["candidates"][2]["duration_min"] = fact(140)
    req = PlanRequest.model_validate(raw)
    out = repair(req, days, days[0].date, 0, "C")
    assert out.strategy == "local", out
    assert out.days[0].items[1].start_min == 770
    assert validate_final(req, out.days).passed


def test_tc010_same_day_scope_and_mandatory_visits():
    req, days = scenario(2)
    reduced = same_day_request(req, days, days[0].date, 0, "C", seconds=1)
    assert len(reduced.days) == 1
    assert {p.poi_id for p in reduced.candidates} == {"B", "C"}
    assert {l.poi_id for l in reduced.locked_items} == {"B", "C"}
    assert all(e.date == days[0].date for e in reduced.edges)
    assert reduced.preferred_start_minutes == {"C": 600, "B": 750}


def test_same_day_solver_minimizes_deviation_and_preserves_feasibility():
    pytest.importorskip("ortools")
    from ourney_travelsync.optimizer.api import optimize
    req, days = scenario()
    reduced = same_day_request(req, days, days[0].date, 0, "C", seconds=2)
    solved = optimize(reduced)
    assert solved.validation_status == "PASSED"
    assert solved.phases[0].objective == "schedule_deviation_minutes"
    assert {v.poi_id: v.start_min for v in solved.days[0].items} == {"C": 600, "B": 750}


def test_tc014_budget_is_not_relaxed():
    req, days = scenario()
    raw = req.model_dump(mode="json")
    raw["budget_total"] = days[0].total_cost
    raw["candidates"][2]["cost_per_person"] = fact(10000)
    out = repair(PlanRequest.model_validate(raw), days, days[0].date, 0, "C")
    assert not out.days and any("BUDGET" in e for e in out.errors)


def test_tc015_exclusion_and_tc020_e3():
    req, days = scenario()
    raw = req.model_dump(mode="json")
    raw["members"][0]["excluded_poi_ids"] = ["C"]
    out = repair(PlanRequest.model_validate(raw), days, days[0].date, 0, "C")
    assert not out.days and any("EXPLICIT_EXCLUSION" in e for e in out.errors)


def test_tc016_locked_target_and_locked_suffix():
    req, days = scenario()
    assert repair(req.model_copy(update={"locked_items": (Lock(poi_id="A", date=days[0].date),)}), days,
                  days[0].date, 0, "C").reason == "locked_activity_conflict"
    raw = req.model_dump(mode="json")
    raw["candidates"][2]["duration_min"] = fact(140)
    locked = PlanRequest.model_validate(raw).model_copy(update={"locked_items": (Lock(poi_id="B", date=days[0].date, start_min=750),)})
    assert not repair(locked, days, days[0].date, 0, "C").days


def test_tc021_duplicate_name_occurrences():
    days = [{"date": "2026-11-30", "items": [{"poi_id": "A", "start": "19:08"}]},
            {"date": "2026-12-01", "items": [{"poi_id": "A", "start": "19:08"}]}]
    intent = extract_replacement("경성대학교 콘서트홀 대신 다른 곳 추천해줘")
    names = {"A": ["경성대학교 콘서트홀", "Concert Hall"]}
    assert resolve_target(intent, days, names)[1] == "ambiguous_target_poi"
    intent["target_poi_name"] = "2026-11-30 19:08 콘서트홀"
    assert resolve_target(intent, days, names)[0]["date"] == "2026-11-30"
    intent["target_poi_name"] = "Concert Hall"
    assert resolve_target(intent, days[:1], names)[0]


def test_tc022_other_days_unchanged():
    req, days = scenario(2)
    out = repair(req, days, days[0].date, 0, "C")
    assert out.days[1] == days[1]


def test_tc026_multiple_openings_full_visit_must_fit_one():
    req, days = scenario()
    raw = req.model_dump(mode="json")
    raw["candidates"][2]["operations"][str(days[0].date)]["openings"] = fact([
        dict(start_min=600, end_min=630), dict(start_min=640, end_min=670)])
    assert not repair(PlanRequest.model_validate(raw), days, days[0].date, 0, "C").days
    raw["candidates"][2]["operations"][str(days[0].date)]["openings"] = fact([
        dict(start_min=600, end_min=630), dict(start_min=640, end_min=730)])
    out = repair(PlanRequest.model_validate(raw), days, days[0].date, 0, "C")
    assert out.days and out.days[0].items[0].start_min == 640


def test_tc027_unknown_route():
    req, days = scenario()
    req = req.model_copy(update={"edges": tuple(e for e in req.edges if e.destination != "C")})
    assert not repair(req, days, days[0].date, 0, "C").days


def test_original_korean_hall_request_at_1908_with_labeled_evidence():
    # Reproduce the reported date/time and name only. Operational/routing facts
    # below are explicitly synthetic, not recovered historical production data.
    raw = request_data(count=4)
    day = "2026-11-30"
    raw["days"] = [dict(date=day, start_min=1080, end_min=1380, start_point="hotel", end_point="hotel")]
    for m in raw["members"]:
        m["available"] = {day: [dict(start_min=1080, end_min=1380)]}
    for p in raw["candidates"]:
        p["operations"] = {day: dict(openings=fact([dict(start_min=1080, end_min=1380)]), closed=fact(False))}
    for e in raw["edges"]:
        e["date"] = day
    req = PlanRequest.model_validate(raw)
    original = scenario()[1][0].model_copy(update={"date": date.fromisoformat(day), "start_min": 1080})
    original = rebuild_day(req, original, [(original.items[0], 1148), (original.items[1], 1290)])
    intent = extract_replacement("경성대학교 콘서트홀 가기 싫어 다른 곳으로 치환해줘.")
    displayed = [{"date": day, "items": [{"poi_id": "A", "start": "19:08"}, {"poi_id": "B", "start": "21:30"}]}]
    target, error = resolve_target(intent, displayed, {"A": ["경성대학교 콘서트홀"]})
    assert not error and target["item_id"] == "2026-11-30@19:08"
    successful = repair(req, (original,), original.date, 0, "C")
    assert successful.strategy == "fixed" and successful.days[0].items[0].start_min == 1148
    unknown = req.model_dump(mode="json")
    unknown["candidates"][2]["operations"][day]["openings"] = {}
    assert repair(PlanRequest.model_validate(unknown), (original,), original.date, 0, "C").reason == "opening_hours_unknown"
