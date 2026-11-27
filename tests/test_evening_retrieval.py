"""Optional nightlife extends rather than displaces the ordinary shortlist."""
import numpy as np
import pytest

pytest.importorskip("pydantic")

from ourney_travelsync.common import GroupInput
from ourney_travelsync.features import FEATURE_NAMES
from ourney_travelsync.optimizer.contracts import DailyComposition
from ourney_travelsync.recommenders.itinerary_candidates import select_days


def test_optional_evening_retains_low_scoring_bars_without_displacing_day_options():
    ids = tuple(f'p{i}' for i in range(42))
    features = np.zeros((42, len(FEATURE_NAMES)))
    features[:, FEATURE_NAMES.index('nature')] = 1
    data = GroupInput(('u',), ids, np.full((1, 10), 3.), features,
                      np.array([[5.] * 40 + [1., 1.]]))
    records = {pid: dict(latitude=35.1, longitude=129.1, duration_min=30,
                        field_status={'duration': 'KNOWN'}, nature=1) for pid in ids}
    for pid in ids[-2:]:
        records[pid].update(category_name='Pub', tags=['alcohol'])
    point = dict(latitude=35.1, longitude=129.1)
    days = [dict(date='2026-11-30', start_min=540, end_min=1380, start_point=point, end_point=point)]
    policy = DailyComposition().model_dump(mode='json')
    baseline, _ = select_days(data, records, days, composition=policy)
    assert not set(ids[-2:]).intersection(baseline.poi_ids)
    policy['evening_enabled'] = True
    with_bars, audit = select_days(data, records, days, composition=policy)
    assert set(baseline.poi_ids).issubset(with_bars.poi_ids)
    assert set(ids[-2:]).issubset(with_bars.poi_ids)
    assert len(with_bars.poi_ids) <= 60
    assert audit['days']['2026-11-30']['visit_role_counts']['evening'] == 2
    for j, pid in enumerate(with_bars.poi_ids):
        assert with_bars.individual_scores[0, j] == data.individual_scores[0, ids.index(pid)]
    days[0]['end_min'] = 1080
    assert not set(ids[-2:]).intersection(select_days(data, records, days, composition=policy)[0].poi_ids)
