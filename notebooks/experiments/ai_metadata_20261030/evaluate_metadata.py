"""Matched A/B/C/D rerun using supplied full-catalogue metadata; no app writes."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import importlib.metadata
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
from time import perf_counter

os.environ['HF_HUB_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
import numpy as np
from scipy.sparse import csr_matrix

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD_CODE=HERE.parent/'enriched_poi_round1_20261028'
PREV_CODE=HERE.parent/'poi_robustness_20261029'
PREV=ROOT/'data/generated/evaluation/poi_robustness_20261029_verified'
sys.path.insert(0,str(PREV_CODE))
from run_robustness import (ROUND1,PHASE1,FEATURES,CANONICAL,IDENTITY_EXCLUSIONS,BASE_FIELDS,EXPANDED_FIELDS,
                           MODEL,clean,text_for,read_csv,write_csv,write_json,digest,protected_snapshot,
                           verify_original_content,rank_info,stability,diagnostics,policy_scores,make_masks,
                           score_individuals,average,load_lexical,SemanticCache)
from run import grams,Lexical
from evaluate import ndcg,pair_agreement,audit_ratings

FIELDS=['detailed_category','food_type','representative_menu','activity_type']
MENU_BASES={'STRUCTURED_MENU_FACTS','OFFICIAL_MENU_FACTS'}
SOURCE=Path(r'D:\Backup\Documents\KakaoTalk Downloads')
SOURCE_NAMES=['travelsync_poi_ai_metadata_v1.csv','travelsync_poi_metadata_enriched_v1.csv','travelsync_poi_metadata_enriched_report.txt']


def verify_legacy_content():
    """Original byte checks, plus explicit LF equivalence for two later analyses."""
    try:
        return verify_original_content()
    except AssertionError:
        from run import verify_frozen
        try:
            verify_frozen(ROUND1)
        except AssertionError as error:
            assert str(error)=='Protected code/data changed', 'Frozen artifact/input content drift'
        original=json.loads((ROUND1/'manifest.json').read_text(encoding='utf-8'))['protected_tracked_files']
        current=protected_snapshot()
        assert original.keys()<=current.keys()
        assert all(current[p]==v for p,v in original.items()), 'Original protected bytes changed'
        allowed={'notebooks/experiments/human_evaluation_phase1_20261029.py','notebooks/experiments/human_evaluation_phase1_20261029_audit.py'}
        assert current.keys()-original.keys()<=allowed
        historic=json.loads((PHASE1/'evaluation_validation.json').read_text(encoding='utf-8'))
        preflight=json.loads((PHASE1.parent/'human_evaluation_phase1_20261029_preflight/evaluation_validation.json').read_text(encoding='utf-8'))
        expected={p.replace('\\','/'):v for p,v in preflight['code_sha256'].items()}
        expected['notebooks/experiments/human_evaluation_phase1_20261029.py']=historic['descriptive_wrapper']['sha256']
        checks={}
        for p in sorted(current.keys()-original.keys()):
            raw=(ROOT/p).read_bytes();normalized=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
            assert normalized==expected[p], 'Later analysis changed beyond CRLF normalization'
            checks[p]={'current_raw_sha256':digest(ROOT/p),'LF_normalized_sha256':normalized,'historical_LF_sha256':expected[p]}
        return {'original_protected_bytes':'ALL_UNCHANGED','known_postfreeze_analysis_additions':checks,'legacy_exact_key_verifier':'Rejects two added analysis scripts; previous helper also rejects Git worktree CRLF normalization. Only these scripts are checked for LF-equivalent content. No file is rewritten and full current raw-byte snapshot is protected before/after.'}


class SparseLexical:
    """Same TF/IDF/L2 equations and ngrams as Lexical; sparse storage only."""
    def __init__(self,documents):
        counts=[grams(t) for t in documents]
        df=Counter()
        for c in counts:
            df.update(c.keys())
        self.vocab=sorted(df)
        self.index={v:i for i,v in enumerate(self.vocab)}
        self.idf=np.asarray([math.log((1+len(documents))/(1+df[v]))+1 for v in self.vocab])

    def encode(self,texts):
        row,col,data=[],[],[]
        for i,text in enumerate(texts):
            for term,count in grams(text).items():
                if term in self.index:
                    j=self.index[term]
                    row.append(i);col.append(j);data.append(count*self.idf[j])
        mat=csr_matrix((data,(row,col)),shape=(len(texts),len(self.vocab)),dtype=np.float64)
        norms=np.sqrt(np.asarray(mat.multiply(mat).sum(axis=1)).ravel())
        inverse=np.divide(1,norms,out=np.zeros_like(norms),where=norms>0)
        return mat.multiply(inverse[:,None]).tocsr()

    def spec(self):
        return {'vocabulary':self.vocab,'idf':self.idf.tolist()}


def sparse_scores(q,d):
    return (q@d.T).toarray()


def validate_sources(out):
    raw=read_csv(SOURCE/SOURCE_NAMES[0]);full=read_csv(SOURCE/SOURCE_NAMES[1]);canonical=read_csv(CANONICAL)
    assert len(raw)==len(full)==len(canonical)==3570
    assert len({r['place_id'] for r in raw})==3570
    assert [r['place_id'] for r in raw]==[r['place_id'] for r in canonical]==[r['place_id'] for r in full]
    assert all(a[k]==b[k] for a,b in zip(canonical,full) for k in a), 'Canonical field drift'
    assert all(a[k]==b[k] for a,b in zip(raw,full) for k in a), 'Compact versus provenance companion mismatch'
    rows,coverage,gaps=[],[],[]
    for a,detail,base in zip(raw,full,canonical):
        assert a['place_name']==base['place_name']
        assert all(a[f]==base[f] for f in FEATURES)
        prov=json.loads(detail['field_provenance_json'])
        r=dict(base)
        r.update(a)
        r['activity_type']=a['representative_activity']
        quarantined=a['detail_verification_status']=='CONFLICT'
        for field in FIELDS+['description_summary']:
            source_field='representative_activity' if field=='activity_type' else field
            if clean(r[field]) and not any(source_field in p['fields'] and p.get('url') and p.get('checked_at') for p in prov):
                gaps.append({'place_id':a['place_id'],'field':field})
            if quarantined:
                r[field]=''
        r['basis']=a['description_basis']
        rows.append(r)
        coverage.append({'place_id':r['place_id'],'place_name':r['place_name'],'description_basis':r['basis'],'description_source_type':a['description_source_type'],'detail_verification_status':a['detail_verification_status'],'raw_description_present':bool(clean(a['description_summary'])),'conflict_metadata_quarantined':quarantined,'previous_identity_exclusion_retained':r['place_id'] in IDENTITY_EXCLUSIONS,'usable_in_matched_pools':bool(clean(r['description_summary'])) and r['place_id'] not in IDENTITY_EXCLUSIONS,'description_source_url':a['description_source_url'],'description_checked_at':detail['description_checked_at'],'metadata_match_notes':detail['metadata_match_notes'],'new_B_text':text_for(r,BASE_FIELDS),'new_C_D_text':text_for(r,EXPANDED_FIELDS)})
    assert not gaps, 'Nonempty ranking fields lack dated provenance: '+repr(gaps[:10])
    write_csv(out/'input_coverage.csv',coverage)
    groups=[]
    for dim in ['description_basis','description_source_type','detail_verification_status']:
        for value in sorted({r[dim] for r in coverage}):
            rs=[r for r in coverage if r[dim]==value]
            groups.append({'dimension':dim,'value':value,'pois':len(rs),'raw_descriptions':sum(r['raw_description_present'] for r in rs),'usable_matched_pool_descriptions':sum(r['usable_in_matched_pools'] for r in rs),'note':'Description-basis menu-fact sentences are not narrative introductions; inherited source evidence is not newly live-verified here'})
    write_csv(out/'coverage_by_basis.csv',groups)
    write_json(out/'source_validation.json',{'status':'PASS','rows':3570,'duplicate_ids':0,'canonical_strings_and_all_10_features_preserved':True,'compact_all_cells_equal_to_companion':True,'all_nonempty_model_fields_have_dated_provenance':True,'activity_adapter':'representative_activity -> activity_type, values unchanged','conflict_rows':sum(r['conflict_metadata_quarantined'] for r in coverage),'conflict_policy':'Keep exact old candidate IDs; clear only newly supplied attributes for all CONFLICT rows. Old two identity exclusions remain excluded. Canonical category text remains available in the matched taxonomy bridge.','raw_description_count':sum(r['raw_description_present'] for r in coverage),'usable_matched_description_count':sum(r['usable_in_matched_pools'] for r in coverage),'source_type_and_basis_counts_reported_separately':True,'source_date_note':'Companion source timestamps retained, not replaced with experiment date; live source collection not repeated','provenance_gaps':gaps})
    return {r['place_id']:r for r in rows},coverage


def get_contexts(rowmap):
    membership=read_csv(PREV/'pool_memberships.csv')
    pool_rows=read_csv(PREV/'candidate_pools.csv')
    contexts=[]
    for p in pool_rows:
        ids=[r['place_id'] for r in membership if r['pool_id']==p['pool_id']]
        contexts.append({'pool_id':p['pool_id'],'domain':p['domain'],'previous_domain':p['domain'],'ids':ids,'comparison':'MATCHED_PREVIOUS_REPRESENTATION'})
        if p['domain']=='taxonomy_extension':
            contexts.append({'pool_id':p['pool_id'],'domain':'enriched_catalogue','previous_domain':'taxonomy_extension','ids':ids,'comparison':'NEW_DETAILED_CATEGORY_REPRESENTATION_SAME_CANDIDATES'})
    domains={}
    for domain in ['original_fields','taxonomy_extension','enriched_catalogue']:
        ids=sorted(set().union(*(set(c['ids']) for c in contexts if c['domain']==domain)))
        domains[domain]=[rowmap[pid] for pid in ids]
    return contexts,domains


def old_backend_control():
    old_models=json.loads((ROUND1/'results/lexical_models.json').read_text(encoding='utf-8'))
    old_data=read_csv(ROUND1/'inputs/travelsync_demo_candidates_enriched_v1.csv')
    eligible={r['place_id'] for r in read_csv(ROUND1/'eligible_candidates.csv')}
    rs=[r for r in old_data if r['place_id'] in eligible]
    scenarios=json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
    result={}
    for method,fields in [('B',BASE_FIELDS),('C',EXPANDED_FIELDS)]:
        texts=[text_for(r,fields) for r in rs]
        sparse=SparseLexical(texts)
        frozen=load_lexical(old_models[method])
        assert sparse.vocab==frozen.vocab
        assert np.array_equal(sparse.idf,frozen.idf)
        queries=[clean(s['preference_ko']) for s in scenarios]
        a=sparse_scores(sparse.encode(queries),sparse.encode(texts))
        b=frozen.encode(queries)@frozen.encode(texts).T
        error=float(np.abs(a-b).max())
        assert error<1e-12
        for av,bv in zip(a,b):
            for i,j in itertools.combinations(range(len(rs)),2):
                assert (av[i]==av[j])==(bv[i]==bv[j]), 'Sparse change affects historical tie partition'
                assert np.sign(av[i]-av[j])==np.sign(bv[i]-bv[j]), 'Sparse change affects historical ordering'
        result[method]={'max_absolute_score_difference':error,'vocabulary_and_idf_exactly_equal':True,'all_orderings_and_exact_tie_partitions_identical':True}
    return result


def annotated_evaluation(out,rowmap,scenarios,semantic,full_matrices,full_positions):
    old=read_csv(ROUND1/'results/scores_ranks.csv')
    pairs=defaultdict(list)
    for r in old:
        pairs[(r['scenario_id'],r['method'])].append(r)
    ids=sorted({r['place_id'] for r in old});rs=[rowmap[pid] for pid in ids]
    meta=[text_for(r,BASE_FIELDS) for r in rs];expanded=[text_for(r,EXPANDED_FIELDS) for r in rs]
    queries=[clean(s['preference_ko']) for s in scenarios]
    b,c=SparseLexical(meta),SparseLexical(expanded)
    data={'A':score_individuals(np.asarray([[s['preference_vector'][f] for f in FEATURES] for s in scenarios]),np.asarray([[float(r[f]) for f in FEATURES] for r in rs])),
          'B':sparse_scores(b.encode(queries),b.encode(meta)),'C':sparse_scores(c.encode(queries),c.encode(expanded)),
          'D':semantic.encode(queries)@semantic.encode(expanded).T}
    d_meta=semantic.encode(queries)@semantic.encode(meta).T
    c_meta=sparse_scores(c.encode(queries),c.encode(meta))
    labels={rid:audit_ratings(PHASE1/f'validated_adapter_{rid}.csv',ROUND1/f'results/annotation_{rid}.csv',rid) for rid in ['R1','R2']}
    sidindex={s['scenario_id']:i for i,s in enumerate(scenarios)};pidindex={pid:i for i,pid in enumerate(ids)}
    metrics=[];ranks=[]
    variants=['new_metadata_same19_fit','new_metadata_full_corpus_fit','new_metadata_no_menu_descriptions_same19_fit']
    for variant in variants:
        selected={m:v.copy() for m,v in data.items()}
        if variant=='new_metadata_full_corpus_fit':
            selected={m:v[:,[full_positions[pid] for pid in ids]] for m,v in full_matrices.items() if m in 'ABCD'}
        if variant=='new_metadata_no_menu_descriptions_same19_fit':
            menu=np.asarray([r['basis'] in MENU_BASES for r in rs])
            selected['C'][:,menu]=c_meta[:,menu]
            selected['D'][:,menu]=d_meta[:,menu]
        for (sid,method),old_rows in sorted(pairs.items()):
            newrows=[{**r,'score':float(selected[method][sidindex[sid],pidindex[r['place_id']]])} for r in old_rows]
            newrows.sort(key=lambda r:(-r['score'],r['place_id']))
            for rank,r in enumerate(newrows,1):
                r['rank']=rank
                ranks.append({'variant':variant,'scenario_id':sid,'method':method,'place_id':r['place_id'],'place_name':r['place_name'],'score':r['score'],'rank_id_fallback':rank,'description_basis':rowmap[r['place_id']]['basis']})
            for rid,judgments in labels.items():
                judged={r['place_id']:judgments[(sid,r['place_id'])] for r in newrows if judgments[(sid,r['place_id'])] is not None}
                missing=len(judged)!=len(newrows);k=min(3,len(newrows))
                pair,count=pair_agreement(newrows,judged)
                tie=ndcg(newrows,judged,k) if not missing else None
                fallback=ndcg(newrows,judged,k,False) if not missing else None
                oldtie=ndcg(old_rows,judged,k) if not missing else None
                metrics.append({'variant':variant,'scenario_id':sid,'method':method,'sheet_id':rid,'actual_human_rater_count':1,'candidate_count':len(newrows),'k':k,'ndcg_tie_expected':tie if tie is not None else 'N/A','ndcg_id_fallback':fallback if fallback is not None else 'N/A','pairwise_agreement':pair if pair is not None else 'N/A','human_strict_pairs':count,'old_ndcg_tie_expected':oldtie if oldtie is not None else 'N/A','delta_vs_old_ndcg':tie-oldtie if tie is not None and oldtie is not None else 'N/A','ndcg_exclusion':'insufficient evidence' if missing else 'zero ideal DCG' if tie is None else '', 'interpretation':'Fixed historical grades tied to original evidence; no new independent label collection'})
    write_csv(out/'annotated_per_scenario_metrics.csv',metrics)
    write_csv(out/'annotated_rankings.csv',ranks)
    summary=[]
    for variant,method in itertools.product(variants,'ABCD'):
        r=[x for x in metrics if x['variant']==variant and x['method']==method]
        summary.append({'variant':variant,'method':method,'mean_tie_expected_ndcg':average([x['ndcg_tie_expected'] for x in r]),'sheet_A_tie_expected_ndcg':average([x['ndcg_tie_expected'] for x in r if x['sheet_id']=='R1']),'sheet_B_tie_expected_ndcg':average([x['ndcg_tie_expected'] for x in r if x['sheet_id']=='R2']),'mean_id_fallback_ndcg':average([x['ndcg_id_fallback'] for x in r]),'mean_pairwise_agreement':average([x['pairwise_agreement'] for x in r]),'old_mean_tie_expected_ndcg':average([x['old_ndcg_tie_expected'] for x in r]),'delta_vs_old_ndcg':average([x['delta_vs_old_ndcg'] for x in r]),'evaluated_scenarios_per_sheet':sum(x['ndcg_tie_expected']!='N/A' and x['sheet_id']=='R1' for x in r),'exclusions':sum(x['ndcg_tie_expected']=='N/A' for x in r),'actual_human_rater_count':1})
    write_csv(out/'annotated_method_comparison.csv',summary)
    oldevidence={r['place_id']:r for r in read_csv(ROUND1/'results/annotation_R1.csv')}
    drift=[]
    for pid in ids:
        a=oldevidence[pid];new_row=rowmap[pid]
        changed=[field for field in EXPANDED_FIELDS if a[field]!=new_row[field]]
        drift.append({'place_id':pid,'place_name':new_row['place_name'],'changed_fields':'|'.join(changed),'old_description':a['description_summary'],'new_description':new_row['description_summary'],'description_changed':a['description_summary']!=new_row['description_summary'],'old_B_text':text_for(a,BASE_FIELDS),'new_B_text':text_for(new_row,BASE_FIELDS),'description_basis':new_row['basis'],'label_validity':'Original evidence supplied to one human; compatibility with revised evidence not re-adjudicated'})
    write_csv(out/'annotated_evidence_drift.csv',drift)
    write_json(out/'annotated_lexical_models.json',{'fit_ids':ids,'B':b.spec(),'C':c.spec()})
    return summary,drift


def main(out):
    if out.exists():
        raise FileExistsError('Use a fresh destination')
    out.mkdir(parents=True);(out/'inputs').mkdir()
    legacy=verify_legacy_content();tracked=protected_snapshot()
    protected=[CANONICAL,*[SOURCE/n for n in SOURCE_NAMES]]
    for folder in [ROUND1,PHASE1,PREV]:
        protected += [p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    before={str(p):digest(p) for p in protected}
    for name in SOURCE_NAMES:
        shutil.copyfile(SOURCE/name,out/'inputs'/name)
    rowmap,coverage=validate_sources(out)
    contexts,domains=get_contexts(rowmap)
    scenarios=json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
    write_json(out/'scenarios.json',scenarios)
    write_csv(out/'candidate_contexts.csv',[{k:v for k,v in c.items() if k!='ids'}|{'candidate_count':len(c['ids'])} for c in contexts])
    write_csv(out/'pool_memberships.csv',[{'pool_id':c['pool_id'],'domain':c['domain'],'place_id':pid} for c in contexts for pid in c['ids']])
    described={pid for pid,r in rowmap.items() if clean(r['description_summary']) and pid not in IDENTITY_EXCLUSIONS}
    masks=make_masks(described)
    mask_sets={m['mask_id']:set(m['masked_place_ids']) for m in masks}
    write_json(out/'description_masks.json',masks)
    config={'experiment':'A/B/C/D rerun on travelsync_poi_ai_metadata_v1.csv','date_korea':'2026-10-30','profiles':'Exactly the 12 frozen Round 1 texts/vectors','candidates':'Exact previous 11 pool identity lists; same three sample seeds, sizes, K and old two exclusions; no resampling','domains':{'original_fields':'Previous 38-POI original field allowlist, activity adapter','taxonomy_extension':'Exact prior taxonomy allowlist, updated food/menu/activity and descriptions; matched metadata-change bridge','enriched_catalogue':'New detailed_category instead of category_name; same candidate IDs, original B/C allowlist; representation change explicit'},'B_fields':FIELDS,'C_D_fields':FIELDS+['description_summary'],'excluded_ranking_fields':'Place names, source/rating/selection metadata, URLs, hours, addresses and statuses','lexical':'Original within-token char2/3-gram raw TF, smoothed IDF log((1+N)/(1+df))+1, L2 cosine; CSR storage backend; dense control test required','fit':'Once per domain across all eligible domain documents, queries excluded; fixed for all pools/masks/ablations. Annotated primary fit uses same original 19 IDs, separately from full-corpus fit sensitivity.','mask_percentages':[0,10,25,50,75],'mask_seeds':[11,29,47,71,101],'mask_policy':'Same random/nested mask parameters, additional loss among currently available descriptions; IDs cannot match old masks because available description set changed','diagnostic_k':10,'annotated_k':'min(3, actual original group size)','NDCG':'gain=2**grade-1, discount=1/log2(rank+1), exact tie-expected primary and ID fallback separate; full scenario exclusion on insufficient; zero ideal N/A','rank_stability':'Same prior ID retention/Jaccard, membership-probability overlap, Spearman average tie ranks; undefined constant correlations N/A','basis_ablation':'Remove only menu-fact descriptions in fixed C/D models; retain B fields and same identities. Structured-menu facts are not pure narrative enrichment.','model':MODEL,'new_raters':False,'single_human_two_sheets':True,'production_changes':False}
    write_json(out/'config.json',config)
    backend=old_backend_control()
    write_json(out/'sparse_backend_control.json',backend)
    queries=[clean(s['preference_ko']) for s in scenarios]
    docs={}
    for domain,rs in domains.items():
        meta=[' '.join(v for f in (['category_name','food_type','representative_menu','activity_type'] if domain=='taxonomy_extension' else FIELDS) if (v:=clean(r[f]))) for r in rs]
        docs[domain]={'meta':meta,'expanded':[' '.join(v for v in [m,clean(r['description_summary'])] if v) for m,r in zip(meta,rs)],'description':np.asarray([bool(clean(r['description_summary'])) for r in rs]),'menu':np.asarray([r['basis'] in MENU_BASES for r in rs])}
    write_csv(out/'model_inputs.csv',[{'domain':domain,'place_id':r['place_id'],'B_text':docs[domain]['meta'][i],'C_D_text':docs[domain]['expanded'][i],'description_basis':r['basis'],'usable_description':bool(docs[domain]['description'][i])} for domain,rs in domains.items() for i,r in enumerate(rs)])
    freeze_files=['config.json','scenarios.json','pool_memberships.csv','candidate_contexts.csv','model_inputs.csv','description_masks.json','source_validation.json']
    write_json(out/'pre_scoring_freeze.json',{name:digest(out/name) for name in freeze_files})
    write_json(out/'manifest.json',{'created_utc':datetime.now(timezone.utc).isoformat(),'client_date_korea':'2026-10-30','git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'code_hashes':{str(p):digest(p) for p in [*HERE.glob('*.py'),PREV_CODE/'run_robustness.py',OLD_CODE/'run.py',OLD_CODE/'evaluate.py']},'input_hashes':{str(p):digest(p) for p in protected if p in [CANONICAL,*[SOURCE/n for n in SOURCE_NAMES]]},'python':sys.version,'python_executable':sys.executable,'platform':platform.platform(),'packages':{n:importlib.metadata.version(n) for n in ['numpy','scipy','torch','sentence-transformers','transformers']}})
    timings=[]
    semantic=SemanticCache(queries+[t for d in docs.values() for k in ['meta','expanded'] for t in d[k]],timings)
    write_json(out/'semantic_metadata.json',semantic.metadata)
    matrices={};models={};runtime=[]
    position={d:{r['place_id']:i for i,r in enumerate(rs)} for d,rs in domains.items()}
    for domain,rs in domains.items():
        start=perf_counter();b,c=SparseLexical(docs[domain]['meta']),SparseLexical(docs[domain]['expanded'])
        timings.append({'stage':'BC_fit_once','domain':domain,'seconds':perf_counter()-start,'notes':'Sparse storage; unchanged TFIDF equations'})
        start=perf_counter();qb,qc=b.encode(queries),c.encode(queries)
        db,dc,dm=b.encode(docs[domain]['meta']),c.encode(docs[domain]['expanded']),c.encode(docs[domain]['meta'])
        qd=semantic.encode(queries);dd=semantic.encode(docs[domain]['expanded']);dmeta=semantic.encode(docs[domain]['meta'])
        pref=np.asarray([[s['preference_vector'][f] for f in FEATURES] for s in scenarios]);features=np.asarray([[float(r[f]) for f in FEATURES] for r in rs])
        matrices[domain]={'A':score_individuals(pref,features),'B':sparse_scores(qb,db),'C':sparse_scores(qc,dc),'C_metadata_projection':sparse_scores(qc,dm),'D':qd@dd.T,'D_metadata_projection':qd@dmeta.T}
        timings.append({'stage':'encode_queries_documents_and_score','domain':domain,'seconds':perf_counter()-start,'notes':'D embeddings already cached; no fit'})
        models[domain]={'B':b.spec(),'C':c.spec()}
        np.savez_compressed(out/f'scores_{domain}.npz',**matrices[domain])
        assert all(np.isfinite(v).all() for v in matrices[domain].values())
        for context in [x for x in contexts if x['domain']==domain]:
            ids=context['ids'];ix=[position[domain][pid] for pid in ids]
            for method,q,d in [('A',pref,features),('B',qb,db),('C',qc,dc),('D',qd,dd)]:
                for repeat in range(3):
                    for qi in range(12):
                        start=perf_counter()
                        vals=score_individuals(q[qi:qi+1],d[ix])[0] if method=='A' else sparse_scores(q[qi:qi+1],d[ix])[0] if method in ['B','C'] else q[qi]@d[ix].T
                        sorted(range(len(ids)),key=lambda i:(-float(vals[i]),ids[i]))[:min(10,len(ids))]
                        runtime.append({'pool_id':context['pool_id'],'domain':domain,'method':method,'repeat':repeat,'scenario_id':scenarios[qi]['scenario_id'],'candidates':len(ids),'warm_encoded_score_sort_ms':(perf_counter()-start)*1000,'notes':'Cached query/documents; includes candidate gathering + score + full sort; excludes encoding/model load/fit; sparse lexical versus dense transformer backend'})
        for method,model in [('B',b),('C',c)]:
            for query in queries:
                start=perf_counter();model.encode([query]);timings.append({'stage':f'{method}_single_query_encoding','domain':domain,'seconds':perf_counter()-start,'notes':'Warm sparse lexical query encoding'})
    for query in queries:
        start=perf_counter();semantic.model.encode([query],normalize_embeddings=True,show_progress_bar=False)
        timings.append({'stage':'D_single_query_encoding','domain':'all','seconds':perf_counter()-start,'notes':'Warm CPU transformer; no candidate scoring'})
    write_json(out/'lexical_models.json',models)
    write_csv(out/'runtime_measurements.csv',runtime)
    write_csv(out/'stage_timings.csv',timings,sorted(set().union(*(r.keys() for r in timings))))
    oldinputs=read_csv(PREV/'model_inputs.csv')
    oldpos={d:{r['place_id']:i for i,r in enumerate([r for r in oldinputs if r['domain']==d])} for d in ['original_fields','taxonomy_extension']}
    oldmat={d:np.load(PREV/f'scores_{d}.npz',allow_pickle=False) for d in oldpos}
    baseline=[];top=[];deltas=[];robust=[];ablation=[]
    for context in contexts:
        name,domain,ids=context['pool_id'],context['domain'],context['ids'];ix=[position[domain][pid] for pid in ids]
        prior_domain=context['previous_domain'];oix=[oldpos[prior_domain][pid] for pid in ids]
        data=matrices[domain];has=docs[domain]['description'][ix]
        pool_masks={m['mask_id']:np.asarray([pid in mask_sets[m['mask_id']] for pid in ids]) for m in masks}
        for qi,s in enumerate(scenarios):
            scores={m:data[m][qi,ix] for m in 'ABCD'}
            assert np.array_equal(scores['A'],oldmat[prior_domain]['A'][qi,oix]), 'A changed despite fixed profiles/candidates/features'
            for method,val in scores.items():
                diag=diagnostics(val,ids)
                baseline.append({'pool_id':name,'domain':domain,'scenario_id':s['scenario_id'],'method':method,**diag})
                oldval=oldmat[prior_domain][method][qi,oix];old_diag=diagnostics(oldval,ids)
                deltas.append({'pool_id':name,'domain':domain,'previous_domain':prior_domain,'comparison':context['comparison'],'scenario_id':s['scenario_id'],'method':method,'old_tie_fraction':old_diag['exact_tied_pair_fraction'],'new_tie_fraction':diag['exact_tied_pair_fraction'],'delta_tie_fraction':diag['exact_tied_pair_fraction']-old_diag['exact_tied_pair_fraction'],'old_positive_score_fraction':old_diag['positive_score_fraction'],'new_positive_score_fraction':diag['positive_score_fraction'],**stability(oldval,val,ids),'no_relevance_claim':'No complete judgments for these pools'})
                for rank,i in enumerate(rank_info(val,ids)['top'],1):
                    top.append({'pool_id':name,'domain':domain,'scenario_id':s['scenario_id'],'method':method,'rank_id_fallback':rank,'place_id':ids[i],'place_name':rowmap[ids[i]]['place_name'],'score':float(val[i]),'description_basis':rowmap[ids[i]]['basis']})
            for method in ['C','D']:
                for kind,lost in [('remove_menu_fact_descriptions',docs[domain]['menu'][ix]),('remove_all_descriptions',has)]:
                    val=np.where(lost,data[method+'_metadata_projection'][qi,ix],scores[method])
                    ablation.append({'pool_id':name,'domain':domain,'scenario_id':s['scenario_id'],'method':method,'ablation':kind,'affected_descriptions':int((lost & has).sum()),**diagnostics(val,ids),**stability(scores[method],val,ids)})
            policies=['C_native','B_global','B_backbone_C_ties','C_described_only','D_native']
            ref={p:scores['D'] if p=='D_native' else policy_scores(p,scores['B'],scores['C'],has) for p in policies}
            for mask in masks:
                lost=pool_masks[mask['mask_id']];cmask=np.where(lost,data['C_metadata_projection'][qi,ix],scores['C'])
                for p in policies:
                    val=np.where(lost,data['D_metadata_projection'][qi,ix],scores['D']) if p=='D_native' else policy_scores(p,scores['B'],cmask,has & ~lost)
                    diag=diagnostics(val,ids)
                    if p=='B_backbone_C_ties':
                        for field in ['positive_score_fraction','zero_score_count','score_min','score_max','score_std']:diag[field]='N/A'
                    robust.append({'pool_id':name,'domain':domain,'scenario_id':s['scenario_id'],'policy':p,'mask_id':mask['mask_id'],'requested_percent':mask['requested_percent'],'seed':mask['seed'],'natural_described_count':int(has.sum()),'masked_in_pool':int((has & lost).sum()),'remaining_described':int((has & ~lost).sum()),**diag,**stability(ref[p],val,ids)})
    write_csv(out/'baseline_ranking_diagnostics.csv',baseline);write_csv(out/'baseline_top10.csv',top)
    write_csv(out/'previous_vs_new_ranking_diagnostics.csv',deltas);write_csv(out/'description_basis_ablation.csv',ablation)
    method_summary=[]
    for context in contexts:
        for method in 'ABCD':
            selected=[r for r in baseline if r['pool_id']==context['pool_id'] and r['domain']==context['domain'] and r['method']==method]
            compared=[r for r in deltas if r['pool_id']==context['pool_id'] and r['domain']==context['domain'] and r['method']==method]
            method_summary.append({'pool_id':context['pool_id'],'domain':context['domain'],'comparison':context['comparison'],'method':method,'candidates':len(context['ids']),'mean_exact_tie_fraction':average([r['exact_tied_pair_fraction'] for r in selected]),'old_mean_exact_tie_fraction':average([r['old_tie_fraction'] for r in compared]),'delta_exact_tie_fraction':average([r['delta_tie_fraction'] for r in compared]),'mean_positive_score_fraction':average([r['positive_score_fraction'] for r in selected]),'all_zero_query_count':sum(r['score_max']==0 for r in selected),'old_to_new_topk_id_retention':average([r['topk_id_retention'] for r in compared]),'old_to_new_membership_probability_overlap':average([r['topk_membership_probability_overlap'] for r in compared]),'old_to_new_spearman':average([r['spearman_average_tie_ranks'] for r in compared]),'rankable_fraction':average([r['rankable_fraction'] for r in selected]),'relevance':'Unlabelled candidate pools: ranking diagnostics only'})
    write_csv(out/'method_summary.csv',method_summary)
    write_csv(out/'masking_robustness.csv',robust)
    mask_summary=[]
    for context in contexts:
        for rate,p in itertools.product([0,10,25,50,75],['C_native','B_global','B_backbone_C_ties','C_described_only','D_native']):
            selected=[r for r in robust if r['pool_id']==context['pool_id'] and r['domain']==context['domain'] and r['requested_percent']==rate and r['policy']==p]
            mask_summary.append({'pool_id':context['pool_id'],'domain':context['domain'],'requested_percent':rate,'policy':p,'query_seed_rows':len(selected),'mean_topk_id_retention':average([r['topk_id_retention'] for r in selected]),'min_topk_id_retention':min(r['topk_id_retention'] for r in selected),'mean_membership_probability_overlap':average([r['topk_membership_probability_overlap'] for r in selected]),'mean_spearman':average([r['spearman_average_tie_ranks'] for r in selected]),'undefined_spearman_rows':sum(r['spearman_average_tie_ranks']=='N/A' for r in selected),'mean_rankable_fraction':average([r['rankable_fraction'] for r in selected]),'mean_positive_score_fraction':average([r['positive_score_fraction'] for r in selected]),'mean_topk_returned':average([r['topk_returned'] for r in selected]),'note':'Same seed parameters; conditional random loss of new usable description set, not identical old masked IDs'})
    write_csv(out/'masking_summary.csv',mask_summary)
    annotated,drift=annotated_evaluation(out,rowmap,scenarios,semantic,matrices['enriched_catalogue'],position['enriched_catalogue'])
    assert all(digest(p)==value for p,value in before.items()), 'Protected source changed'
    assert protected_snapshot()==tracked
    assert verify_legacy_content()==legacy
    assert all(digest(out/name)==v for name,v in json.loads((out/'pre_scoring_freeze.json').read_text()).items())
    write_json(out/'verification.json',{'status':'PASS','canonical_and_production_tracked_files_unchanged':True,'input_and_previous_artifacts_unchanged':True,'protected_hashes':before,'same_original_profiles_and_candidate_id_lists':True,'A_scores_exactly_equal_to_previous_in_all_contexts':True,'legacy_freeze':legacy,'sparse_backend_historical_control':backend,'no_new_pool_NDCG':True,'annotated_evidence_drift_place_count':sum(bool(r['changed_fields']) for r in drift),'annotated_description_changed_count':sum(r['description_changed'] for r in drift),'baseline_rows':len(baseline),'mask_rows':len(robust),'contexts':len(contexts),'commands':{'run':subprocess.list2cmdline([sys.executable,str(Path(__file__).resolve()),'--out',str(out)])}})
    from metadata_report import build_report
    build_report(out,coverage,baseline,deltas,mask_summary,annotated,drift,runtime,ablation)
    write_json(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps({'output':str(out),'raw_descriptions':sum(r['raw_description_present'] for r in coverage),'usable_descriptions':len(described),'contexts':len(contexts),'baseline_rows':len(baseline),'masking_rows':len(robust),'annotated_same19':[r for r in annotated if r['variant']=='new_metadata_same19_fit'],'preservation':'PASS'},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'data/generated/evaluation/ai_metadata_20261030')
    args=p.parse_args();main(args.out.resolve())
