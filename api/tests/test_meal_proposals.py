"""Meal tool previews use real offline CP-SAT/E3 without replacing saved plans."""
from .test_optimizer_integration import audits, c, configuration, generate, ready


def proposal(h, tid, **change):
    return c.post(f"/trips/{tid}/itineraries/meal-proposals",headers=h,
                  json=dict(configuration=configuration(),change=dict(date="2026-11-06",name="lunch",**change)))


def test_preview_reoptimizes_without_changing_approved_plan_or_votes(planning_adapter):
    h,tid,_=ready(individual_method="feature_mean")
    old=generate(h,tid).json()["plans"][0]
    c.post(f"/itineraries/{old['id']}/vote",headers=h)
    response=proposal(h,tid,fixed_start_min=900)
    assert response.status_code==200,response.text
    result=response.json()
    assert result["status"]=="AWAITING_APPROVAL" and result["validation_status"]=="PASSED"
    assert any(v["start_min"]==900 for d in result["days"] for v in d["items"])
    saved=c.get(f"/trips/{tid}/itineraries",headers=h).json()
    assert saved["plans"][0]=={**old,"votes":1}
    assert saved["my_vote"]==old["id"]
    audit=audits(tid)[-1]
    assert audit.status=="PREVIEWED" and audit.itinerary_id is None
    assert audit.optimizer_request["enforce_cost"] is False
    assert audit.optimizer_request["enforce_opening_hours"] is False
    assert audit.optimizer_request==audit.response["input_snapshot"]


def test_infeasible_change_keeps_plan_and_reports_conflict(planning_adapter):
    h,tid,_=ready(individual_method="feature_mean")
    old=generate(h,tid).json()["plans"][0]
    response=proposal(h,tid,fixed_start_min=1070)
    assert response.status_code==409,response.text
    assert response.json()["detail"]["conflicts"]
    assert c.get(f"/trips/{tid}/itineraries",headers=h).json()["plans"][0]==old


def test_invalid_day_and_time_return_validation_errors(planning_adapter):
    h,tid,_=ready(individual_method="feature_mean")
    response=proposal(h,tid,fixed_start_min=1200)
    assert response.status_code==422
    assert "outside travel" in response.text
    assert not planning_adapter.calls


def test_meal_proposals_require_authentication():
    assert c.post("/trips/missing/itineraries/meal-proposals",json={}).status_code in (401,422)


def test_member_cannot_use_owner_only_meal_tool(planning_adapter):
    from uuid import uuid4
    from .test_flow import signup
    owner,tid,_=ready(individual_method="feature_mean")
    member=signup("MealMember"+uuid4().hex[:8])
    code=c.get(f"/trips/{tid}",headers=owner).json()["invite_code"]
    assert c.post(f"/trips/join/{code}",headers=member).status_code==200
    assert proposal(member,tid,fixed_start_min=810).status_code==403
    assert not planning_adapter.calls
