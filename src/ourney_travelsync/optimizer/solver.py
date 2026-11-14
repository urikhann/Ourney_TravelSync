"""CP-SAT construction/extraction. Raw solutions are private until E3 passes."""
from dataclasses import dataclass
from time import monotonic

from ortools.sat.python import cp_model

from .contracts import DayPlan, Eligibility, Phase, PlanRequest, Visit
from .eligibility import common_bounds, usable_cost


@dataclass(frozen=True)
class RawSolution:
    status: str
    days: tuple[DayPlan, ...] = ()
    phases: tuple[Phase, ...] = ()


def solve_prepared(req: PlanRequest, eligibility: tuple[Eligibility, ...]) -> RawSolution:
    """Internal entry point: eligibility must be derived from this same snapshot."""
    if req.search_seconds == 0:
        return RawSolution("UNKNOWN")
    model = cp_model.CpModel()
    bounds = common_bounds(req)
    if len(bounds) != len(req.days):
        return RawSolution("INFEASIBLE")
    eligible = {e.poi_id: e for e in eligibility}
    pois = {p.poi_id: p for p in sorted(req.candidates, key=lambda p: p.poi_id)
            if p.poi_id in eligible and eligible[p.poi_id].schedule_eligible}
    edges = {(e.date, e.origin, e.destination): e for e in req.edges if e.usable}
    visits, times, day_arcs = {}, {}, {}
    total_cost = []
    for day in sorted(req.days, key=lambda d: d.date):
        nodes = [day.start_point, *pois, day.end_point]
        end = len(nodes) - 1
        bound = bounds[day.date]
        starts = {0: model.new_constant(day.start_min),
                  end: model.new_int_var(day.start_min, bound.end_min, f"end_{day.date}")}
        circuit = [(end, 0, model.new_constant(1))]
        arcs = {}
        for i, pid in enumerate(pois, 1):
            y = model.new_bool_var(f"visit_{day.date}_{pid}")
            visits[day.date, pid] = y
            total_cost.append(len(req.members) * (pois[pid].cost_per_person.value if usable_cost(pois[pid].cost_per_person) else 0) * y)
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
        composition = req.daily_composition
        if composition is not None:
            cafe_ids = [pid for pid,p in pois.items() if p.visit_category == "cafe"]
            meal_ids = [pid for pid,p in pois.items() if p.visit_category == "meal"]
            activity_ids = [pid for pid,p in pois.items() if p.visit_category == "activity"]
            model.add(sum(visits[day.date,pid] for pid in cafe_ids) <= composition.max_cafes)
            meal_count = sum(visits[day.date,pid] for pid in meal_ids)
            model.add(meal_count >= composition.min_meals)
            model.add(meal_count <= composition.max_meals)
            model.add(sum(visits[day.date,pid] for pid in activity_ids) >= composition.min_activities)
            covered = []
            for category in sorted({pois[pid].activity_category for pid in activity_ids}):
                z = model.new_bool_var(f"activity_category_{day.date}_{category}")
                model.add_max_equality(z, [visits[day.date,pid] for pid in activity_ids
                                          if pois[pid].activity_category == category])
                covered.append(z)
            model.add(sum(covered) >= composition.min_activity_categories)
            assigned = {k: [] for k in range(len(composition.meal_windows))}
            for pid in meal_ids:
                choices = []
                for k,w in enumerate(composition.meal_windows):
                    z = model.new_bool_var(f"meal_{day.date}_{pid}_{w.name}")
                    model.add(times[day.date,pid] >= w.start_min).only_enforce_if(z)
                    model.add(times[day.date,pid] + pois[pid].duration_min.value <= w.end_min).only_enforce_if(z)
                    choices.append(z)
                    assigned[k].append(z)
                model.add(sum(choices) == visits[day.date,pid])
            for choices in assigned.values():
                model.add(sum(choices) <= 1)

        for i in range(end):
            for j in range(1, end + 1):
                if i == j:
                    continue
                if (req.daily_composition is not None and req.daily_composition.separate_food_drink
                        and i != 0 and j != end
                        and pois[nodes[i]].visit_category in ("meal", "cafe")
                        and pois[nodes[j]].visit_category in ("meal", "cafe")):
                    continue  # an activity must separate food/drink stops
                edge = edges.get((day.date, nodes[i], nodes[j]))
                if edge is None:
                    continue  # no fabricated zero-time arc
                if req.max_leg_minutes is not None and edge.duration_minutes > req.max_leg_minutes:
                    continue
                a = model.new_bool_var(f"arc_{day.date}_{i}_{j}")
                circuit.append((i, j, a))
                arcs[i, j] = (a, edge)
                total_cost.append(edge.group_fare * a)
                stay = 0 if i == 0 else pois[nodes[i]].duration_min.value
                model.add(starts[j] >= starts[i] + stay + edge.duration_minutes).only_enforce_if(a)
        model.add_circuit(circuit)
        if req.max_total_walk_minutes is not None:
            model.add(sum(a * e.walk_minutes for a, e in arcs.values()) <= req.max_total_walk_minutes)
        day_arcs[day.date] = (nodes, arcs)
    for pid in pois:
        model.add(sum(visits[day.date, pid] for day in req.days) <= 1)
    if req.enforce_cost:
        model.add(sum(total_cost) <= req.budget_total)
    for lock in req.locked_items:
        if lock.poi_id not in pois:
            return RawSolution("INFEASIBLE")
        model.add(visits[lock.date, lock.poi_id] == 1)
        if lock.start_min is not None:
            model.add(times[lock.date, lock.poi_id] == lock.start_min)
    utility = sum(pois[pid].group_utility * y for (_, pid), y in visits.items())
    minimum = model.new_int_var(0, len(pois) * 10000, "minimum_member_relevance")
    model.add_min_equality(minimum, [sum(round(pois[pid].relevance[m.member_id] * 10000) * y
                                        for (_, pid), y in visits.items()) for m in req.members])
    travel = sum(a * e.duration_minutes for _, arcs in day_arcs.values() for a, e in arcs.values())
    # Fixed phase caps sum to the request cap. Also check an elapsed deadline so
    # CP-SAT teardown/overhead cannot buy another full search allocation.
    deadline = monotonic() + req.search_seconds
    phases, incumbent, all_optimal = [], (), True
    objectives = [("group_utility", utility, True, 0.7),
                  ("minimum_relevance", minimum, True, 0.2),
                  ("travel_minutes", travel, False, 0.1)]
    for name, expression, maximize, fraction in objectives:
        allocated = min(req.search_seconds * fraction, max(0, deadline - monotonic()))
        if allocated <= 0:
            all_optimal = False
            break
        if maximize:
            model.maximize(expression)
        else:
            model.minimize(expression)
        if model.validate():
            phases.append(Phase(objective=name, solver_status="MODEL_INVALID", objective_value=None,
                                best_bound=None, wall_seconds=0, allocated_seconds=0))
            if not incumbent:
                return RawSolution("MODEL_INVALID", phases=tuple(phases))
            all_optimal = False
            break
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = allocated
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = req.seed
        started = monotonic()
        status = solver.solve(model)
        elapsed = monotonic() - started
        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        phases.append(Phase(objective=name, solver_status=solver.status_name(status),
                            objective_value=solver.objective_value if has_solution else None,
                            best_bound=solver.best_objective_bound if status != cp_model.MODEL_INVALID else None,
                            wall_seconds=elapsed, allocated_seconds=allocated))
        if not has_solution:
            if not incumbent:
                return RawSolution(solver.status_name(status), phases=tuple(phases))
            all_optimal = False
            break  # retain earlier solution; never read values from UNKNOWN
        incumbent = _extract(req, solver, pois, day_arcs, times)
        all_optimal = all_optimal and status == cp_model.OPTIMAL
        model.add(expression == solver.value(expression))
        model.clear_hints()
        for index in range(len(model.proto.variables)):
            var = model.get_int_var_from_proto_index(index)
            model.add_hint(var, solver.value(var))
    if not incumbent:
        return RawSolution("UNKNOWN", phases=tuple(phases))
    return RawSolution("OPTIMAL" if all_optimal and len(phases) == 3 else "FEASIBLE",
                       incumbent, tuple(phases))


def _extract(req, solver, pois, day_arcs, times):
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
            visit_cost = len(req.members) * (p.cost_per_person.value if usable_cost(p.cost_per_person) else 0)
            cost += visit_cost
            items.append(Visit(poi_id=p.poi_id, arrival_min=arrival, start_min=start,
                               departure_min=depart, travel_minutes=edge.duration_minutes,
                               stay_minutes=p.duration_min.value, visit_cost=visit_cost))
            i = j
        days.append(DayPlan(date=day.date, start_point=day.start_point, end_point=day.end_point,
                            start_min=day.start_min, items=tuple(items), end_arrival_min=arrival,
                            return_travel_minutes=edge.duration_minutes, total_cost=cost,
                            travel_minutes=travel, walk_minutes=walk))
    return tuple(days)
