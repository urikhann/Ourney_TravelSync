"""Deterministic day-specific retrieval; geographic screening is not route evidence."""
from collections import Counter
from datetime import date
from types import SimpleNamespace
import os
import math
import numpy as np
from ..common import GroupInput
from ..features import FEATURE_NAMES
from ..geography import haversine_km as km
from ..planning_categories import EVENING_START_MIN, visit_role

POLICY = "catalogue-day-clusters-v1"


def select_days(data, records, days, locks=(), composition=None):
    """Retain at most 60 distinct POIs, with explicit per-day membership.

    Full-catalogue preference scores must already exist before calling this.
    An optimistic 130 km/h straight-line screen only removes impossible days;
    verified directed routes and the optimizer remain responsible for scheduling.
    No hours, closures, or prices enter retrieval. Unknown durations are retained
    for the operating-data adapter to resolve, never invented.
    """
    if not 1 <= len(days) <= 3:
        raise ValueError("invalid planning days")
    dates = [date.fromisoformat(d["date"]).isoformat() for d in days]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate planning date")
    for d in days:
        if not 0 <= d["start_min"] < d["end_min"] <= 1440:
            raise ValueError("invalid planning times")
        for key in ("start_point", "end_point"):
            p = d[key]
            if not (math.isfinite(p["latitude"]) and abs(p["latitude"]) <= 90
                    and math.isfinite(p["longitude"]) and abs(p["longitude"]) <= 180):
                raise ValueError("invalid planning coordinates")
    ids = {pid: j for j, pid in enumerate(data.poi_ids)}
    if any(l["poi_id"] not in ids or l["date"] not in dates for l in locks):
        raise ValueError("locked place excluded or unknown")
    if len({l["poi_id"] for l in locks}) != len(locks):
        raise ValueError("duplicate lock")
    normalized = (data.individual_scores - 1) / 4 if data.score_scale == "1-5" else data.individual_scores
    quality = .6 * normalized.mean(axis=0) + .4 * normalized.min(axis=0)
    preferred = [i for i in range(len(FEATURE_NAMES)) if np.any(data.user_features[:, i] >= 4)]
    if not preferred:
        preferred = list(range(len(FEATURE_NAMES)))
    # A full directed matrix grows quadratically; retain role alternatives in a
    # bounded first pass rather than spending routing quota on forty stops/day.
    limit = min(max(12, min(40, int(os.getenv("PLANNING_DAY_CANDIDATE_LIMIT", "16")))), 60 // len(days))
    evening_enabled = bool(composition and composition.get("evening_enabled"))
    optional_limit = min(limit + 3 + (3 if evening_enabled else 0), 60 // len(days))
    previous, selected, by_day, diagnostics = set(), set(), {}, {}
    roles = [visit_role(records[pid]) for pid in data.poi_ids]
    coordinates = [(records[pid]["latitude"], records[pid]["longitude"]) for pid in data.poi_ids]
    for d in sorted(days, key=lambda d: d["date"]):
        start = tuple(d["start_point"][k] for k in ("latitude", "longitude"))
        end = tuple(d["end_point"][k] for k in ("latitude", "longitude"))
        span = d["end_min"] - d["start_min"]
        direct = km(start, end)
        pool, detour = [], {}
        for j, pid in enumerate(data.poi_ids):
            r = records[pid]
            # Nightlife requires an optional preference or an explicit place lock.
            if (roles[j][0] == "evening" and not (evening_enabled and d["end_min"] > EVENING_START_MIN)
                    and not any(l["poi_id"] == pid and l["date"] == d["date"] for l in locks)):
                continue
            duration = r.get("duration_min")
            status = (r.get("field_status") or {}).get("duration")
            # Unverified dwell values are not used as exclusion evidence.
            dwell = duration if status in ("KNOWN", "ESTIMATED") and type(duration) is int and duration > 0 else 0
            distance = km(start, coordinates[j]) + km(coordinates[j], end)
            if dwell + distance / 130 * 60 <= span:
                pool.append(j)
                detour[j] = max(0, distance-direct) / max(5, span / 60 * 30)
        locked = [ids[l["poi_id"]] for l in locks if l["date"] == d["date"]]
        if len(locked) > limit or any(j not in pool for j in locked):
            raise ValueError("locked visit does not fit day retrieval")
        # Keep automatic evening choices outside the ordinary shortlist so they
        # cannot displace alternatives needed for mandatory meals/activities.
        optional_evening = [j for j in pool if roles[j][0] == "evening" and j not in locked]
        pool = [j for j in pool if j not in optional_evening]
        chosen = list(locked)
        # Select a cluster anchor near the route corridor with strong group fit.
        base = lambda j: float(quality[j]) - .35 * detour[j] - (.12 if j in previous else 0)
        anchor = max(pool, key=lambda j: (base(j), -j)) if pool else None
        def value(j):
            cluster_penalty = min(km(coordinates[j], coordinates[anchor]) / 15, 2) if anchor is not None else 0
            return base(j) - .12 * cluster_penalty
        optional_breakfast = []
        if composition is not None:
            breakfast_enabled = composition.get("breakfast_enabled", False) or any(
                w["name"] == "breakfast" for w in composition.get("meal_windows", ()))
            if breakfast_enabled:
                from ..breakfast_metadata import breakfast_metadata, inferred_breakfast_suitability
                from ..optimizer.contracts import DailyComposition
                from ..optimizer.meals import breakfast_service_windows, effective_meals
                day = SimpleNamespace(date=date.fromisoformat(d["date"]), start_min=d["start_min"], end_min=d["end_min"])
                meal = next(m for m in effective_meals(day, DailyComposition.model_validate(composition))
                            if m.name == "breakfast")
                options = []
                for j in pool:
                    if roles[j][0] not in ("meal", "cafe"):
                        continue
                    record = records[data.poi_ids[j]]
                    offering, hours = breakfast_metadata(record)
                    windows = breakfast_service_windows(offering, hours.get(day.date),
                                                        inferred_breakfast_suitability(record), meal.require_verified)
                    duration = record.get("duration_min")
                    dwell = duration if type(duration) is int and duration > 0 else 1
                    start = max(meal.start_min, day.start_min)
                    end = min(meal.end_min, day.end_min)
                    if meal.fixed_start_min is not None:
                        start = meal.fixed_start_min
                        end = min(end, start + dwell)
                    if end - start >= dwell and (windows is None or any(
                            min(w.end_min, end) - max(w.start_min, start) >= dwell for w in windows)):
                        options.append(j)
                options.sort(key=lambda j: (value(j), -j), reverse=True)
                if meal.require_verified:
                    for j in options[:3]:
                        if j not in chosen and len(chosen) < limit:
                            chosen.append(j)
                else:
                    optional_breakfast = options
            # Reserve alternatives before preferences can fill the pool with cafes.
            # These are retrieval opportunities; only CP-SAT enforces visit counts.
            targets = (("meal", max(6, composition["max_meals"] * 2)),
                       ("activity", max(6, composition["min_activities"] * 2)),
                       ("cafe", composition["max_cafes"] * 2))
            for role, target in targets:
                if role == "activity":
                    for category in sorted({roles[j][1] for j in pool if roles[j][0] == role}):
                        options = [j for j in pool if j not in chosen and roles[j] == (role, category)]
                        if options and len(chosen) < limit:
                            chosen.append(max(options, key=lambda j: (value(j), -j)))
                while sum(roles[j][0] == role for j in chosen) < target and len(chosen) < limit:
                    options = [j for j in pool if j not in chosen and roles[j][0] == role]
                    if not options:
                        break
                    chosen.append(max(options, key=lambda j: (value(j), -j)))
        # Preserve opportunities for each member's requested activity types.
        for feature in preferred:
            for u in range(len(data.user_ids)):
                if data.user_features[u, feature] < 4:
                    continue
                options = [j for j in pool if j not in chosen and data.poi_features[j, feature]]
                if options and len(chosen) < limit:
                    chosen.append(max(options, key=lambda j: (.5 * normalized[u, j] + value(j), -j)))
        counts = Counter()
        for j in chosen:
            for f in preferred:
                if data.poi_features[j, f]:
                    counts[f] += 1
        while len(chosen) < min(limit, len(pool)):
            options = [j for j in pool if j not in chosen]
            def benefit(j):
                novelty = max((.10 / (1+counts[f]) for f in preferred if data.poi_features[j, f]), default=0)
                return value(j) + novelty, -j
            j = max(options, key=benefit)
            chosen.append(j)
            for f in preferred:
                if data.poi_features[j, f]:
                    counts[f] += 1
        # Keep the ordinary shortlist intact so optional breakfast cannot remove
        # an alternative needed for a hard constraint. Stay within 60 overall.
        previous.update(chosen)
        for j in optional_breakfast:
            if len(chosen) >= min(limit + 3, 60 // len(days)):
                break
            if j not in chosen:
                chosen.append(j)
                for f in preferred:
                    if data.poi_features[j, f]:
                        counts[f] += 1
        for j in sorted(optional_evening, key=lambda j: (value(j), -j), reverse=True)[:3]:
            if len(chosen) >= optional_limit:
                break
            chosen.append(j)
            for f in preferred:
                if data.poi_features[j, f]:
                    counts[f] += 1
        by_day[d["date"]] = [data.poi_ids[j] for j in chosen]
        diagnostics[d["date"]] = {"screened_pool": len(pool), "selected": len(chosen),
            "requested_activities": [FEATURE_NAMES[f] for f in preferred],
            "activity_counts": {FEATURE_NAMES[f]: counts[f] for f in preferred},
            "cluster_anchor": data.poi_ids[anchor] if anchor is not None else None,
            "visit_role_counts": dict(Counter(roles[j][0] for j in chosen)),
            "activity_category_counts": dict(Counter(roles[j][1] for j in chosen if roles[j][0] == "activity"))}
        selected.update(chosen)
    indices = sorted(selected)
    subset = GroupInput(data.user_ids, tuple(data.poi_ids[j] for j in indices), data.user_features,
        data.poi_features[indices], data.individual_scores[:, indices],
        data.scoring.subset(indices) if data.scoring else None, data.score_scale)
    return subset, {"policy": POLICY, "eligible_catalogue_count": len(data.poi_ids),
        "selected_count": len(indices), "per_day_limit": max(limit, *(len(ids) for ids in by_day.values())), "day_candidate_ids": by_day,
        "days": diagnostics, "geography_is_approximate": True}
