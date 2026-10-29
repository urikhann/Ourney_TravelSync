"""Read-only Phase 1 preflight; never substitute an outdated evaluator sheet."""
import collections
import csv
import hashlib
import io
import itertools
import json
import pathlib
import platform
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXP = ROOT / 'data/generated/evaluation/enriched_poi_round1_20261028'
CODE = ROOT / 'notebooks/experiments/enriched_poi_round1_20261028'
PACKAGE = pathlib.Path(r'C:\Users\Administrator\.codex\.chatgpt-projects\g-p-6a962829564c8191b974c172961babe5\deliverables\phase1_annotation_20261029\english')
DEST = ROOT / 'data/generated/evaluation/human_evaluation_phase1_20261029_preflight'
sys.path.insert(0, str(CODE))
from evaluate import audit_ratings
from run import verify_frozen


def digest(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def read(p):
    with pathlib.Path(p).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        assert len(reader.fieldnames) == len(set(reader.fieldnames))
        rows = list(reader)
        assert all(None not in r and None not in r.values() for r in rows)
        return rows


def main():
    DEST.mkdir(exist_ok=False)
    protected = list(EXP.rglob('*')) + list(CODE.glob('*.py'))
    protected = [p for p in protected if p.is_file() and '__pycache__' not in p.parts]
    before = {str(p): digest(p) for p in protected}
    frozen = verify_frozen(EXP)
    scores = read(EXP / 'results/scores_ranks.csv')
    expected = {(r['scenario_id'], r['place_id']) for r in scores}
    source = PACKAGE / 'evaluator_A_English.csv'
    rows = read(source)
    template_rows = read(EXP / 'results/annotation_R1.csv')
    templates = {r['annotation_id']: r for r in template_rows}
    errors = []
    if len(rows) != 38 or len({(r['scenario_id'], r['place_id']) for r in rows}) != 38:
        errors.append('Expected 38 unique pairs')
    if {(r['scenario_id'], r['place_id']) for r in rows} != expected:
        errors.append('Frozen pair set mismatch')
    for row in rows:
        original = templates.get(row['annotation_id'])
        if original is None:
            errors.append('Unknown annotation ID: ' + row['annotation_id'])
            continue
        for field in original.keys() - {'relevance_0_3', 'insufficient_evidence_YES_NO', 'notes'}:
            if row.get(field) != original[field]:
                errors.append('Frozen field changed: ' + row['annotation_id'] + ':' + field)
        flag, grade = row['insufficient_evidence_YES_NO'].strip().upper(), row['relevance_0_3'].strip()
        if not ((flag == 'YES' and grade == '') or (flag == 'NO' and grade in {'0', '1', '2', '3'})):
            errors.append('Invalid evidence/grade: ' + row['annotation_id'])
    with zipfile.ZipFile(PACKAGE / 'evaluator_A_English_packet.zip') as z:
        csv_name = next(n for n in z.namelist() if n.endswith('.csv'))
        blank = list(csv.DictReader(io.StringIO(z.read(csv_name).decode('utf-8-sig'))))
        blank_by_id = {r['annotation_id']: r for r in blank}
        for row in rows:
            for key, value in row.items():
                if key not in {'relevance_0_3', 'insufficient_evidence_YES_NO', 'notes'} and value != blank_by_id[row['annotation_id']][key]:
                    errors.append('English packet field changed: ' + row['annotation_id'] + ':' + key)
        packet_members = z.namelist()
    adapter = DEST / 'validated_adapter_R1.csv'
    if not errors:
        with adapter.open('x', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(template_rows[0]), extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        audit_ratings(adapter, EXP / 'results/annotation_R1.csv', 'R1')
    groups = collections.defaultdict(list)
    for row in scores:
        groups[(row['scenario_id'], row['method'])].append(row)
    ties = {m: sum(float(a['score']) == float(b['score']) for (sid, method), group in groups.items() if method == m for a, b in itertools.combinations(group, 2)) for m in 'ABCD'}
    tests_command = [sys.executable, '-m', 'unittest', 'discover', '-s', str(CODE), '-p', 'test_experiment.py', '-v']
    tests = subprocess.run(tests_command, cwd=ROOT, capture_output=True, text=True)
    (DEST / 'validation_tests.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
    unchanged = all(p.exists() and digest(p) == value for path, value in before.items() for p in [pathlib.Path(path)])
    state = {
        'status': 'BLOCKED_MISSING_UPDATED_B_AND_COLLECTION_PROVENANCE',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'client_date_korea': '2026-10-29',
        'A': {'path': str(source), 'sha256': digest(source), 'rows': len(rows), 'unique_pairs': len(expected), 'schema_validation_passed': not errors, 'errors': errors, 'evidence_flags': dict(collections.Counter(r['insufficient_evidence_YES_NO'] for r in rows)), 'adapter': str(adapter) if not errors else None},
        'B': {'required_filename': 'evaluator_B_English(1).csv', 'status': 'NOT_FOUND_IN_SEARCHED_LOCATIONS', 'older_file_not_substituted': str(PACKAGE / 'evaluator_B_English.csv')},
        'search_locations': [str(PACKAGE.parent), str(ROOT), r'C:\Users\Administrator\.codex\attachments', r'C:\Users\Administrator\Downloads', r'D:\Backup\Documents\KakaoTalk Downloads'],
        'collection_provenance': {'status': 'MISSING', 'collection_date': None, 'independence': 'NOT_ESTABLISHED', 'method_blinding': 'PACKET_STRUCTURE_SUPPORTS_BLINDING_BUT_ACTUAL_COLLECTION_UNCONFIRMED', 'packet_members_A': packet_members},
        'frozen_checks_passed': True, 'frozen_files_unchanged_after_preflight': unchanged, 'frozen_file_count_hashed': len(before), 'frozen_input_hashes': frozen['inputs'],
        'exact_tied_pairs_verified_from_scores': ties,
        'parameters': {'k': 'min(3, actual candidate-group size)', 'gain': '2**relevance-1', 'discount': '1/log2(position+1)', 'primary': 'tie-expected NDCG', 'insufficient_evidence': 'Exclude entire scenario from NDCG for affected label set; pairwise retains judged strict pairs', 'zero_IDCG': 'N/A', 'mean_R1_R2': 'Original script computes metrics on arithmetic mean grades; not the mean of individual-rater metrics and not adjudicated consensus'},
        'tests': {'command': tests_command, 'exit_code': tests.returncode, 'log': 'validation_tests.txt'},
        'environment': {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform()},
        'code_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'git_status': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
        'code_sha256': {str(p.relative_to(ROOT)): digest(p) for p in [pathlib.Path(__file__), CODE / 'evaluate.py', CODE / 'run.py', CODE / 'prepare.py']},
        'source_sha256': {str(p): digest(p) for p in [EXP / 'results/scores_ranks.csv', EXP / 'scenarios.json', EXP / 'config.json', EXP / 'manifest.json', EXP / 'verification.json', EXP / 'results/ANNOTATION_RUBRIC.md', PACKAGE / 'ENGLISH_RUBRIC.md', PACKAGE / 'evaluator_A_English_packet.zip']},
        'execution_command': '.venv/Scripts/python.exe notebooks/experiments/human_evaluation_phase1_20261029_audit.py',
        'evaluate_py_executed': False, 'reason': 'Required updated B unavailable and actual independent blinded collection declaration missing; no provenance booleans fabricated',
        'relevance_metrics_and_kappa': 'NOT_CALCULATED',
    }
    (DEST / 'evaluation_validation.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    report = ['# TravelSync Phase 1 human evaluation: validation report', '', 'Status: evaluation blocked pending the updated Evaluator B sheet and actual collection provenance.', '',
              '## Validated evidence', '', f'Evaluator A: {len(rows)} rows. Structural validation: {"PASS" if not errors else "FAIL"}. Errors: {errors}. All 38 grades have explicit NO evidence flags. Stable scenario/place pairs match the frozen experiment. The adapter retains original IDs, notes, grades and Korean evidence; English evidence is retained unchanged in the source sheet. English prompt/evidence fields were compared with the packaged blank sheet.', '',
              'All seven original experiment unit tests passed. Frozen source/configuration, canonical dataset and protected tracked files passed the original verify_frozen check. Preflight did not fit models, compute embeddings, alter rankings, or run database operations.', '',
              '## Missing inputs', '', 'The request explicitly requires evaluator_B_English(1).csv and forbids substituting evaluator_B_English.csv. Only the older filename was located in the searched locations. Its available presence does not establish that it is the requested revision. No B adapter or two-rater metrics were created.', '',
              'The actual collection date and collector confirmation of independent human ratings without method/score/rank/report exposure are missing. The supplied English rubric and packet structure require blinding and independence, but files cannot establish whether that procedure occurred. Do not fabricate the human-evidence JSON required by evaluate.py. A user statement that human evaluation is complete does not establish the missing procedural facts.', '',
              'The Korean and English rubrics both require grade blank when insufficient_evidence=YES. No such conflict was found in A. English translations are AI-assisted and were not independently validated by a bilingual reviewer; translated names are not verified official names. Original sources were not rechecked for current place attributes.', '',
              '## Frozen ranking diagnostics', '', '| Method | Exactly tied pairs |', '| --- | ---: |']
    report += [f'| {m} | {ties[m]} |' for m in 'ABCD']
    report += ['', 'These counts were recalculated by comparing every unordered pair within each frozen scenario and method. They do not measure human relevance.', '', '## Requested cases: frozen evidence only', '']
    scenario_map = {s['scenario_id']: s for s in json.loads((EXP / 'scenarios.json').read_text(encoding='utf-8'))}
    a_map = {(r['scenario_id'], r['place_id']): r for r in rows}
    for sid in ['S01', 'S07', 'S08']:
        report += [f'### {sid}', '', scenario_map[sid]['preference_ko'], '', '| POI ID | Candidate | A human grade | Updated B human grade | Model A rank | B rank | C rank | D rank |', '| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |']
        for r in sorted(groups[(sid, 'A')], key=lambda x: x['place_id']):
            pid = r['place_id']
            ranks = [next(q['rank'] for q in groups[(sid, m)] if q['place_id'] == pid) for m in 'ABCD']
            report += ['| ' + ' | '.join([pid, r['place_name'], a_map[(sid, pid)]['relevance_0_3'], 'Unavailable'] + ranks) + ' |']
        report += ['', 'Ranks use the frozen ID fallback within exact ties. Use the original scores to identify ties; these display ranks do not imply a strict preference within tied bands. Two-rater alignment analysis is deferred until the requested updated B sheet and collection provenance are supplied.', '']
    report += ['## Evaluation policy on resumption', '', 'Run the unchanged evaluate.py CLI using validated original-schema adapters and the actual collection declaration. Preserve its per-rater and mean-grade rows unchanged. Report the arithmetic mean of the two separate rater metrics explicitly, alongside the script mean-grade result, because these are different calculations. Never describe averaged grades as adjudicated ground truth.', '',
               'Use quadratic weighted Cohen kappa on paired grades only when both raters indicate sufficient evidence. Exclude pairs with either insufficient rating and report the excluded count. Align by scenario_id and place_id. Report exact agreement and differences of 1, 2 and 3 with their denominators and retain notes. The previously quoted 28/38 and 0.881 remain unverified cross-checks.', '',
               '## Conclusions and remaining deliverables', '', 'No relevance winner can be selected from this preflight. Whether enrichment improves relevance, whether C improves on B, and whether D improves on lexical methods remain unanswered. No implementation change is justified by the evidence validated here. Even completed metrics would describe only 12 synthetic scenarios, 19 selected POIs in six groups and 38 pairs; they would not establish production generalization or statistical significance.', '',
               'Before integration with GFAR, collect a broader held-out evaluation using real independently collected member preferences, adequate verified description coverage, and group-level relevance/satisfaction evidence. Validate candidate recall and utility calibration separately; similarity scores across methods have different scales and are not 1–5 member utilities.', '',
               'Created: evaluation_validation.json, validated_adapter_R1.csv, validation_tests.txt and this report. Not created: method_comparison.csv, per_scenario_metrics.csv, inter_rater_agreement.csv and original evaluate.py outputs, because required inputs remain missing. No placeholder numerical results are presented.', '',
               'Reproduce this preflight in a new destination (the script refuses overwrite): .venv/Scripts/python.exe notebooks/experiments/human_evaluation_phase1_20261029_audit.py. The JSON records revision, Python environment, commands, input/code hashes and preservation checks.']
    (DEST / 'human_evaluation_report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(json.dumps({'A_passed': not errors, 'updated_B': 'missing', 'provenance': 'missing', 'ties': ties, 'tests_exit': tests.returncode, 'unchanged': unchanged, 'output': str(DEST)}, indent=2))


if __name__ == '__main__':
    main()
