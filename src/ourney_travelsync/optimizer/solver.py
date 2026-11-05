"""CP-SAT construction/extraction. Raw solutions are private until E3 passes."""
from dataclasses import dataclass
from time import monotonic

from ortools.sat.python import cp_model

from .contracts import DayPlan, Eligibility, Phase, PlanRequest, Visit


@dataclass(frozen=True)
class RawSolution:
    status: str
    days: tuple[DayPlan, ...] = ()
    phases: tuple[Phase, ...] = ()


def solve_prepared(req: PlanRequest, eligibility: tuple[Eligibility, ...]) -> RawSolution:
    """Internal entry point: eligibility must be derived from this same snapshot."""
    if len(req.days) != 1:
        raise ValueError("single-day milestone only")
    if req.search_seconds == 0:
        return RawSolution("UNKNOWN")
    model = cp_model.CpModel()
    eligible = {e.poi_id: e for e in eligibility}
    pois = {p.poi_id: p for p in sorted(req.candidates, key=lambda p: p.poi_id)
            if p.poi_id in eligible and eligible[p.poi_id].schedule_eligible}
    edges = {(e.date, e.origin, e.destination): e for e in req.edges if e.usable}
    visits, times, day_arcs = {}, {}, {}
    for day in sorted(req.days, key=lambda d: d.date):
        nodes = [day.start_point, *pois, day.end_point]
        end = len(nodes) - 1
        starts = {0: model.new_constant(day.start_min),
                  end: model.new_int_var(day.start_min, day.end_min, f"end_{day.date}")}
        circuit = [(end, 0, model.new_constant(1))]
        arcs = {}
        for i, pid in enumerate(pois, 1):
            y = model.new_bool_var(f"visit_{day.date}_{pid}")
            visits[day.date, pid] = y
            starts[i] = model.new_int_var(day.start_min, day.end_min, f"time_{day.date}_{pid}")
            times[day.date, pid] = starts[i]
            circuit.append((i, i, y.Not()))
            windows = eligible[pid].windows.get(day.date, ())
            choices = []
            for k, w in enumerate(windows):
                chosen = model.new_bool_var(f"window_{day.date}_{pid}_{k}")
                model.add(starts[i] >= w.start_min).only_enforce_if(chosen)
                model.add(starts[i] + pois[pid].duration_min.value <= w.end_min).only_enforce_if(chosen)
                choices.append(chosen)
            model.add(sum(choices) == y)
        model.add(sum(visits[day.date, pid] for pid in pois) >= 1)
        for i in range(end):
            for j in range(1, end + 1):
                if i == j:
                    continue
                edge = edges.get((day.date, nodes[i], nodes[j]))
                if edge is None:
                    continue  # no fabricated zero-time arc
                a = model.new_bool_var(f"arc_{day.date}_{i}_{j}")
                circuit.append((i, j, a))
                arcs[i, j] = (a, edge)
                stay = 0 if i == 0 else pois[nodes[i]].duration_min.value
                model.add(starts[j] >= starts[i] + stay + edge.duration_minutes).only_enforce_if(a)
        model.add_circuit(circuit)
        day_arcs[day.date] = (nodes, arcs)
    utility = sum(pois[pid].group_utility * y for (_, pid), y in visits.items())
    model.maximize(utility)
    error = model.validate()
    if error:
        return RawSolution("MODEL_INVALID")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.search_seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = req.seed
    started = monotonic()
    status = solver.solve(model)
    elapsed = monotonic() - started
    has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    phase = Phase(objective="group_utility", solver_status=solver.status_name(status),
                  objective_value=solver.objective_value if has_solution else None,
                  best_bound=solver.best_objective_bound if status != cp_model.MODEL_INVALID else None,
                  wall_seconds=elapsed, allocated_seconds=req.search_seconds)
    if not has_solution:
        return RawSolution(solver.status_name(status), phases=(phase,))
    days = []
    for day in sorted(req.days, key=lambda d: d.date):
        nodes, arcs = day_arcs[day.date]
        successors = {i: (j, edge) for (i, j), (a, edge) in arcs.items() if solver.value(a)}
        i, depart, items, cost, travel, walk = 0, day.start_min, [], 0, 0, 0
        while True:
            j, edge = successors[i]
            arrival = depart + edge.duration_minutes
            cost += edge.group_fare
            travel += edge.duration_minutes
            walk += edge.walk_minutes
            if j == len(nodes) - 1:
                break
            p = pois[nodes[j]]
            start = solver.value(times[day.date, p.poi_id])
            depart = start + p.duration_min.value
            visit_cost = len(req.members) * p.cost_per_person.value
            cost += visit_cost
            items.append(Visit(poi_id=p.poi_id, arrival_min=arrival, start_min=start,
                               departure_min=depart, travel_minutes=edge.duration_minutes,
                               stay_minutes=p.duration_min.value, visit_cost=visit_cost))
            i = j
        days.append(DayPlan(date=day.date, start_point=day.start_point, end_point=day.end_point,
                            start_min=day.start_min, items=tuple(items), end_arrival_min=arrival,
                            return_travel_minutes=edge.duration_minutes, total_cost=cost,
                            travel_minutes=travel, walk_minutes=walk))
    return RawSolution(solver.status_name(status), tuple(days), (phase,))
