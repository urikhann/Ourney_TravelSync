"""App planning policy: partial hours, endpoint feasibility, and optional route objective."""
from copy import deepcopy

import pytest

pytest.importorskip("ortools")
pytest.importorskip("pydantic")

from optimizer_fixtures import DAY, fact, request_data
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final


def relaxed_hours():
    raw = request_data(1)
    raw.update(allow_unknown_opening_hours=True, enforce_opening_hours=True, enforce_cost=False)
    return raw


def test_unknown_hours_allowed_without_rewriting_source_facts():
    raw = relaxed_hours()
    raw['candidates'][0]['operations'] = {}
    original = deepcopy(raw)
    result = optimize(raw)
    assert result.validation_status == 'PASSED'
    assert any('OPENING_HOURS_UNVERIFIED' in r for r in result.eligibility[0].reasons)
    assert validate_final(PlanRequest.model_validate(raw), result.days).passed
    assert raw == original


@pytest.mark.parametrize('operations', [
    {'closed': fact(True)},
    {'openings': fact([])},
    {'openings': fact([dict(start_min=600, end_min=630)])},
    {'openings': fact([dict(start_min=600, end_min=630), dict(start_min=660, end_min=690)])},
])
def test_partial_hours_policy_never_ignores_known_closure_or_full_stay(operations):
    raw = relaxed_hours()
    raw['candidates'][0]['operations'] = {DAY: operations}
    assert optimize(raw).validation_status != 'PASSED'


def test_partial_known_interval_is_enforced_and_independently_rechecked():
    raw = relaxed_hours()
    raw['candidates'][0]['operations'] = {DAY: {'openings': fact([dict(start_min=600, end_min=660)])}}
    result = optimize(raw)
    assert result.validation_status == 'PASSED'
    assert result.days[0].items[0].start_min == 600
    changed = deepcopy(raw)
    changed['candidates'][0]['operations'][DAY]['openings'] = fact([dict(start_min=600, end_min=659)])
    assert not validate_final(PlanRequest.model_validate(changed), result.days).passed
    changed['candidates'][0]['operations'][DAY]['closed'] = fact(True)
    assert not validate_final(PlanRequest.model_validate(changed), result.days).passed


def test_travel_objective_can_be_disabled_but_return_still_must_fit():
    raw = relaxed_hours()
    raw['optimize_travel'] = False
    result = optimize(raw)
    assert result.validation_status == 'PASSED'
    assert [p.objective for p in result.phases] == ['group_utility', 'minimum_relevance']
    for edge in raw['edges']:
        if edge['destination'] == 'hotel':
            edge['duration_minutes'] = 1440
    assert optimize(raw).validation_status != 'PASSED'


def test_must_visit_is_not_dropped_when_it_is_closed():
    raw = relaxed_hours()
    raw['locked_items'] = [dict(poi_id='A', date=DAY)]
    raw['candidates'][0]['operations'][DAY]['closed'] = fact(True)
    result = optimize(raw)
    assert result.validation_status != 'PASSED' and not result.days
