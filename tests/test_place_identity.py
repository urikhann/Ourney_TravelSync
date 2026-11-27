"""Cross-source aliases must not buy a second visit to the same physical place."""
from copy import deepcopy
from datetime import date

import pytest

pytest.importorskip("ortools")

from optimizer_fixtures import request_data
from ourney_travelsync.place_identity import visit_identity
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.optimizer.repair import repair
from test_localized_repair import scenario
from test_optimizer_waiting import locked_gap
from ourney_travelsync.optimizer.waiting import gap_retry_request


def test_observed_korean_source_aliases_share_identity_despite_pin_difference():
    tour = visit_identity("경상좌수영성지", "부산광역시 수영구 수영성로 43 (수영동)")
    kakao = visit_identity("경상좌수영성지", "부산 수영구 수영성로 43")
    assert tour is not None and tour == kakao


@pytest.mark.parametrize('name,address', [
    ('Cafe Main branch', 'Busan Road 43'), ('Cafe', 'Busan Road 44'),
    ('Cafe', 'Busan Road 43-1'), ('Cafe', 'Busan Road 431'), ('Cafe', 'Busan Road 43 Floor 2'),
    ('Cafe', 'Busan Road 43 (2층)'),
])
def test_distinct_branches_and_numbered_addresses_are_not_merged(name, address):
    assert visit_identity(name, address) != visit_identity('Cafe', 'Busan Road 43')


def test_name_alone_and_incomplete_addresses_are_not_identity_evidence():
    assert visit_identity('Same Name', '') is None
    assert visit_identity('Same Name', 'Busan') is None
    assert visit_identity('', 'Busan Road 43') is None
    assert visit_identity('Cafe', 'Busan Road 43-1') != visit_identity('Cafe', 'Busan Road 431')


@pytest.mark.parametrize('days', [1, 2])
def test_solver_keeps_locked_source_and_selects_alias_only_once_across_trip(days):
    raw = request_data(3, days=days)
    for p in raw['candidates'][:2]:
        p['visit_identity'] = 'same-physical-venue'
    raw['locked_items'] = [dict(poi_id='B', date=raw['days'][0]['date'], start_min=660)]
    before = deepcopy(raw)
    result = optimize(raw)
    assert result.validation_status == 'PASSED'
    visits = [(d.date, v) for d in result.days for v in d.items]
    assert not any(v.poi_id == 'A' for _, v in visits)
    assert sum(v.poi_id == 'B' for _, v in visits) == 1
    assert next(v.start_min for _, v in visits if v.poi_id == 'B') == 660
    assert raw == before


def test_e3_rejects_previously_valid_schedule_containing_two_aliases():
    raw = request_data(3)
    old_result = optimize(raw)
    assert old_result.validation_status == 'PASSED'
    for p in raw['candidates'][:2]:
        p['visit_identity'] = 'same-physical-venue'
    checked = validate_final(PlanRequest.model_validate(raw), old_result.days)
    assert not checked.passed and any('DUPLICATE_PLACE' in e for e in checked.errors)


def test_conflicting_alias_locks_are_not_silently_removed():
    raw = request_data(2)
    for p in raw['candidates']:
        p['visit_identity'] = 'same-physical-venue'
    raw['locked_items'] = [dict(poi_id=p['poi_id'], date=raw['days'][0]['date']) for p in raw['candidates']]
    assert optimize(raw).solver_status == 'INFEASIBLE'
    assert len(raw['locked_items']) == 2


def test_localized_repair_rejects_alias_of_an_existing_visit():
    req, days = scenario()
    candidates = tuple(p.model_copy(update={'visit_identity': 'same-physical-venue'}) if p.poi_id in ('B', 'C')
                       else p for p in req.candidates)
    req = req.model_copy(update={'candidates': candidates})
    assert repair(req, days, date(2026, 11, 5), 0, 'C').reason == 'duplicate_poi'


def test_gap_filling_does_not_target_alias_of_a_selected_place():
    raw = locked_gap(3)
    for p in raw['candidates']:
        if p['poi_id'] in ('A', 'C'):
            p['visit_identity'] = 'same-physical-venue'
    req = PlanRequest.model_validate(raw)
    result = optimize(req)
    assert result.validation_status == 'PASSED'
    retry = gap_retry_request(req, result, 1)
    assert retry is None or retry.gap_target is None
