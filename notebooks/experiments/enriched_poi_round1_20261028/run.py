"""Run frozen within-feature-group comparisons. Inputs are read-only; outputs must be new."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import itertools
import json
import math
import os
from pathlib import Path
import random
import sys

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import numpy as np
from prepare import (BASE_FIELDS, CANONICAL, DEFAULT_OUT, EXPANDED_FIELDS, FEATURES,
                     HERE, ROOT, INPUT_NAMES, MODEL, clean, digest, protected_snapshot,
                     read_csv, signature, versions, write_csv, write_json)
sys.path.insert(0, str(ROOT / "src"))
from ourney_travelsync.recommenders.individual_scoring import score_individuals


def text_for(row, fields):
    # Strict allowlist is the only route from source rows to ranking text.
    if tuple(fields) not in (BASE_FIELDS, EXPANDED_FIELDS):
        raise ValueError("Unexpected ranking field list")
    return " ".join(part for f in fields if (part := clean(row[f])))


def grams(text):
    return Counter(token[i:i + n] for token in text.split() for n in (2, 3) for i in range(len(token) - n + 1))


class Lexical:
    def __init__(self, documents):
        counts = [grams(t) for t in documents]
        self.vocab = sorted(set().union(*(set(c) for c in counts)))
        self.index = {word: i for i, word in enumerate(self.vocab)}
        self.idf = np.asarray([math.log((1 + len(counts)) / (1 + sum(w in c for c in counts))) + 1 for w in self.vocab])

    def encode(self, texts):
        result = np.zeros((len(texts), len(self.vocab)))
        for i, text in enumerate(texts):
            for word, count in grams(text).items():
                if word in self.index:
                    result[i, self.index[word]] = count * self.idf[self.index[word]]
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        return np.divide(result, norms, out=np.zeros_like(result), where=norms > 0)


def verify_frozen(out):
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["frozen_artifacts"].items():
        assert digest(out / name) == expected, name
    for item in manifest["inputs"]:
        assert digest(out / item["frozen_path"]) == item["sha256"]
        original = Path(item["original_path"])
        if original.exists():
            assert digest(original) == item["sha256"], "Original changed since freeze"
    assert digest(CANONICAL) == manifest["canonical_sha256"]
    assert protected_snapshot() == manifest["protected_tracked_files"], "Protected code/data changed"
    return manifest


def add_ranks(rows):
    ranked = sorted(rows, key=lambda r: (-r["score"], r["place_id"]))
    bands = {score: i + 1 for i, score in enumerate(sorted({r["score"] for r in rows}, reverse=True))}
    counts = Counter(r["score"] for r in rows)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        row["score_band"] = bands[row["score"]]
        row["score_band_size"] = counts[row["score"]]
    return ranked


def diagnostics(scores):
    groups = defaultdict(list)
    for r in scores:
        groups[(r["scenario_id"], r["method"])].append(r)
    result = []
    for (sid, method), rows in sorted(groups.items()):
        counts = Counter(r["score"] for r in rows)
        pairs = len(rows) * (len(rows) - 1) // 2
        result.append({"scenario_id": sid, "method": method, "candidates": len(rows), "k": min(3, len(rows)),
                       "distinct_score_bands": len(counts), "tied_pair_fraction": sum(c * (c - 1) // 2 for c in counts.values()) / pairs if pairs else "N/A",
                       "positive_score_fraction": sum(r["score"] > 0 for r in rows) / len(rows),
                       "score_min": min(counts), "score_max": max(counts), "interpretation": "Ranking diagnostic only; relevance not evaluated"})
    changes = []
    for sid in sorted({r["scenario_id"] for r in scores}):
        methods = sorted(m for s, m in groups if s == sid)
        for left, right in itertools.combinations(methods, 2):
            a = {r["place_id"]: r for r in groups[(sid, left)]}
            b = {r["place_id"]: r for r in groups[(sid, right)]}
            assert a.keys() == b.keys()
            strict_reversals = tie_status_changes = 0
            for p, q in itertools.combinations(sorted(a), 2):
                sa = np.sign(a[p]["score"] - a[q]["score"])
                sb = np.sign(b[p]["score"] - b[q]["score"])
                strict_reversals += int(sa * sb < 0)
                tie_status_changes += int((sa == 0) != (sb == 0))
            changes.append({"scenario_id": sid, "method_left": left, "method_right": right,
                            "changed_fallback_positions": sum(a[p]["rank"] != b[p]["rank"] for p in a),
                            "strict_pair_reversals": strict_reversals, "pair_tie_status_changes": tie_status_changes,
                            "pair_count": len(a) * (len(a) - 1) // 2})
    return result, changes


def semantic(all_docs, queries, target):
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(1)
    torch.manual_seed(20261028)
    torch.use_deterministic_algorithms(True)
    model = SentenceTransformer(MODEL["id"], revision=MODEL["revision"],
                                cache_folder=str(HERE / ".model-cache"), local_files_only=True,
                                device="cpu", trust_remote_code=False)
    model.eval()
    assert model.max_seq_length == 128
    assert model.get_sentence_embedding_dimension() == 384
    texts = all_docs + queries
    enc = model.encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False)
    repeat = model.encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False)
    assert np.array_equal(enc, repeat), "Semantic repeat is not byte deterministic"
    assert np.isfinite(enc).all()
    np.save(target / "embeddings.npy", enc, allow_pickle=False)
    lengths = [len(model.tokenizer(t, truncation=False)["input_ids"]) for t in texts]
    write_json(target / "semantic_metadata.json", {**MODEL, "execution": "CPU float32, one torch thread, eval mode, deterministic algorithms, no prompts", "truncation": "right truncation to 128 total tokens including special tokens; tokenizer default padding", "untruncated_token_lengths_documents_then_scenarios": lengths,
               "truncated_input_count": sum(n > 128 for n in lengths), "repeat_embeddings_byte_identical": True,
               "model_file_hashes": {p.relative_to(HERE / ".model-cache").as_posix(): digest(p) for p in (HERE / ".model-cache").glob("models--*/snapshots/" + MODEL["revision"] + "/**/*") if p.is_file()}})
    return enc[len(all_docs):] @ enc[:len(all_docs)].T


def annotation(out, target, scenarios, rows, seed):
    paired = []
    for s in scenarios:
        for r in rows:
            if signature(r) != s["group_id"]:
                continue
            paired.append({"annotation_id": s["scenario_id"] + "_" + r["place_id"], "scenario_id": s["scenario_id"],
                           "preference_ko": s["preference_ko"], "place_id": r["place_id"], "place_name": r["place_name"],
                           **{f: r[f] for f in EXPANDED_FIELDS + ("usage_conditions", "target_restriction", "detail_limitations", "detail_source_url")},
                           "relevance_0_3": "", "insufficient_evidence_YES_NO": "", "notes": ""})
    for i in (1, 2):
        shuffled = list(paired)
        random.Random(seed + i).shuffle(shuffled)
        write_csv(target / f"annotation_R{i}.csv", [{"presentation_order": n, "rater_id": f"R{i}", **r} for n, r in enumerate(shuffled, 1)])
    (target / "ANNOTATION_RUBRIC.md").write_text("""# 독립 관련성 평가 안내

각 평가자는 자신의 파일만 받고, 다른 평가자나 추천 방법의 결과를 보지 않고 평가하세요. 두 파일은 같은 38쌍을 서로 다른 고정 난수 순서로 제시합니다. 평가 전 연습에는 별도의 장소/문장을 사용하세요. 평가자는 방법별 결과 파일과 보고서를 보지 않아야 합니다.

장소가 주어진 선호에 얼마나 부합하는지를 제공된 설명/분류/메뉴/활동 근거로 평가합니다. 가격, 현재 영업, 접근성, 품질, 조용함, 만족도를 추정하지 마세요. NULL은 미확인 또는 비해당이므로 부정적인 사실로 해석하지 마세요. 이용조건은 프로그램 범위를 이해하기 위한 정보이며 현재 방문 가능성을 평가하는 자료가 아닙니다.

- 3: 명시한 핵심 경험/음식/활동이 자료에 직접 확인됨.
- 2: 핵심 선호의 일부에 직접 근거가 있으나 원하는 세부 경험은 일부만 확인됨.
- 1: 넓은 주제의 관련성만 근거가 있음.
- 0: 자료가 다른 핵심 경험을 명확히 설명하며 명시된 선호와 관련성이 없음.
- 근거가 부족해 관련성 등급을 판단할 수 없으면 insufficient_evidence_YES_NO=YES, relevance_0_3는 비워 둡니다. 정보 없음 자체를 0점으로 매기지 마세요.

판단 가능하면 insufficient_evidence_YES_NO=NO와 0~3 정수를 기록합니다. notes에 필요한 짧은 근거를 남기세요. 서로 상의하거나 자동 모델로 채우지 마세요. 합의/수정이 필요하면 최초 두 독립 평가 파일을 보존하고 후속 파일로 남기세요.

두 평가가 끝난 뒤 별도 evidence JSON에 origin=independent_human, independent=true, method_blinded=true, rater_ids=[R1,R2], collection_date, collection_description을 기록하세요. 이는 수집자의 출처 확인이며 자동으로 독립성을 입증하지는 않습니다. 코드 작성자가 인간 라벨을 대신 채우지 않았습니다.
""", encoding="utf-8")


def run(out, name, with_semantic):
    manifest = verify_frozen(out)
    target = out / name
    target.mkdir(exist_ok=False)
    config = json.loads((out / "config.json").read_text(encoding="utf-8"))
    scenarios = json.loads((out / "scenarios.json").read_text(encoding="utf-8"))
    rows = read_csv(out / "inputs" / INPUT_NAMES[0])
    eligible_ids = {r["place_id"] for r in read_csv(out / "eligible_candidates.csv")}
    eligible = [r for r in rows if r["place_id"] in eligible_ids]
    queries = [clean(s["preference_ko"]) for s in scenarios]
    texts_b = [text_for(r, BASE_FIELDS) for r in rows]
    texts_c = [text_for(r, EXPANDED_FIELDS) for r in rows]
    b = Lexical([text_for(r, BASE_FIELDS) for r in eligible])
    c = Lexical([text_for(r, EXPANDED_FIELDS) for r in eligible])
    qb, qc, db, dc = b.encode(queries), c.encode(queries), b.encode(texts_b), c.encode(texts_c)
    methods = {"A": score_individuals(np.asarray([[s["preference_vector"][f] for f in FEATURES] for s in scenarios]), np.asarray([[float(r[f]) for f in FEATURES] for r in rows])), "B": qb @ db.T, "C": qc @ dc.T}
    assert np.array_equal(methods["B"], b.encode(queries) @ b.encode(texts_b).T)
    assert np.array_equal(methods["C"], c.encode(queries) @ c.encode(texts_c).T)
    if with_semantic:
        methods["D"] = semantic(texts_c, queries, target)
    for m, matrix in methods.items():
        assert np.isfinite(matrix).all(), m
    # All available text inputs, including excluded rows, are inspectable without selection metadata.
    write_csv(target / "model_inputs.csv", [{"place_id": r["place_id"], "primary_eligible": r["place_id"] in eligible_ids, "B_text": tb, "C_D_text": tc} for r, tb, tc in zip(rows, texts_b, texts_c)])
    write_json(target / "lexical_models.json", {"B": {"vocabulary": b.vocab, "idf": b.idf.tolist()}, "C": {"vocabulary": c.vocab, "idf": c.idf.tolist()}})
    primary, sensitivity, contributions = [], [], []
    for si, s in enumerate(scenarios):
        for m, matrix in methods.items():
            all_group = [{"scenario_id": s["scenario_id"], "group_id": s["group_id"], "place_id": r["place_id"], "place_name": r["place_name"], "method": m, "score": float(matrix[si, ri])} for ri, r in enumerate(rows) if signature(r) == s["group_id"]]
            assert len(set(r["score"] for r in all_group if r["method"] == "A")) <= 1
            primary.extend(add_ranks([dict(r) for r in all_group if r["place_id"] in eligible_ids]))
            sensitivity.extend(add_ranks([dict(r) for r in all_group]))
        for m, lex, qv, dv in (("B", b, qb, db), ("C", c, qc, dc)):
            for ri, r in enumerate(rows):
                if r["place_id"] not in eligible_ids or signature(r) != s["group_id"]:
                    continue
                products = qv[si] * dv[ri]
                for wi in np.flatnonzero(products):
                    contributions.append({"scenario_id": s["scenario_id"], "place_id": r["place_id"], "method": m, "ngram": lex.vocab[wi], "cosine_contribution": float(products[wi])})
    diag, differences = diagnostics(primary)
    write_csv(target / "scores_ranks.csv", primary)
    write_csv(target / "diagnostics.csv", diag)
    write_csv(target / "ranking_differences.csv", differences)
    write_csv(target / "sensitivity_all_original_group_candidates.csv", sensitivity)
    write_csv(target / "lexical_contributions.csv", contributions)
    # Drop one description at a time, holding C vocabulary/IDF and candidate identity fixed.
    masked = []
    for si, s in enumerate(scenarios):
        original = {r["place_id"]: r for r in primary if r["scenario_id"] == s["scenario_id"] and r["method"] == "C"}
        for ri, r in enumerate(rows):
            if r["place_id"] not in original:
                continue
            modified = [dict(v) for v in original.values()]
            replacement = float((qc[si] @ c.encode([texts_b[ri]])[0]).item())
            for item in modified:
                if item["place_id"] == r["place_id"]:
                    item["score"] = replacement
            ranked = add_ranks(modified)
            masked.append({"scenario_id": s["scenario_id"], "masked_place_id": r["place_id"], "original_score": original[r["place_id"]]["score"], "masked_score": replacement,
                           "changed_positions": sum(x["rank"] != original[x["place_id"]]["rank"] for x in ranked),
                           "top1_changed": ranked[0]["place_id"] != min(original.values(), key=lambda x: x["rank"])["place_id"]})
    write_csv(target / "description_mask_sensitivity.csv", masked)
    annotation(out, target, scenarios, eligible, config["seed"])
    write_json(target / "relevance_metrics.json", {"status": "NOT_YET_EVALUATED", "reason": "No independent human relevance labels; blank annotation fields were not filled.", "ndcg_at_k": None, "pairwise_preference_agreement": None})
    for s in scenarios:
        sets = [{r["place_id"] for r in primary if r["scenario_id"] == s["scenario_id"] and r["method"] == m} for m in methods]
        assert all(x == sets[0] for x in sets)
    # Perturb every forbidden field and confirm the strict allowlist prevents leakage.
    for r in rows:
        poisoned = {f: (r[f] if f in EXPANDED_FIELDS else "SELECTION_METADATA_CANARY") for f in r}
        assert text_for(r, BASE_FIELDS) == text_for(poisoned, BASE_FIELDS)
        assert text_for(r, EXPANDED_FIELDS) == text_for(poisoned, EXPANDED_FIELDS)
    verify_frozen(out)
    checks = {"shared_candidate_sets": True, "finite_scores": True, "binary_within_group_exact_ties": True, "lexical_repeat_exact": True,
              "selection_metadata_canary_excluded": True, "original_inputs_unchanged": True, "canonical_and_tracked_production_files_unchanged": True,
              "human_fields_blank": all(not r["relevance_0_3"] and not r["insufficient_evidence_YES_NO"] for r in read_csv(target / "annotation_R1.csv") + read_csv(target / "annotation_R2.csv")),
              "semantic_repeat_exact": True if with_semantic else "NOT_RUN"}
    write_json(target / "checks.json", checks)
    write_json(target / "run_manifest.json", {"frozen_manifest_sha256": digest(out / "manifest.json"), "run_code_sha256": digest(__file__), "python_packages": versions(), "methods_executed": list(methods), "semantic_status": "EXECUTED" if with_semantic else "NOT_REQUESTED: run --semantic after fetching pinned model", "scenario_place_pairs": len(primary) // len(methods), "scores": len(primary), "embedding_document_order": [r["place_id"] for r in rows], "embedding_scenario_order": [s["scenario_id"] for s in scenarios], "output_sha256": {p.name: digest(p) for p in sorted(target.iterdir()) if p.is_file()}})
    print(json.dumps({"result_directory": str(target), "checks": checks, "score_rows": len(primary)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results-name", default="results")
    parser.add_argument("--semantic", action="store_true")
    args = parser.parse_args()
    if Path(args.results_name).name != args.results_name:
        raise ValueError("results-name must be one directory name")
    run(args.out, args.results_name, args.semantic)
