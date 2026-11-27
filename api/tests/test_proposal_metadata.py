"""Issue 32: public, snapshot-grounded proposal details never change approval."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ChatMessage, Itinerary, OptimizationRun, ProposalVote
from app.proposal_details import public_url, stop_view
from app.replacements import plans
from ourney_travelsync.optimizer.contracts import Fact, Operation, PlanRequest
from .test_optimizer_integration import c
from .test_proposal_approval import planned_trip, swap_for, ask, vote, replacement_fixture_clock


def proposal():
    heads, tid, plan = planned_trip()
    _, _, suggestion = swap_for(tid, plan)
    message = ask(heads[0], tid, suggestion)
    assert message['agent']['proposal_status'] == 'PENDING', message
    return heads, tid, plan, message


def read_message(h, tid, mid):
    response = c.get(f'/trips/{tid}/chat', headers=h)
    assert response.status_code == 200, response.text
    return next(m for m in response.json()['messages'] if m['id'] == mid)


def test_full_proposal_facts_and_impact_match_validated_snapshots_without_votes(planning_adapter, replacement_fixture_clock):
    heads, tid, plan, msg = proposal()
    details = msg['agent']['proposal_details']
    assert details and details['schema_version'] == 'proposal-details-1.0'
    assert details['dataset_version'] == plan['stats']['dataset_version']
    with SessionLocal() as db:
        base = db.get(OptimizationRun, msg['agent']['plan_run_id'])
        draft = db.get(OptimizationRun, msg['agent']['draft_id'])
        req = PlanRequest.model_validate(draft.optimizer_request)
        old, new = plans(base.response['days'])[0], plans(draft.response['days'])[0]
        stop = details['proposed'][0]
        candidate = next(p for p in req.candidates if p.poi_id == stop['poi_id'])
        visit = next(v for v in new.items if v.poi_id == stop['poi_id'])
        assert stop['place']['name'] and stop['place']['category']
        assert (stop['start_min'], stop['end_min'], stop['duration_minutes']) == (visit.start_min, visit.departure_min, visit.stay_minutes)
        assert stop['duration_status'] == candidate.duration_min.status
        assert stop['cost_per_person']['value'] == candidate.cost_per_person.value
        assert stop['hours']['status'] == 'KNOWN' and stop['hours']['fits_visit'] is True
        assert stop['hours']['checked_at']
        assert stop['recommendation_score']['value'] == candidate.mean_score
        assert stop['recommendation_score']['semantics'] == 'normalized_preference'
        assert any(r['code'] == 'shared_interest' for r in stop['recommendation_reasons'])
        impact = details['impact']
        assert impact['before']['travel_minutes'] == old.travel_minutes
        assert impact['after']['travel_minutes'] == new.travel_minutes
        assert impact['change']['travel_minutes'] == new.travel_minutes-old.travel_minutes
        assert impact['change']['itinerary_minutes'] == new.end_arrival_min-new.start_min-(old.end_arrival_min-old.start_min)
        assert details['original'][0]['poi_id'] != stop['poi_id']
        assert db.get(Itinerary, plan['id']).days == plan['days'] or plans(db.get(Itinerary, plan['id']).days) == plans(plan['days'])
        assert draft.status == 'VALIDATED' and not db.scalar(select(ProposalVote).where(ProposalVote.message_id == msg['id']))
    saved = read_message(heads[1], tid, msg['id'])
    assert saved['agent']['proposal_details'] == details
    assert saved['agent']['votes']['approve'] == 0


def test_details_ignore_fabricated_agent_fields_and_exclude_member_snapshots(planning_adapter, replacement_fixture_clock):
    heads, tid, _, msg = proposal()
    expected = msg['agent']['proposal_details']
    with SessionLocal() as db:
        record = db.get(ChatMessage, msg['id'])
        record.agent = {**record.agent, 'proposal_details': {'name': 'FABRICATED_LLM_FACT', 'travel_minutes': -99999}}
        author_ids = [m['user_id'] for m in db.get(OptimizationRun, record.agent['draft_id']).input['state']['members']]
        db.commit()
    actual = read_message(heads[1], tid, msg['id'])['agent']['proposal_details']
    assert actual == expected
    serialized = json.dumps(actual)
    assert 'FABRICATED_LLM_FACT' not in serialized and all(uid not in serialized for uid in author_ids)
    assert 'member_scores' not in serialized and 'personal_scores' not in serialized


def test_historical_details_stay_bound_to_original_plan_after_explicit_approval(planning_adapter, replacement_fixture_clock):
    heads, tid, original, msg = proposal()
    expected = msg['agent']['proposal_details']
    assert vote(heads[0], tid, msg['id']).json()['messages'][0]['agent']['proposal_status'] == 'PENDING'
    approved = vote(heads[1], tid, msg['id']).json()['messages'][0]
    assert approved['agent']['proposal_status'] == 'APPLIED'
    assert approved['agent']['proposal_details'] == expected
    assert read_message(heads[1], tid, msg['id'])['agent']['proposal_details'] == expected
    with SessionLocal() as db:
        assert plans(db.get(Itinerary, original['id']).days) == plans(original['days'])


def test_other_group_draft_and_legacy_proposal_do_not_expose_details(planning_adapter, replacement_fixture_clock):
    heads, tid, _, msg = proposal()
    _, _, _, other = proposal()
    with SessionLocal() as db:
        record = db.get(ChatMessage, msg['id'])
        original = deepcopy(record.agent)
        record.agent = {**original, 'draft_id': other['agent']['draft_id'], 'proposal_details': {'name': 'DO_NOT_TRUST'}}
        db.commit()
    assert read_message(heads[0], tid, msg['id'])['agent']['proposal_details'] is None
    with SessionLocal() as db:
        record = db.get(ChatMessage, msg['id'])
        record.agent = {**original, 'draft_id': None}
        db.commit()
    legacy = read_message(heads[0], tid, msg['id'])
    assert legacy['agent']['proposal_details'] is None and legacy['agent']['proposal_status'] == 'STALE'


def test_serializer_marks_missing_facts_and_preserves_relative_score_semantics(planning_adapter, replacement_fixture_clock):
    _, _, _, msg = proposal()
    with SessionLocal() as db:
        draft = db.get(OptimizationRun, msg['agent']['draft_id'])
        req = PlanRequest.model_validate(draft.optimizer_request)
        day = plans(draft.response['days'])[0]
        visit = next(v for v in day.items if v.poi_id == msg['agent']['proposal']['add'][0]['poi_id'])
        candidate = next(p for p in req.candidates if p.poi_id == visit.poi_id)
        unknown = candidate.model_copy(update={'cost_per_person': Fact(), 'operations': {day.date: Operation()}})
        req = req.model_copy(update={'candidates': tuple(unknown if p.poi_id == unknown.poi_id else p for p in req.candidates)})
        audit = SimpleNamespace(input={'candidate_retrieval': {'score_scale': 'relative_relevance_0_1'}}, response={'validation_status': 'PASSED'})
        fake = SimpleNamespace(name='Reported museum', category='', enriched_metadata={'description_summary': 'NULL'},
            image_url='', road_address='', address='', lat=35.1, lng=129.1, features={'history': 1}, place_url='', avg_cost_krw=None, field_status={})
        detail = stop_view((day.date.isoformat(), visit.poi_id), visit, req, audit, fake)
    assert detail['place']['description'] is None and detail['place']['image_url'] is None and detail['place']['address'] is None
    assert detail['hours']['status'] == 'UNKNOWN' and detail['hours']['fits_visit'] is None
    assert detail['hours']['windows'] == [] and detail['cost_per_person']['value'] is None
    assert detail['cost_per_person']['status'] == 'UNKNOWN'
    assert detail['recommendation_reasons'] == []
    assert detail['recommendation_score']['semantics'] == 'relative_relevance'
    fake.category = 'Museum'
    fake.enriched_metadata = {'description_summary': 'A source description.', 'description_source_url': 'https://example.org/about'}
    fake.image_url = 'https://example.org/photo.jpg'
    fake.address = 'A reported street 12'
    available = stop_view((day.date.isoformat(), visit.poi_id), visit, req, audit, fake)
    assert available['place']['description'] == 'A source description.'
    assert available['place']['image_url'] == fake.image_url and available['place']['address'] == fake.address


@pytest.mark.parametrize('broken', ['missing_binding', 'invalid_request', 'invalid_days'])
def test_incomplete_legacy_snapshots_preserve_chat_without_details(broken, planning_adapter, replacement_fixture_clock):
    heads, tid, _, msg = proposal()
    with SessionLocal() as db:
        draft = db.get(OptimizationRun, msg['agent']['draft_id'])
        if broken == 'missing_binding':
            draft.input = {**draft.input, 'replacement': None}
        elif broken == 'invalid_request':
            draft.optimizer_request = {}
        else:
            draft.response = {**draft.response, 'days': None}
        db.commit()
    actual = read_message(heads[0], tid, msg['id'])
    assert actual['agent']['proposal_details'] is None
    assert actual['agent']['votes']['approve'] == 0


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://user:secret@example.org/path',
    'https://apis.data.go.kr/info?serviceKey=credential', 'https://example.org/?api_key=credential', None, 123])
def test_proposal_links_never_expose_credentials_or_unsafe_schemes(url):
    assert public_url(url) is None
