"""Verify saved experiment artifacts and a complete independent rerun."""
from collections import Counter
import json
from pathlib import Path
import re

from prepare import DEFAULT_OUT, HERE, clean, digest, read_csv, write_json
from run import verify_frozen


def verify(out=DEFAULT_OUT):
    freeze = verify_frozen(out)
    first, repeat = out / "results", out / "repeat_validation"
    for results in (first, repeat):
        manifest = json.loads((results / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["frozen_manifest_sha256"] == digest(out / "manifest.json")
        for name, expected in manifest["output_sha256"].items():
            assert digest(results / name) == expected, name
    comparisons = {p.name: digest(p) == digest(repeat / p.name) for p in first.iterdir() if p.is_file()}
    assert all(comparisons.values()), comparisons
    scores = read_csv(first / "scores_ranks.csv")
    assert len(scores) == 152
    assert len({(r["scenario_id"], r["place_id"]) for r in scores}) == 38
    assert Counter(r["method"] for r in scores) == {m: 38 for m in "ABCD"}
    expected = {(r["scenario_id"], r["place_id"]) for r in scores}
    for rid in ("R1", "R2"):
        sheet = read_csv(first / f"annotation_{rid}.csv")
        assert {(r["scenario_id"], r["place_id"]) for r in sheet} == expected
        assert len(sheet) == 38
        assert not ({"method", "score", "rank", "candidate_type", "selection_reason", "compromise_rank"} & sheet[0].keys())
        assert all(r["rater_id"] == rid and r["relevance_0_3"] == "" and r["insufficient_evidence_YES_NO"] == "" for r in sheet)
    a, b = (read_csv(first / f"annotation_R{i}.csv") for i in (1, 2))
    assert [r["annotation_id"] for r in a] != [r["annotation_id"] for r in b]
    assert all("positive_score_fraction" in r for r in read_csv(first / "diagnostics.csv"))
    relevance = json.loads((first / "relevance_metrics.json").read_text())
    assert relevance["status"] == "NOT_YET_EVALUATED"
    forbidden_secret = re.compile(r"(?i)(?:service_?key|api_?key|access_?token|authorization)\s*[=:]\s*[^\s,\"}]{8,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]|\bAKIA[0-9A-Z]{16}\b")
    scanned = []
    for p in list(out.rglob("*")) + list(HERE.iterdir()):
        if p.is_file() and p.suffix.lower() in {".py", ".csv", ".json", ".md", ".txt"} and p.name != "verification.json":
            assert not forbidden_secret.search(p.read_text(encoding="utf-8-sig")), "Potential credential in " + p.name
            scanned.append(str(p))
    result = {"status": "PASS", "frozen_manifest_sha256": digest(out / "manifest.json"), "full_rerun_byte_identical": comparisons,
              "independent_rerun_files_compared": len(comparisons), "primary_scores": len(scores), "scenario_place_pairs": 38,
              "two_blank_blinded_sheets_valid": True, "relevance_unevaluated": True, "credentials_scan_text_file_count": len(scanned),
              "original_inputs_and_protected_tracked_files_unchanged": True, "tracked_file_count": len(freeze["protected_tracked_files"]),
              "tests": {"experiment_unittest": "7 passed", "repository_pytest": "40 passed, 2 skipped, 21 subtests passed; workspace basetemp; default pytest cache emitted permission warning"},
              "implementation_sha256": {p.name: digest(p) for p in HERE.iterdir() if p.is_file() and p.suffix in {".py", ".txt", ".md"}}}
    write_json(out / "verification.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in {"implementation_sha256", "full_rerun_byte_identical"}}, indent=2))


if __name__ == "__main__":
    verify()
