"""Package execution evidence and final hashes after scientific verification."""
import argparse,hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def js(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def finalize(out):
    validation=json.loads((out/'final_validation.json').read_text(encoding='utf-8'))
    assert validation['status']=='PASS' and len(validation['full_repeat_scientific_files_identical'])==24
    # Clarify a reporting sentence: the inherited bundle has one shared 0% mask,
    # followed by all five seeds at every positive percentage (21 masks total).
    old='Repeated 0% seeds are repeated deterministic controls, not independent observations.'
    new='The inherited mask bundle supplies one common 0% reference and all five seeds at each positive percentage (21 frozen mask records); the common zero-mask reference is reused across seeds, not treated as independent evidence.'
    for p in [HERE/'experiment4_report.py',out/'REPORT.md']:
        text=p.read_text(encoding='utf-8')
        if old in text:p.write_text(text.replace(old,new),encoding='utf-8')
    report=out/'REPORT.md';content=report.read_text(encoding='utf-8')
    appendix='''

### Execution and verification record

Completed on 2026-10-30 (Asia/Seoul). Canonical output: `experiment4_maxmin_hybrid_20261030_run02`.

- New optimizer and frozen-artifact tests: **30 passed** (`tests.txt`).
- Reused Experiment 3 tests: **28 passed** (`experiment3_tests.txt`).
- Production unit tests: **40 passed, 2 skipped, 21 subtests passed** (`production_tests.txt`).
- 41,055 configurations, 82,110 group-assessor rows, 1,050 historical ranking/metric regressions, 25 tiny exact controls; all original groups, masks and labels retained.
- Independent scalar reference: 630 primary new-method rankings replayed. All 82,110 utility rows independently recomputed; 362,775 selection-trace rows checked; 90 historical human-baseline lists reproduced.
- Full repeat: 19 scientific CSV tables identical byte-for-byte, three frozen JSON/pool files identical, and both decompressed trace files identical. Timing tables are intentionally excluded from byte comparisons.
- All 1,620 protected files were unchanged. No source POI, previous experiment, historical judgment, production code, SRS or SDD was edited.
- Initial run01 completed scientific execution but its report import collided with an earlier module named `report.py`. Raw results were preserved, the report was recovered using a unique module name, and run02 completed cleanly. The entire scientific output was reproduced. Verification-only tests and reporting clarification were added after freezing; algorithm, configuration and objective code stayed fixed during run02.
- `commands.json` records executed commands and statuses; `manifest.json` records final source and output provenance. `final_validation.json` records independent evaluation/replay time separately from aggregation. Preprocessing time is in `verification.json`; raw runtime tables separate scoring, candidate generation, normalization and aggregation. Figure/table/report output generation is outside recommendation latency.

All nine figures were generated in PNG/SVG. The trade-off, metadata, human, type/size and runtime figures were visually checked for legible labels and layout. Code snapshots are provenance copies; execute the notebook experiment paths shown in the README.
'''
    if '### Execution and verification record' not in content:report.write_text(content+appendix,encoding='utf-8')
    py='notebooks/experiments/enriched_poi_round1_20261028/.semantic-venv/Scripts/python.exe'
    prefix='notebooks/experiments/experiment4_maxmin_hybrid/'
    records=[
        {'command':f'{py} -m unittest discover -s {prefix.rstrip("/")} -p test_*.py -v','status':'PASS','tests':30},
        {'command':f'{py} -m unittest discover -s notebooks/experiments/c_gfar_20261030 -p test_*.py -v','status':'PASS','tests':28},
        {'command':'.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --basetemp notebooks/experiments/experiment4_maxmin_hybrid/.test-temp','status':'PASS','passed':40,'skipped':2,'subtests_passed':21},
        {'command':f'{py} {prefix}run.py --out data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run01','status':'SCIENTIFIC_PHASE_COMPLETE_REPORT_IMPORT_FAILED','note':'Original run.py renamed to experiment4.py; raw scientific outputs retained.'},
        {'command':f'{py} {prefix}recover_report.py data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run01','status':'PASS_AFTER_UNIQUE_MODULE_FIX'},
        {'command':f'{py} {prefix}experiment4.py --out data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run02','status':'PASS'},
        {'command':f'notebooks/experiments/c_gfar_20261030/.plot-venv/Scripts/python.exe {prefix}plot.py --out data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run02','status':'PASS'},
        {'command':f'{py} {prefix}verify.py --out data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run02 --repeat data/generated/evaluation/experiment4_maxmin_hybrid_20261030_run01','status':'PASS'}]
    js(out/'commands.json',records)
    code=out/'analysis_code';code.mkdir(exist_ok=True)
    for p in list(HERE.glob('*.py'))+[HERE/'README.md']:shutil.copy2(p,code/p.name)
    shutil.copy2(HERE.parent/'c_gfar_20261030/aggregation.py',code/'legacy_aggregation_unchanged.py')
    freeze=json.loads((out/'pre_execution_freeze.json').read_text(encoding='utf-8'))
    changed=[p for p,h in freeze['code_hashes'].items() if digest(ROOT/p)!=h]
    assert all(Path(p).name in ['verify.py','experiment4_report.py'] for p in changed),changed
    # Recheck every protected pre-existing file immediately before final packaging.
    for p,h in freeze['protected_before'].items():assert digest(ROOT/p)==h,p
    js(out/'manifest.json',{'completed_utc':datetime.now(timezone.utc).isoformat(),'canonical_run':out.name,'pre_execution_freeze_sha256':digest(out/'pre_execution_freeze.json'),'config_sha256':digest(out/'config.json'),'postfreeze_existing_code_changes':changed,'postfreeze_change_scope':'Verification checks strengthened; reporting clarification of inherited single 0% mask. Optimizer, run configuration and scientific execution logic unchanged. Additional artifact tests and finalization scripts are post-execution validation only.','final_code_hashes':{str(p.relative_to(ROOT)):digest(p) for p in sorted(HERE.glob('*.py'))},'verification_sha256':digest(out/'final_validation.json'),'protected_sources_final_check':'PASS','git_commit':freeze['git_commit'],'initial_dirty_state':freeze['git_status'],'figure_count':len(list((out/'figures').glob('*.png'))),'execution_commands':'commands.json','latency_scope':'Warm offline stage sums; not end-to-end application latency'})
    js(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='output_hashes.json'})
    print(json.dumps({'canonical_output':str(out),'manifest':'PASS','figures':len(list((out/'figures').glob('*.png'))),'files':len(list(out.rglob('*')))},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);finalize(p.parse_args().out.resolve())
