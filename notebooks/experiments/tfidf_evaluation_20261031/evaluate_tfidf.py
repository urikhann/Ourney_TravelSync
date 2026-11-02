"""Isolated, no-tuning evaluation of current Option C and original SDD binary sum.

CSV files are machine-readable experiment outputs, not spreadsheet workbooks.
Historical labels are used separately by sheet, never promoted to fresh truth.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import csv
import hashlib
import importlib.util
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from ourney_travelsync import FEATURE_NAMES, load_places
from ourney_travelsync.recommenders.individual_scoring import score_individuals
from ourney_travelsync.recommenders.tfidf import FIELDS, VECTORIZER_CONFIG, clean, document, score_tfidf
from ourney_travelsync.recommenders.ties import ranking

HERE = Path(__file__).resolve().parent
LEGACY_REF = 'exp:notebooks/experiments/enriched_poi_round1_20261028/evaluate.py'


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def write_csv(path, rows):
    if not rows:
        raise ValueError(f'Empty output: {path}')
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_legacy_functions():
    """Reuse unchanged legacy NDCG and annotation audit functions, not its CLI guard.

    AST extraction avoids importing obsolete experiment dependencies or bypassing
    the original two-independent-human CLI. Truthful single-person provenance is
    retained in this explicitly separate descriptive evaluation.
    """
    source = subprocess.run(['git', 'show', LEGACY_REF], cwd=ROOT, check=True,
                            capture_output=True, encoding='utf-8').stdout
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'ndcg', 'audit_ratings'}]
    assert len(functions) == 2
    namespace = dict(defaultdict=defaultdict, math=math, read_csv=read_csv)
    exec(compile(ast.Module(body=functions, type_ignores=[]), LEGACY_REF, 'exec'), namespace)
    return namespace['ndcg'], namespace['audit_ratings'], hashlib.sha256(source.encode()).hexdigest()


def score_bands(scores):
    groups = defaultdict(list)
    for i, score in enumerate(scores):
        groups[float(score)].append(i)
    return [groups[value] for value in sorted(groups, reverse=True)]


def expected_reciprocal_rank(scores, relevant):
    """Exact expected first relevant rank, uniformly permuting exact ties."""
    offset = 0
    for band in score_bands(scores):
        size, positives = len(band), sum(bool(relevant[i]) for i in band)
        if positives:
            # Probability first success at j in sampling without replacement.
            survival, result = 1.0, 0.0
            for j in range(1, size - positives + 2):
                probability = survival * positives / (size - j + 1)
                result += probability / (offset + j)
                survival *= (size - positives - j + 1) / (size - j + 1)
            return result
        offset += size
    return None  # no known relevant item: no applicable relevance query


def metric_values(ids, scores, labels, k, threshold=2, expected=True):
    """Only accepts a completely judged ranking pool; never fills unjudged=0."""
    if set(ids) != set(labels) or any(value is None for value in labels.values()):
        raise ValueError('Metrics require a completely judged candidate pool')
    ndcg, _, _ = load_legacy_functions_cached()
    order = ranking(np.asarray(scores), tuple(ids), 0)
    rows = [dict(place_id=ids[i], score=float(scores[i]), rank=r)
            for r, i in enumerate(order, 1)]
    relevant = [labels[pid] >= threshold for pid in ids]
    total_relevant = sum(relevant)
    if expected:
        hits, offset = 0.0, 0
        for band in score_bands(scores):
            taken = max(0, min(len(band), k - offset))
            hits += taken * sum(relevant[i] for i in band) / len(band)
            offset += len(band)
    else:
        hits = sum(relevant[i] for i in order[:k])
    first = next((i for i, idx in enumerate(order, 1) if relevant[idx]), None)
    return dict(ndcg=ndcg(rows, labels, k, tie_expected=expected), precision=hits / k,
                recall=hits / total_relevant if total_relevant else None,
                mrr=expected_reciprocal_rank(scores, relevant) if expected else (1 / first if first else None),
                relevant_count=total_relevant, returned_count=min(k, len(ids)))


_LEGACY = None


def load_legacy_functions_cached():
    global _LEGACY
    if _LEGACY is None:
        _LEGACY = load_legacy_functions()
    return _LEGACY


def protected_snapshot(inputs):
    tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True,
                             capture_output=True).stdout.decode().split('\0')
    paths = {ROOT / name for name in tracked if name and (ROOT / name).is_file()}
    paths.update((ROOT / 'src/ourney_travelsync').rglob('*.py'))
    paths.update(Path(path) for path in inputs)
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def mean(values):
    values = [float(value) for value in values if value is not None]
    return float(np.mean(values)) if values else None


def audit_labels(label_dir, frozen, current):
    _, audit_ratings, _ = load_legacy_functions_cached()
    sheets, rows, hashes = {}, {}, {}
    metadata = json.loads((label_dir / 'evaluation_validation.json').read_text(encoding='utf-8'))
    provenance = json.loads((label_dir / 'collection_provenance.json').read_text(encoding='utf-8'))
    assert provenance['actual_human_rater_count'] == 1 and provenance['independent'] is False
    for sheet, rid in [('A', 'R1'), ('B', 'R2')]:
        path = label_dir / f'validated_adapter_{rid}.csv'
        rows[sheet] = read_csv(path)
        template = frozen / 'results' / f'annotation_{rid}.csv'
        sheets[sheet] = audit_ratings(path, template, rid)
        hashes[str(path.relative_to(ROOT))] = digest(path)
        # Adapter evidence is checked against the original frozen packet, not current scoring text.
        source = Path(metadata['sheets'][sheet]['source_path'])
        assert source.is_file(), 'Original human-label source unavailable'
        assert digest(source) == metadata['sheets'][sheet]['sha256'], 'Human source changed'
        source_rows = {(r['scenario_id'], r['place_id']): r for r in read_csv(source)}
        for row in rows[sheet]:
            original = source_rows[(row['scenario_id'], row['place_id'])]
            for field in ('relevance_0_3', 'insufficient_evidence_YES_NO', 'notes', 'preference_ko'):
                assert row[field] == original[field], f'Human source mismatch: {field}'
        hashes[str(source)] = digest(source)
    assert sheets['A'].keys() == sheets['B'].keys()
    assert all(pid in current for _, pid in sheets['A'])
    return sheets, rows, provenance, hashes, metadata['semantic_rubric_review']


def run(destination):
    config = json.loads((HERE / 'protocol.json').read_text(encoding='utf-8'))
    assert config['tfidf'] == {**VECTORIZER_CONFIG, 'ngram_range': list(VECTORIZER_CONFIG['ngram_range'])}
    frozen = ROOT / 'data/generated/evaluation/enriched_poi_round1_20261028'
    label_dir = ROOT / config['labels_directory']
    scenarios_path = ROOT / config['scenarios']
    scenarios = json.loads(scenarios_path.read_text(encoding='utf-8'))
    original_paths = [ROOT / config['dataset'], scenarios_path, HERE / 'protocol.json', Path(__file__),
                      *frozen.rglob('*.csv'), *label_dir.rglob('*.csv'),
                      label_dir / 'collection_provenance.json', label_dir / 'evaluation_validation.json']
    human_validation = json.loads((label_dir/'evaluation_validation.json').read_text(encoding='utf-8'))
    human_source_paths = [Path(info['source_path']) for info in human_validation['sheets'].values()]
    human_before = {str(path):digest(path) for path in human_source_paths}
    before = protected_snapshot(original_paths)
    places = load_places(ROOT / config['dataset'])
    assert len(places.place_ids) == 3570
    eligible = [i for i, row in enumerate(places.records) if not row.get('exclude_reason', '').strip()]
    records = [places.records[i] for i in eligible]
    ids = tuple(places.place_ids[i] for i in eligible)
    features = places.features[eligible]
    current = dict(zip(ids, records))
    queries = [s['preference_ko'] for s in scenarios]
    # The scoring stage sees ONLY eligible POI documents and user preference inputs.
    # No grades, judged identity sets or prior score files are loaded here.
    option_c = score_tfidf(queries, records)
    vectors = np.asarray([[s['preference_vector'][f] for f in FEATURE_NAMES] for s in scenarios])
    spec = importlib.util.spec_from_file_location('travelsync_sdd_binary_reference', ROOT / 'services/mocks/group_rec.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    weighted = np.asarray([[reference.score(reference.weights(s['preference_vector']),
                                           {f: int(row[f]) for f in FEATURE_NAMES})
                            for row in records] for s in scenarios])
    methods = dict(option_c=option_c, binary_weighted_sum_sdd=weighted,
                   binary_active_mean_package=score_individuals(vectors, features))
    assert all(matrix.shape == (len(scenarios), len(ids)) and np.isfinite(matrix).all()
               for matrix in methods.values())
    # Independently fitted diagnostic oracle: fit on document list only.
    texts = [document(row) for row in records]
    vectorizer = TfidfVectorizer(**VECTORIZER_CONFIG)
    poi_matrix = vectorizer.fit_transform(texts)
    query_matrix = vectorizer.transform([clean(q) for q in queries])
    np.testing.assert_allclose(option_c, cosine_similarity(query_matrix, poi_matrix), atol=1e-15)
    fitted_hash = hashlib.sha256(json.dumps({'vocabulary': vectorizer.vocabulary_,
                                            'idf': vectorizer.idf_.tolist()}, sort_keys=True).encode()).hexdigest()
    # Labels enter only AFTER all three primary score matrices are finalized.
    sheets, sheet_rows, provenance, human_hashes, rubric_issues = audit_labels(label_dir, frozen, current)
    scenario_by_id = {scenario['scenario_id']:scenario for scenario in scenarios}
    for rows in sheet_rows.values():
        for row in rows:
            scenario = scenario_by_id[row['scenario_id']]
            assert row['preference_ko'] == scenario['preference_ko'], 'Query changed since judgment'
            assert ''.join(str(int(current[row['place_id']][f])) for f in FEATURE_NAMES) == scenario['group_id'], 'Feature group drift'
    columns = {pid: i for i, pid in enumerate(ids)}
    historical_pois = {r['place_id']: r for r in sheet_rows['A']}
    drift = []
    for pid, old in sorted(historical_pois.items()):
        changed = [f for f in FIELDS if clean(old.get(f)) != clean(current[pid].get(f))]
        drift.append(dict(place_id=pid, place_name=current[pid]['place_name'],
                          changed_fields='|'.join(changed), exact_scoring_evidence_match=not changed,
                          historical_document=document(old), current_document=document(current[pid])))
    unchanged = {row['place_id'] for row in drift if row['exact_scoring_evidence_match']}
    scenario_index = {s['scenario_id']: i for i, s in enumerate(scenarios)}
    category = {s['scenario_id']: '+'.join(f for f, value in zip(FEATURE_NAMES, s['group_id']) if value == '1')
                for s in scenarios}
    metrics, full_metrics, diagnostics, full_ranks, candidates = [], [], [], [], []
    score_diagnostics, judged_rankings = [], []
    ranked = {}
    for si, scenario in enumerate(scenarios):
        sid = scenario['scenario_id']
        judged = sorted(pid for s, pid in sheets['A'] if s == sid)
        analyzer_grams = vectorizer.build_analyzer()(clean(queries[si]))
        known_grams = sum(gram in vectorizer.vocabulary_ for gram in analyzer_grams)
        diagnostics.append(dict(scenario_id=sid, category=category[sid], query=queries[si],
            eligible_pois=len(ids), empty_query=not bool(clean(queries[si])),
            oov_query=bool(clean(queries[si])) and query_matrix[si].nnz == 0,
            query_ngrams=len(analyzer_grams), in_vocabulary_ngram_occurrences=known_grams,
            query_ngram_coverage=known_grams / len(analyzer_grams) if analyzer_grams else None,
            query_vector_nnz=query_matrix[si].nnz, positive_score_pois=int((option_c[si] > 0).sum()),
            zero_score_pois=int((option_c[si] == 0).sum()), zero_score_fraction=float((option_c[si] == 0).mean()),
            query_has_positive_match=bool((option_c[si] > 0).any()), judged_pois=len(judged),
            judged_catalogue_fraction=len(judged) / len(ids),
            unchanged_evidence_judged_pois=len(set(judged) & unchanged)))
        for method, matrix in methods.items():
            scores = matrix[si]
            order = ranking(scores, ids, 0)
            ranked[(sid, method)] = order
            rank_by_id = {ids[i]: r for r, i in enumerate(order, 1)}
            bands = score_bands(scores)
            score_diagnostics.append(dict(scenario_id=sid, category=category[sid], method=method,
                positive_score_pois=int((scores>0).sum()), zero_score_pois=int((scores==0).sum()),
                zero_score_fraction=float((scores==0).mean()), distinct_score_bands=len(bands),
                tied_poi_fraction=sum(len(b) for b in bands if len(b)>1)/len(ids),
                max_tie_size=max(map(len,bands))))
            for r, i in enumerate(order, 1):
                full_ranks.append(dict(scenario_id=sid, category=category[sid], method=method,
                    place_id=ids[i], place_name=records[i]['place_name'], score=float(scores[i]), rank=r,
                    historically_judged=ids[i] in judged))
            for sheet, all_labels in sheets.items():
                labels = {pid: all_labels[(sid, pid)] for pid in judged}
                complete = all(value is not None for value in labels.values())
                judged_scores = [scores[columns[pid]] for pid in judged]
                relevant_ids = {pid for pid, grade in labels.items() if grade is not None and grade >= 2}
                judged_order = ranking(np.asarray(judged_scores), tuple(judged), 0)
                for pool_rank, j in enumerate(judged_order, 1):
                    pid=judged[j]
                    judged_rankings.append(dict(sheet=sheet, scenario_id=sid, category=category[sid], method=method,
                        place_id=pid, place_name=current[pid]['place_name'], query=scenario['preference_ko'],
                        grade=labels[pid], score=float(judged_scores[j]), judged_pool_rank=pool_rank,
                        catalogue_rank=rank_by_id[pid], unchanged_evidence=pid in unchanged))
                for k in (5, 10):
                    top = [ids[i] for i in order[:k]]
                    judged_top = [pid for pid in top if pid in labels and labels[pid] is not None]
                    precision = sum(labels[pid] >= 2 for pid in judged_top) / k if len(judged_top) == k else None
                    full_metrics.append(dict(scope='full_catalogue', sheet=sheet, scenario_id=sid,
                        method=method, k=k, ndcg=None, precision=precision, recall=None, mrr=None,
                        judged_at_k=len(judged_top), judgment_coverage_at_k=len(judged_top)/k,
                        unavailable_reason='Incomplete corpus judgments; unjudged POIs are unknown, never nonrelevant'))
                    for threshold in (2, 1, 3):
                        for ties in ('expected', 'seeded'):
                            values = (metric_values(judged, judged_scores, labels, k, threshold, ties == 'expected')
                                      if complete else dict(ndcg=None, precision=None, recall=None, mrr=None,
                                                            relevant_count=None, returned_count=min(k, len(judged))))
                            metrics.append(dict(scope='historical_label_transfer_judged_pool', sheet=sheet,
                                scenario_id=sid, category=category[sid], method=method, k=k,
                                relevance_threshold=threshold, ties=ties, candidate_count=len(judged),
                                evidence_changed_pois=len(set(judged)-unchanged), **values))
                for cutoff in (5, 10, 20, 100):
                    nominated = {ids[i] for i in order[:cutoff]}
                    def inclusion_probability(pid):
                        value=scores[columns[pid]]
                        above=int((scores>value).sum())
                        tied=int((scores==value).sum())
                        return max(0,min(tied,cutoff-above))/tied
                    expected_judged=sum(inclusion_probability(pid) for pid in judged)
                    expected_relevant=sum(inclusion_probability(pid) for pid in relevant_ids)
                    candidates.append(dict(sheet=sheet, scenario_id=sid, category=category[sid], method=method,
                        cutoff=cutoff, returned_count=min(cutoff, len(ids)),
                        judged_candidates_retrieved=len(nominated & set(judged)),
                        judged_candidate_coverage=len(nominated & set(judged))/len(judged),
                        known_relevant_retrieved=len(nominated & relevant_ids), known_relevant_count=len(relevant_ids),
                        known_relevant_retrieval_fraction=len(nominated & relevant_ids)/len(relevant_ids) if relevant_ids else None,
                        tie_expected_judged_candidate_coverage=expected_judged/len(judged),
                        tie_expected_known_relevant_retrieval_fraction=expected_relevant/len(relevant_ids) if relevant_ids else None,
                        note='Coverage of historically judged subset, not catalogue recall'))
    primary = [r for r in metrics if r['ties'] == 'expected' and r['relevance_threshold'] == 2]
    summaries = []
    for sheet in sheets:
        for cat in ['ALL', *sorted(set(category.values()))]:
            for method in methods:
                for k in (5, 10):
                    subset = [r for r in primary if r['sheet']==sheet and r['method']==method and r['k']==k
                              and (cat=='ALL' or r['category']==cat)]
                    summaries.append(dict(sheet=sheet, category=cat, method=method, k=k, scenario_count=len(subset),
                        **{name: mean([r[name] for r in subset]) for name in ('ndcg','precision','recall','mrr')},
                        applicable_recall_queries=sum(r['recall'] is not None for r in subset),
                        scope='historical_label_transfer_judged_pool'))
    comparisons = []
    for sheet in sheets:
        for sid in scenario_index:
            for k in (5, 10):
                selected = {r['method']:r for r in primary if r['sheet']==sheet and r['scenario_id']==sid and r['k']==k}
                c, b = selected['option_c'], selected['binary_weighted_sum_sdd']
                comparisons.append(dict(sheet=sheet, scenario_id=sid, category=category[sid], k=k,
                    option_c_ndcg=c['ndcg'], binary_ndcg=b['ndcg'], ndcg_delta=c['ndcg']-b['ndcg'],
                    outcome='C higher' if c['ndcg']>b['ndcg']+1e-12 else 'binary higher' if b['ndcg']>c['ndcg']+1e-12 else 'tie',
                    option_c_top=ids[ranked[(sid,'option_c')][0]], binary_top=ids[ranked[(sid,'binary_weighted_sum_sdd')][0]]))
    # Missing-data robustness: diagnostic ablations only, never parameter selection.
    sensitivity = []
    for field in FIELDS:
        masked = [dict(row, **{field: None}) for row in records]
        masked_scores = score_tfidf(queries, masked)
        for si, scenario in enumerate(scenarios):
            base_order = ranked[(scenario['scenario_id'], 'option_c')]
            mask_order = ranking(masked_scores[si], ids, 0)
            sensitivity.append(dict(masked_field=field, scenario_id=scenario['scenario_id'],
                category=category[scenario['scenario_id']], affected_documents=sum(bool(clean(r.get(field))) for r in records),
                zero_score_pois=int((masked_scores[si]==0).sum()),
                added_zero_score_pois=int(((masked_scores[si]==0)&(option_c[si]>0)).sum()),
                lost_zero_score_pois=int(((masked_scores[si]>0)&(option_c[si]==0)).sum()),
                top1_changed=bool(base_order[0]!=mask_order[0]),
                top5_overlap=len(set(base_order[:5])&set(mask_order[:5]))/5,
                top10_overlap=len(set(base_order[:10])&set(mask_order[:10]))/10,
                interpretation='Refit eligible-only corpus after omission; ranking sensitivity, not quality gain'))
    metadata = [dict(field=f, nonempty_pois=sum(bool(clean(row.get(f))) for row in records),
                     missing_pois=sum(not bool(clean(row.get(f))) for row in records)) for f in FIELDS]
    missing_groups=[]
    for present in (False,True):
        indices=[i for i,row in enumerate(records) if bool(clean(row.get('description_summary')))==present]
        missing_groups.append(dict(description_present=present, poi_count=len(indices),
            empty_five_field_documents=sum(not texts[i] for i in indices),
            query_poi_zero_score_fraction=float((option_c[:,indices]==0).mean()),
            interpretation='Observed missingness groups differ; this is not a causal quality comparison'))
    strict = dict(status='INSUFFICIENT_COMPARABLE_EVIDENCE', exact_match_pois=len(unchanged),
        exact_match_place_ids=sorted(unchanged),
        nondegenerate_scenarios=sum(r['unchanged_evidence_judged_pois']>=2 for r in diagnostics),
        ndcg_at_5=None, ndcg_at_10=None, precision_at_5=None, precision_at_10=None,
        recall_at_5=None, recall_at_10=None, mrr=None,
        reason='No scenario has two candidates with unchanged scoring evidence; a singleton cannot compare rankers')
    # Reproducibility: fresh scoring, input independence, no source changes.
    np.testing.assert_array_equal(option_c, score_tfidf(queries, records))
    np.testing.assert_array_equal(option_c[:1], score_tfidf(queries[:1], records))
    assert fitted_hash == hashlib.sha256(json.dumps({'vocabulary': vectorizer.vocabulary_,
        'idf': vectorizer.idf_.tolist()}, sort_keys=True).encode()).hexdigest()
    after = protected_snapshot(original_paths)
    assert before == after, 'Protected source/input changed'
    assert human_before == {str(path):digest(path) for path in human_source_paths}, 'Original human source changed'
    destination.mkdir(parents=True, exist_ok=False)
    outputs = dict(per_scenario_metrics=metrics, summary=summaries, scenario_comparison=comparisons,
        full_catalogue_metrics=full_metrics, query_diagnostics=diagnostics, candidate_coverage=candidates,
        missing_field_sensitivity=sensitivity, metadata_coverage=metadata, evidence_drift=drift,
        rankings=full_ranks, score_diagnostics=score_diagnostics, judged_rankings=judged_rankings,
        observed_missing_metadata=missing_groups)
    for name, rows in outputs.items():
        write_csv(destination/f'{name}.csv', rows)
    write_json(destination/'strict_evidence_metrics.json', strict)
    write_json(destination/'summary.json', dict(summary=summaries, strict_evidence=strict,
        full_catalogue_quality='Not established: incomplete relevance judgments',
        conclusion='Option C population/full-catalogue superiority is not established',
        query_coverage=dict(total=len(scenarios), positive_matches=sum(r['query_has_positive_match'] for r in diagnostics),
                            oov_queries=sum(r['oov_query'] for r in diagnostics),
                            zero_score_fraction=float((option_c==0).mean()))))
    write_json(destination/'manifest.json', dict(protocol=config, source_hashes=before, human_source_hashes=human_hashes,
        provenance=provenance, rubric_issues=rubric_issues, source_paths=dict(scenarios=str(scenarios_path), labels=str(label_dir)),
        legacy_function_reference=LEGACY_REF, legacy_evaluator_source_sha256=load_legacy_functions_cached()[2],
        fit_corpus_place_ids=list(ids), fit_corpus_sha256=hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest(),
        fitted_vocabulary_idf_sha256=fitted_hash, vocabulary_size=len(vectorizer.vocabulary_),
        scoring_configuration=VECTORIZER_CONFIG, environment=dict(python=platform.python_version(),
            numpy=version('numpy'), sklearn=version('scikit-learn')),
        checks=dict(protected_inputs_unchanged=True, repeat_scores_identical=True,
            query_batch_does_not_influence_fit=True, cosine_oracle_matches=True,
            primary_matrices_computed_before_label_loading=True, no_prior_metric_or_ranking_files_used=True),
        analysis_date_korea='2026-10-31'))
    write_report(destination, summaries, comparisons, diagnostics, metadata, sensitivity, strict, provenance, rubric_issues,
                 candidates, judged_rankings)
    write_json(destination/'output_hashes.json', {p.name:digest(p) for p in sorted(destination.iterdir()) if p.is_file()})
    print(json.dumps({'destination': str(destination), 'scenarios': len(scenarios), 'eligible_pois': len(ids),
                      'judged_pairs_per_sheet': len(sheets['A']), 'protected_inputs_unchanged': before==after},indent=2))


def write_report(dest, summaries, comparisons, diagnostics, metadata, sensitivity, strict, provenance, rubric_issues,
                 candidates, judged_rankings):
    def fmt(value):
        return 'N/A' if value is None else f'{value:.4f}' if isinstance(value, float) else str(value)
    def table(headers, rows):
        return ['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|',
                *['| '+' | '.join(fmt(x).replace('|','/') for x in row)+' |' for row in rows]]
    lines = ['# Option C versus binary baseline: new evaluation', '',
        'Date: 2026-10-31. **Full-catalogue superiority is not established.** All scores and metrics below were newly computed from current production scorer code and the enriched 3,570-POI CSV. No previous NDCG or ranking result was reused.', '',
        '## Validity and comparable conditions', '',
        'All methods score the same eligible catalogue, then restrict to the exact same historically judged pool per scenario. Option C vocabulary/IDF is fitted on all eligible catalogue documents, never on queries, judgments or the small judged pool. Config is fixed in protocol.json; no result selects a tokenizer, field weight, threshold or algorithm. Empty exclusion reasons leave all 3,570 rows eligible; no trip timing or budget is invented.', '',
        'The primary binary comparator is the original SDD weighted sum, reused from services/mocks/group_rec.py: sum(r_k*x_k)/sum(r_k). The package binary scorer instead averages preferences over active POI features: sum(r_k*x_k)/sum(x_k); its separately named reference rows are included. These differ across the catalogue but both tie inside each original feature-matched judged pool.', '',
        'Twelve Korean scenarios and their synthetic 1-5 feature projections are reused unchanged. They are not collected user survey/query pairs. TF-IDF receives detailed text; binary features cannot express those details. This compares the information/interface settings as well as the scorers.', '',
        'Two 38-pair sheets are from **one blinded human**, not two independent raters or consensus ground truth. Each is reported separately. Human source hashes and adapters were verified against the frozen packet. Eighteen of 19 POIs have changed scoring text since annotation. Historical-label transfer is a descriptive diagnostic, not freshly validated current-corpus relevance. Only one POI has exact scoring-text agreement, leaving zero nondegenerate strict-evidence scenarios. Even exact text agreement alone would not establish current real-world relevance.', '',
        'No full-catalogue NDCG or Recall is reported: nearly all scenario–POI pairs are unjudged. Full-catalogue Precision@K is emitted only if every top-K item is judged; otherwise null. MRR is also withheld on incompletely judged full rankings. Unknown items never become grade zero. The small-pool metrics below do not establish catalogue retrieval quality.', '',
        '## Retrospective judged-pool results (primary threshold >=2)', '',
        'Gain=2^grade-1, logarithmic DCG discount. NDCG reuses the original evaluator function unchanged. Primary scores average uniformly over exact ties; seeded production ordering (seed 0) is a separate diagnostic. Precision divides by requested K even for short lists; Recall divides by known relevant items in that judged pool only. No-relevant Recall/MRR and zero-IDCG NDCG are N/A. Grade thresholds >=1 and >=3 are sensitivity rows, never alternatives chosen after results. MRR is over the complete judged pool and repeated for each cutoff for tabular convenience.', '']
    lines += table(['Sheet','Method','K','NDCG','Precision','Recall','MRR'],
        [[r['sheet'],r['method'],r['k'],r['ndcg'],r['precision'],r['recall'],r['mrr']]
         for r in summaries if r['category']=='ALL'])
    lines += ['', 'Every scenario has only 2–6 judged candidates. At K=10 all are retrieved, so Recall@10 is 1 where applicable and cannot distinguish methods; short-list Precision@10 mostly reflects pool size. At K=5, only the six-candidate scenarios permit membership differences. NDCG/MRR carry most of the ordering information here. These are not standard full-catalogue Top-5/10 benchmark results.', '',
              '## Categories and scenario differences', '']
    lines += table(['Sheet','Category','Method','NDCG@5','MRR'],
        [[r['sheet'],r['category'],r['method'],r['ndcg'],r['mrr']] for r in summaries
         if r['category']!='ALL' and r['k']==5 and r['method']!='binary_active_mean_package'])
    lines += ['', 'Scenario-wise higher/equal/lower reflects one person’s historical grades, not independent replications.', '']
    lines += table(['Sheet','Scenario','Category','C NDCG@5','Binary NDCG@5','Delta','Outcome'],
        [[r['sheet'],r['scenario_id'],r['category'],r['option_c_ndcg'],r['binary_ndcg'],r['ndcg_delta'],r['outcome']]
         for r in comparisons if r['k']==5])
    for sheet in ('A','B'):
        rows=[r for r in comparisons if r['sheet']==sheet and r['k']==5]
        lines += ['', f"Sheet {sheet} C higher / tie / binary higher: " + ' / '.join(str(sum(r['outcome']==v for r in rows)) for v in ('C higher','tie','binary higher')) + '.']
    lines += ['', 'Selected cases (current scores, historically judged alternatives only):', '']
    for sid in ('S01','S03','S07','S12'):
        rows=[r for r in judged_rankings if r['sheet']=='B' and r['scenario_id']==sid and r['method']=='option_c']
        lines += [f"- {sid}: {rows[0]['query']} Option C orders " + ' > '.join(
            f"{r['place_name']} (historical sheet B grade {r['grade']}, corpus rank {r['catalogue_rank']})" for r in rows) +
            '. The binary scorer ties these alternatives.']
    lines += ['', 'S12 is the counterexample on both sheets: the lower-graded museum outranks the temple. The history category mean therefore ties the binary expectation across S11/S12. Other categories show higher retrospective mean NDCG. These observations do not prove why a scorer made a particular error.', '']
    lines += ['', '## Coverage and missing metadata (separate from relevance)', '']
    lines += table(['Scenario','Query n-gram coverage','OOV','Positive-score POIs','Zero-score fraction','Judged POIs'],
        [[r['scenario_id'],r['query_ngram_coverage'],r['oov_query'],r['positive_score_pois'],r['zero_score_fraction'],r['judged_pois']] for r in diagnostics])
    lines += ['', 'Query coverage is in-vocabulary character n-gram occurrences divided by all query n-gram occurrences. OOV means a nonempty cleaned query transforms to an all-zero vector; partial OOV is visible in the coverage fraction. Positive-match coverage does not establish relevance. Candidate coverage tables separately report how many historically judged/relevant IDs occur in full-catalogue Top-5/10/20/100; they are not global recall.', '']
    total_pairs=sum(r['eligible_pois'] for r in diagnostics)
    zeros=sum(r['zero_score_pois'] for r in diagnostics)
    lines += [f"{sum(r['query_has_positive_match'] for r in diagnostics)}/{len(diagnostics)} queries have a positive match; entirely OOV queries={sum(r['oov_query'] for r in diagnostics)}. Across {total_pairs:,} query–POI pairs, Option C has {zeros/total_pairs:.4%} zero scores; both binary scorers have no zero scores. Nonzero binary values also do not establish relevance. Full-catalogue Precision@5/10 is unavailable for every scenario/method because no returned list is fully judged.", '',
              'Candidate coverage below is the mean fraction of historical grade>=2 IDs retrieved in the full catalogue. Seeded retrieval uses production seed 0; expected retrieval averages exact-score ties to show the baseline tie sensitivity. Neither measures recall over unknown relevant POIs.', '']
    coverage_rows=[]
    for sheet in ('A','B'):
        for method in ('option_c','binary_weighted_sum_sdd','binary_active_mean_package'):
            for cutoff in (5,10,100):
                subset=[r for r in candidates if r['sheet']==sheet and r['method']==method and r['cutoff']==cutoff]
                coverage_rows.append([sheet,method,cutoff,mean([r['known_relevant_retrieval_fraction'] for r in subset]),
                                      mean([r['tie_expected_known_relevant_retrieval_fraction'] for r in subset])])
    lines += table(['Sheet','Method','Catalogue cutoff','Known relevant coverage (seed 0)','Tie expectation'],coverage_rows)
    lines += table(['Field','Nonempty POIs','Missing POIs'], [[r['field'],r['nonempty_pois'],r['missing_pois']] for r in metadata])
    lines += ['', 'There are 25 empty five-field documents; these remain eligible and score zero. The current CSV has representative_activity but no activity_type. The strict implementation does not alias it, so that entire input field is absent. Missing values are not negative relevance evidence.', '',
              'One-field omission diagnostics refit the eligible corpus without that field, holding queries, identities and parameters fixed; no configuration is selected from these results.', '']
    lines += table(['Masked field','Top-1 changes / 12','Mean Top-10 overlap','Mean added-zero POIs'],
        [[field,sum(r['top1_changed'] for r in sensitivity if r['masked_field']==field),
          mean([r['top10_overlap'] for r in sensitivity if r['masked_field']==field]),
          mean([r['added_zero_score_pois'] for r in sensitivity if r['masked_field']==field])] for field in FIELDS])
    lines += ['', '## Interpretation and recommendations', '',
        'Implementation correctness and recommendation quality are separate. Direct scikit-learn cosine checks, repeat runs, query-batch independence, audited label alignment and protected-input hashes test correctness. They do not validate relevance. The historical descriptive advantages or losses above cannot establish improvement for current users, catalogue retrieval, GFAR group outcomes or itinerary quality.', '',
        'Collect a new independently blinded pooled judgment set for the current catalogue: include Top-K candidates from both fixed methods, sampled nonretrieved items and relevant alternatives; keep unjudged items explicit. Use real paired preference text and ten-feature surveys, prospectively freeze scenarios, relevance definitions and tie policies, and obtain more than one independent person. For valid catalogue Recall/NDCG, use a sufficiently judged declared universe or a defensible incomplete-judgment protocol; Top-K pooling alone cannot prove total relevant recall.', '',
        'Review the absent activity_type schema field and missing descriptions using verified sources before any separately authorized production change. Any future mapping, text enrichment or tuning needs held-out evaluation; this run changes none of them. Avoid interpreting cosine as satisfaction or calibrating from these reused labels. Reassess candidate coverage before studying GFAR.', '',
        'Known unresolved rubric issues are retained without relabeling:', *['- '+issue for issue in rubric_issues], '',
        'Limitations: one annotator; synthetic queries authored from the old POI evidence (not held-out); tiny feature-matched judged pools; repeated POIs/dependent scenarios; historical evidence drift; AI-assisted English annotation translation; no current availability verification; zero strict evidence-comparable rankings. Previous exposure of these scenarios during development means this is a reused diagnostic set, never a held-out test. No significance or causal improvement claim is made.', '',
        '## Reproducibility and files', '',
        'Run evaluate_tfidf.py --destination <fresh directory>; existing outputs are never overwritten. protocol.json fixes all settings. manifest.json records hashes of the current code/data, frozen inputs, original human sheets, legacy evaluator source, fitted vocabulary/IDF and environment. All pre-existing tracked files and protected source files must match before/after. Output hashes support an independent clean repeat. Production recommendation code, GFAR, constraints, dataset and labels remain unchanged.', '',
        'summary.csv/json: sheet/category means; per_scenario_metrics.csv: thresholds and tie policies; scenario_comparison.csv: wins/losses; full_catalogue_metrics.csv: explicitly unavailable metrics and judgment coverage; query_diagnostics.csv; score_diagnostics.csv; candidate_coverage.csv; metadata_coverage.csv; missing_field_sensitivity.csv; observed_missing_metadata.csv; evidence_drift.csv; rankings.csv: fresh full-catalogue scores/ranks; judged_rankings.csv: grades and pool/corpus ranks; strict_evidence_metrics.json: insufficient-evidence result. All CSV empty numeric cells correspond to JSON null/N/A, never fabricated zeros.']
    (dest/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path,
        default=ROOT/'data/generated/evaluation/tfidf_vs_binary_20261031_run01')
    run(parser.parse_args().destination.resolve())
