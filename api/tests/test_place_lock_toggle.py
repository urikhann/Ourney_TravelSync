"""Owner lock edits preserve the schedule and invalidate stale conversational work."""
from copy import deepcopy

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Itinerary, OptimizationRun
from app.optimization import fingerprint
from .test_optimizer_integration import c
from .test_proposal_approval import planned_trip, replacement_fixture_clock


def toggle(headers, tid, plan, item, locked, revision=0):
    return c.post(f"/trips/{tid}/itineraries/{plan['id']}/lock", headers=headers,
                  json=dict(item_id=f"{plan['days'][0]['date']}@{item['start']}",
                            locked=locked, expected_revision=revision))


def test_owner_can_lock_and_unlock_without_changing_schedule_or_votes(planning_adapter, replacement_fixture_clock):
    heads, tid, plan = planned_trip()
    item = plan['days'][0]['items'][0]
    assert not item['locked']
    c.post(f"/itineraries/{plan['id']}/vote", headers=heads[1])
    with SessionLocal() as db:
        original = deepcopy(db.get(Itinerary, plan['id']).days)
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == plan['id']))
        replay = deepcopy(audit.optimizer_request)
    result = toggle(heads[0], tid, plan, item, True)
    assert result.status_code == 200, result.text
    view = result.json()
    assert view['plans'][0]['days'][0]['items'][0]['locked']
    assert view['voted'] == 1 and view['chosen_id'] is None
    assert len(view['setup']['configuration']['locked_items']) == 1
    assert toggle(heads[0], tid, plan, item, False).json()['detail'] == 'place_lock_changed'
    with SessionLocal() as db:
        assert fingerprint(db.get(Itinerary, plan['id']).days) != fingerprint(original)
    result = toggle(heads[0], tid, plan, item, False, revision=1)
    assert result.status_code == 200, result.text
    assert not result.json()['plans'][0]['days'][0]['items'][0]['locked']
    assert result.json()['setup']['configuration']['locked_items'] == []
    assert result.json()['plans'][0]['lock_revision'] == 2
    with SessionLocal() as db:
        after = db.get(Itinerary, plan['id']).days
        for day_before, day_after in zip(original, after):
            for before, current in zip(day_before['items'], day_after['items']):
                assert {k: v for k, v in before.items() if k not in ('locked', 'lock_reason')} == {
                    k: v for k, v in current.items() if k not in ('locked', 'lock_reason')}
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == plan['id']))
        assert audit.optimizer_request == replay
        assert len(audit.input['lock_edits']) == 2


def test_non_owner_cannot_change_locks(planning_adapter, replacement_fixture_clock):
    heads, tid, plan = planned_trip()
    assert toggle(heads[1], tid, plan, plan['days'][0]['items'][0], True).status_code == 403


def test_edit_preserves_temporary_retention_provenance(planning_adapter, replacement_fixture_clock):
    heads, tid, plan = planned_trip()
    first, second = plan['days'][0]['items'][:2]
    with SessionLocal() as db:
        it = db.get(Itinerary, plan['id'])
        days = deepcopy(it.days)
        days[0]['items'][1].update(locked=True, lock_reason=None)
        it.days = days
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == plan['id']))
        audit.input = {**audit.input, 'lock_provenance': {}, 'temporary_lock_ids': [second['poi_id']]}
        db.commit()
    result = toggle(heads[0], tid, plan, first, True)
    assert result.status_code == 200, result.text
    items = result.json()['plans'][0]['days'][0]['items']
    assert items[0]['locked'] and not items[1]['locked']


def test_reservations_and_completed_activities_cannot_be_unlocked(planning_adapter, replacement_fixture_clock):
    heads, tid, plan = planned_trip()
    for flag in ('reservation_confirmed', 'completed'):
        with SessionLocal() as db:
            it = db.get(Itinerary, plan['id'])
            days = deepcopy(it.days)
            days[0]['items'][0][flag] = True
            it.days = days
            db.commit()
        result = toggle(heads[0], tid, plan, plan['days'][0]['items'][0], False)
        assert result.status_code == 409 and result.json()['detail'] == 'place_lock_protected'
        with SessionLocal() as db:
            it = db.get(Itinerary, plan['id'])
            days = deepcopy(it.days)
            days[0]['items'][0].pop(flag)
            it.days = days
            db.commit()
