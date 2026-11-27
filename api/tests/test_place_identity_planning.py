"""API boundaries derive identities from the request's pinned POI version."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import delete, select

from app import config
from app.db import SessionLocal
from app.models import Poi
from app.optimization import with_visit_identities
from ourney_travelsync.optimizer.contracts import PlanRequest
from .test_optimizer_integration import ready, generate, configuration, audits
from .test_proposal_approval import replacement_fixture_clock
from .test_flow import FEATURES


def test_pinned_aliases_cannot_both_be_published_and_old_replays_are_upgraded(
    planning_adapter, replacement_fixture_clock, monkeypatch, request,
):
    monkeypatch.setenv('PLANNING_DAY_CANDIDATE_LIMIT', '40')
    monkeypatch.setattr(config, 'OPTIMIZER_SEARCH_SECONDS', 5)
    h, tid, rec = ready(individual_method='feature_mean')
    ids = ['synthetic-tour-' + uuid4().hex, 'synthetic-kakao-' + uuid4().hex]
    with SessionLocal() as db:
        template = db.scalar(select(Poi).where(Poi.dataset_version == rec['dataset_version']))
        for i, pid in enumerate(ids):
            values = {c.name: deepcopy(getattr(template, c.name)) for c in Poi.__table__.columns}
            values.update(id=pid, name='Synthetic Fortress',
                address='부산광역시 수영구 수영성로 43 (수영동)' if i == 0 else '부산 수영구 수영성로 43',
                road_address='', lat=35.15 + .004 * i, lng=129.1,
                features={**dict.fromkeys(FEATURES, 0), 'history': 1})
            db.add(Poi(**values))
        db.commit()
    def cleanup():
        with SessionLocal() as db:
            db.execute(delete(Poi).where(Poi.id.in_(ids), Poi.dataset_version == rec['dataset_version']))
            db.commit()
    request.addfinalizer(cleanup)
    body = configuration()
    body['days'][0]['end_min'] = 1380
    body['locked_items'] = [dict(poi_id=ids[0], date=body['days'][0]['date'], start_min=600)]
    result = generate(h, tid, body)
    assert result.status_code == 200, result.text
    visits = [v for d in result.json()['plans'][0]['days'] for v in d['items']]
    assert [v['poi_id'] for v in visits if v['poi_id'] in ids] == [ids[0]]
    assert next(v['start'] for v in visits if v['poi_id'] == ids[0]) == '10:00'
    audit = audits(tid)[0]
    aliases = [p for p in audit.optimizer_request['candidates'] if p['poi_id'] in ids]
    assert len(aliases) == 2 and aliases[0]['visit_identity'] == aliases[1]['visit_identity']
    old = deepcopy(audit.optimizer_request)
    for p in old['candidates']:
        p.pop('visit_identity', None)
    before = deepcopy(old)
    with SessionLocal() as db:
        upgraded = with_visit_identities(PlanRequest.model_validate(old), db)
    aliases = [p for p in upgraded.candidates if p.poi_id in ids]
    assert aliases[0].visit_identity and aliases[0].visit_identity == aliases[1].visit_identity
    assert old == before
