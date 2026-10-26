"""Reference version of the group recommender from TS SDD 1.1 (chapters 8–9), used by the mock service.

The recommender teammate owns the real service. This file exists so the backend and the web app can be built
and tested against the same numbers the design document specifies:

  w(u,k) = r(u,k) / sum_k r(u,k)              raw 1–5 scores -> weights that sum to 1
  P(u,i) = sum_k w(u,k) * x(i,k)              x = the place's 10 binary features -> score 0..1
  Borda(u,i) = L_u - rank_u(i)                 inside u's top-L list (rank starts at 1), 0 outside
  p(u,i) = Borda(u,i) / sum_j Borda(u,j)       "how relevant is i to u"
  gain(i) = sum_u p(u,i) * q(u),  q(u) starts at 1 and becomes q(u) * (1 - p(u,i)) after i is picked
  group_utility(i) = round(1000 * (K' - group_rank(i) + 1) / K')

Tie rules (SDD 9.2): score ties -> poi_id ascending; gain ties -> mean_score descending, then poi_id ascending.
"""
FEATURES = ["food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival"]


def weights(ratings: dict) -> dict:
    total = sum(ratings[f] for f in FEATURES)
    return {f: ratings[f] / total for f in FEATURES}


def score(w: dict, x: dict) -> float:
    return sum(w[f] * (x.get(f) or 0) for f in FEATURES)


def rank_for(scores: dict[str, float]) -> list[str]:
    """Personal ranking: score descending, poi_id ascending on ties."""
    return sorted(scores, key=lambda pid: (-scores[pid], pid))


def relevance(top: list[str]) -> dict[str, float]:
    """p(u,i) from one member's top list using Borda = L - rank."""
    n = len(top)
    borda = {pid: n - r for r, pid in enumerate(top, start=1)}
    total = sum(borda.values())
    if total == 0:  # only one candidate: its relevance is 1 (SDD 9.2 boundary rule)
        return {pid: 1.0 for pid in top}
    return {pid: b / total for pid, b in borda.items()}


def gfar(tops: dict[str, list[str]], k: int, mean: dict[str, float]) -> list[dict]:
    """Greedy GFAR. tops = each member's top-L list; mean = mean_score per candidate (for ties)."""
    pool = sorted({pid for top in tops.values() for pid in top})
    p = {u: relevance(top) for u, top in tops.items()}
    q = {u: 1.0 for u in tops}
    picked: list[dict] = []
    chosen: set[str] = set()
    while len(picked) < min(k, len(pool)):
        gains = {pid: sum(p[u].get(pid, 0.0) * q[u] for u in tops) for pid in pool if pid not in chosen}
        pid = min(gains, key=lambda i: (-round(gains[i], 12), -mean.get(i, 0.0), i))
        gain = gains[pid]
        share = {u: p[u].get(pid, 0.0) * q[u] for u in tops}
        top_member = min(share, key=lambda u: (-share[u], u)) if gain > 0 else None
        picked.append({"poi_id": pid, "selection_gain": round(gain, 6), "picked_for": top_member})
        chosen.add(pid)
        for u in tops:
            q[u] *= 1 - p[u].get(pid, 0.0)
    return picked


def utility(rank: int, k_actual: int) -> int:
    return round(1000 * (k_actual - rank + 1) / k_actual)


def recommend(members: list[dict], places: dict[str, dict], top_l: int = 100, group_k: int = 30,
              method: str = "gfar", awm_threshold: float | None = None) -> dict:
    """members: [{"user_id", "ratings", "exclusions": {"tags", "poi_ids"}}]; places: {poi_id: {"features", "tags"}}."""
    removed, eligible = [], []
    for pid in sorted(places):  # E1 pre-filter: only explicit, confirmed exclusions
        poi = places[pid]
        if not any((poi["features"] or {}).get(f) for f in FEATURES):
            removed.append({"poi_id": pid, "stage": "E1", "rule": "no_features", "detail": "all 10 features are 0"})
            continue
        hit = next(((m["user_id"], t) for m in members for t in (m.get("exclusions") or {}).get("tags", [])
                    if t in (poi.get("tags") or [])), None)
        hit = hit or next(((m["user_id"], "place") for m in members
                           if pid in (m.get("exclusions") or {}).get("poi_ids", [])), None)
        if hit:
            removed.append({"poi_id": pid, "stage": "E1", "rule": "member_exclusion", "detail": f"{hit[1]} ({hit[0]})"})
            continue
        eligible.append(pid)

    w = {m["user_id"]: weights(m["ratings"]) for m in members}
    scores = {u: {pid: score(w[u], places[pid]["features"]) for pid in eligible} for u in w}
    ranking = {u: rank_for(s) for u, s in scores.items()}
    tops = {u: r[:top_l] for u, r in ranking.items()}
    pool = sorted({pid for t in tops.values() for pid in t})
    mean = {pid: sum(scores[u][pid] for u in w) / len(w) for pid in pool}

    if method == "awm" and awm_threshold is not None:  # comparison only: drop places someone scores too low
        low = [pid for pid in pool if min(scores[u][pid] for u in w) < awm_threshold]
        for pid in low:
            worst = min(w, key=lambda u: scores[u][pid])
            removed.append({"poi_id": pid, "stage": "AWM", "rule": "misery",
                            "detail": f"{worst} {scores[worst][pid]:.3f} < {awm_threshold}"})
        pool = [pid for pid in pool if pid not in set(low)]
    if method in ("average", "awm"):
        order = sorted(pool, key=lambda pid: (-mean[pid], pid))[:group_k]
        picked = [{"poi_id": pid, "selection_gain": None, "picked_for": None} for pid in order]
    elif len(members) == 1:  # single member: their own ranking is the group ranking
        u = members[0]["user_id"]
        picked = [{"poi_id": pid, "selection_gain": None, "picked_for": u} for pid in tops[u][:group_k]]
    else:
        picked = gfar(tops, group_k, mean)

    k_actual = len(picked)
    group = []
    for rank, it in enumerate(picked, start=1):
        pid = it["poi_id"]
        group.append({"poi_id": pid, "group_rank": rank, "mean_score": round(mean[pid], 6),
                      "selection_gain": it["selection_gain"], "group_utility": utility(rank, k_actual),
                      "member_scores": {u: round(scores[u][pid], 6) for u in w},
                      "picked_for": it["picked_for"]})
    rel = {u: relevance(t) for u, t in tops.items()}
    per_member = {u: round(sum(rel[u].get(g["poi_id"], 0.0) for g in group), 4) for u in w}
    return {
        "group": group, "removed": removed,
        "individual": {u: [{"poi_id": pid, "score": round(scores[u][pid], 6), "rank": r}
                           for r, pid in enumerate(tops[u], start=1)] for u in w},
        "metrics": {"eligible": len(eligible), "candidate_pool": len(pool), "returned": k_actual,
                    "per_member_relevance": per_member,
                    "min_member_relevance": min(per_member.values()) if per_member else 0},
    }
