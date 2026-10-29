"""Evaluate only supplied human annotations; never generate or infer relevance labels."""
from __future__ import annotations

import argparse
from collections import defaultdict
import itertools
import json
import math
from pathlib import Path

from prepare import DEFAULT_OUT, digest, read_csv, write_csv, write_json
from run import verify_frozen


def ndcg(rows, labels, k, tie_expected=True):
    """Gain=2**grade-1, discount=1/log2(position+1), IDCG on full judged group."""
    ranked = sorted(rows, key=lambda r: int(r["rank"]))
    gains = {pid: 2 ** grade - 1 for pid, grade in labels.items()}
    ideal = sum(g / math.log2(i + 2) for i, g in enumerate(sorted(gains.values(), reverse=True)[:k]))
    if ideal == 0:
        return None
    band = defaultdict(list)
    for row in rows:
        band[float(row["score"])].append(gains[row["place_id"]])
    dcg = sum((sum(band[float(row["score"])]) / len(band[float(row["score"])]) if tie_expected else gains[row["place_id"]]) / math.log2(i + 2) for i, row in enumerate(ranked[:k]))
    return dcg / ideal


def pair_agreement(rows, labels):
    """Human strict preferences only. Matching=1, model tie=0.5, reversal=0."""
    scores = {r["place_id"]: float(r["score"]) for r in rows}
    values = []
    for p, q in itertools.combinations(sorted(labels), 2):
        human = labels[p] - labels[q]
        if human == 0:
            continue
        model = scores[p] - scores[q]
        values.append(0.5 if model == 0 else float(model * human > 0))
    return (sum(values) / len(values) if values else None), len(values)


def audit_ratings(path, template, rater_id):
    ratings = read_csv(path)
    expected = {r["annotation_id"]: r for r in read_csv(template)}
    assert len(ratings) == len(expected), "Incomplete or duplicate annotation rows"
    assert len({r["annotation_id"] for r in ratings}) == len(ratings)
    assert {r["annotation_id"] for r in ratings} == expected.keys(), "Wrong candidate identity set"
    parsed = {}
    for row in ratings:
        original = expected[row["annotation_id"]]
        assert row["rater_id"] == rater_id
        for field in original.keys() - {"presentation_order", "relevance_0_3", "insufficient_evidence_YES_NO", "notes"}:
            assert row[field] == original[field], "Annotation prompt/evidence changed: " + field
        flag = row["insufficient_evidence_YES_NO"].strip().upper()
        grade = row["relevance_0_3"].strip()
        assert flag in {"YES", "NO"}, "Every row needs explicit evidence status"
        if flag == "YES":
            assert grade == "", "Insufficient evidence must not become grade zero"
            value = None
        else:
            assert grade in {"0", "1", "2", "3"}, "Expected an integer human grade 0..3"
            value = int(grade)
        parsed[(row["scenario_id"], row["place_id"])] = value
    return parsed


def evaluate(out, results, r1, r2, evidence_path, destination):
    verify_frozen(out)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence.get("origin") == "independent_human"
    assert evidence.get("independent") is True and evidence.get("method_blinded") is True
    assert evidence.get("rater_ids") == ["R1", "R2"]
    assert evidence.get("collection_date") and evidence.get("collection_description")
    assert r1.resolve() != r2.resolve(), "Two independent source files required"
    a = audit_ratings(r1, results / "annotation_R1.csv", "R1")
    b = audit_ratings(r2, results / "annotation_R2.csv", "R2")
    assert a.keys() == b.keys()
    consensus = {key: (a[key] + b[key]) / 2 if a[key] is not None and b[key] is not None else None for key in a}
    grouped = defaultdict(list)
    for row in read_csv(results / "scores_ranks.csv"):
        grouped[(row["scenario_id"], row["method"])].append(row)
    metrics = []
    for label_set, labels in (("R1", a), ("R2", b), ("mean_R1_R2", consensus)):
        for (sid, method), rows in sorted(grouped.items()):
            available = {r["place_id"]: labels[(sid, r["place_id"])] for r in rows if labels[(sid, r["place_id"])] is not None}
            missing = [r["place_id"] for r in rows if r["place_id"] not in available]
            k = min(3, len(rows))
            full = not missing
            pair, pair_count = pair_agreement(rows, available)
            tie_ndcg = ndcg(rows, available, k) if full else None
            deterministic_ndcg = ndcg(rows, available, k, False) if full else None
            metrics.append({"label_set": label_set, "scenario_id": sid, "method": method, "candidate_count": len(rows), "k": k,
                            "judged_places": len(available), "insufficient_evidence_place_ids": "|".join(missing),
                            "ndcg_tie_expected": tie_ndcg if tie_ndcg is not None else "N/A",
                            "ndcg_id_fallback": deterministic_ndcg if deterministic_ndcg is not None else "N/A",
                            "ndcg_exclusion": "insufficient evidence; keep full identity set" if missing else "zero ideal DCG" if tie_ndcg is None else "",
                            "pairwise_preference_agreement": pair if pair is not None else "N/A", "human_strict_pair_count": pair_count})
    destination.mkdir(exist_ok=False)
    write_csv(destination / "relevance_metrics.csv", metrics)
    jointly_judged = [(a[k], b[k]) for k in a if a[k] is not None and b[k] is not None]
    write_json(destination / "evaluation_manifest.json", {"evidence": evidence, "evidence_note": "Collector declaration audited for required fields; code cannot independently prove human origin, independence or blinding.", "source_sha256": {"R1": digest(r1), "R2": digest(r2), "collection_evidence": digest(evidence_path), "rankings": digest(results / "scores_ranks.csv")},
               "evaluator_code_sha256": digest(__file__), "jointly_judged_pairs": len(jointly_judged),
               "exact_rater_grade_agreement": sum(x == y for x, y in jointly_judged) / len(jointly_judged) if jointly_judged else None,
               "mean_absolute_grade_disagreement": sum(abs(x - y) for x, y in jointly_judged) / len(jointly_judged) if jointly_judged else None,
               "metric_definition": "NDCG uses gain 2**grade-1; discount 1/log2(rank+1); k=min(3, full group size). Primary NDCG averages gains across each exact score band, including bands crossing k. ID fallback NDCG is diagnostic. Any insufficient evidence excludes the full scenario from NDCG for that label set. Zero IDCG is N/A. Pairwise metric uses judged pairs with unequal human grades: correct strict model order=1, exact model tie=0.5, reversal=0; no pairs=N/A. Consensus averages two grades only if both are sufficient. No global average hides exclusions; scenario rows remain available."})
    print("Wrote audited human-label metrics to", destination)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--results-name", default="results")
    p.add_argument("--rater1", required=True, type=Path)
    p.add_argument("--rater2", required=True, type=Path)
    p.add_argument("--human-evidence", required=True, type=Path)
    p.add_argument("--destination", required=True, type=Path)
    args = p.parse_args()
    evaluate(args.out, args.out / args.results_name, args.rater1, args.rater2, args.human_evidence, args.destination)
