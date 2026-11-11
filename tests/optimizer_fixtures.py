"""Synthetic only: no fixture facts or routing are production defaults."""
from ourney_travelsync.optimizer.contracts import Eligibility, PlanRequest, Window

DAY = "2026-11-05"
STAMP = "2026-11-01T00:00:00Z"


def fact(value, **kwargs):
    return dict(value=value, status="KNOWN", source="synthetic fixture", verified_at=STAMP, **kwargs)


def request_data(count=3, days=1):
    dates = [f"2026-11-{5 + i:02d}" for i in range(days)]
    ids = [chr(65 + i) for i in range(count)]
    members = [dict(member_id=m, available={d: [dict(start_min=540, end_min=1080)] for d in dates})
               for m in ("u1", "u2")]
    candidates = [dict(poi_id=pid, dataset_version="synthetic-v1", group_rank=i + 1,
                       group_utility=1000 - 100 * i, mean_score=0.5,
                       personal_scores={"u1": 0.4, "u2": 0.6}, relevance={"u1": 0.8, "u2": 0.2},
                       duration_min=fact(60), cost_per_person=fact(100),
                       operations={d: dict(openings=fact([dict(start_min=600, end_min=1020)]),
                                           closed=fact(False)) for d in dates})
                  for i, pid in enumerate(ids)]
    edges = [dict(date=d, origin=a, destination=b, status="KNOWN", duration_minutes=30,
                  group_fare=10, walk_minutes=5, distance_m=1000,
                  provider="synthetic fixture", fetched_at=STAMP)
             for d in dates for a in ["hotel", *ids] for b in [*ids, "hotel"] if a != b]
    return dict(run_id="synthetic-run", dataset_version="synthetic-v1", constraint_version="c1",
                preference_version="p1", mapping_version="m1", feature_schema_version="fs-1.0",
                operating_snapshot_id="synthetic-ops", routing_version="synthetic-routes",
                transport_mode="walk", days=[dict(date=d, start_min=540, end_min=1080,
                                                 start_point="hotel", end_point="hotel") for d in dates],
                members=members, candidates=candidates, edges=edges,
                budget_total=10000, search_seconds=2)


def prepared(data):
    req = PlanRequest.model_validate(data)
    eligibility = tuple(Eligibility(poi_id=p.poi_id, schedule_eligible=True,
                                   feasible_dates=tuple(d.date for d in req.days),
                                   windows={d.date: (Window(start_min=600, end_min=1020),) for d in req.days},
                                   reasons=()) for p in req.candidates)
    return req, eligibility
