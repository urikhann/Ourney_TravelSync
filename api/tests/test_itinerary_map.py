"""Map endpoints belong to the displayed itinerary, never a later attempt."""
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Itinerary, OptimizationRun, Vote
from app.routes.itineraries import map_endpoints
from .test_optimizer_integration import c
from .test_proposal_approval import planned_trip, replacement_fixture_clock, swap_for, ask, vote


def test_map_uses_saved_endpoints_after_a_new_attempt_without_mutating_plan(planning_adapter, replacement_fixture_clock):
    heads, tid, plan = planned_trip()
    day = plan['days'][0]
    with SessionLocal() as db:
        base = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == plan['id']))
        configuration = deepcopy(base.input['configuration'])
        saved = deepcopy(db.get(Itinerary, plan['id']).days)
        replay = deepcopy(base.optimizer_request)
        expected = {kind: configuration['days'][0][f'{kind}_point'] for kind in ('start', 'end')}
        configuration['days'][0]['start_point'] = dict(label='Later attempt', latitude=37.5, longitude=127.0)
        db.add(OptimizationRun(trip_id=tid, recommendation_run_id=base.recommendation_run_id,
            requested_by=base.requested_by, status='FAILED', error_code='SEARCH_TIMEOUT',
            input={'configuration': configuration}))
        db.commit()
    response = c.get(f'/trips/{tid}/itineraries', headers=heads[1])
    assert response.status_code == 200, response.text
    view = response.json()
    assert view['plans'][0]['days'][0]['map_endpoints'] == expected == day['map_endpoints']
    assert view['setup']['configuration']['days'][0]['start_point']['label'] == 'Later attempt'
    assert view['voted'] == 0
    with SessionLocal() as db:
        assert db.get(Itinerary, plan['id']).days == saved
        assert db.get(OptimizationRun, base.id).optimizer_request == replay
        assert not db.scalar(select(Vote).where(Vote.trip_id == tid))


def test_missing_legacy_endpoints_are_not_replaced_with_other_days_or_private_fields():
    point = dict(label='Saved start', latitude=35.1, longitude=129.1, private_note='DO_NOT_EXPOSE')
    audit = SimpleNamespace(input={'configuration': {'days': [{'date': '2026-11-30', 'start_point': point}]}})
    assert map_endpoints(audit, '2026-11-30') == {
        'start': dict(label='Saved start', latitude=35.1, longitude=129.1), 'end': None}
    assert map_endpoints(audit, '2026-12-01') == {'start': None, 'end': None}
    assert map_endpoints(SimpleNamespace(input={}), '2026-11-30') == {'start': None, 'end': None}


def test_each_saved_plan_loads_its_own_pinned_place_coordinates(planning_adapter, replacement_fixture_clock, monkeypatch):
    from app.routes import itineraries
    heads, tid, old = planned_trip(1)
    _, _, suggestion = swap_for(tid, old)
    message = ask(heads[0], tid, suggestion)
    assert vote(heads[0], tid, message['id']).json()['messages'][0]['agent']['proposal_status'] == 'APPLIED'
    # Simulate an older catalogue snapshot at the metadata-loading boundary.
    # No active dataset, imported POI, score or saved schedule is modified.
    with SessionLocal() as db:
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == old['id']))
        audit.optimizer_request = {**audit.optimizer_request, 'dataset_version': 'older-map-fixture'}
        db.commit()
    original_loader = itineraries.load_places
    calls = []
    def pinned_loader(db, version, ids):
        calls.append(version)
        rows = original_loader(db, old['stats']['dataset_version'], ids)
        return {pid: {**place, 'lat': 35.0 if version == 'older-map-fixture' else 36.0} for pid, place in rows.items()}
    monkeypatch.setattr(itineraries, 'load_places', pinned_loader)
    view = c.get(f'/trips/{tid}/itineraries', headers=heads[0]).json()
    assert 'older-map-fixture' in calls and old['stats']['dataset_version'] in calls
    assert len(view['plans']) == 2
    for plan in view['plans']:
        assert {i['place']['lat'] for d in plan['days'] for i in d['items']} == {35.0 if plan['id'] == old['id'] else 36.0}
