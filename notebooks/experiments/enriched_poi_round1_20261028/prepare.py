"""Audit inputs and freeze scenarios before any comparative scoring. No network/DB access."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "data/generated/evaluation/enriched_poi_round1_20261028"
FEATURES = ("food", "cafe", "nature", "history", "art", "shopping", "activity", "entertainment", "relax", "festival")
BASE_FIELDS = ("detailed_category", "food_type", "representative_menu", "activity_type")
EXPANDED_FIELDS = BASE_FIELDS + ("description_summary",)
INPUT_NAMES = ("travelsync_demo_candidates_enriched_v1.csv", "travelsync_demo_candidates_enriched_report.txt", "travelsync_demo_candidates_detail_guide.txt", "travelsync_demo_candidates_missing_info.csv")
CANONICAL = ROOT / "src/ourney_travelsync/data/travelsync_final_places_v2.csv"
IDENTITY_EXCLUSIONS = {
    "kakao:25969605": "Unresolved birthplace/memorial boundary and address: source identity scope uncertain.",
    "kakao:1584060565": "Unresolved original cafe feature/category versus sourced restaurant/food attributes.",
}
MODEL = {"id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42", "license": "Apache-2.0", "language": "ko included in official multilingual language list", "dimension": 384, "pooling": "attention-mask mean pooling", "normalization": "L2", "similarity": "dot product of unit embeddings = cosine; not satisfaction probability", "max_seq_length": 128, "documentation": ["https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "https://sbert.net/docs/sentence_transformer/pretrained_models.html"], "metadata_verified_utc_date": "2026-10-28"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    rows = list(rows)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean(value):
    import unicodedata
    if value.strip().upper() in {"", "NULL", "N/A", "NA", "UNKNOWN", "NOT_APPLICABLE"}:
        return ""
    value = unicodedata.normalize("NFKC", value).lower()
    return " ".join(re.sub(r"[^가-힣a-z0-9\s]", " ", value).split())


def signature(row):
    return "".join(str(int(float(row[f]))) for f in FEATURES)


def protected_snapshot():
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    return {p: digest(ROOT / p) for p in tracked if p and (ROOT / p).is_file() and not p.startswith(HERE.relative_to(ROOT).as_posix() + "/")}


def versions():
    return dict(sorted({d.metadata.get("Name"): d.version for d in importlib.metadata.distributions() if d.metadata.get("Name")}.items()))


def scenarios():
    # Authored from source content, never ranking results. Two contrasts per eligible group.
    specs = [
        ("S01", "1000000000", "떡볶이를 먹을 수 있는 분식집에 가고 싶어요.", "떡볶이 versus 감자탕", ["kakao:20522414", "kakao:16239547"]),
        ("S02", "1000000000", "감자탕이나 목뼈해장국을 먹으러 가고 싶어요.", "감자탕/해장국 versus 떡볶이", ["kakao:16239547", "kakao:20522414"]),
        ("S03", "0000001000", "메밀가루로 수타 소바를 직접 만들어 보고 싶어요.", "소바 만들기 versus 메이커 교육", ["kakao:1508609240", "kakao:316557466"]),
        ("S04", "0000001000", "코딩이나 VR, 제작 체험을 하는 메이커 프로그램에 참여하고 싶어요.", "메이커 프로그램 versus 소바 만들기", ["kakao:316557466", "kakao:1508609240"]),
        ("S05", "0000001010", "글램핑 시설에서 숙박하는 캠핑을 하고 싶어요.", "글램핑 versus 일반/오토캠핑/체육공원", ["tour:2741526", "tour:2734674"]),
        ("S06", "0000001010", "운동기구와 다목적코트가 있는 체육공원을 둘러보고 싶어요.", "운동시설 versus 캠핑시설", ["kakao:1052516304", "tour:2729157"]),
        ("S07", "0010000010", "편백과 삼나무가 자라는 숲의 산책로를 걷고 싶어요.", "명시된 수종/숲길 versus 해변/기타 숲", ["kakao:10481915", "tour:126122"]),
        ("S08", "0010000010", "백사장과 해변공원이 있는 해수욕장을 둘러보고 싶어요.", "백사장/해변공원 versus 산/숲", ["tour:126122", "kakao:25339484"]),
        ("S09", "0010000110", "어린이공원으로 등록된 공원을 둘러보고 싶어요.", "어린이공원 분류 versus 도시 생태숲", ["kakao:18203633", "kakao:1170050324"]),
        ("S10", "0010000110", "다양한 수목과 어린이 놀이시설이 있는 도시 생태숲에 가고 싶어요.", "수목/놀이시설 명시 versus 공원 등록만 확인", ["kakao:1170050324", "kakao:18203633"]),
        ("S11", "0001000000", "일제강제동원의 역사를 다루는 상설 전시를 보고 싶어요.", "역사 전시 versus 사찰", ["kakao:1822480182", "tour:309338"]),
        ("S12", "0001000000", "사찰을 둘러보며 절의 역사를 알아보고 싶어요.", "사찰 역사 versus 역사관 전시", ["tour:309338", "kakao:1822480182"]),
    ]
    result = []
    for sid, sig, text, distinction, evidence in specs:
        active = [f for f, bit in zip(FEATURES, sig) if bit == "1"]
        result.append({"scenario_id": sid, "group_id": sig, "preference_ko": text,
                       "preference_vector": {f: 5 if f in active else 3 for f in FEATURES},
                       "mapping_rationale": "Author maps the stated experience to this existing candidate group's active categories (" + ", ".join(active) + "); 5 for those and neutral 3 for the rest. This is a coarse experimental projection, not a collected survey.",
                       "distinction": distinction, "evidence_place_ids": evidence,
                       "assumptions": "Synthetic preference authored for feasibility. Desired experience does not assert current participation/access, quality, quietness, cost or accessibility. Historical/program-specific facts are not availability guarantees. Richer text supplies information absent from the original survey."})
    return result


def prepare(source_dir, out):
    if out.exists():
        raise FileExistsError("Use a new output directory; frozen experiments are never overwritten.")
    sources = [source_dir / n for n in INPUT_NAMES]
    for p in sources:
        raw = p.read_text(encoding="utf-8-sig")
        if re.search(r"(?i)(?:service_?key|api_?key|access_?token|authorization)\s*[=:]\s*[^\s,\"}]{8,}|-----BEGIN .*PRIVATE KEY-----", raw):
            raise ValueError("Potential credential detected; source not copied.")
    before = protected_snapshot()
    out.mkdir(parents=True)
    (out / "inputs").mkdir()
    for p in sources:
        shutil.copyfile(p, out / "inputs" / p.name)
    rows = read_csv(out / "inputs" / INPUT_NAMES[0])
    canonical = {r["place_id"]: r for r in read_csv(CANONICAL)}
    assert len(rows) == 40 and len({r["place_id"] for r in rows}) == 40
    groups = defaultdict(list)
    audit, provenance, missing, conflicts = [], [], [], []
    preeligible = []
    for r in rows:
        assert all(r[f] in {"0", "1", "0.0", "1.0"} for f in FEATURES)
        assert sum(float(r[f]) for f in FEATURES) > 0
        assert r["place_id"] in canonical
        original = canonical[r["place_id"]]
        assert all(float(r[f]) == float(original[f]) for f in FEATURES)
        assert all(r[f] == original[f] for f in ("place_name", "address", "latitude", "longitude"))
        sig = signature(r)
        groups[sig].append(r)
        prov = json.loads(r["field_provenance_json"])
        assert isinstance(prov, list)
        for record in prov:
            for field in record["fields"]:
                provenance.append({"place_id": r["place_id"], "field": field, **{k: record.get(k, "") for k in ("url", "source_type", "checked_at", "retrieval", "note")}})
        for field, raw in r.items():
            if raw.strip().upper() in {"", "NULL", "N/A", "NA", "UNKNOWN", "NOT_APPLICABLE"}:
                state = "explicit_not_applicable" if raw.strip().upper() in {"N/A", "NA", "NOT_APPLICABLE"} else "explicit_unknown" if raw.strip().upper() == "UNKNOWN" else "unspecified_null_unknown_or_not_applicable"
                missing.append({"place_id": r["place_id"], "field": field, "raw_value": raw, "state": state})
        present = [f for f in EXPANDED_FIELDS if clean(r[f])]
        uncovered = [f for f in present if not any(f in p["fields"] and p.get("url") and p.get("checked_at") for p in prov)]
        reason = []
        if not clean(r["description_summary"]):
            reason.append("Missing description_summary")
        if r["place_id"] in IDENTITY_EXCLUSIONS:
            reason.append(IDENTITY_EXCLUSIONS[r["place_id"]])
        if uncovered:
            reason.append("Missing field-level source/date: " + ", ".join(uncovered))
        if not reason:
            preeligible.append(r)
        audit.append({"place_id": r["place_id"], "place_name": r["place_name"], "group_id": sig,
                      "has_description": bool(clean(r["description_summary"])), "present_matching_fields": "|".join(present),
                      "provenance_gaps": "|".join(uncovered), "detail_verification_status": r["detail_verification_status"],
                      "exclusion_reason": "; ".join(reason), "detail_limitations": r["detail_limitations"]})
        if r["detail_verification_status"] == "CONFLICT" or r["place_id"] in IDENTITY_EXCLUSIONS or "경계" in r["detail_limitations"] or "서로 다른 지점" in r["detail_limitations"]:
            conflicts.append({"place_id": r["place_id"], "place_name": r["place_name"], "primary_attribute_exclusion": r["place_id"] in IDENTITY_EXCLUSIONS, "detail_limitations": r["detail_limitations"], "operating_update_relation": r["operating_update_relation"], "operating_update_raw": r["operating_update_raw"]})
    counts = Counter(signature(r) for r in preeligible)
    eligible = [r for r in preeligible if counts[signature(r)] >= 2]
    eligible_ids = {r["place_id"] for r in eligible}
    for a in audit:
        if not a["exclusion_reason"] and a["place_id"] not in eligible_ids:
            a["exclusion_reason"] = "Fewer than two eligible described candidates in feature group"
        a["primary_eligible"] = a["place_id"] in eligible_ids
    write_csv(out / "candidate_audit.csv", audit)
    write_csv(out / "eligible_candidates.csv", [{"place_id": r["place_id"], "place_name": r["place_name"], "group_id": signature(r), **{f: r[f] for f in EXPANDED_FIELDS}} for r in eligible])
    write_csv(out / "field_provenance.csv", provenance)
    write_csv(out / "missing_values.csv", missing)
    write_csv(out / "conflicts.csv", conflicts)
    write_csv(out / "feature_groups.csv", [{"group_id": sig, "place_count": len(g), "described_count": sum(bool(clean(r["description_summary"])) for r in g), "primary_count": sum(r["place_id"] in eligible_ids for r in g), "place_ids": "|".join(r["place_id"] for r in g)} for sig, g in sorted(groups.items())])
    frozen = scenarios()
    assert all(sum(signature(r) == s["group_id"] for r in eligible) >= 2 for s in frozen)
    for s in frozen:
        assert all(p in canonical for p in s["evidence_place_ids"])
    write_json(out / "scenarios.json", frozen)
    config = {"experiment_id": "enriched_poi_round1_20261028", "seed": 20261028, "features": FEATURES, "base_text_fields": BASE_FIELDS, "expanded_text_fields": EXPANDED_FIELDS,
              "lexical": {"analyzer": "within-token character 2/3-gram raw term counts; no cross-word ngrams, morphology, stopwords, synonyms or learned weights", "idf": "log((1+N)/(1+df))+1", "fit": "separately on all primary eligible documents for B and C; never queries; fixed across scenarios", "normalization": "L2", "similarity": "cosine; zero vector -> score 0"},
              "cleaning": "NFKC, lowercase, replace non Hangul/a-z/0-9/whitespace with spaces, collapse whitespace; exact missing tokens omitted, never invented attributes. Join allowed field values with one space, in listed order, without labels.",
              "ties": "Descending unrounded score then ascending full place_id string; exact native score equality defines bands. No relevance claim from fallback order.",
              "k": "min(3, actual group size)", "model": MODEL,
              "annotation": {"raters": 2, "relevance": [0, 1, 2, 3], "insufficient_evidence": ["YES", "NO"], "hide": "method identity, score, rank, selection metadata", "aggregation": "mean of two independent grades only when both have NO insufficient_evidence"},
              "independent_labels": {"available": False, "audit": "Four supplied files contain model A/B/C/D scores and curation notes, no independent scenario relevance grades. Repository filename/content search found no suitable human relevance annotations; docs/data_workflows.md mentions feature validation, which is not preference relevance. No external private label source was supplied."},
              "identity_exclusions": IDENTITY_EXCLUSIONS}
    write_json(out / "config.json", config)
    frozen_names = ["scenarios.json", "config.json", "eligible_candidates.csv", "candidate_audit.csv", "field_provenance.csv", "feature_groups.csv", "missing_values.csv", "conflicts.csv"]
    manifest = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "phase": "audit_and_scenarios_before_comparative_scoring", "dataset_version": "enriched_v1, review date 2026-10-27; canonical v2", "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
                "git_status_before": subprocess.check_output(["git", "status", "--short"], cwd=ROOT).decode(), "python": platform.python_version(), "packages": versions(),
                "inputs": [{"original_path": str(p), "frozen_path": "inputs/" + p.name, "sha256": digest(p)} for p in sources],
                "canonical_sha256": digest(CANONICAL), "prepare_code_sha256": digest(__file__), "frozen_artifacts": {n: digest(out / n) for n in frozen_names}, "protected_tracked_files": before,
                "counts": {"places": len(rows), "descriptions": sum(bool(clean(r["description_summary"])) for r in rows), "feature_groups": len(groups), "groups_with_two_descriptions": sum(sum(bool(clean(r["description_summary"])) for r in g) >= 2 for g in groups.values()), "primary_places": len(eligible), "primary_groups": len(set(signature(r) for r in eligible)), "scenarios": len(frozen), "source_missing_info_rows": len(read_csv(sources[-1]))}}
    assert before == protected_snapshot()
    write_json(out / "manifest.json", manifest)
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(r"D:\Backup\Documents\KakaoTalk Downloads"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    prepare(args.source_dir, args.out)
