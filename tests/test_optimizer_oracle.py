"""Independent exhaustive oracle on tiny synthetic static-routing instances."""
from itertools import permutations, product
from random import Random

import pytest
pytest.importorskip("ortools")

from optimizer_fixtures import fact, request_data
from ourney_travelsync.optimizer.api import optimize
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.validation import validate_final


def exhaustive(req):
    pois = {p.poi_id: p for p in req.candidates}
    edges = {(e.date, e.origin, e.destination): e for e in req.edges}
    per_day = []
    for day in req.days:
        routes = []
        for length in range(1, len(pois) + 1):
            for order in permutations(pois, length):
                time, prev, cost, travel, walk = day.start_min, day.start_point, 0, 0, 0
                for dest in (*order, day.end_point):
                    e = edges.get((day.date, prev, dest))
                    if e is None or e.status != "KNOWN" or e.duration_minutes > req.max_leg_minutes:
                        break
                    time += e.duration_minutes
                    travel += e.duration_minutes
                    walk += e.walk_minutes
                    cost += e.group_fare
                    if dest != day.end_point:
                        p = pois[dest]
                        windows = p.operations[day.date].openings.value
                        options = [max(time, w.start_min) for w in windows
                                   if max(time, w.start_min) + p.duration_min.value <= w.end_min]
                        if not options:
                            break
                        time = min(options) + p.duration_min.value
                        cost += len(req.members) * p.cost_per_person.value
                    prev = dest
                else:
                    if time <= day.end_min and walk <= req.max_total_walk_minutes:
                        routes.append((frozenset(order), cost, travel))
        per_day.append(routes)
    best = None
    for combination in product(*per_day):
        selected = set().union(*(r[0] for r in combination))
        if sum(len(r[0]) for r in combination) != len(selected):
            continue
        if sum(r[1] for r in combination) > req.budget_total:
            continue
        utility = sum(pois[pid].group_utility for pid in selected)
        minimum = min(sum(round(pois[pid].relevance[m.member_id] * 10000) for pid in selected)
                      for m in req.members)
        score = (utility, minimum, -sum(r[2] for r in combination))
        best = score if best is None else max(best, score)
    return best


@pytest.mark.parametrize("days", [1, 2])
@pytest.mark.parametrize("seed", range(8))
def test_cpsat_matches_exhaustive_optimum(days, seed):
    rng = Random(seed)
    data = request_data(4, days)
    data.update(budget_total=rng.randint(180, 900), max_leg_minutes=40,
                max_total_walk_minutes=rng.randint(20, 70))
    for d in data["days"]:
        d["end_min"] = rng.choice([750, 840, 900])
    for p in data["candidates"]:
        p["duration_min"] = fact(rng.choice([20, 40, 60]))
        p["cost_per_person"] = fact(rng.randint(0, 120))
        p["relevance"] = {"u1": rng.randint(0, 10) / 10, "u2": rng.randint(0, 10) / 10}
        for op in p["operations"].values():
            op["openings"] = fact([dict(start_min=570, end_min=690), dict(start_min=750, end_min=1000)])
    for e in data["edges"]:
        e.update(duration_minutes=rng.randint(10, 45), group_fare=rng.randint(0, 20),
                 walk_minutes=rng.randint(0, 10), status="UNREACHABLE" if rng.random() < 0.1 else "KNOWN")
    req = PlanRequest.model_validate(data)
    expected = exhaustive(req)
    result = optimize(req)
    if expected is None:
        assert not result.days
        assert result.solver_status in ("INFEASIBLE", "NOT_RUN")
    else:
        assert result.validation_status == "PASSED"
        assert validate_final(req, result.days).passed
        chosen = {v.poi_id for day in result.days for v in day.items}
        actual = (result.metrics["group_utility"],
                  min(sum(round(p.relevance[m.member_id] * 10000)
                          for p in req.candidates if p.poi_id in chosen) for m in req.members),
                  -result.metrics["travel_minutes"])
        assert actual == expected
