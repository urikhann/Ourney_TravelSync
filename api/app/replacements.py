"""Private, validated replacement drafts. No optimizer runs during approval."""
import asyncio
import copy
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from ourney_travelsync.optimizer.contracts import Candidate, DayPlan, Lock, PlanRequest
from ourney_travelsync.optimizer.repair import repair, same_day_request
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.optimizer.eligibility import check_eligibility, usable_cost
from ourney_travelsync.geography import haversine_km
from ourney_travelsync.replacement_intent import extract_replacement, resolve_target

from . import config, services
from .drive_planning import solve_checked
from .models import Itinerary, OptimizationRun, Poi, ProposalVote, Trip, Vote
from .optimization import PlanningSnapshot, fingerprint, state, with_visit_identities
from ourney_travelsync.place_identity import candidate_identity

log = logging.getLogger("uvicorn.error")


def current_time():
    return datetime.now(timezone.utc)


def plans(days):
    fields = set(DayPlan.model_fields)
    return tuple(DayPlan.model_validate({k: v for k, v in d.items() if k in fields} | {
        "items": [{k: v for k, v in i.items() if k in {"poi_id", "arrival_min", "start_min", "departure_min",
                   "travel_minutes", "stay_minutes", "visit_cost", "breakfast_status"}} for i in d["items"]]}) for d in days)


def protected(plan, audit=None, db=None):
    from .lock_policy import describe
    info = describe(plan, audit, db, current_time())
    return {i["poi_id"] for d in plan.days for i in d["items"] if info[f"{d['date']}@{i['start']}"]["protection_reason"]}


def persist_draft(db, trip, message, plan, audit, req, days, configuration, before, meta, evidence):
    """One private persistence gate for every planning scope."""
    from .proposals import published_plan
    checked = validate_final(req, days)
    if not checked.passed:
        raise HTTPException(409, "final_validation_failed")
    base_hash = meta["base_plan_hash"]
    db.scalar(select(Trip).where(Trip.id == trip.id).with_for_update())
    if db.bind.dialect.name == "sqlite":
        db.execute(update(Trip).where(Trip.id == trip.id).values(name=Trip.name))
    db.expire(plan)
    _, latest = published_plan(db, trip.id)
    if state(db, trip.id) != before or not latest or latest.id != audit.id or fingerprint(plan.days) != base_hash:
        raise HTTPException(409, "stale_proposal")
    from .lock_policy import describe
    info = describe(plan, audit, db, current_time())
    explicit = {v["poi_id"]: info[f"{d['date']}@{v['start']}"]["lock_reason"]
                for d in plan.days for v in d["items"] if info[f"{d['date']}@{v['start']}"]["locked"]}
    draft = OptimizationRun(trip_id=trip.id, recommendation_run_id=audit.recommendation_run_id,
        requested_by=message.author_id, status="VALIDATED", input={**copy.deepcopy(audit.input),
            "configuration": configuration, "state": before, "lock_provenance": explicit,
            "replacement": {**meta, "base_run_id": audit.id, "base_itinerary_id": plan.id,
                "created_at": current_time().isoformat(), "explicit_lock_ids": list(explicit)}},
        optimizer_request=req.model_dump(mode="json"), planning_data=copy.deepcopy(audit.planning_data),
        response={"days": [d.model_dump(mode="json") for d in days], "metrics": checked.metrics,
                  "validation_status": "PASSED", "solver_status": "FEASIBLE", "replacement_evidence": evidence})
    db.add(draft)
    db.flush()
    return draft


def explanation(code, korean):
    messages = {
        "ambiguous_target_poi": ("Which scheduled occurrence do you mean? Please give its date and time.", "어느 일정의 장소인가요? 날짜와 시간을 알려 주세요."),
        "target_poi_not_found": ("Which scheduled place should I replace? Please give its name, date and time.", "바꿀 장소의 이름과 날짜, 시간을 알려 주세요."),
        "locked_activity_conflict": ("That activity is locked or already started. The plan is unchanged.", "잠겨 있거나 이미 시작한 일정은 바꿀 수 없어요. 기존 일정은 유지됩니다."),
        "replacement_search_timeout": ("The replacement search reached its time limit; this does not prove no replacement exists. The plan is unchanged.", "대체 장소 검색 시간이 초과됐어요. 대체 장소가 없다는 뜻은 아니며 기존 일정은 유지됩니다."),
        "opening_hours_unknown": ("Required opening hours could not be verified. No change is ready for voting; the plan is unchanged.", "필요한 운영시간을 확인하지 못했어요. 투표할 변경안을 만들지 않았으며 기존 일정은 유지됩니다."),
        "routing_data_unavailable": ("Required travel data could not be verified. The plan is unchanged.", "필요한 이동시간을 확인하지 못했어요. 기존 일정은 유지됩니다."),
        "stale_proposal": ("The plan or its constraints changed. Please request a new proposal.", "일정이나 조건이 변경됐어요. 새 변경안을 요청해 주세요."),
        "unsupported_replacement_constraints": ("This request includes additional conditions I cannot verify automatically. Please clarify which conditions are mandatory.", "추가 조건을 자동으로 검증할 수 없어요. 반드시 지켜야 할 조건을 구체적으로 알려 주세요."),
    }
    default = ("No validated replacement was found within the search limits. The plan is unchanged. Would you like to request broader replanning?",
               "검색 범위 안에서 조건을 만족하는 대체 장소를 찾지 못했어요. 기존 일정은 유지했습니다. 더 넓은 범위로 다시 계획해 볼까요?")
    return messages.get(code, default)[bool(korean)]


async def refresh(req, audit, ids, legs, *, attempts=None):
    context = copy.deepcopy(audit.planning_data["request"])
    context["pois"] = [p for p in context["pois"] if p["poi_id"] in ids]
    if {p["poi_id"] for p in context["pois"]} != ids:
        raise HTTPException(409, "replacement_candidate_data_missing")
    context["day_candidate_ids"] = {d.date.isoformat(): sorted(ids) for d in req.days}
    context.update(route_legs=[dict(date=d.isoformat(), origin=a, destination=b) for d, a, b in legs],
                   routing_attempts=attempts or {}, expand_routing_budget=False)
    context["context_id"] = fingerprint({k: v for k, v in context.items() if k != "context_id"})
    response = await services.planning_data(context, selected=True)
    data = PlanningSnapshot.model_validate(response)
    if any(getattr(data, k) != context[k] for k in ("context_id", "dataset_version", "transport_mode")):
        raise HTTPException(502, "planning_data_invalid_response")
    facts = {p.poi_id: p for p in data.pois}
    if set(facts) != ids or len(facts) != len(data.pois):
        raise HTTPException(502, "planning_data_invalid_response")
    requested = set(legs)
    # Some adapters return extra cached edges. Never consume unrequested ones.
    # Never mix routing versions. Reusing edges is safe only within one provider policy.
    if data.routing_version != req.routing_version:
        raise HTTPException(409, "routing_version_changed")
    edges = {(e.date, e.origin, e.destination): e for e in req.edges}
    for key in requested:
        edges.pop(key, None)
    for e in data.edges:
        if (e.date, e.origin, e.destination) in requested:
            edges[e.date, e.origin, e.destination] = e
    candidates = []
    for p in req.candidates:
        raw = p.model_dump(mode="json")
        if p.poi_id in facts:
            # Category/user duration policy is shared with normal generation; adapter
            # durations do not override the captured rule/user duration.
            raw.update(facts[p.poi_id].model_dump(mode="json", exclude={"poi_id", "duration_min"}))
        candidates.append(Candidate.model_validate(raw))
    return PlanRequest.model_validate({**req.model_dump(mode="json"),
        "candidates": [p.model_dump(mode="json") for p in candidates],
        "edges": [e.model_dump(mode="json") for e in edges.values()],
        "operating_snapshot_id": data.operating_snapshot_id, "routing_version": data.routing_version}), response


def path_edges(days):
    return [(d.date, a, b) for d in days
            for a, b in zip((d.start_point, *(i.poi_id for i in d.items)),
                            (*(i.poi_id for i in d.items), d.end_point))]


async def validate_proposal(db, trip, message, plan, audit, answer, *, intent_override=None):
    """Backend gate for model suggestions and deterministic replacement intents alike."""
    intent = intent_override or extract_replacement(message.text)
    suggested = answer.get("proposal")
    if not intent and not suggested:
        return answer
    ko = any("가" <= c <= "힣" for c in message.text)
    started, request_id = time.monotonic(), uuid4().hex
    answer = {**answer, "proposal": None, "language": "ko" if ko else "en"}
    reason, rejections = "no_replacement_candidates", []
    try:
        async with asyncio.timeout(config.REPLACEMENT_TOTAL_SECONDS):
            if not plan or not audit:
                raise HTTPException(409, "target_poi_not_found")
            if intent and intent["replacement_constraints"].get("needs_clarification"):
                raise HTTPException(409, "unsupported_replacement_constraints")
            base = with_visit_identities(PlanRequest.model_validate(audit.optimizer_request), db)
            base_hash = fingerprint(plan.days)
            original = plans(plan.days)
            before = state(db, trip.id)
            if before != audit.input["state"]:
                raise HTTPException(409, "stale_proposal")
            rows = db.scalars(select(Poi).where(Poi.dataset_version == base.dataset_version)).all()
            names = {p.id: [p.name, p.name_ko] for p in rows}
            if intent:
                target, error = resolve_target(intent, plan.days, names)
                if error:
                    raise HTTPException(409, error)
            else:
                if len(suggested.get("remove_item_ids", [])) != 1 or len(suggested.get("add", [])) != 1:
                    raise HTTPException(409, "ambiguous_target_poi")
                targets = [dict(date=d["date"], item=i, item_id=f"{d['date']}@{i['start']}")
                           for d in plan.days for i in d["items"]
                           if f"{d['date']}@{i['start']}" == suggested["remove_item_ids"][0] and d["date"] == suggested.get("date")]
                if len(targets) != 1:
                    raise HTTPException(409, "target_poi_not_found")
                target = targets[0]
            protected_ids = protected(plan, audit, db)
            old_id = target["item"]["poi_id"]
            if old_id in protected_ids:
                raise HTTPException(409, "locked_activity_conflict")
            day = next(d for d in original if d.date.isoformat() == target["date"])
            index = next(i for i, v in enumerate(day.items) if v.poi_id == old_id)
            used = {v.poi_id for d in original for v in d.items}
            used_identities = {candidate_identity(p) for p in base.candidates if p.poi_id in used}
            desired = (intent or {}).get("replacement_constraints", {}).get("category")
            banned = {pid for m in base.members for pid in m.excluded_poi_ids}
            tags = {tag for m in base.members for tag in m.excluded_tags}
            pool = [p for p in base.candidates if p.poi_id not in used | banned and not tags.intersection(p.tags)
                    and candidate_identity(p) not in used_identities
                    and (not desired or p.visit_category == desired)
                    and (p.allowed_dates is None or day.date in p.allowed_dates)
                    and p.visit_category != "evening"]
            named_replacement = (intent or {}).get("replacement_constraints", {}).get("replacement_poi_name")
            if named_replacement:
                pool = [p for p in pool if named_replacement.casefold() in [n.casefold() for n in names.get(p.poi_id, []) if n]]
                if len(pool) > 1:
                    raise HTTPException(409, "ambiguous_target_poi")
            # List-context fairness uses the resulting per-member relevance sums,
            # never GFAR's selection gain as an unconditional item utility.
            retained = [p for p in base.candidates if p.poi_id in used - {old_id}]
            coordinates = {p.id: (p.lat, p.lng) for p in rows}
            configuration = audit.input["configuration"]
            daily_input = next(d for d in configuration["days"] if d["date"] == target["date"])
            prev = day.items[index-1].poi_id if index else day.start_point
            nxt = day.items[index+1].poi_id if index+1 < len(day.items) else day.end_point
            coordinates[day.start_point] = (daily_input["start_point"]["latitude"], daily_input["start_point"]["longitude"])
            coordinates[day.end_point] = (daily_input["end_point"]["latitude"], daily_input["end_point"]["longitude"])
            def relevance(p):
                fairness = min(sum(q.relevance[m.member_id] for q in retained) + p.relevance[m.member_id]
                               for m in base.members) / (len(retained)+1)
                added_km = max(0, haversine_km(coordinates[prev], coordinates[p.poi_id])
                               + haversine_km(coordinates[p.poi_id], coordinates[nxt])
                               - haversine_km(coordinates[prev], coordinates[old_id])
                               - haversine_km(coordinates[old_id], coordinates[nxt]))
                return (config.REPLACEMENT_FAIRNESS_WEIGHT*fairness + config.REPLACEMENT_RELEVANCE_WEIGHT*p.mean_score
                        - config.REPLACEMENT_DISTANCE_WEIGHT * min(1, added_km/20))
            pool.sort(key=lambda p: (-relevance(p), -p.mean_score, p.group_rank, p.poi_id))
            if suggested and not intent:
                preferred = suggested["add"][0]["poi_id"]
                pool.sort(key=lambda p: p.poi_id != preferred)
            pool = pool[:config.REPLACEMENT_CANDIDATE_LIMIT]
            log.info("replacement request=%s base=%s target=%s candidates=%s intent=replace_poi", request_id, audit.id, old_id, len(pool))
            # Preserve displayed locks even when a legacy audit had only a date lock.
            from .lock_policy import provenance
            configured_locks = set(provenance(db, audit)[0])
            locks = {l.poi_id: l for l in base.locked_items if l.poi_id in configured_locks or l.poi_id in protected_ids}
            for d in original:
                for item in d.items:
                    if item.poi_id in protected_ids:
                        locks[item.poi_id] = Lock(poi_id=item.poi_id, date=d.date, start_min=item.start_min)
            base = base.model_copy(update={"locked_items": tuple(locks.values())})
            selected = None
            attempts, day_trials = {}, []
            for candidate in pool:
                prev = day.items[index-1].poi_id if index else day.start_point
                nxt = day.items[index+1].poi_id if index+1 < len(day.items) else day.end_point
                needed = [(day.date, prev, candidate.poi_id), (day.date, candidate.poi_id, nxt)]
                req, evidence = await refresh(base, audit, (used - {old_id}) | {candidate.poi_id}, needed, attempts=attempts)
                attempts = evidence.get("routing_attempts") or attempts
                available_edges = {(e.date, e.origin, e.destination) for e in req.edges if e.usable}
                if not set(needed) <= available_edges:
                    rejections.append("routing_data_unavailable")
                    continue
                req = req.model_copy(update={"locked_items": (*req.locked_items, Lock(poi_id=candidate.poi_id, date=day.date))})
                eligibility = next(e for e in check_eligibility(req) if e.poi_id == candidate.poi_id)
                if day.date not in eligibility.feasible_dates:
                    rejections.append("candidate_not_schedule_eligible")
                    log.info("replacement request=%s candidate=%s e2_rejected=%s", request_id, candidate.poi_id, eligibility.reasons)
                    continue
                outcome = repair(req, original, day.date, index, candidate.poi_id,
                                 radius=config.REPLACEMENT_REPAIR_RADIUS, shift_minutes=config.REPLACEMENT_SHIFT_MINUTES,
                                 protected_ids=protected_ids)
                log.info("replacement request=%s candidate=%s repair=%s reason=%s e3=%s", request_id, candidate.poi_id,
                         outcome.strategy, outcome.reason, bool(outcome.days))
                if outcome.days:
                    selected = req, outcome, candidate, evidence
                    break
                rejections.append(outcome.reason)
                if outcome.reason == "local_repair_failed":
                    day_trials.append((req, candidate, evidence))
            # Exhaust cheap local repairs before spending a solver budget.
            for req, candidate, evidence in ([] if selected else day_trials):
                remaining = config.REPLACEMENT_TOTAL_SECONDS - (time.monotonic()-started)
                trial = same_day_request(req, original, day.date, index, candidate.poi_id,
                                         seconds=min(config.REPLACEMENT_SOLVER_SECONDS, max(0, remaining)),
                                         protected_ids=protected_ids)
                solver_started = time.monotonic()
                _, result = await solve_checked(trial)
                log.info("replacement request=%s strategy=same_day solver=%s seconds=%.3f", request_id, result.solver_status, time.monotonic()-solver_started)
                if result.solver_status == "UNKNOWN":
                    rejections.append("optimizer_search_timeout")
                if result.validation_status == "PASSED" and result.solver_status in ("OPTIMAL", "FEASIBLE") and not result.error_code:
                    merged = tuple(result.days[0] if d.date == day.date else d for d in original)
                    if validate_final(req, merged).passed:
                        from ourney_travelsync.optimizer.repair import RepairResult
                        selected = req, RepairResult(merged, "same_day", ""), candidate, evidence
                        break
            if not selected:
                reason = ("replacement_search_timeout" if "optimizer_search_timeout" in rejections else
                          "opening_hours_unknown" if "opening_hours_unknown" in rejections else
                          "routing_data_unavailable" if "routing_data_unavailable" in rejections else
                          "no_feasible_replacement" if pool else "no_replacement_candidates")
                raise HTTPException(409, reason)
            req, outcome, candidate, evidence = selected
            checked = validate_final(req, outcome.days)
            if not checked.passed:
                raise HTTPException(409, "final_validation_failed")
            configuration = copy.deepcopy(audit.input["configuration"])
            configuration["excluded_poi_ids"] = sorted(set(configuration.get("excluded_poi_ids", [])) | {old_id})
            draft = persist_draft(db, trip, message, plan, audit, req, outcome.days, configuration, before,
                dict(base_plan_hash=base_hash, request_id=request_id, target_item_id=target["item_id"],
                     strategy=outcome.strategy, action_type="replace_poi", affected_dates=[target["date"]],
                     protected_ids=sorted(protected_ids)), evidence)
            log.info("replacement request=%s proposal=%s base=%s strategy=%s e3=PASSED status=PENDING",
                     request_id, draft.id, audit.id, outcome.strategy)
            visit = next(v for d in outcome.days for v in d.items if v.poi_id == candidate.poi_id)
            changes = [dict(date=d.date.isoformat(), poi_id=v.poi_id, start_min=v.start_min, departure_min=v.departure_min)
                       for d in outcome.days for v in d.items if v.poi_id != candidate.poi_id and
                       any(o.poi_id == v.poi_id and (o.start_min, o.departure_min) != (v.start_min, v.departure_min)
                           for original_day in original for o in original_day.items)]
            name = next((p.name for p in rows if p.id == candidate.poi_id), candidate.poi_id)
            reply = (f"{name} 방문이 가능한 변경안을 검증했어요. " if ko else f"I validated a feasible replacement with {name}. ")
            reply += (("주변 일정 시간 변경을 확인한 뒤 투표해 주세요." if ko else "Review the changed activity times before voting.") if changes else
                      ("다른 일정 시간은 유지됩니다. 변경안에 투표해 주세요." if ko else "Other activity times stay unchanged. Please vote on this proposal."))
            return {**answer, "state": "SUCCEEDED", "reply": reply, "needs_clarification": False,
                    "proposal": {"date": target["date"], "remove_item_ids": [target["item_id"]],
                                 "add": [{"poi_id": candidate.poi_id, "start": f"{visit.start_min//60:02d}:{visit.start_min%60:02d}"}]},
                    "draft_id": draft.id, "validation_status": "PASSED", "repair_strategy": outcome.strategy,
                    "affected_activities": changes}
    except TimeoutError:
        reason = "replacement_search_timeout"
    except HTTPException as exc:
        reason = str(exc.detail)
        answer["diagnostic_code"] = reason
        if reason.startswith(("routing_", "planning_data_")):
            reason = "routing_data_unavailable"
    except (ValueError, KeyError, TypeError):
        reason = "final_validation_failed"
        log.warning("replacement invalid evidence request=%s", request_id)
    finally:
        log.info("replacement request=%s elapsed=%.3f rejections=%s", request_id, time.monotonic()-started, rejections)
    return {**answer, "reply": explanation(reason, ko), "reason": reason,
            "state": "NEEDS_CLARIFICATION" if reason in ("ambiguous_target_poi", "target_poi_not_found", "unsupported_replacement_constraints") else "FAILED"}


async def apply_draft(db, trip, msg):
    from .proposals import published_plan
    draft = db.get(OptimizationRun, msg.agent.get("draft_id"))
    plan, base = published_plan(db, trip.id)
    if not draft or draft.trip_id != trip.id or draft.status != "VALIDATED":
        return {"status": "STALE", "reason": "unvalidated_proposal"}
    meta = draft.input["replacement"]
    if (not base or base.id != meta["base_run_id"] or plan.id != meta["base_itinerary_id"] or fingerprint(plan.days) != meta["base_plan_hash"]
            or state(db, trip.id) != draft.input["state"]
            or (current_time() - datetime.fromisoformat(meta["created_at"])).total_seconds() > config.REPLACEMENT_VALID_SECONDS):
        return {"status": "STALE", "reason": "stale_proposal"}
    req, days = with_visit_identities(PlanRequest.model_validate(draft.optimizer_request), db), plans(draft.response["days"])
    # Do not apply a proposal whose target started while members were voting.
    if protected(plan, base, db) - set(meta["protected_ids"]):
        return {"status": "STALE", "reason": "locked_activity_conflict"}
    try:
        async with asyncio.timeout(config.REPLACEMENT_TOTAL_SECONDS):
            refreshed, evidence = await refresh(req, draft, {v.poi_id for d in days for v in d.items}, path_edges(days))
    except (HTTPException, TimeoutError, ValueError, KeyError):
        return {"status": "FAILED", "reason": "routing_data_unavailable"}
    # Account for current facts using the exact approved schedule; no regeneration.
    from ourney_travelsync.optimizer.repair import rebuild_day
    rebuilt = tuple(rebuild_day(refreshed, d, [(v, v.start_min) for v in d.items]) for d in days)
    if any(d is None for d in rebuilt):
        return {"status": "STALE", "reason": "proposal_evidence_changed"}
    if rebuilt != days:
        return {"status": "STALE", "reason": "proposal_evidence_changed"}
    checked = validate_final(refreshed, days)
    # A replacement always requires positively verified hours, even when retained
    # historical visits are allowed to have unknown hours under the app policy.
    old_ids = {v["poi_id"] for d in plan.days for v in d["items"]}
    for day in days:
        for visit in day.items:
            if visit.poi_id in old_ids and meta.get("action_type", "replace_poi") != "replace_poi":
                continue
            if visit.poi_id not in {a["poi_id"] for a in msg.agent["proposal"].get("add", [])}:
                continue
            candidate = next(p for p in refreshed.candidates if p.poi_id == visit.poi_id)
            op = candidate.operations.get(day.date)
            if op is None or not op.openings.known or not op.closed.known:
                return {"status": "FAILED", "reason": "final_validation_failed"}
    if not checked.passed:
        return {"status": "FAILED", "reason": "final_validation_failed"}
    # Trip is locked by the voting endpoint across the freshness check and write.
    db.expire(plan)
    _, latest = published_plan(db, trip.id)
    if latest.id != base.id or state(db, trip.id) != draft.input["state"] or fingerprint(plan.days) != meta["base_plan_hash"]:
        return {"status": "STALE", "reason": "stale_proposal"}
    rendered = copy.deepcopy(plan.days)
    by_id = {p.poi_id: p for p in refreshed.candidates}
    from .routes.itineraries import hhmm, visit_availability
    for raw, day in zip(rendered, days):
        if raw["date"] != day.date.isoformat():
            return {"status": "FAILED", "reason": "final_validation_failed"}
        if raw["date"] not in meta.get("affected_dates", [msg.agent["proposal"]["date"]]):
            continue
        old_items = {i["poi_id"]: i for i in raw["items"]}
        raw.update(day.model_dump(mode="json"))
        raw.update(start=hhmm(day.start_min), end_arrival=hhmm(day.end_arrival_min),
                   gaps=[g for g in checked.metrics["waiting_gaps"] if g["date"] == raw["date"]])
        raw["items"] = [{**old_items.get(v.poi_id, {}), **v.model_dump(mode="json"),
            "start": hhmm(v.start_min), "end": hhmm(v.departure_min), "arrival": hhmm(v.arrival_min),
            "locked": v.poi_id in set(meta.get("explicit_lock_ids", meta["protected_ids"])),
            "lock_reason": draft.input.get("lock_provenance", {}).get(v.poi_id), "moved_from": None,
            "visit_category": by_id[v.poi_id].visit_category, "activity_category": by_id[v.poi_id].activity_category,
            "duration_source": by_id[v.poi_id].duration_min.source,
            "duration_estimated": by_id[v.poi_id].duration_min.status == "ESTIMATED",
            "availability": visit_availability(by_id[v.poi_id], day.date, v)} for v in day.items]
        if refreshed.daily_composition is not None:
            from ourney_travelsync.optimizer.meals import effective_meals
            requested = next(d for d in refreshed.days if d.date == day.date)
            meals = effective_meals(requested, refreshed.daily_composition)
            raw["breakfast_omitted"] = (any(m.name == "breakfast" and not m.require_verified for m in meals)
                                        and not any(v.breakfast_status for v in day.items))
            raw["evening_omitted"] = (refreshed.daily_composition.evening_enabled
                                      and not any(by_id[v.poi_id].visit_category == "evening" for v in day.items))
    # Keep the original as a separate version. The new audit becomes the published head.
    new_plan = Itinerary(trip_id=trip.id, run_id=plan.run_id, letter=chr(min(ord(plan.letter)+1, ord("Z"))),
        variant="optimized", days=rendered, stats={**plan.stats, "total_cost": checked.metrics["total_cost"],
        "group_fit": checked.metrics["mean_personal_fit"] if plan.stats.get("group_fit") is not None else None,
        "validation_status": "PASSED", "travel_minutes": checked.metrics["travel_minutes"],
        "waiting_minutes": checked.metrics["waiting_minutes"], "optimization_run_id": draft.id,
        "routing_version": refreshed.routing_version, "operating_snapshot_id": refreshed.operating_snapshot_id,
        "solver_status": "FEASIBLE", "objective_values": [], "routing_warnings": evidence.get("routing_warnings", []),
        "cost_unknown_count": sum(not usable_cost(by_id[v.poi_id].cost_per_person) for d in days for v in d.items)})
    db.add(new_plan)
    db.flush()
    # A member's approval of this exact schedule is also their plan selection.
    # Preserve dissenting votes and the original version; the actual approving
    # majority makes the approved version visible as the chosen plan.
    approving = db.scalars(select(ProposalVote.user_id).where(ProposalVote.message_id == msg.id, ProposalVote.approve.is_(True))).all()
    for uid in approving:
        selection = db.scalar(select(Vote).where(Vote.trip_id == trip.id, Vote.user_id == uid))
        if selection:
            selection.itinerary_id = new_plan.id
        else:
            db.add(Vote(trip_id=trip.id, user_id=uid, itinerary_id=new_plan.id))
    draft.status, draft.itinerary_id = "PASSED", new_plan.id
    draft.optimizer_request = refreshed.model_dump(mode="json")
    draft.response = {**draft.response, "metrics": checked.metrics, "replacement_evidence": evidence}
    start = next((a["start"] for a in msg.agent["proposal"].get("add", [])), None)
    return {"status": "APPLIED", "mode": "keep" if meta["strategy"] != "same_day" else "rebuild",
            "strategy": meta["strategy"], "start": start, "plan_run_id": draft.id}
