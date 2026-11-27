"""Optional evening preference, without choosing or locking a particular venue."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app import config
from app.db import SessionLocal
from app.models import Poi, TripMember
from ourney_travelsync.optimizer.contracts import DailyComposition
from .test_optimizer_integration import ready, generate, configuration, c
from .test_proposal_approval import replacement_fixture_clock


@pytest.mark.parametrize('scenario,mode', [('included', 'walk'), ('included', 'drive'),
    ('closed', 'walk'), ('excluded', 'walk'), ('early_return', 'walk'), ('slow_route', 'drive')])
def test_automatic_evening_preference(planning_adapter, replacement_fixture_clock, monkeypatch, request, scenario, mode):
    monkeypatch.setattr(config, 'OPTIMIZER_SEARCH_SECONDS', 12)
    h, tid, rec = ready(individual_method='feature_mean')
    pid = 'synthetic-optional-pub-' + uuid4().hex
    with SessionLocal() as db:
        template = db.scalar(select(Poi).where(Poi.dataset_version == rec['dataset_version']))
        values = {column.name: deepcopy(getattr(template, column.name)) for column in Poi.__table__.columns}
        values.update(id=pid, name='Synthetic optional pub', category='Pub', tags=['alcohol'])
        db.add(Poi(**values))
        if scenario == 'excluded':
            member = db.scalar(select(TripMember).where(TripMember.trip_id == tid))
            member.exclusions = {'tags': ['alcohol'], 'poi_ids': []}
        db.commit()
    def cleanup():
        with SessionLocal() as db:
            db.execute(delete(Poi).where(Poi.id == pid, Poi.dataset_version == rec['dataset_version']))
            db.commit()
    request.addfinalizer(cleanup)
    if scenario == 'excluded':
        # Exclusions change the group snapshot; refresh recommendations as the UI
        # requires rather than bypassing stale-input protection in this fixture.
        refreshed = c.post(f'/trips/{tid}/recommendations', headers=h,
                           json={'individual_method': 'feature_mean', 'group_k': 3})
        assert refreshed.status_code == 200, refreshed.text
    def evidence(req, response):
        if scenario == 'closed':
            for p in response['pois']:
                if p['poi_id'] == pid:
                    for op in p['operations'].values():
                        op['closed']['value'] = True
        if mode == 'drive':
            legs = {(leg['date'], leg['origin'], leg['destination']) for leg in req.get('route_legs', [])}
            response['edges'] = [e for e in response['edges'] if (e['date'], e['origin'], e['destination']) in legs]
            response['routing_attempts'] = req.get('routing_attempts', {})
            for edge in response['edges']:
                edge['duration_minutes'] = 600 if scenario == 'slow_route' and pid in (edge['origin'], edge['destination']) else 1
        return response
    planning_adapter.transform = evidence
    body = configuration()
    body['transport_mode'] = mode
    body['days'][0]['end_min'] = 1080 if scenario == 'early_return' else 1380
    body['daily_composition'] = DailyComposition(evening_enabled=True).model_dump(mode='json')
    response = generate(h, tid, body)
    assert response.status_code == 200, response.text
    day = response.json()['plans'][0]['days'][0]
    bars = [v for v in day['items'] if v['visit_category'] == 'evening']
    if scenario == 'included':
        assert len(bars) == 1 and bars[0]['poi_id'] == pid and bars[0]['start'] >= '18:00'
        assert not bars[0]['locked'] and not day['evening_omitted']
    else:
        assert not bars and day['evening_omitted']
    assert response.json()['latest_optimization']['configuration']['locked_items'] == []
