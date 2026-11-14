"""Deterministic day-specific retrieval; geographic screening is not route evidence."""
from collections import Counter
from datetime import date
import math
import numpy as np
from ..common import GroupInput
from ..features import FEATURE_NAMES
from ..planning_categories import visit_role

POLICY = "catalogue-day-clusters-v1"


def km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


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
    limit = min(40, 60 // len(days))
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
        chosen = list(locked)
        # Select a cluster anchor near the route corridor with strong group fit.
        base = lambda j: float(quality[j]) - .35 * detour[j] - (.12 if j in previous else 0)
        anchor = max(pool, key=lambda j: (base(j), -j)) if pool else None
        def value(j):
            cluster_penalty = min(km(coordinates[j], coordinates[anchor]) / 15, 2) if anchor is not None else 0
            return base(j) - .12 * cluster_penalty
        if composition is not None:
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
        by_day[d["date"]] = [data.poi_ids[j] for j in chosen]
        diagnostics[d["date"]] = {"screened_pool": len(pool), "selected": len(chosen),
            "requested_activities": [FEATURE_NAMES[f] for f in preferred],
            "activity_counts": {FEATURE_NAMES[f]: counts[f] for f in preferred},
            "cluster_anchor": data.poi_ids[anchor] if anchor is not None else None,
            "visit_role_counts": dict(Counter(roles[j][0] for j in chosen)),
            "activity_category_counts": dict(Counter(roles[j][1] for j in chosen if roles[j][0] == "activity"))}
        selected.update(chosen)
        previous.update(chosen)
    indices = sorted(selected)
    subset = GroupInput(data.user_ids, tuple(data.poi_ids[j] for j in indices), data.user_features,
        data.poi_features[indices], data.individual_scores[:, indices],
        data.scoring.subset(indices) if data.scoring else None, data.score_scale)
    return subset, {"policy": POLICY, "eligible_catalogue_count": len(data.poi_ids),
        "selected_count": len(indices), "per_day_limit": limit, "day_candidate_ids": by_day,
        "days": diagnostics, "geography_is_approximate": True}
