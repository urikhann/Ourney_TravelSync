"""Recover only the report phase of the initial run's import-name collision.

Never changes raw scientific results, traces, source inputs or prior experiments.
"""
import sys
from pathlib import Path
from experiment4 import read,js,digest
from experiment4_report import generate

out=Path(sys.argv[1])
rows=read(out/'per_group_results.csv');times=read(out/'runtime_measurements.csv')
integers=['size','seed','K','L','mask_percent','candidate_count','eligible_count','returned']
numeric=['member_support_coverage','exact_tie_fraction','optimization_minimum','optimization_mean','minimum_utility','mean_utility','utility_gap','utility_std','jain','utility_threshold_coverage']
for r in rows:
    for k in integers:r[k]=int(r[k])
    for k in numeric:r[k]=(int(r[k]) if k=='member_support_coverage' and r[k] in ['0','1'] else float(r[k])) if r[k] else None
for r in times:
    r['size']=int(r['size']);r['mask_percent']=int(r['mask_percent'])
    for k in ['aggregation_ms','normalization_ms','warm_scoring_ms','candidate_generation_ms','warm_offline_sum_ms']:r[k]=float(r[k])
generate(out,rows,times)
js(out/'report_phase_recovery.json',{'reason':'Initial scientific execution and protection checks completed, report import collided with earlier experiment report.py on modified sys.path. Fixed by unique experiment4_report module name.','raw_results_changed':False,'raw_results_sha256':digest(out/'per_group_results.csv'),'full_clean_repeat_required':True})
