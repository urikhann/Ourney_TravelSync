"""Removal preserves its scope when a food/drink separator needs rearranging."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import planning_actions, services
from app.db import SessionLocal
from app.models import Itinerary, OptimizationRun, Poi
from app.replacements import plans
from app.routes.itineraries import hhmm
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.repair import rebuild_day
from ourney_travelsync.optimizer.validation import validate_final
from .test_conversation_actions import post
from .test_proposal_approval import planned_trip, vote, replacement_fixture_clock
from .test_optimizer_integration import c, ready, generate, configuration


def separator_trip(*, fixed_activities=False, mode='walk', multiple_days=False):
    if multiple_days:
        h, tid, rec = ready(individual_method='feature_mean', days=('2026-11-06', '2026-11-07'))
        with SessionLocal() as db:
            cafe_id = next(p.id for p in db.scalars(select(Poi).where(Poi.dataset_version == rec['dataset_version']))
                           if p.features.get('cafe'))
        body = configuration(days=('2026-11-06', '2026-11-07'))
        body['locked_items'] = [dict(poi_id=cafe_id, date='2026-11-06', start_min=620)]
        result = generate(h, tid, body)
        assert result.status_code == 200, result.text
        plan_id = result.json()['plans'][0]['id']
    else:
        (h, _), tid, plan = planned_trip(2, transport_mode=mode)
        plan_id = plan['id']
    with SessionLocal() as db:
        plan = db.get(Itinerary, plan_id)
        audit = db.scalar(select(OptimizationRun).where(OptimizationRun.itinerary_id == plan_id))
        req = PlanRequest.model_validate(audit.optimizer_request)
        if mode == 'drive':
            # Controlled saved test evidence uses a uniform one-minute road
            # matrix. Runtime repair must still fetch/verify its selected legs.
            edge = req.edges[0]
            req = req.model_copy(update={'edges': tuple(edge.model_copy(update={
                'date': d.date, 'origin': a, 'destination': b, 'duration_minutes': 1})
                for d in req.days for a in (d.start_point, *(p.poi_id for p in req.candidates))
                for b in (*(p.poi_id for p in req.candidates), d.end_point) if a != b)})
        day = plans(plan.days)[0]
        other_ids = {v.poi_id for d in plans(plan.days)[1:] for v in d.items}
        candidates = [p for p in req.candidates if p.poi_id not in other_ids and (
            p.allowed_dates is None or day.date in p.allowed_dates)]
        first = next(p for p in candidates if p.visit_category == 'activity' and p.duration_min.value == 60)
        target = next(p for p in candidates if p.visit_category == 'activity' and p.duration_min.value == 60 and p != first)
        spare = next(p for p in candidates if p.visit_category == 'activity' and p not in (first, target)
                     and p.activity_category != first.activity_category)
        cafe = next(p for p in candidates if p.visit_category == 'cafe' and (not multiple_days or p.poi_id == cafe_id))
        meal = next(p for p in candidates if p.visit_category == 'meal')
        chosen = [(first, 550), (cafe, 620), (target, 675), (meal, 800), (spare, 870)]
        template = day.items[0]
        controlled = rebuild_day(req, day, [(template.model_copy(update={'poi_id': p.poi_id, 'breakfast_status': None}), start)
                                            for p, start in chosen])
        merged = (controlled, *plans(plan.days)[1:])
        assert controlled and validate_final(req, merged).passed
        raw = deepcopy(plan.days)
        raw[0] = {**raw[0], **controlled.model_dump(mode='json')}
        protected = {cafe.poi_id, meal.poi_id} | ({first.poi_id, spare.poi_id} if fixed_activities else set())
        for item in raw[0]['items']:
            p = next(p for p in candidates if p.poi_id == item['poi_id'])
            item.update(start=hhmm(item['start_min']), end=hhmm(item['departure_min']), arrival=hhmm(item['arrival_min']),
                        visit_category=p.visit_category, locked=p.poi_id in protected,
                        lock_reason='explicit_lock' if p.poi_id in protected else None)
        locks = [dict(poi_id=v.poi_id, date=day.date.isoformat(), start_min=v.start_min)
                 for v in controlled.items if v.poi_id in protected]
        metadata = deepcopy(audit.input)
        metadata['configuration']['locked_items'] = locks
        metadata['lock_provenance'] = dict.fromkeys(protected, 'explicit_lock')
        audit.input = metadata
        audit.optimizer_request = {**req.model_dump(mode='json'), 'locked_items': locks}
        plan.days = raw
        target_name = db.get(Poi, (target.poi_id, req.dataset_version)).name
        db.commit()
    return h, tid, plan_id, raw, target.poi_id, target_name, protected


@pytest.mark.parametrize('mode', ['walk', 'drive'])
def test_yes_removal_repairs_separator_without_adding_places_or_bypassing_votes(
    planning_adapter, replacement_fixture_clock, monkeypatch, mode,
):
    if mode == 'drive':
        def selected(req, response):
            legs = {(x['date'], x['origin'], x['destination']) for x in req.get('route_legs', [])}
            response['edges'] = [e for e in response['edges'] if (e['date'], e['origin'], e['destination']) in legs]
            response['routing_attempts'] = req.get('routing_attempts', {})
            response['routing_attempts'] = {'point': response['routing_attempts'].get('point', 0) + len(legs)}
            for e in response['edges']:
                e['duration_minutes'] = 1
            return response
        planning_adapter.transform = selected
    h, tid, plan_id, before, target_id, _, protected = separator_trip(mode=mode)
    target = next(v for v in before[0]['items'] if v['poi_id'] == target_id)
    async def question(_):
        return dict(reply='Shall I remove that stop?', proposal=None, tool_calls=[],
                    pending_action=dict(action_type='remove_poi', target_item_id=f"{before[0]['date']}@{target['start']}"))
    monkeypatch.setattr(services, 'agent_turn', question)
    q = post(h, tid, '@TravelSync Help with this stop please.')
    async def no_model(_):
        raise AssertionError('Yes must use the saved action')
    monkeypatch.setattr(services, 'agent_turn', no_model)
    first_call = len(planning_adapter.calls)
    answer = post(h, tid, '@TravelSync Yes', reply_to=q['id'])
    agent = answer['agent']
    assert agent.get('validation_status') == 'PASSED', answer
    assert agent['action_type'] == 'remove_poi' and agent['proposal_status'] == 'PENDING'
    assert not agent['proposal']['add'] and agent['votes']['approve'] == 0
    if mode == 'drive':
        # One bypass route was already verified before the repair starts.
        assert planning_adapter.calls[first_call + 1]['routing_attempts']['point'] == 1
    with SessionLocal() as db:
        assert db.get(Itinerary, plan_id).days == before
        draft = db.get(OptimizationRun, agent['draft_id'])
        after = plans(draft.response['days'])
        before_ids = {v['poi_id'] for d in before for v in d['items']}
        assert {v.poi_id for d in after for v in d.items} == before_ids - {target_id}
        fixed = {v['poi_id']: v['start_min'] for d in before for v in d['items'] if v['poi_id'] in protected}
        assert {v.poi_id: v.start_min for d in after for v in d.items if v.poi_id in protected} == fixed
        assert validate_final(PlanRequest.model_validate(draft.optimizer_request), after).passed
        assert set(draft.input['lock_provenance']) == protected
    assert vote(h, tid, answer['id']).json()['messages'][0]['agent']['proposal_status'] == 'PENDING'


def test_infeasible_removal_reports_food_drink_rule_and_keeps_saved_plan(
    planning_adapter, replacement_fixture_clock,
):
    h, tid, plan_id, before, _, name, _ = separator_trip(fixed_activities=True)
    answer = post(h, tid, f'@TravelSync remove {name}')
    assert answer['agent']['state'] == 'FAILED', answer
    assert any(e.endswith(':CONSECUTIVE_FOOD_DRINK') for e in answer['agent']['validation_errors'])
    assert 'two food/drink stops in a row' in answer['text']
    with SessionLocal() as db:
        assert db.get(Itinerary, plan_id).days == before
        assert not db.scalar(select(OptimizationRun).where(OptimizationRun.trip_id == tid, OptimizationRun.status == 'VALIDATED'))


def test_rule_explanation_preserves_timeout_and_korean_reason():
    errors = ['2026-11-30::CONSECUTIVE_FOOD_DRINK']
    en = planning_actions.failure('planning_action_timeout', False, details=errors)
    assert en['reason'] == 'planning_action_timeout' and 'time limit' in en['reply']
    assert 'two food/drink stops' in en['reply']
    ko = planning_actions.failure('final_validation_failed', True, details=errors)
    assert '음식·음료 일정이 연속' in ko['reply'] and ko['validation_errors'] == errors


def test_removal_repair_keeps_other_day_unchanged(planning_adapter, replacement_fixture_clock):
    h, tid, plan_id, before, _, name, _ = separator_trip(multiple_days=True)
    answer = post(h, tid, f'@TravelSync remove {name}')
    assert answer['agent'].get('validation_status') == 'PASSED', answer
    with SessionLocal() as db:
        draft = db.get(OptimizationRun, answer['agent']['draft_id'])
        assert plans(draft.response['days'])[1] == plans(before)[1]
        assert db.get(Itinerary, plan_id).days == before
    assert vote(h, tid, answer['id']).json()['messages'][0]['agent']['proposal_status'] == 'APPLIED'
    saved = c.get(f'/trips/{tid}/itineraries', headers=h).json()
    applied = next(p for p in saved['plans'] if p['id'] != plan_id)
    assert plans(applied['days'])[1] == plans(before)[1]


def test_route_timeout_keeps_initial_constraint_reason_and_does_not_publish(
    planning_adapter, replacement_fixture_clock, monkeypatch,
):
    h, tid, plan_id, before, _, name, _ = separator_trip()
    original = services.planning_data
    calls = 0
    async def timeout_on_repair(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPException(504, 'planning_data_request_timeout')
        return await original(*args, **kwargs)
    monkeypatch.setattr(services, 'planning_data', timeout_on_repair)
    answer = post(h, tid, f'@TravelSync remove {name}')
    assert answer['agent']['reason'] == 'planning_data_request_timeout', answer
    assert calls == 2 and 'two food/drink stops' in answer['text'] and 'timed out' in answer['text']
    with SessionLocal() as db:
        assert db.get(Itinerary, plan_id).days == before
