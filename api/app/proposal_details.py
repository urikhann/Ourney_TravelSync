"""Public proposal facts from pinned POIs and private validated snapshots only."""
from urllib.parse import parse_qs, quote, urlsplit

from pydantic import ValidationError
from sqlalchemy import select, tuple_

from ourney_travelsync.optimizer.contracts import PlanRequest
from .metadata import clean_meta
from .models import Poi
from .replacements import plans


def public_url(value):
    if not isinstance(value, str):
        return None
    for part in (value or "").split("|"):
        try:
            url = urlsplit(part.strip())
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                    or url.hostname == "apis.data.go.kr" or any(k.lower() in {
                        "servicekey", "apikey", "api_key", "token", "access_token", "authorization"
                    } for k in parse_qs(url.query))):
                continue
            return url.geturl()
        except ValueError:
            continue
    return None


def fact_view(fact):
    return dict(value=fact.value, status=fact.status, source=public_url(fact.source),
                checked_at=fact.verified_at.isoformat() if fact.verified_at else None)


def visit_map(days):
    return {(d.date.isoformat(), v.poi_id): v for d in days for v in d.items}


def measures(day):
    return dict(travel_minutes=sum(v.travel_minutes for v in day.items) + day.return_travel_minutes,
                itinerary_minutes=day.end_arrival_min-day.start_min,
                visit_minutes=sum(v.stay_minutes for v in day.items),
                waiting_minutes=sum(max(0, v.start_min-v.arrival_min) for v in day.items[1:]),
                stops=len(day.items))


def impact(before, after, dates):
    old, new = {d.date.isoformat(): d for d in before}, {d.date.isoformat(): d for d in after}
    rows = []
    for day in sorted(dates):
        if day not in old or day not in new:
            return None
        a, b = measures(old[day]), measures(new[day])
        rows.append(dict(date=day, before=a, after=b, change={k: b[k]-a[k] for k in a}))
    if not rows:
        return None
    total = {name: {k: sum(row[name][k] for row in rows) for k in rows[0][name]}
             for name in ("before", "after", "change")}
    return dict(days=rows, **total)


def stop_view(key, visit, req, audit, poi):
    day, pid = key
    candidate = next((p for p in req.candidates if p.poi_id == pid), None)
    operation = candidate.operations.get(next(d.date for d in req.days if d.date.isoformat() == day)) if candidate else None
    meta = clean_meta(poi.enriched_metadata) if poi else {}
    place = dict(id=pid, name=poi.name if poi else pid, category=poi.category or None if poi else None,
                 description=meta.get("description_summary") if isinstance(meta.get("description_summary"), str) else None,
                 description_source_url=public_url(meta.get("description_source_url")),
                 image_url=public_url(poi.image_url) if poi else None,
                 address=(poi.road_address or poi.address or None) if poi else None,
                 latitude=poi.lat if poi else None, longitude=poi.lng if poi else None,
                 features=[k for k, v in (poi.features or {}).items() if v] if poi else [],
                 map_url=public_url(poi.place_url) if poi else None, metadata_available=poi is not None)
    if poi and not place["map_url"]:
        place["map_url"] = f"https://map.kakao.com/link/map/{quote(poi.name, safe='')},{poi.lat},{poi.lng}"
    recommendation = audit.input.get("candidate_retrieval") or {}
    if not isinstance(recommendation, dict):
        recommendation = {}
    row = next((r for r in recommendation.get("group") or [] if isinstance(r, dict) and r.get("poi_id") == pid), {})
    reasons = []
    for reason in row.get("reasons") or []:
        if not isinstance(reason, dict):
            continue
        if reason.get("code") == "fair_pick":
            reasons.append(dict(code="fair_pick", text="Helps balance the group's preferences."))
        elif isinstance(reason.get("text"), str):
            reasons.append(dict(code=reason.get("code", "recommendation"), text=reason["text"]))
    scale = recommendation.get("score_scale")
    score = (dict(value=candidate.mean_score,
                  semantics="normalized_preference" if scale in {"1-5", "0-1"} else "relative_relevance" if scale == "relative_relevance_0_1" else "unspecified",
                  rank=candidate.group_rank) if candidate else None)
    cost = fact_view(candidate.cost_per_person) if candidate else dict(value=None, status="UNKNOWN", source=None, checked_at=None)
    if cost["value"] is None and poi and poi.avg_cost_krw is not None:
        cost = dict(value=poi.avg_cost_krw, status=(poi.field_status or {}).get("cost", "UNKNOWN"), source=None, checked_at=None)
    openings = operation.openings if operation else None
    closed = operation.closed if operation else None
    hours_known = bool(openings and closed and openings.known and closed.known)
    windows = [w.model_dump(mode="json") for w in openings.value] if openings and openings.value is not None else []
    fits = (not closed.value and any(w["start_min"] <= visit.start_min and visit.departure_min <= w["end_min"] for w in windows)) if hours_known else None
    return dict(poi_id=pid, date=day, start_min=visit.start_min, end_min=visit.departure_min,
                duration_minutes=visit.stay_minutes,
                duration_status=candidate.duration_min.status if candidate else "UNKNOWN", place=place,
                hours=dict(status="KNOWN" if hours_known else "ESTIMATED" if openings and openings.status == "ESTIMATED" else "UNKNOWN",
                           windows=windows, fits_visit=fits,
                           closed=closed.value if closed and closed.known else None,
                           checked_at=openings.verified_at.isoformat() if openings and openings.verified_at else None),
                cost_per_person=cost, recommendation_score=score, recommendation_reasons=reasons)


def detail_views(db, trip_id, messages, audits):
    """Batch-load POIs, omit unbound/legacy drafts, never serialize member snapshots."""
    bound, wanted = {}, set()
    for message in messages:
        agent = message.agent or {}
        base, draft = audits.get(agent.get("plan_run_id")), audits.get(agent.get("draft_id"))
        replacement = draft.input.get("replacement") if draft else None
        if (not base or not draft or base.trip_id != trip_id or draft.trip_id != trip_id
                or draft.status not in {"VALIDATED", "PASSED"} or draft.response.get("validation_status") != "PASSED"
                or not isinstance(replacement, dict) or replacement.get("base_run_id") != base.id):
            continue
        try:
            old_req, new_req = PlanRequest.model_validate(base.optimizer_request), PlanRequest.model_validate(draft.optimizer_request)
            old_days, new_days = plans(base.response["days"]), plans(draft.response["days"])
            if old_req.dataset_version != new_req.dataset_version:
                continue
            old, new = visit_map(old_days), visit_map(new_days)
            removed, added = set(old)-set(new), set(new)-set(old)
            declared = agent.get("proposal") or {}
            expected_removed = {f"{d}@{old[d, pid].start_min//60:02d}:{old[d, pid].start_min%60:02d}" for d, pid in removed}
            expected_added = {(pid, d, new[d, pid].start_min) for d, pid in added}
            declared_added = {(p["poi_id"], p.get("date", declared["date"]), int(p["start"][:2])*60+int(p["start"][3:]))
                              for p in declared.get("add", [])}
            if expected_removed != set(declared.get("remove_item_ids", [])) or expected_added != declared_added:
                continue
            bound[message.id] = base, draft, old_req, new_req, old_days, new_days, old, new, removed, added
            changed = {k for k in set(old) & set(new) if (old[k].start_min, old[k].departure_min) != (new[k].start_min, new[k].departure_min)}
            wanted.update((pid, old_req.dataset_version) for _, pid in removed | added | changed)
        except (ValidationError, ValueError, TypeError, KeyError, StopIteration):
            continue
    pois = {(p.id, p.dataset_version): p for p in db.scalars(select(Poi).where(
        tuple_(Poi.id, Poi.dataset_version).in_(sorted(wanted))))} if wanted else {}
    views = {}
    for mid, (base, draft, old_req, new_req, old_days, new_days, old, new, removed, added) in bound.items():
        sort = lambda keys, visits: sorted(keys, key=lambda k: (k[0], visits[k].start_min, k[1]))
        adjusted = [dict(poi_id=pid, name=pois[pid, new_req.dataset_version].name if (pid, new_req.dataset_version) in pois else pid,
                         date=d, before_start_min=old[d, pid].start_min, after_start_min=new[d, pid].start_min,
                         before_end_min=old[d, pid].departure_min, after_end_min=new[d, pid].departure_min,
                         stop=stop_view((d, pid), new[d, pid], new_req, draft, pois.get((pid, new_req.dataset_version))))
                    for d, pid in sort(set(old) & set(new), new)
                    if (old[d, pid].start_min, old[d, pid].departure_min) != (new[d, pid].start_min, new[d, pid].departure_min)]
        dates = set(draft.input["replacement"].get("affected_dates", [])) | {d for d, _ in removed | added} | {a["date"] for a in adjusted}
        views[mid] = dict(schema_version="proposal-details-1.0", dataset_version=new_req.dataset_version,
            snapshot_created_at=draft.input["replacement"].get("created_at"),
            original=[stop_view(k, old[k], old_req, base, pois.get((k[1], old_req.dataset_version))) for k in sort(removed, old)],
            proposed=[stop_view(k, new[k], new_req, draft, pois.get((k[1], new_req.dataset_version))) for k in sort(added, new)],
            adjusted=adjusted, impact=impact(old_days, new_days, dates))
    return views
