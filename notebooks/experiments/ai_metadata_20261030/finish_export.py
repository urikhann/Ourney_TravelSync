"""Finish report/model export after a serialization-only failure; reuse saved scores.

Never reruns or alters saved model scores. Rebuilds tiny 19-ID lexical model
metadata, independently validates saved scores, and preserves source attempt.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from evaluate_metadata import (ROOT,PREV,ROUND1,PHASE1,SOURCE,SOURCE_NAMES,CANONICAL,BASE_FIELDS,EXPANDED_FIELDS,
    SparseLexical,sparse_scores,clean,text_for,read_csv,write_json,digest,validate_sources,verify_legacy_content)


def finish(source,out):
    source,out=source.resolve(),out.resolve()
    assert not out.exists(), 'Fresh destination required'
    original_manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    assert all(digest(p)==v for p,v in original_manifest['input_hashes'].items())
    # Verify exactly the recoverable numerical-code change, not an arbitrary edit.
    current=(HERE/'evaluate_metadata.py').read_text(encoding='utf-8')
    old=current.replace("a=oldevidence[pid];new_row=rowmap[pid]","a=oldevidence[pid];b=rowmap[pid]")
    old=old.replace("a[field]!=new_row[field]","a[field]!=b[field]")
    old=old.replace("'place_name':new_row['place_name']","'place_name':b['place_name']")
    old=old.replace("new_row['description_summary']","b['description_summary']")
    old=old.replace("text_for(new_row,BASE_FIELDS)","text_for(b,BASE_FIELDS)")
    old=old.replace("'description_basis':new_row['basis']","'description_basis':b['basis']")
    old_hash=hashlib.sha256(old.encode('utf-8')).hexdigest()
    assert old_hash==original_manifest['code_hashes'][str(HERE/'evaluate_metadata.py')], 'Unreviewed numerical-code changes'
    legacy=verify_legacy_content()
    # Existing independent pre-run records bind all scientific inputs from earlier stages.
    previous_verification=json.loads((PREV/'verification.json').read_text(encoding='utf-8'))
    assert all(digest(p)==v for p,v in previous_verification['source_hashes'].items())
    prev_hashes=json.loads((PREV/'output_hashes.json').read_text(encoding='utf-8'))
    assert all(digest(PREV/p)==v for p,v in prev_hashes.items())
    assert all(digest(source/name)==v for name,v in json.loads((source/'pre_scoring_freeze.json').read_text()).items())
    shutil.copytree(source,out)
    (out/'code_at_scoring').mkdir()
    (out/'code_at_scoring/evaluate_metadata.py').write_bytes(old.encode('utf-8'))
    assert digest(out/'code_at_scoring/evaluate_metadata.py')==old_hash
    # Audit and regenerate coverage copies in the new directory, checking byte identity.
    rowmap,coverage=validate_sources(out)
    for name in ['input_coverage.csv','coverage_by_basis.csv','source_validation.json']:
        assert digest(out/name)==digest(source/name)
    ids=sorted({r['place_id'] for r in read_csv(ROUND1/'results/scores_ranks.csv')})
    meta=[text_for(rowmap[p],BASE_FIELDS) for p in ids]
    expanded=[text_for(rowmap[p],EXPANDED_FIELDS) for p in ids]
    b,c=SparseLexical(meta),SparseLexical(expanded)
    write_json(out/'annotated_lexical_models.json',{'fit_ids':ids,'B':b.spec(),'C':c.spec()})
    scenarios=json.loads((out/'scenarios.json').read_text(encoding='utf-8'))
    q=[clean(s['preference_ko']) for s in scenarios]
    scores={'B':sparse_scores(b.encode(q),b.encode(meta)),'C':sparse_scores(c.encode(q),c.encode(expanded))}
    qi={s['scenario_id']:i for i,s in enumerate(scenarios)};pi={p:i for i,p in enumerate(ids)}
    checked=0
    for r in read_csv(out/'annotated_rankings.csv'):
        if r['variant']=='new_metadata_same19_fit' and r['method'] in ['B','C']:
            assert abs(scores[r['method']][qi[r['scenario_id']],pi[r['place_id']]]-float(r['score']))<1e-14
            checked+=1
    # Preserve evidence of the original scoring manifest and bind the completion code separately.
    write_json(out/'completion_manifest.json',{'source_attempt':str(source),'source_scoring_manifest_sha256':digest(source/'manifest.json'),'corrected_export_only':'Rename b dictionary variable in drift loop so the B model still has spec() during final model metadata serialization. Scoring/metrics were saved before this error and are not changed.','report_portability_fix':'Report reads PHASE1 from canonical experiment location rather than assuming output sibling location; no metric calculation changed.','recomputed_annotated_BC_scores_checked':checked,'complete_reexecution_of_all_models':False,'semantic_repeat':'Original encoding of all unique texts executed twice with exact embeddings equality; full second cold-start run not performed','current_code_sha256':{str(p):digest(p) for p in HERE.glob('*.py')},'command':subprocess.list2cmdline([sys.executable,str(Path(__file__).resolve()),'--source',str(source),'--out',str(out)])})
    protected={str(p):digest(p) for p in [CANONICAL,*[SOURCE/n for n in SOURCE_NAMES]]}
    for folder in [ROUND1,PHASE1,PREV]:
        protected.update({str(p):digest(p) for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    write_json(out/'verification.json',{'status':'PASS','original_canonical_production_raw_bytes_verified':True,'source_hashes_match_scoring_start_manifest':True,'prior_scientific_artifacts_match_historical_records':True,'protected_hashes':protected,'legacy_freeze':legacy,'A_equality':'Independently checked by final verifier; scoring pipeline asserts exact equality in every context before saving all baseline tables','pre_scoring_artifacts_unchanged':True,'completion_does_not_rescore_semantics':True})
    from metadata_report import build_report
    # Convert exported CSV scalars back to the types expected by the report builder.
    def typed(name,bools=()):
        rows=read_csv(out/name)
        for r in rows:
            for key,value in r.items():
                if key in bools:r[key]=value=='True'
                elif value not in ['N/A','']:
                    try:r[key]=float(value) if any(c in value.lower() for c in ['.','e']) else int(value)
                    except ValueError:pass
        return rows
    build_report(out,coverage,typed('baseline_ranking_diagnostics.csv'),typed('previous_vs_new_ranking_diagnostics.csv'),typed('masking_summary.csv'),typed('annotated_method_comparison.csv'),typed('annotated_evidence_drift.csv',bools=['description_changed']),typed('runtime_measurements.csv'),typed('description_basis_ablation.csv'))
    write_json(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps({'completion':'PASS','output':str(out),'exported_BC_rank_scores_independently_recomputed':checked,'semantic_scores':'Saved scores unchanged; original double-encoding exact repeat preserved'},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();finish(a.source,a.out)
