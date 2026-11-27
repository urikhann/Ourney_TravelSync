"""Scoped planning operations producing drafts through the existing E3 gate."""
import asyncio
import copy
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select

from ourney_travelsync.optimizer.contracts import Lock, PlanRequest, Visit
from ourney_travelsync.optimizer.repair import rebuild_day
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.replacement_intent import resolve_target
from ourney_travelsync.place_identity import candidate_identity

from . import config, replacements as repair, services, planning_intents
from .drive_planning import plan_drive, solve_checked
from .models import OptimizationRun, Poi, RecommendationRun
from .optimization import GenerateInput, fingerprint, solver_request, state, with_visit_identities


def failure(reason, ko, *, details=None):
    if reason == "no_itinerary_changes":
        return repair.unchanged_answer(ko)
    messages = {
        "optimizer_search_timeout": ("Planning reached its search time limit; infeasibility is not established. The current plan is unchanged.", "일정 검색 시간이 초과됐어요. 불가능하다고 확정된 것은 아니며 기존 일정은 유지됩니다."),
        "optimizer_no_feasible_itinerary": ("No feasible plan was found for the requested scope and hard constraints. The current plan is unchanged.", "요청한 범위와 필수 조건을 만족하는 일정을 찾지 못했어요. 기존 일정은 유지됩니다."),
        "scope_date_required": ("Which date should I change? Please give YYYY-MM-DD.", "변경할 날짜를 YYYY-MM-DD 형식으로 알려 주세요."),
        "no_pending_action": ("There is no confirmation question associated with this reply. Please state the action or reply to its question.", "이 응답에 연결된 확인 질문이 없어요. 원하는 작업을 말씀하거나 해당 질문에 답해 주세요."),
        "ambiguous_pending_action": ("More than one question is awaiting your answer. Reply to the specific question using its message ID.", "답변을 기다리는 질문이 여러 개예요. 해당 질문의 메시지 ID를 지정해 답해 주세요."),
        "pending_action_expired": ("That confirmation has expired. Please request the action again.", "확인 요청이 만료됐어요. 작업을 다시 요청해 주세요."),
        "pending_action_consumed": ("That confirmation was already handled. It cannot execute again.", "이미 처리한 확인 요청이라 다시 실행할 수 없어요."),
        "pending_action_stale": ("The itinerary, permissions or constraints changed after that question. Please request a fresh action.", "질문 이후 일정·권한·조건이 변경됐어요. 작업을 다시 요청해 주세요."),
        "locked_activity_conflict": ("Protected activities cannot be changed implicitly. I can replan unlocked activities while preserving explicit locks, reservations and completed visits.", "보호된 일정은 임의로 변경할 수 없어요. 명시적 잠금·예약·완료 일정을 유지하며 잠기지 않은 일정을 다시 계획할 수 있어요."),
        "planning_action_failed": ("The planning operation failed after processing began. No change was applied; please request it again.", "계획 작업 처리 중 오류가 발생했어요. 변경은 적용되지 않았으니 작업을 다시 요청해 주세요."),
        "final_validation_failed": ("The draft failed the final constraint check. No change was applied.", "변경안이 최종 제약조건 검증을 통과하지 못했어요. 변경은 적용되지 않았습니다."),
        "planning_data_request_timeout": ("The operational/routing data service timed out. The current plan is unchanged.", "운영·이동정보 서비스 응답 시간이 초과됐어요. 기존 일정은 유지됩니다."),
        "optimizer_request_timeout": ("The optimizer service timed out. The current plan is unchanged; this does not prove infeasibility.", "최적화 서비스 응답 시간이 초과됐어요. 기존 일정은 유지되며 불가능하다고 확정된 것은 아닙니다."),
        "planning_action_timeout": ("The planning operation reached its total time limit. The current plan is unchanged; infeasibility is not established.", "계획 작업의 전체 시간 제한을 초과했어요. 기존 일정은 유지되며 불가능하다고 확정된 것은 아닙니다."),
        "no_unlocked_activities": ("Every scheduled activity is protected. No locks or reservations were changed. Changing protected activities requires separate explicit authorization in the plan settings.", "모든 일정이 보호되어 있어요. 잠금이나 예약을 변경하지 않았습니다. 보호된 일정을 바꾸려면 일정 설정에서 별도의 명시적 변경 권한을 확인해야 해요."),
        "no_itinerary_yet": ("There is no published itinerary to replan. Build an initial plan with dates and endpoints in the Plan tab first.", "다시 계획할 게시된 일정이 없어요. 일정 탭에서 날짜와 출발·도착 지점을 정해 먼저 일정을 만들어 주세요."),
        "invalid_pending_action": ("I could not verify the requested confirmation action. Please state the operation and target explicitly.", "확인할 작업 내용을 검증하지 못했어요. 작업과 대상 일정을 구체적으로 말씀해 주세요."),
        "agent_invalid_response": ("The assistant returned an invalid structured response. Please state the planning action and target explicitly; the plan is unchanged.", "도우미의 응답 형식이 올바르지 않아요. 작업과 대상 일정을 구체적으로 말씀해 주세요. 기존 일정은 유지됩니다."),
        "planning_owner_required": ("Only the trip owner can request day or whole-trip replanning. Members can still propose individual changes for the group vote.", "하루 또는 전체 일정의 재계획은 여행 소유자만 요청할 수 있어요. 개별 일정 변경 제안은 그룹 투표로 승인할 수 있습니다."),
        "agent_request_timeout": ("The assistant service timed out before returning a planning result. The saved itinerary is unchanged.", "계획 결과를 받기 전에 도우미 서비스 응답 시간이 초과됐어요. 저장된 일정은 유지됩니다."),
        "agent_unavailable": ("The assistant service is unavailable. No itinerary change was applied.", "도우미 서비스에 연결할 수 없어요. 일정 변경은 적용되지 않았습니다."),
    }
    text = messages.get(reason)
    if text is None and reason.startswith("agent_"):
        text = ("The assistant service failed before returning a planning result. The saved itinerary is unchanged.",
                "계획 결과를 받기 전에 도우미 서비스에서 오류가 발생했어요. 저장된 일정은 유지됩니다.")
    reply = text[bool(ko)] if text else repair.explanation(reason, ko)
    if any(e.endswith(":CONSECUTIVE_FOOD_DRINK") for e in details or []):
        context = ("Direct removal would put two food/drink stops in a row. This trip requires an activity between them. ",
                   "이 장소만 제거하면 음식·음료 일정이 연속돼요. 이 여행은 그 사이에 활동 일정이 필요합니다. ")
        reply = context[bool(ko)] + reply
    return dict(state="NEEDS_CLARIFICATION" if reason in {"scope_date_required", "no_pending_action", "ambiguous_pending_action",
                "ambiguous_target_poi", "target_poi_not_found", "unsupported_replacement_constraints", "invalid_pending_action"} else "FAILED",
        reply=reply, reason=reason,
        diagnostic_code=reason, validation_errors=details or [], proposal=None, tool_calls=[], language="ko" if ko else "en")


def normalized_request(plan, audit, db):
    req = with_visit_identities(PlanRequest.model_validate(audit.optimizer_request), db)
    protected = repair.protected(plan, audit, db)
    locks = tuple(Lock(poi_id=i["poi_id"], date=d["date"], start_min=i["start_min"])
                  for d in plan.days for i in d["items"] if i["poi_id"] in protected)
    return req.model_copy(update={"locked_items": locks}), protected


def target_for(action, plan, rows):
    if action.get("target_item_id"):
        matches = [dict(date=d["date"], item=i, item_id=f"{d['date']}@{i['start']}") for d in plan.days for i in d["items"]
                   if f"{d['date']}@{i['start']}" == action["target_item_id"]]
        if len(matches) == 1:
            return matches[0]
        raise HTTPException(409, "target_poi_not_found")
    name = " ".join(v for v in (action.get("date"), action.get("target_name")) if v)
    target, error = resolve_target(dict(target_poi_name=name), plan.days, {p.id: [p.name, p.name_ko] for p in rows})
    if error:
        raise HTTPException(409, error)
    return target


def strict_new_hours(req, existing):
    candidates = []
    for p in req.candidates:
        if p.poi_id in existing:
            candidates.append(p)
            continue
        allowed = tuple(d.date for d in req.days if (p.allowed_dates is None or d.date in p.allowed_dates)
                        and (op := p.operations.get(d.date)) is not None and op.openings.known and op.closed.known and not op.closed.value)
        candidates.append(p.model_copy(update={"allowed_dates": allowed}))
    return req.model_copy(update={"candidates": tuple(candidates)})


async def scoped_solve(db, trip, message, plan, audit, req, original, action, protected, rows,
                       *, retained_ids=None, routing_attempts=None):
    scope = action["action_type"]
    repair.log.info("planning_action request=%s trip=%s scope=%s stage=start", message.id, trip.id, scope)
    dates = {action["date"]} if scope in {"replan_day", "remove_poi"} else {d["date"] for d in plan.days}
    if None in dates or not dates <= {d["date"] for d in plan.days}:
        raise HTTPException(409, "scope_date_required")
    preserved = {v.poi_id for d in original if d.date.isoformat() not in dates for v in d.items}
    raw = copy.deepcopy(audit.input["configuration"])
    raw["days"] = [d for d in raw["days"] if d["date"] in dates]
    raw["locked_items"] = [l.model_dump(mode="json") for l in req.locked_items if l.date.isoformat() in dates]
    if retained_ids is not None:
        # Solver-only retention constraints: removal cannot add or drop other POIs.
        # Genuine protection remains in req and in the persisted lock provenance.
        fixed = {l["poi_id"]: l for l in raw["locked_items"]}
        raw["locked_items"] = [fixed.get(pid, dict(poi_id=pid, date=action["date"])) for pid in sorted(retained_ids)]
    raw["daily_composition"]["meal_overrides"] = [m for m in raw["daily_composition"].get("meal_overrides", []) if m["date"] in dates]
    body = GenerateInput.model_validate(raw)
    candidates = tuple(p.model_copy(update={"allowed_dates": tuple(d.date for d in req.days if d.date.isoformat() in dates and
                       (p.allowed_dates is None or d.date in p.allowed_dates))}) for p in req.candidates
                       if p.poi_id not in preserved and (retained_ids is None or p.poi_id in retained_ids))
    context = copy.deepcopy(audit.planning_data["request"])
    ids = {p.poi_id for p in candidates}
    context["days"] = [d for d in context["days"] if d["date"] in dates]
    context["pois"] = [p for p in context["pois"] if p["poi_id"] in ids]
    context["day_candidate_ids"] = {d: [p.poi_id for p in candidates if date.fromisoformat(d) in p.allowed_dates] for d in dates}
    context["points"] = {k: v for k, v in context["points"].items() if any(k.endswith(d) for d in dates)}
    work = OptimizationRun(trip_id=trip.id, recommendation_run_id=audit.recommendation_run_id,
        requested_by=message.author_id, status="VALIDATING", input={"action_type": scope, "chat_message_id": message.id,
            "base_run_id": audit.id, **({"retained_ids": sorted(retained_ids)} if retained_ids is not None else {})})
    db.add(work)
    db.flush()
    context["run_id"] = f"{audit.recommendation_run_id}:{work.id}"
    context["context_id"] = fingerprint({k: v for k, v in context.items() if k != "context_id"})
    prepared = req.members, candidates, context["days"], context["points"], {p.id: p for p in rows if p.id in ids}, req.mapping_version
    run = db.get(RecommendationRun, audit.recommendation_run_id)
    existing = {v.poi_id for d in original for v in d.items}
    def transform(r):
        r = strict_new_hours(r, existing)
        preference = action.get("constraints", {}).get("day_preference")
        if preference:
            metadata = {p.id: p for p in rows}
            r = r.model_copy(update={"day_preference": preference, "candidates": tuple(
                p.model_copy(update={"indoor": metadata[p.poi_id].indoor,
                                     "walking_level": metadata[p.poi_id].walking_level}) for p in r.candidates)})
        if retained_ids is not None:
            r = r.model_copy(update={"preferred_start_minutes": {v.poi_id: v.start_min for d in original
                if d.date.isoformat() in dates for v in d.items if v.poi_id in retained_ids}})
        return r
    try:
        if body.transport_mode == "drive":
            updated, _, result, evidence = await plan_drive(run, body, prepared, context, work, db,
                request_transform=transform, initial_attempts=routing_attempts)
        else:
            evidence = await services.planning_data(context)
            updated = transform(solver_request(run, work.id, body, prepared, context, evidence))
            _, result = await solve_checked(updated)
        work.status = "PREVIEWED" if result.validation_status == "PASSED" else "FAILED"
        work.optimizer_request = updated.model_dump(mode="json")
        work.response = result.model_dump(mode="json")
        if result.error_code or result.solver_status not in ("FEASIBLE", "OPTIMAL") or result.validation_status != "PASSED":
            raise HTTPException(409, "optimizer_search_timeout" if result.solver_status == "UNKNOWN" else "optimizer_no_feasible_itinerary")
        if not validate_final(updated, result.days).passed:
            raise HTTPException(409, "final_validation_failed")
    except BaseException as exc:
        work.status = "FAILED"
        work.error_code = str(exc.detail) if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else type(exc).__name__
        raise
    # Merge only affected dates/facts/edges, preserving untouched stored visits.
    fresh = {p.poi_id: p for p in updated.candidates}
    merged_candidates = []
    for old in req.candidates:
        if old.poi_id in fresh:
            p = fresh[old.poi_id]
            merged_candidates.append(p.model_copy(update={"operations": {**old.operations, **p.operations},
                "allowed_dates": tuple(set(old.allowed_dates or [d.date for d in req.days]) - {date.fromisoformat(d) for d in dates}
                                       | set(p.allowed_dates or [d.date for d in updated.days]))}))
        else:
            merged_candidates.append(old)
    merged = req.model_copy(update={"candidates": tuple(merged_candidates),
        "edges": (*[e for e in req.edges if e.date.isoformat() not in dates], *updated.edges),
        "operating_snapshot_id": updated.operating_snapshot_id, "routing_version": updated.routing_version})
    days = tuple(next(x for x in result.days if x.date == d.date) if d.date.isoformat() in dates else d for d in original)
    return merged, days, evidence, dates


async def execute(db, trip, message, plan, audit, action):
    ko = any("가" <= c <= "힣" for c in message.text)
    scope = action["action_type"]
    if scope.startswith("replan_") and trip.created_by != message.author_id:
        return failure("planning_owner_required", ko)
    if planning_intents.unsupported_constraints(action):
        return failure("unsupported_replacement_constraints", ko)
    if scope == "replace_poi":
        rows = db.scalars(select(Poi).where(Poi.dataset_version == PlanRequest.model_validate(audit.optimizer_request).dataset_version)).all() if audit else []
        try:
            if not plan:
                raise HTTPException(409, "target_poi_not_found")
            target = target_for(action, plan, rows)
        except HTTPException as exc:
            return failure(str(exc.detail), ko)
        suggested = dict(date=target["date"], remove_item_ids=[target["item_id"]], add=[dict(poi_id="", start=target["item"]["start"])])
        names = {p.id: p.name for p in rows}
        intent = dict(intent="replace_poi", target_poi_name=f"{target['date']} {target['item']['start']} {names[target['item']['poi_id']]}",
                      replacement_constraints=action.get("constraints", {}))
        return await repair.validate_proposal(db, trip, message, plan, audit,
            dict(reply="", proposal=suggested, tool_calls=[]), intent_override=intent)
    if not plan or not audit:
        return failure("no_itinerary_yet", ko)
    before, base_hash = state(db, trip.id), fingerprint(plan.days)
    if before != audit.input["state"]:
        return failure("stale_proposal", ko)
    req, protected = normalized_request(plan, audit, db)
    if action.get("date") == "today":
        from zoneinfo import ZoneInfo
        action = {**action, "date": repair.current_time().astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()}
    if scope == "replan_unlocked" and len(protected) == sum(len(d["items"]) for d in plan.days):
        return failure("no_unlocked_activities", ko)
    original = repair.plans(plan.days)
    rows = db.scalars(select(Poi).where(Poi.dataset_version == req.dataset_version)).all()
    repair_errors = []
    try:
        async with asyncio.timeout(config.PLANNING_ACTION_SECONDS):
            if scope.startswith("replan_"):
                if scope == "replan_all" and protected:
                    return failure("locked_activity_conflict", ko)
                req, days, evidence, affected = await scoped_solve(db, trip, message, plan, audit, req, original, action, protected, rows)
            else:
                target = target_for(action, plan, rows) if scope == "remove_poi" else None
                day_string = target["date"] if target else action.get("date") or (plan.days[0]["date"] if len(plan.days) == 1 else None)
                if day_string not in {d["date"] for d in plan.days}:
                    raise HTTPException(409, "scope_date_required")
                day = next(d for d in original if d.date.isoformat() == day_string)
                used = {v.poi_id for d in original for v in d.items}
                if target and target["item"]["poi_id"] in protected:
                    raise HTTPException(409, "locked_activity_conflict")
                if target:
                    pid = target["item"]["poi_id"]
                    index = next(i for i, v in enumerate(day.items) if v.poi_id == pid)
                    left = day.items[index-1].poi_id if index else day.start_point
                    right = day.items[index+1].poi_id if index+1 < len(day.items) else day.end_point
                    req, evidence = await repair.refresh(req, audit, used-{pid}, [(day.date, left, right)])
                    changed = rebuild_day(req, day, [(v, v.start_min) for v in day.items if v.poi_id != pid])
                else:
                    used_identities = {candidate_identity(p) for p in req.candidates if p.poi_id in used}
                    matches = [p for p in req.candidates if candidate_identity(p) not in used_identities and any(
                        action.get("target_name", "").casefold() == n.casefold() for n in
                        next(([r.name, r.name_ko] for r in rows if r.id == p.poi_id), []) if n)]
                    if len(matches) != 1:
                        raise HTTPException(409, "ambiguous_target_poi" if matches else "no_replacement_candidates")
                    candidate, changed, evidence = matches[0], None, {}
                    for index in range(len(day.items)+1):
                        left = day.items[index-1].poi_id if index else day.start_point
                        right = day.items[index].poi_id if index < len(day.items) else day.end_point
                        req, evidence = await repair.refresh(req, audit, used|{candidate.poi_id}, [(day.date, left, candidate.poi_id), (day.date, candidate.poi_id, right)])
                        p = next(p for p in req.candidates if p.poi_id == candidate.poi_id)
                        op = p.operations.get(day.date)
                        if op is None or not op.openings.known or not op.closed.known:
                            raise HTTPException(409, "opening_hours_unknown")
                        edge = next((e for e in req.edges if (e.date, e.origin, e.destination) == (day.date, left, p.poi_id) and e.usable), None)
                        if not edge or not p.duration_min.value or op.closed.value:
                            continue
                        arrival = (day.items[index-1].departure_min if index else day.start_min) + edge.duration_minutes
                        for window in op.openings.value:
                            start = max(arrival, window.start_min)
                            if start + p.duration_min.value > window.end_min:
                                continue
                            v = Visit(poi_id=p.poi_id, arrival_min=arrival, start_min=start, departure_min=start+p.duration_min.value,
                                      travel_minutes=edge.duration_minutes, stay_minutes=p.duration_min.value, visit_cost=0)
                            visits = [(v, v.start_min) for v in day.items]
                            visits.insert(index, (v, start))
                            proposed = rebuild_day(req, day, visits)
                            merged = tuple(proposed if d.date == day.date else d for d in original)
                            if proposed and validate_final(req, merged).passed:
                                changed = proposed
                                break
                        if changed:
                            break
                if changed is None:
                    raise HTTPException(409, "optimizer_no_feasible_itinerary")
                days = tuple(changed if d.date == day.date else d for d in original)
                affected = {day_string}
            checked = validate_final(req, days)
            repair.log.info("planning_action request=%s scope=%s stage=e3 passed=%s errors=%s", message.id, scope, checked.passed, checked.errors)
            if scope == "remove_poi" and not checked.passed and all(
                    e.endswith(":CONSECUTIVE_FOOD_DRINK") for e in checked.errors):
                repair_errors = list(checked.errors)
                retained = {v.poi_id for v in day.items if v.poi_id != pid}
                req, days, evidence, affected = await scoped_solve(db, trip, message, plan, audit, req, original,
                    {**action, "date": day_string}, protected, rows, retained_ids=retained,
                    routing_attempts=evidence.get("routing_attempts"))
                checked = validate_final(req, days)
            if not checked.passed:
                return failure("final_validation_failed", ko, details=list(checked.errors))
            preference = action.get("constraints", {}).get("day_preference")
            preference_evidence = None
            if preference:
                from ourney_travelsync.optimizer.comfort import day_burden
                baseline = next(d for d in original if d.date.isoformat() == action["date"])
                proposed = next(d for d in days if d.date == baseline.date)
                before_burden = day_burden(req, baseline, preference)
                after_burden = day_burden(req, proposed, preference)
                if after_burden >= before_burden:
                    return dict(state="SUCCEEDED", reason="no_comfort_improvement", proposal=None, tool_calls=[],
                        reply="필수 조건을 지키면서 하루 전체의 부담을 줄이는 변경안을 찾지 못했어요. 기존 일정은 유지됩니다." if ko else
                              "I could not find a validated improvement for the whole day under the required constraints. The current plan is unchanged.")
                preference_evidence = dict(preference=preference, before=before_burden, after=after_burden,
                    basis="Saved indoor metadata; unknown counts as outdoor" if preference == "indoor" else
                          "Estimated visit walking from saved walking levels, plus route walking minutes")
            old = {v.poi_id: (d.date.isoformat(), v) for d in original for v in d.items}
            new = {v.poi_id: (d.date.isoformat(), v) for d in days for v in d.items}
            removed = [f"{d}@{v.start_min//60:02d}:{v.start_min%60:02d}" for pid, (d, v) in old.items() if pid not in new]
            added = [dict(poi_id=pid, date=d, start=f"{v.start_min//60:02d}:{v.start_min%60:02d}") for pid, (d, v) in new.items() if pid not in old]
            changes = [dict(poi_id=pid, date=d, start_min=v.start_min, departure_min=v.departure_min) for pid, (d, v) in new.items()
                       if pid not in old or old[pid] != (d, v)]
            draft = repair.persist_draft(db, trip, message, plan, audit, req, days, copy.deepcopy(audit.input["configuration"]), before,
                dict(base_plan_hash=base_hash, action_type=scope, strategy=scope, affected_dates=sorted(affected),
                     protected_ids=sorted(protected), comfort=preference_evidence), evidence)
            repair.log.info("planning_action request=%s scope=%s stage=stored draft=%s", message.id, scope, draft.id)
            reply = ("요청한 범위의 변경안을 검증했어요. 변경 내용을 확인한 뒤 그룹 투표로 승인해 주세요." if ko else
                     "I validated a draft for the requested scope. Review the changes and approve it through the group vote.")
            if preference:
                reply = (("오늘 하루 전체의 야외 활동 예상 시간을 줄인 변경안을 검증했어요. 실내 여부는 저장된 장소 정보에 기반해요. 그룹 투표로 승인해 주세요." if ko else
                          "I validated a draft reducing outdoor visit time across today's whole plan, based on saved indoor metadata. Review it for the group vote.") if preference == "indoor" else
                         ("오늘 하루 전체의 걷기 부담을 줄인 변경안을 검증했어요. 장소 내 걷기는 추정치예요. 그룹 투표로 승인해 주세요." if ko else
                          "I validated a draft reducing walking across today's whole plan. Walking within places is estimated. Review it for the group vote."))
            return dict(state="SUCCEEDED", reply=reply, proposal=dict(date=min(affected), remove_item_ids=removed, add=added),
                action_type=scope, affected_dates=sorted(affected), affected_activities=changes, draft_id=draft.id,
                validation_status="PASSED", repair_strategy=scope, tool_calls=[], language="ko" if ko else "en")
    except TimeoutError:
        return failure("planning_action_timeout", ko, details=repair_errors)
    except HTTPException as exc:
        return failure(str(exc.detail), ko, details=repair_errors)
    except (ValueError, KeyError, TypeError) as exc:
        repair.log.warning("planning_action_invalid_response scope=%s type=%s", scope, type(exc).__name__)
        return failure("final_validation_failed", ko, details=repair_errors)
