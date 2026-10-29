"""Descriptive evaluation of two sheets completed by ONE blinded human.

Uses frozen scores and unchanged evaluate.py metric functions. Never asserts
two-rater independence. Refuses to overwrite an earlier output directory.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / 'notebooks/experiments/enriched_poi_round1_20261028'
FROZEN = ROOT / 'data/generated/evaluation/enriched_poi_round1_20261028'
PACKAGE = Path(r'C:\Users\Administrator\.codex\.chatgpt-projects\g-p-6a962829564c8191b974c172961babe5\deliverables\phase1_annotation_20261029\english')
sys.path.insert(0, str(CODE))
from evaluate import audit_ratings, ndcg, pair_agreement
from run import verify_frozen


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)), 'Invalid headers'
        rows = list(reader)
        assert all(None not in r and None not in r.values() for r in rows), 'Malformed row'
        return rows


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_csv(path, rows, fields=None):
    with Path(path).open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def mean(values):
    values = [float(v) for v in values if v != 'N/A']
    return statistics.mean(values) if values else 'N/A'


def fmt(value):
    return f'{value:.6f}' if isinstance(value, float) else str(value)


def verify_by_permutation(metrics, groups, label_sets):
    """Independent check: enumerate all allowed rankings rather than mean gains."""
    checked, largest_error = 0, 0.0
    for row in metrics:
        rows = groups[(row['scenario_id'], row['method'])]
        labels = {r['place_id']: label_sets[row['label_set']][(row['scenario_id'], r['place_id'])] for r in rows}
        if any(v is None for v in labels.values()):
            assert row['ndcg_tie_expected'] == row['ndcg_id_fallback'] == 'N/A'
            continue
        gains = {p:2**g-1 for p,g in labels.items()}
        ideal = sum(g/math.log2(i+2) for i,g in enumerate(sorted(gains.values(),reverse=True)[:row['k']]))
        if not ideal:
            assert row['ndcg_tie_expected'] == row['ndcg_id_fallback'] == 'N/A'
            continue
        bands = defaultdict(list)
        for r in rows:
            bands[float(r['score'])].append(r['place_id'])
        values = []
        for chosen in itertools.product(*(list(itertools.permutations(bands[s])) for s in sorted(bands,reverse=True))):
            order = [p for band in chosen for p in band]
            values.append(sum(gains[p]/math.log2(i+2) for i,p in enumerate(order[:row['k']]))/ideal)
        error = abs(statistics.mean(values)-row['ndcg_tie_expected'])
        largest_error = max(largest_error,error)
        assert error < 1e-12
        id_order = sorted(rows,key=lambda r:int(r['rank']))
        manual = sum(gains[r['place_id']]/math.log2(i+2) for i,r in enumerate(id_order[:row['k']]))/ideal
        assert abs(manual-row['ndcg_id_fallback']) < 1e-12
        checked += 1
    return {'ndcg_rows_checked':checked,'max_absolute_error':largest_error,'method':'Enumerate every within-exact-score-band permutation; mean explicit DCGs / ideal DCG; separately verify deterministic rank DCG','tolerance':1e-12}


def table(fields, rows):
    def safe(v):
        return str(v).replace('|', '\\|').replace('\n', ' ')
    return ['| ' + ' | '.join(fields) + ' |', '| ' + ' | '.join(['---'] * len(fields)) + ' |'] + ['| ' + ' | '.join(safe(fmt(x)) for x in r) + ' |' for r in rows]


def main(destination):
    destination.mkdir(exist_ok=False)
    source_paths = [PACKAGE / f'evaluator_{label}_English.csv' for label in 'AB']
    preserved = [p for p in FROZEN.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    preserved += [p for p in CODE.glob('*') if p.is_file()] + source_paths
    original_manifest = verify_frozen(FROZEN)
    preserved += [Path(i['original_path']) for i in original_manifest['inputs'] if Path(i['original_path']).exists()]
    before = {str(p): sha(p) for p in preserved}
    scores = read(FROZEN / 'results/scores_ranks.csv')
    expected_pairs = {(r['scenario_id'], r['place_id']) for r in scores}
    groups = defaultdict(list)
    for row in scores:
        groups[(row['scenario_id'], row['method'])].append(row)
    judgments, sources, validation = {}, {}, {}
    for label, rid, source in zip('AB', ['R1', 'R2'], source_paths):
        rows = read(source)
        template = read(FROZEN / f'results/annotation_{rid}.csv')
        expected = {r['annotation_id']: r for r in template}
        errors = []
        pairs = {(r['scenario_id'], r['place_id']) for r in rows}
        if len(rows) != 38 or len(pairs) != 38 or pairs != expected_pairs:
            errors.append('Expected precisely 38 unique frozen scenario/place pairs')
        if len({r['annotation_id'] for r in rows}) != 38 or {r['annotation_id'] for r in rows} != set(expected):
            errors.append('Annotation identity set differs')
        with zipfile.ZipFile(PACKAGE / f'evaluator_{label}_English_packet.zip') as packet:
            members = packet.namelist()
            blank = list(csv.DictReader(io.StringIO(packet.read(next(n for n in members if n.endswith('.csv'))).decode('utf-8-sig'))))
        blank_by_id = {r['annotation_id']: r for r in blank}
        for row in rows:
            aid = row['annotation_id']
            for key, value in row.items():
                if key not in {'relevance_0_3', 'insufficient_evidence_YES_NO', 'notes'} and value != blank_by_id.get(aid, {}).get(key):
                    errors.append(f'Changed English prompt/evidence/identifier: {aid}:{key}')
            for field in expected.get(aid, {}).keys() - {'relevance_0_3', 'insufficient_evidence_YES_NO', 'notes'}:
                if row.get(field) != expected[aid][field]:
                    errors.append(f'Changed frozen Korean field: {aid}:{field}')
            flag, grade = row['insufficient_evidence_YES_NO'].strip().upper(), row['relevance_0_3'].strip()
            if not ((flag == 'YES' and grade == '') or (flag == 'NO' and grade in {'0', '1', '2', '3'})):
                errors.append(f'Invalid grade/evidence combination: {aid}')
        validation[label] = {'source_path': str(source), 'sha256': sha(source), 'row_count': len(rows), 'unique_pairs': len(pairs), 'errors': errors, 'structural_pass': not errors, 'evidence_flags': dict(Counter(r['insufficient_evidence_YES_NO'] for r in rows)), 'packet_members': members, 'immutable_fields_match_blank_packet_and_frozen_template': not errors}
        if errors:
            write_json(destination / 'evaluation_validation.json', {'status': 'ANNOTATION_VALIDATION_FAILED', 'sheets': validation})
            raise ValueError(errors)
        adapter = destination / f'validated_adapter_{rid}.csv'
        write_csv(adapter, [{k: r[k] for k in template[0]} for r in rows], list(template[0]))
        judgments[rid] = audit_ratings(adapter, FROZEN / f'results/annotation_{rid}.csv', rid)
        sources[rid] = {(r['scenario_id'], r['place_id']): r for r in rows}
    assert judgments['R1'].keys() == judgments['R2'].keys()
    print('Annotation validation: both sheets PASS structural checks. PROTOCOL FAILURE: one human completed both; independent-rater requirement unmet. Collection date unknown. Metrics below are descriptive only.', flush=True)
    provenance = {'origin': 'human_single_rater_two_sheets', 'independent': False, 'method_blinded': True, 'rater_ids': ['R1', 'R2'], 'actual_human_rater_count': 1, 'rater_ids_note': 'Legacy sheet identifiers, not two distinct humans', 'collection_date': None, 'collection_description': 'User confirmed the available evaluator_B_English.csv is the updated file, and one rater completed both sheets without knowledge of model identities, scores, rankings or conclusions. No collection date or session spacing was supplied.', 'user_statement': 'It is the updated file. One rater did both, but without any knowledge on model identities, scores, rankings and conclusions.'}
    write_json(destination / 'collection_provenance.json', provenance)
    # Invoke unchanged CLI with truthful provenance. Preserve the actual rejection.
    cli = [sys.executable, str(CODE / 'evaluate.py'), '--rater1', str(destination / 'validated_adapter_R1.csv'), '--rater2', str(destination / 'validated_adapter_R2.csv'), '--human-evidence', str(destination / 'collection_provenance.json'), '--destination', str(destination / 'original_evaluator_output')]
    original = subprocess.run(cli, cwd=ROOT, capture_output=True, text=True)
    (destination / 'original_evaluator_stdout.txt').write_text(original.stdout, encoding='utf-8')
    (destination / 'original_evaluator_stderr.txt').write_text(original.stderr, encoding='utf-8')
    assert original.returncode != 0 and not (destination / 'original_evaluator_output').exists(), 'Unexpected independent-human evaluation acceptance'
    average_grades = {k: (judgments['R1'][k] + judgments['R2'][k]) / 2 if judgments['R1'][k] is not None and judgments['R2'][k] is not None else None for k in judgments['R1']}
    metrics = []
    # This loop preserves the original evaluate.evaluate calculations and output schema.
    for label_set, labels in [('R1', judgments['R1']), ('R2', judgments['R2']), ('mean_R1_R2', average_grades)]:
        for (sid, method), rows in sorted(groups.items()):
            available = {r['place_id']: labels[(sid, r['place_id'])] for r in rows if labels[(sid, r['place_id'])] is not None}
            missing = [r['place_id'] for r in rows if r['place_id'] not in available]
            k = min(3, len(rows))
            pair, pair_count = pair_agreement(rows, available)
            tie = ndcg(rows, available, k) if not missing else None
            fallback = ndcg(rows, available, k, False) if not missing else None
            metrics.append({'label_set': label_set, 'scenario_id': sid, 'method': method, 'candidate_count': len(rows), 'k': k, 'judged_places': len(available), 'insufficient_evidence_place_ids': '|'.join(missing), 'ndcg_tie_expected': tie if tie is not None else 'N/A', 'ndcg_id_fallback': fallback if fallback is not None else 'N/A', 'ndcg_exclusion': 'insufficient evidence; keep full identity set' if missing else 'zero ideal DCG' if tie is None else '', 'pairwise_preference_agreement': pair if pair is not None else 'N/A', 'human_strict_pair_count': pair_count})
    write_csv(destination / 'per_scenario_metrics.csv', metrics)
    raw_dir = destination / 'descriptive_metric_function_outputs'
    raw_dir.mkdir()
    write_csv(raw_dir / 'relevance_metrics.csv', metrics)
    aligned = []
    for key in sorted(judgments['R1']):
        a, b = judgments['R1'][key], judgments['R2'][key]
        ra, rb = sources['R1'][key], sources['R2'][key]
        joint = a is not None and b is not None
        aligned.append({'scenario_id': key[0], 'place_id': key[1], 'place_name': ra['place_name'], 'sheet_A_annotation_id': ra['annotation_id'], 'sheet_B_annotation_id': rb['annotation_id'], 'sheet_A_presentation_order': ra['presentation_order'], 'sheet_B_presentation_order': rb['presentation_order'], 'sheet_A_rating': a if a is not None else '', 'sheet_B_rating': b if b is not None else '', 'sheet_A_insufficient_evidence': ra['insufficient_evidence_YES_NO'], 'sheet_B_insufficient_evidence': rb['insufficient_evidence_YES_NO'], 'jointly_judged': joint, 'exact_agreement': a == b if joint else '', 'absolute_disagreement': abs(a-b) if joint else '', 'sheet_A_notes': ra['notes'], 'sheet_B_notes': rb['notes'], 'interpretation': 'between sheets completed by one human; not inter-rater reliability'})
    write_csv(destination / 'inter_rater_agreement.csv', aligned)
    joint = [(r['sheet_A_rating'], r['sheet_B_rating']) for r in aligned if r['jointly_judged']]
    n = len(joint)
    ca, cb = Counter(a for a,b in joint), Counter(b for a,b in joint)
    observed = sum((a-b)**2/9 for a,b in joint) / n
    expected = sum(ca[a]*cb[b]*(a-b)**2/9 for a in range(4) for b in range(4)) / n**2
    kappa = 1-observed/expected if expected else None
    try:
        from sklearn.metrics import cohen_kappa_score
        cross_kappa = float(cohen_kappa_score([a for a,b in joint], [b for a,b in joint], labels=[0,1,2,3], weights='quadratic'))
        assert abs(cross_kappa-kappa) < 1e-12
    except ImportError:
        cross_kappa = 'sklearn unavailable; direct observed/expected weighted-disagreement calculation used'
    agreement = {'interpretation': 'between-sheet consistency from one human, not inter-rater reliability; repeat-session reliability also unestablished because timing/order/history unknown', 'jointly_judged': n, 'excluded_for_either_insufficient': len(aligned)-n, 'exact_count': sum(a==b for a,b in joint), 'exact_fraction': sum(a==b for a,b in joint)/n, 'quadratic_weighted_kappa': kappa, 'kappa_sklearn_crosscheck': cross_kappa, 'absolute_disagreement_counts': {str(d): sum(abs(a-b)==d for a,b in joint) for d in range(4)}, 'insufficient_counts': {label: sum(v is None for v in judgments[rid].values()) for label,rid in [('A','R1'),('B','R2')]}, 'kappa_policy': 'Labels 0,1,2,3; weight squared difference/9; 1-observed weighted disagreement/expected marginal weighted disagreement. Exclude any pair with either insufficient rating.', 'mean_absolute_difference': statistics.mean(abs(a-b) for a,b in joint)}
    write_json(destination / 'between_sheet_agreement_summary.json', agreement)
    ties = {m: sum(float(a['score'])==float(b['score']) for (sid, method),rows in groups.items() if method==m for a,b in itertools.combinations(rows,2)) for m in 'ABCD'}
    comparison = []
    for m in 'ABCD':
        by_label = {lab: [r for r in metrics if r['method']==m and r['label_set']==lab] for lab in ['R1','R2','mean_R1_R2']}
        av = lambda lab, field: mean([r[field] for r in by_label[lab]])
        row = {'method': m, 'mean_tie_expected_ndcg': mean([av('R1','ndcg_tie_expected'),av('R2','ndcg_tie_expected')]), 'mean_id_fallback_ndcg': mean([av('R1','ndcg_id_fallback'),av('R2','ndcg_id_fallback')]), 'sheet_A_tie_expected_ndcg': av('R1','ndcg_tie_expected'), 'sheet_B_tie_expected_ndcg': av('R2','ndcg_tie_expected'), 'sheet_A_id_fallback_ndcg': av('R1','ndcg_id_fallback'), 'sheet_B_id_fallback_ndcg': av('R2','ndcg_id_fallback'), 'mean_pairwise_agreement': mean([av('R1','pairwise_preference_agreement'),av('R2','pairwise_preference_agreement')]), 'sheet_A_pairwise_agreement': av('R1','pairwise_preference_agreement'), 'sheet_B_pairwise_agreement': av('R2','pairwise_preference_agreement'), 'mean_grade_tie_expected_ndcg_original_script_definition': av('mean_R1_R2','ndcg_tie_expected'), 'mean_grade_id_fallback_ndcg_original_script_definition': av('mean_R1_R2','ndcg_id_fallback'), 'mean_grade_pairwise_original_script_definition': av('mean_R1_R2','pairwise_preference_agreement'), 'sheet_A_evaluated_ndcg_scenarios': sum(r['ndcg_tie_expected']!='N/A' for r in by_label['R1']), 'sheet_B_evaluated_ndcg_scenarios': sum(r['ndcg_tie_expected']!='N/A' for r in by_label['R2']), 'mean_grade_evaluated_ndcg_scenarios': sum(r['ndcg_tie_expected']!='N/A' for r in by_label['mean_R1_R2']), 'exactly_tied_pairs': ties[m], 'actual_human_rater_count': 1}
        for lab in ['R1','R2']:
            applicable = [r for r in by_label[lab] if r['pairwise_preference_agreement']!='N/A']
            count = sum(r['human_strict_pair_count'] for r in applicable)
            row[lab+'_strict_human_pairs'] = count
            row[lab+'_pooled_pairwise_agreement'] = sum(float(r['pairwise_preference_agreement'])*r['human_strict_pair_count'] for r in applicable)/count if count else 'N/A'
        comparison.append(row)
    write_csv(destination / 'method_comparison.csv', comparison)
    tests_cmd = [sys.executable,'-m','unittest','discover','-s',str(CODE),'-p','test_experiment.py','-v']
    tests = subprocess.run(tests_cmd,cwd=ROOT,capture_output=True,text=True)
    (destination/'validation_tests.txt').write_text(tests.stdout+tests.stderr,encoding='utf-8')
    assert tests.returncode == 0
    verify_frozen(FROZEN)
    assert all(sha(p)==v for p,v in before.items()), 'Protected input changed'
    assert len(metrics)==144 and len(aligned)==38
    independent_check = verify_by_permutation(metrics, groups, {'R1':judgments['R1'],'R2':judgments['R2'],'mean_R1_R2':average_grades})
    write_json(destination/'independent_metric_verification.json',independent_check)
    assert read(raw_dir/'relevance_metrics.csv') == read(destination/'per_scenario_metrics.csv')
    best = max(comparison,key=lambda r:r['mean_tie_expected_ndcg'])['method']
    validation_doc = {'status':'DESCRIPTIVE_METRICS_COMPLETE_INDEPENDENT_RATER_PROTOCOL_FAILED','sheets':validation,'source_B_revision_resolution':'User confirmed available evaluator_B_English.csv is the updated file; supersedes earlier requested filename (1).','collection_provenance':provenance,'validation_failures_before_metrics':['Two independent humans required by original protocol; actual human count is one','Collection date not supplied'],'semantic_rubric_review':['S01 gamjatang: sheet A grade 2 with note describing only broad Korean-restaurant relation may conflict with rubric grade 2 requirement of direct partial core preference evidence; preserved without adjudication. Sheet B grade 1.','S05 Daejeo camping: sheet B note says no overnight-stay information but assigns 2/NO. Relevance to partial camping experience versus missing overnight evidence requires human clarification; not automatically treated as missing.'],'agreement':agreement,'exact_ties':ties,'parameters':{'k':'min(3, actual frozen group size)','gain':'2**grade-1','discount':'1/log2(rank+1)','primary':'equal-weight scenario macro mean of tie-expected NDCG, then arithmetic mean of the two sheet-specific macro means','pairwise_macro':'mean over scenarios with strict human preferences, then mean of sheet means; pooled-pair diagnostics also reported','tie_policy':'unchanged original exact floating score bands, expected gain across full band including cutoff crossings','exclusions':'whole scenario NDCG excluded on any insufficient evidence; zero ideal DCG N/A; pairwise uses available unequal-grade pairs','mean_R1_R2':'Original script definition: score metrics after averaging grades; diagnostic only, no consensus ground truth'},'original_evaluator':{'command':cli,'exit_code':original.returncode,'rejection':'Truthful origin/independent fields do not meet required two-independent-human assertions','stdout':'original_evaluator_stdout.txt','stderr':'original_evaluator_stderr.txt','successful_original_output_files':[]},'descriptive_wrapper':{'file':str(Path(__file__).resolve()),'sha256':sha(__file__),'metric_functions_imported_from':str(CODE/'evaluate.py'),'formula_changes':False,'provenance_guard_bypassed_in_original':False,'note':'Original CLI rejection preserved. Separate descriptive wrapper applies unchanged ndcg, pair_agreement, audit_ratings functions; outputs are not original CLI outputs.'},'tests':{'command':tests_cmd,'exit_code':tests.returncode,'checks':['7 original experiment tests passed','144 scenario/method/label rows','38 aligned pairs','raw and deliverable metric CSV values identical','manual quadratic kappa vs sklearn when available','verify_frozen before and after','all protected/input hashes unchanged']},'created_utc':datetime.now(timezone.utc).isoformat(),'client_date_korea':'2026-10-29','collection_date':None,'environment':{'python':sys.version,'python_executable':sys.executable,'platform':platform.platform()},'code_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),'protected_hashes_before_and_after':before,'protected_files_unchanged':True,'input_and_code_hashes':{str(p):sha(p) for p in [*source_paths,CODE/'evaluate.py',CODE/'run.py',CODE/'prepare.py',FROZEN/'manifest.json',FROZEN/'config.json',FROZEN/'scenarios.json',FROZEN/'results/scores_ranks.csv',FROZEN/'results/ANNOTATION_RUBRIC.md',PACKAGE/'ENGLISH_RUBRIC.md',PACKAGE/'evaluator_A_English_packet.zip',PACKAGE/'evaluator_B_English_packet.zip']},'reproduction_command':f'.venv/Scripts/python.exe notebooks/experiments/human_evaluation_phase1_20261029.py --destination "{destination}"','best_descriptive_primary_method':best}
    validation_doc['independent_metric_verification'] = independent_check
    write_json(destination/'evaluation_validation.json',validation_doc)
    write_json(raw_dir/'evaluation_manifest.json',{'provenance':provenance,'generator':'Separate descriptive wrapper; not successful original evaluate.py output','metric_definition':validation_doc['parameters'],'original_evaluator_sha256':sha(CODE/'evaluate.py'),'source_sha256':{str(p):sha(p) for p in source_paths},'rows':len(metrics)})
    build_report(destination, comparison, metrics, aligned, agreement, groups, sources, best)
    write_json(destination/'output_hashes.json',{str(p.relative_to(destination)):sha(p) for p in destination.rglob('*') if p.is_file()})
    print('Between-sheet agreement (one human):',agreement['exact_count'],'/',n,'kappa=',kappa)
    for row in comparison:
        print(row['method'],'tie-expected NDCG=',row['mean_tie_expected_ndcg'],'ID fallback=',row['mean_id_fallback_ndcg'],'pairwise macro=',row['mean_pairwise_agreement'])
    print('Best descriptive primary:',best,'; no production implementation change justified. Outputs:',destination)


def build_report(dest, comparison, metrics, aligned, agreement, groups, sources, best):
    cm = {r['method']:r for r in comparison}
    scenarios = {s['scenario_id']:s for s in json.loads((FROZEN/'scenarios.json').read_text(encoding='utf-8'))}
    ix = {(r['label_set'],r['scenario_id'],r['method']):r for r in metrics}
    doc = ['# TravelSync Phase 1 human relevance evaluation', '', '## Result and scope', '',
           f'Method {best} has the highest descriptive mean tie-expected NDCG across the two supplied sheets. Both sheets pass structural validation. However, one human completed both: the intended independent two-rater evaluation has not been completed. These results describe alignment with one person’s two rating sets, not independent-rater agreement or validated production performance.', '',
           'The user confirmed that evaluator_B_English.csv is the updated file intended by the request, and that the person had no knowledge of model identities, scores, rankings or conclusions. This clarification resolves the filename issue recorded in the earlier preflight report. The collection date, session spacing, order of sheet completion and access to earlier judgments were not supplied. The analysis date is 2026-10-29 (Asia/Seoul); it is not asserted to be the collection date.', '',
           '## Experiment and methods', '',
           'The frozen 2026-10-28 experiment uses 12 synthetic Korean preference scenarios, 19 eligible POIs in six original-feature groups, 38 scenario–POI pairs and 152 frozen scores. Candidate membership, texts, model scores, exact tie bands and ranks were preserved. Method A is the original ten-dimensional binary-feature scorer; B is character 2/3-gram TF-IDF on structured metadata; C adds description summaries; D uses pretrained paraphrase-multilingual-MiniLM-L12-v2 embeddings. No model was refit, no embeddings were recomputed and no production, GFAR, application or database files were changed.', '',
           'Primary metric: NDCG@K with K=min(3, actual frozen candidate-group size), gain 2^grade−1, and discount 1/log2(position+1). Expected gain is averaged over the full exact model-score band, including bands crossing K. ID fallback NDCG is a separate diagnostic. Any insufficient evidence excludes the full scenario from NDCG for the relevant label set; zero ideal DCG is N/A. Pairwise agreement includes only strict human preferences: correct=1, exact model tie=0.5, reverse=0. No strict human pairs gives N/A.', '',
           'Each sheet is evaluated separately. Summary “mean” is the arithmetic mean of the two sheet-specific scenario macro means. The original script also computes metrics on arithmetic mean grades (mean_R1_R2); those values are reported separately, are not the arithmetic mean of metrics and do not represent adjudicated consensus. The two sheets do not double the independent human sample size.', '',
           'Pairwise summary is a macro mean across applicable scenarios, then the mean of those sheet means. The comparison CSV also includes pooled strict-pair agreement and pair counts. These weight scenarios differently; neither should silently replace the other.', '',
           '## Validation and provenance', '',
           'Both sheets contain exactly 38 unique stable (scenario_id, place_id) pairs, exactly matching the frozen scenario/candidate set. Annotation IDs, original Korean prompts/evidence and presentation fields match the original templates. English prompt/evidence fields match the blank packaged English sheets. All supplied grades are integers 0–3 with explicit NO flags; both sheets have zero insufficient-evidence rows. No grade-plus-YES conflict exists. All notes, ratings, identifiers and row order were preserved in original-schema adapters, and the English source sheets remain unchanged.', '',
           'The blank packets contain only the corresponding English sheet and rubric. Immutable sheet fields match those packets and contain no model-score/ranking columns. The original rubric and packet provenance support intended blinding; the user’s explicit declaration supplies evidence of actual absence of knowledge of models/results. That declaration does not supply a second human or prove independent repeated judgments. Ratings were never generated from model scores by this analysis.', '',
           'Protocol failure was reported before metrics: one person completed both sheets, contradicting the independent-two-rater requirement. The unchanged evaluate.py was invoked using truthful provenance and rejected origin=human_single_rater_two_sheets / independent=false. Its actual stdout/stderr are preserved. No successful original CLI metric files exist. A separate minimal descriptive wrapper imports unchanged audit_ratings, ndcg and pair_agreement, and retains the original metric loop and exclusions. It does not alter the original evaluator or submit false declarations. Files under descriptive_metric_function_outputs are wrapper outputs, explicitly labelled as such.', '',
           '### Unresolved interpretation issues', '',
           'S01: sheet A assigns gamjatang grade 2 while its note says the rationale is that it is a Korean restaurant and not directly related to tteokbokki. The rubric requires direct evidence for part of the core preference for grade 2; a broad restaurant connection may instead fit grade 1. Sheet B assigns 1. This possible semantic conflict is preserved and flagged, with no automatic relabelling or favourable sensitivity adjustment.', '',
           'S05: sheet B assigns Daejeo Camping Ground 2/NO while noting “No info about overnight stay.” This may reflect partial support for camping with an unconfirmed detail, but the rater should clarify whether the missing detail actually prevented judgment under the insufficient-evidence rule. The submitted rating remains unchanged. These issues affect interpretation even though the files are structurally valid.', '',
           '## Agreement between the sheets', '',
           f'Exact agreement is {agreement["exact_count"]}/{agreement["jointly_judged"]} ({agreement["exact_fraction"]:.1%}). Quadratic weighted Cohen kappa is {agreement["quadratic_weighted_kappa"]:.6f}. These match the earlier approximate numerical cross-checks, but must be interpreted as consistency between two sheets completed by one human. Inter-rater reliability is unavailable. A formal test–retest reliability claim is also unsupported because collection timing, order and prior-sheet access are unknown.', '',
           f'Absolute disagreements: 1 point={agreement["absolute_disagreement_counts"]["1"]}, 2 points={agreement["absolute_disagreement_counts"]["2"]}, 3 points={agreement["absolute_disagreement_counts"]["3"]}. Insufficient evidence: A=0, B=0. Kappa excludes pairs with either insufficient rating; excluded here=0, eligible=38. Quadratic disagreement weights are (a−b)^2/9 with fixed categories 0–3; kappa=1−observed/expected weighted disagreement from the two marginal grade distributions.', '',
           'All disagreement cases are below. A/B here denote sheet labels, not distinct people. Notes are reproduced verbatim.', '']
    doc += table(['Scenario','POI ID','POI','Sheet A','Sheet B','A notes','B notes'], [[r['scenario_id'],r['place_id'],r['place_name'],r['sheet_A_rating'],r['sheet_B_rating'],r['sheet_A_notes'],r['sheet_B_notes']] for r in aligned if r['jointly_judged'] and not r['exact_agreement']])
    doc += ['', '## Method comparison', '', 'Each summary mean below averages the two sheet-specific scenario means. A higher value is better for all performance metrics. Exact-tie counts are ranking diagnostics, recalculated from frozen scores.', '']
    fields = [('Mean tie-expected NDCG@K','mean_tie_expected_ndcg'),('Mean ID fallback NDCG@K','mean_id_fallback_ndcg'),('Sheet A tie-expected NDCG@K','sheet_A_tie_expected_ndcg'),('Sheet B tie-expected NDCG@K','sheet_B_tie_expected_ndcg'),('Mean model–human pairwise (scenario macro)','mean_pairwise_agreement'),('Evaluated NDCG scenarios per sheet','sheet_A_evaluated_ndcg_scenarios'),('Exactly tied pairs','exactly_tied_pairs'),('Mean-grade tie-expected NDCG (original script definition)','mean_grade_tie_expected_ndcg_original_script_definition'),('Mean-grade ID fallback NDCG','mean_grade_id_fallback_ndcg_original_script_definition'),('Mean-grade pairwise agreement','mean_grade_pairwise_original_script_definition')]
    doc += table(['Metric','A','B','C','D'], [[title]+[cm[m][field] for m in 'ABCD'] for title,field in fields])
    doc += ['', '### Pair counts and exclusions', '']
    doc += table(['Sheet','Method','Applicable pairwise scenarios','Strict human pair count','Pooled pairwise agreement','NDCG excluded scenarios'], [[lab,m,sum(r['pairwise_preference_agreement']!='N/A' for r in metrics if r['label_set']==rid and r['method']==m),cm[m][rid+'_strict_human_pairs'],cm[m][rid+'_pooled_pairwise_agreement'],sum(r['ndcg_tie_expected']=='N/A' for r in metrics if r['label_set']==rid and r['method']==m)] for lab,rid in [('A','R1'),('B','R2')] for m in 'ABCD'])
    doc += ['', '## Per-scenario metrics', '', 'All scenario counts, K values, pair counts and exclusion reasons are retained in per_scenario_metrics.csv. The following table displays A/B-sheet tie-expected NDCG and pairwise agreement as paired values. It includes every scenario without pooling human grades.', '']
    scenario_table = []
    for sid in sorted(scenarios):
        row = [sid,len(groups[(sid,'A')]),min(3,len(groups[(sid,'A')]))]
        for m in 'ABCD':
            row += [' / '.join(fmt(ix[(lab,sid,m)]['ndcg_tie_expected']) for lab in ['R1','R2']), ' / '.join(fmt(ix[(lab,sid,m)]['pairwise_preference_agreement']) for lab in ['R1','R2'])]
        scenario_table.append(row)
    doc += table(['Scenario','Candidates','K']+[f'{m} {v} A/B' for m in 'ABCD' for v in ['NDCG','pairwise']],scenario_table)
    doc += ['', '### Differences among lexical and semantic methods', '', 'Deltas below compare scenario tie-expected NDCG after averaging the two sheet-specific results. Win/tie/loss counts count scenarios, not independent people, and are descriptive. Exact equality is a tie. The frozen ranking_differences.csv remains the source for strict rank reversals and tie-status changes.', '']
    for left,right in [('C','B'),('D','B'),('D','C')]:
        deltas = [mean([ix[(lab,sid,left)]['ndcg_tie_expected'] for lab in ['R1','R2']])-mean([ix[(lab,sid,right)]['ndcg_tie_expected'] for lab in ['R1','R2']]) for sid in sorted(scenarios)]
        doc += [f'{left} versus {right}: mean delta {statistics.mean(deltas):+.6f}; scenarios higher/equal/lower = {sum(d>0 for d in deltas)}/{sum(d==0 for d in deltas)}/{sum(d<0 for d in deltas)}.', '']
    doc += ['C exceeds B in only one scenario (S07), ties in eight and is lower in three. Its higher overall mean is therefore driven by the size of the S07 improvement, rather than broad scenario-wise dominance. B has the highest ID fallback NDCG, but its favourable fallback within tied bands is precisely why that diagnostic is kept separate from the primary metric.', '', '## Case analysis', '']
    for sid in ['S01','S07','S08']:
        doc += [f'### {sid}', '', scenarios[sid]['preference_ko'], '']
        rows = []
        for r in sorted(groups[(sid,'A')],key=lambda x:x['place_id']):
            pid = r['place_id']
            rows.append([pid,r['place_name'],sources['R1'][(sid,pid)]['relevance_0_3'],sources['R2'][(sid,pid)]['relevance_0_3']]+[next(q['rank'] for q in groups[(sid,m)] if q['place_id']==pid) for m in 'ABCD'])
        doc += table(['POI ID','Candidate','Sheet A grade','Sheet B grade','A rank','B rank','C rank','D rank'],rows)
        doc += ['', 'Rank columns display the frozen ID fallback within exact ties. Method A ties the whole group in every scenario. ' + ('Method B also ties all six candidates at score zero here; its apparent first place does not indicate preference.' if sid=='S07' else ''), '']
        for m in 'BCD':
            ranked = sorted(groups[(sid,m)],key=lambda r:int(r['rank']))
            doc += [f'{m} order: ' + ' > '.join(r['place_name'] for r in ranked) + ('. This order is only ID fallback for the all-zero B tie.' if sid=='S07' and m=='B' else '.'), '']
        if sid=='S01':
            doc += ['Both sheets prefer 빨간떡볶이 (3/3) over 맛나감자탕 부산경남지사 (2/1). B and C order them accordingly; D reverses that strict preference in both sheets, giving pairwise agreement zero for this scenario. The original inputs explicitly identify tteokbokki in the snack restaurant metadata, while the other place describes gamjatang/neck-bone soup. The frozen output establishes the semantic ranking error, but does not establish why the embedding model made it. The grade-2 rationale in sheet A remains a rubric interpretation concern.', '']
        elif sid=='S07':
            doc += ['Both sheets give 성지곡산림욕장 grade 3; its frozen summary explicitly mentions hinoki cypress and Japanese cedar. B has zero lexical overlap and all six scores equal zero. C and D rank 성지곡산림욕장 first, supported by both sheets. Compare the scenario NDCG values above to assess the full list: top-place agreement alone does not show that every lower ordering improved. The sheets disagree on 동백섬 (1/2), illustrating uncertainty in intermediate relevance. Descriptions support the specific tree/trail match, while current access or operating conditions were not verified.', '']
            doc += ['For the complete top-K list, C improves on B in both sheets: NDCG A/B is 0.782363/0.903779 for C versus 0.546771/0.615118 for B. D reaches 0.903779 in both sheets, exceeding C on sheet A and tying C on sheet B. C still places lower-graded coastal candidates ahead of the two grade-2 forest/mountain candidates on sheet A; adding descriptions did not eliminate ranking errors.', '']
        else:
            doc += ['C ranks 부산 송도해수욕장 above 광안리해수욕장; B and D reverse that order. Sheet A gives both beaches grade 3, so it supports no strict preference between them. Sheet B gives Songdo 3 and Gwangalli 2, supporting the C ordering for this beach pair. This does not imply that C has the best full-scenario list: the position of 동백섬 and other candidates also contributes to NDCG/pairwise results. The scenario asks for white sand and a beach park; the provided frozen descriptions support the rater’s comparison, but no current-condition or visitor-satisfaction evidence was collected.', '']
            doc += ['The supplied Songdo summary explicitly mentions a sandy beach and beach park; the Gwangalli summary identifies a beach and travel-course listing without explicitly confirming a beach park. No absence of a park is inferred from that omission. Full-scenario mean NDCG is B=0.964488, C=0.913180 and D=0.928976: C’s supported Songdo/Gwangalli ordering does not make its whole list best. Its rank-3 forest candidate displaces the grade-2 coastal walk at 동백섬.', '']
        doc += ['Evidence limitation: supplied summaries and provenance URLs were used as frozen; no site visit, telephone confirmation or live-source refresh was performed. English translation was AI-assisted and not independently bilingual-validated. No additional place attributes or model-causality claims are inferred.', '']
    doc += ['## Hypothesis assessment and recommendations', '',
            f'1. Enriched information and relevance: descriptive primary means are A={cm["A"]["mean_tie_expected_ndcg"]:.6f}, B={cm["B"]["mean_tie_expected_ndcg"]:.6f}, C={cm["C"]["mean_tie_expected_ndcg"]:.6f}, D={cm["D"]["mean_tie_expected_ndcg"]:.6f}. Differences are alignment evidence for the one annotator’s sheets beyond the verified tie reductions. They do not establish improvement for independent users or causal effects of enrichment.', '',
            f'2. Descriptions versus metadata: C−B mean tie-expected NDCG is {cm["C"]["mean_tie_expected_ndcg"]-cm["B"]["mean_tie_expected_ndcg"]:+.6f}; mean macro pairwise difference is {cm["C"]["mean_pairwise_agreement"]-cm["B"]["mean_pairwise_agreement"]:+.6f}. S07 offers a specific evidence-supported benefit; overall and per-scenario differences must both be considered. No significance claim is made.', '',
            f'3. Sentence Transformer versus lexical methods: D−B={cm["D"]["mean_tie_expected_ndcg"]-cm["B"]["mean_tie_expected_ndcg"]:+.6f}, D−C={cm["D"]["mean_tie_expected_ndcg"]-cm["C"]["mean_tie_expected_ndcg"]:+.6f} on the primary metric. D’s zero exact ties do not establish superior relevance; S01 is an explicit counterexample.', '',
            f'4. Best descriptive match: {best} ranks first under the prespecified tie-expected NDCG aggregated as stated above. Treat numeric differences as descriptive; two sheets from one person cannot establish population superiority. Check individual-sheet and mean-grade rows rather than treating them as independent replications.', '',
            '5. Production decision: this sample does not justify choosing or integrating a production model. Keep the existing implementation unchanged.', '',
            '6. Generalization: scenarios were synthetically authored to distinguish a deliberately selected enriched subset. Small feature-matched groups limit ranking difficulty and cover few real alternatives. Repeated POIs and grouped scenarios are dependent. More detailed text is supplied to B/C/D than the ten-feature production baseline, and the baseline cannot express the exact free-text request; this evaluates a different information/interface setting as well as scorer behavior. One human, translated annotation material, unverified current source conditions, rubric interpretation issues and absent collection dates further limit conclusions. Minor descriptive differences do not imply statistical significance.', '',
            '7. Before GFAR integration: obtain a genuinely independent blinded second human annotation and adjudicate rubric issues while preserving originals; collect held-out real member preferences and broader verified POI evidence; evaluate candidate recall separately from ranking; calibrate similarity-derived scores before treating them as comparable member utilities; and compare group relevance/satisfaction, minority-member outcomes, list coverage, latency and missing-data handling in an isolated evaluation. Do not reuse the current sheets for model tuning and then present them as a held-out test.', '',
            '## Reproducibility and files', '',
            'evaluation_validation.json records source/code hashes, truthful provenance, configuration, code revision, environment, exact commands, CLI failure and preservation checks. All seven original unit tests pass. The frozen verifier passes before and after; original inputs, canonical data, protected tracked files, source rating sheets and original experimental artifacts remain unchanged. Original scores are read only. The wrapper creates an unused destination and refuses overwrite. output_hashes.json hashes the completed result files.', '',
            'Primary deliverables: human_evaluation_report.md; method_comparison.csv; per_scenario_metrics.csv (144 rows, with R1/R2 denoting legacy sheet IDs and mean_R1_R2 the diagnostic mean-grade definition); inter_rater_agreement.csv (38 aligned rows, explicitly labelled one-human between-sheet consistency); evaluation_validation.json. Supporting files: validated_adapter_R1.csv and validated_adapter_R2.csv; collection_provenance.json; between_sheet_agreement_summary.json; validation_tests.txt; original_evaluator_stdout.txt and original_evaluator_stderr.txt; descriptive_metric_function_outputs/relevance_metrics.csv and evaluation_manifest.json; output_hashes.json. The requested successful original evaluate.py outputs cannot be supplied because the original protocol guard correctly rejects the actual provenance.', '',
            'Rerun the reproduction command in evaluation_validation.json with a fresh --destination. The existing result directory is intentionally not overwritten. No credentials, environment-secret values or private repository settings are included.']
    (dest/'human_evaluation_report.md').write_text('\n'.join(doc)+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,default=ROOT/'data/generated/evaluation/human_evaluation_phase1_20261029_single_rater')
    main(parser.parse_args().destination.resolve())
