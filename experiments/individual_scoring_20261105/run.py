"""Sequential, offline study. Run with the existing isolated semantic Python env."""
import csv
from collections import Counter, defaultdict
import difflib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import time
import ctypes
import numpy as np
from scipy.stats import spearmanr
from scoring import (ROOT, FEATURE_NAMES, LABELS, clean, structured, document,
                     semantic, hybrid, aggregate, encode)

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
DATA = ROOT / 'src/ourney_travelsync/data'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write(name, rows):
    if not rows:
        return
    with (OUT/name).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def language(s):
    ko, en = bool(re.search('[가-힣]', s)), bool(re.search('[A-Za-z]', s))
    return 'mixed' if ko and en else 'Korean' if ko else 'Latin' if en else 'other_or_empty'

def audit(paths):
    audits, fields = {}, []
    for path in paths:
        rows = read(path)
        x = np.array([[float(r[f]) for f in FEATURE_NAMES] for r in rows])
        usable = [r for r in rows if clean(r.get('description_summary')) and clean(r.get('description_source_url'))
                  and r.get('detail_verification_status') != 'CONFLICT']
        names = Counter(clean(r['place_name']) for r in rows)
        coord = Counter((r['latitude'], r['longitude']) for r in rows)
        desc = [clean(r.get('description_summary')) for r in usable]
        audits[path.name] = dict(count=len(rows), unique_ids=len(set(r['place_id'] for r in rows)),
            feature_counts=dict(Counter(map(str,x.sum(1).astype(int)))),
            feature_frequency=dict(zip(FEATURE_NAMES, x.sum(0).astype(int).tolist())),
            feature_vectors=len(np.unique(x,axis=0)), usable_source_backed_descriptions=len(usable),
            description_mean_chars=float(np.mean(list(map(len,desc)))) if desc else None,
            description_language=dict(Counter(language(s) for s in desc)),
            name_language=dict(Counter(language(r['place_name']) for r in rows)),
            description_basis=dict(Counter(clean(r.get('description_basis')) for r in usable)),
            verification_status=dict(Counter(r.get('detail_verification_status','absent') for r in rows)),
            duplicate_name_extra_rows=sum(v-1 for v in names.values()),
            duplicate_coordinate_extra_rows=sum(v-1 for v in coord.values()), sha256=digest(path))
        for key in rows[0]:
            fields.append(dict(dataset=path.name, field=key,
                               missing_pct=100*sum(not clean(r[key]) for r in rows)/len(rows)))
    # Heuristic flags for review only; same normalized name, or very similar name within coordinate grid.
    rows = read(paths[0]); buckets = defaultdict(list); pairs = []
    for row in rows:
        buckets[(round(float(row['latitude']),2),round(float(row['longitude']),2))].append(row)
    for group in buckets.values():
        for i,a in enumerate(group):
            for b in group[i+1:]:
                an,bn = [re.sub(r'\W','',r['place_name']).lower() for r in (a,b)]
                ratio=difflib.SequenceMatcher(None,an,bn).ratio()
                if ratio >= .9:
                    pairs.append(dict(id_a=a['place_id'],id_b=b['place_id'],name_a=a['place_name'],name_b=b['place_name'],similarity=ratio,policy='same rounded .01-degree grid; name similarity >= .9; review only'))
    write('near_duplicate_review.csv',pairs)
    write('field_missingness.csv',fields)
    save('data_audit.json',audits)
    return audits

def rss():
    class PMC(ctypes.Structure):
        _fields_ = [('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(x,ctypes.c_size_t) for x in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    p=PMC();p.cb=ctypes.sizeof(p)
    ctypes.windll.kernel32.GetCurrentProcess.restype=ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(p),p.cb)
    return {'working_set_mib':p.WorkingSetSize/2**20,'process_peak_mib':p.PeakWorkingSetSize/2**20}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    config=json.loads((HERE/'config.json').read_text())
    np.random.seed(config['seed'])
    paths=[DATA/n for n in ['travelsync_final_places_v2.csv','travelsync_poi_metadata_enriched_v3.csv','travelsync_demo40.csv']]
    protected={str(p.relative_to(ROOT)):digest(p) for p in paths}
    # Record all tracked files before/after, plus the pre-existing untracked enrichment CSV.
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    protected.update({p:digest(ROOT/p) for p in tracked if p and (ROOT/p).is_file()})
    extra=DATA/'travelsync_demo_candidates_enriched_v1.csv'
    protected[str(extra.relative_to(ROOT))]=digest(extra)
    audits=audit(paths)
    canonical=read(paths[0]); enriched={r['place_id']:r for r in read(paths[1])}
    rows=[enriched[r['place_id']] for r in sorted(canonical,key=lambda r:r['place_id'])]
    assert all(all(r[f]==enriched[r['place_id']][f] for f in FEATURE_NAMES) for r in canonical)
    ids=[r['place_id'] for r in rows]
    x=np.array([[float(r[f]) for f in FEATURE_NAMES] for r in rows])
    rng=np.random.default_rng(config['seed'])
    users=np.vstack([np.full(10,3),np.full(10,4),np.full(10,1),np.full(10,5),rng.integers(1,6,(12,10))]).astype(float)
    changed=users.copy();changed[:,0]=np.minimum(5,changed[:,0]+1)
    write('synthetic_profiles.csv',[dict(profile=f'P{i:02d}',**dict(zip(FEATURE_NAMES,u))) for i,u in enumerate(users)])
    sf,unclassified=structured(users,x)
    sf_changed,_=structured(changed,x)
    write('structured_by_feature_count.csv',[dict(feature_count=int(n),profile=f'P{u:02d}',count=int((x.sum(1)==n).sum()),feature_mean=float(sf[u,x.sum(1)==n].mean()),legacy_dot_over_sum_ratings=float(((users@x.T)/users.sum(1,keepdims=True))[u,x.sum(1)==n].mean())) for n in sorted(set(x.sum(1))) for u in range(len(users))])
    methods={'feature':(sf,'survey_feature_1_5')}
    changed_scores={'feature':sf_changed}
    for penalty in config['penalties']:
        methods[f'dislike_{penalty}']=(structured(users,x,penalty)[0],'survey_feature_1_5')
        changed_scores[f'dislike_{penalty}']=structured(changed,x,penalty)[0]
    # This weighted sum is a reconstruction of the stated legacy mismatch, NOT current binary code.
    methods['legacy_weighted_reconstruction']=(users@x.T/users.sum(1,keepdims=True),'legacy_unvalidated')
    from ourney_travelsync.recommenders.tfidf import score_texts
    methods['tfidf_empty_actual']=(score_texts(rows,['']*len(users)),'cosine')
    queries=[' '.join(LABELS[k] for k in range(10) if u[k]>=4) for u in users]
    methods['tfidf_positive_profile_control']=(score_texts(rows,queries),'cosine')
    save('phase1_gate.json',dict(canonical_descriptions=0,enriched=audits[paths[1].name],
        decision='Run source-backed enrichment pilot and full-catalog taxonomy controls; partial provenance is not independent verification. No quality winner can be selected without aligned independent labels.'))
    print('Phase 1 audit and structured scoring complete',flush=True)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(4);torch.manual_seed(config['seed'])
    benches=[]; failures=[]; subset=[]
    described=np.array([bool(clean(r.get('description_summary')) and clean(r.get('description_source_url')) and r.get('detail_verification_status')!='CONFLICT') for r in rows])
    for m in config['models']:
        label=m['id'].split('/')[-1]
        try:
            snapshot=ROOT/m['cache']/('models--'+m['id'].replace('/','--'))/'snapshots'/m['revision']
            t=time.perf_counter()
            model=SentenceTransformer(str(snapshot),device='cpu',local_files_only=True)
            model._experiment_identity=m
            load=time.perf_counter()-t
            interests,_=encode(model,list(LABELS),HERE/'cache',m['query_prefix'])
            for rep in config['representations']:
                texts=[document(r,rep) for r in rows]
                t=time.perf_counter();emb,hit=encode(model,texts,HERE/'cache',m['passage_prefix']);elapsed=time.perf_counter()-t
                # Warm query and document encoding timings, separate from cache reads.
                timing=[]
                for _ in range(3):
                    t=time.perf_counter();model.encode([m['query_prefix']+s for s in LABELS],normalize_embeddings=True,show_progress_bar=False);timing.append(time.perf_counter()-t)
                token_lengths=[len(model.tokenizer(t,add_special_tokens=True,truncation=False)['input_ids']) for t in [m['passage_prefix']+s for s in texts]]
                benches.append(dict(model=m['id'],revision=m['revision'],representation=rep,load_seconds=load,encode_or_cache_seconds=elapsed,cache_hit=hit,query_10_interest_median_seconds=float(np.median(timing)),parameters=sum(p.numel() for p in model.parameters()),snapshot_bytes=sum(p.stat().st_size for p in snapshot.rglob('*') if p.is_file()),embedding_bytes=emb.nbytes,max_sequence_length=model.max_seq_length,truncated_documents=sum(n>model.max_seq_length for n in token_lengths),**rss()))
                for strategy in ['weighted','centroid']:
                    for beta in config['betas']:
                        key=f'{label}/{rep}/{strategy}/beta{beta}'
                        scores=semantic(users,emb,interests,beta,strategy)
                        methods[key]=(scores,'uncalibrated_semantic')
                        changed_scores[key]=semantic(changed,emb,interests,beta,strategy)
                        # Controls use the same described rows, so no subset-selection advantage.
                        subset.append(dict(method=key,n_described=int(described.sum()),mean=float(scores[:,described].mean()),std=float(scores[:,described].std()),feature_rank_correlation=float(np.nanmean([spearmanr(sf[u,described],scores[u,described]).statistic for u in range(4,len(users))]))))
                        if strategy=='weighted' and beta==1:
                            # Diagnostic hybrids only; not selected for production or by quality.
                            for mode in ['percentile','minmax']:
                                for alpha in config['alphas']:
                                    h=f'hybrid/{key}/{mode}/alpha{alpha}'
                                    methods[h]=(hybrid(sf,scores,alpha,mode),'ranking_only')
                                    changed_scores[h]=hybrid(sf_changed,changed_scores[key],alpha,mode)
                np.savez_compressed(HERE/'cache'/f'{label}_{rep}_scores.npz',ids=np.array(ids),users=users,embeddings=emb)
                print(label,rep,'seconds',round(elapsed,2),'cache',hit,flush=True)
            del model
        except Exception as e:
            failures.append(dict(model=m['id'],error=repr(e)))
            print('MODEL FAILED',label,repr(e),flush=True)
    save('model_failures.json',failures);write('model_benchmarks.csv',benches);write('described_subset_controls.csv',subset)
    diagnostics=[]; groups=[]; recommendations=[]; individual=[]
    excluded={ids[0],ids[10]}
    for key,(scores,scale) in methods.items():
        overlaps=[]
        for u in range(len(users)):
            order=np.argsort(-scores[u],kind='stable')
            for rank,i in enumerate(order[:10],1):
                individual.append(dict(method=key,profile=f'P{u:02d}',rank=rank,place_id=ids[i],score=float(scores[u,i]),scale=scale))
            if key in changed_scores:
                new=np.argsort(-changed_scores[key][u],kind='stable')[:10]
                overlaps.append(len(set(order[:10])&set(new))/10)
        diagnostics.append(dict(method=key,scale=scale,min=float(scores.min()),p05=float(np.quantile(scores,.05)),median=float(np.median(scores)),p95=float(np.quantile(scores,.95)),max=float(scores.max()),finite_coverage=float(np.isfinite(scores).mean()),mean_distinct_scores=float(np.mean([len(np.unique(s.round(8))) for s in scores])),perturbed_food_top10_overlap=float(np.mean(overlaps)) if overlaps else '',mean_within_feature_signature_std=float(np.mean([scores[:,np.all(x==v,axis=1)].std(1).mean() for v in np.unique(x,axis=0)]))))
        for group in range(4):
            ix=np.arange(group*4,group*4+4);us=users[ix];ss=scores[ix]
            for agg in ['average','awm','gfar']:
                if agg=='awm' and scale!='survey_feature_1_5':
                    # Explicitly log unsupported comparisons rather than silently omit or rescale.
                    groups.append(dict(method=key,group=group,aggregation=agg,status='NOT_APPLICABLE_INCOMPATIBLE_SCALE',candidate_count=len(ids)-len(excluded),surviving='',returned='',member_feature_proxy_means='',mean_feature_proxy='',min_feature_proxy='',disparity_feature_proxy='',member_top20_coverage='',feature_diversity='',reason='No satisfaction calibration'))
                    continue
                selected,state=aggregate(us,x,ids,ss,agg,scale=scale,excluded=excluded)
                assert not ({ids[i] for i in selected}&excluded)
                proxy=sf[ix][:,selected].mean(1) if selected else None
                allowed=np.array([i for i,pid in enumerate(ids) if pid not in excluded])
                coverage=[float(np.isin(selected,allowed[np.argsort(-s[allowed],kind='stable')[:20]]).any()) for s in ss] if selected else []
                groups.append(dict(method=key,group=group,aggregation=agg,status='TECHNICAL_ONLY',candidate_count=state['before_awm'],surviving=state['surviving'],returned=len(selected),member_feature_proxy_means=json.dumps(proxy.tolist()) if proxy is not None else '',mean_feature_proxy=float(proxy.mean()) if proxy is not None else '',min_feature_proxy=float(proxy.min()) if proxy is not None else '',disparity_feature_proxy=float(np.ptp(proxy)) if proxy is not None else '',member_top20_coverage=json.dumps(coverage),feature_diversity=int((x[selected].sum(0)>0).sum()) if selected else '',reason=state['reason']))
                for rank,i in enumerate(selected,1):
                    recommendations.append(dict(method=key,group=group,aggregation=agg,rank=rank,place_id=ids[i],member_native_scores=json.dumps(ss[:,i].tolist()),member_feature_proxy_scores=json.dumps(sf[ix,i].tolist())))
    write('individual_diagnostics.csv',diagnostics);write('group_comparisons.csv',groups)
    write('group_recommendations.csv',recommendations);write('individual_top10.csv',individual)
    # Replicate the current TF-IDF AWM failure as a diagnostic only, outside the safe adapter.
    write('scale_mismatch_diagnostic.csv',[dict(method=key,threshold=threshold,group=g,surviving=int((scores[g*4:g*4+4]>=threshold).all(0).sum()),interpretation='Unvalidated threshold diagnostic; not misery or acceptance') for key,(scores,_) in methods.items() if key in ['tfidf_empty_actual','tfidf_positive_profile_control','legacy_weighted_reconstruction'] for threshold in [.25] for g in range(4)])
    paired=[]
    for key,(scores,scale) in methods.items():
        if '/description/' in key and not key.startswith('hybrid/'):
            base=methods[key.replace('/description/','/name_category/')][0]
            for tag,mask in [('described',described),('missing_description',~described)]:
                ix=np.flatnonzero(mask)
                paired.append(dict(method=key,subset=tag,poi_count=len(ix),mean_abs_score_change=float(np.abs(scores[:,ix]-base[:,ix]).mean()),mean_top10_overlap=float(np.mean([len(set(ix[np.argsort(-scores[u,ix],kind='stable')[:10]]) & set(ix[np.argsort(-base[u,ix],kind='stable')[:10]]))/10 for u in range(len(users))]))))
    write('paired_description_ablation.csv',paired)
    sensitivity=[]
    for key,(scores,_) in methods.items():
        if key.startswith('hybrid/') and '/percentile/' in key:
            other=methods[key.replace('/percentile/','/minmax/')][0]
            sensitivity.append(dict(method=key,mean_top10_overlap=float(np.mean([len(set(np.argsort(-s,kind='stable')[:10])&set(np.argsort(-t,kind='stable')[:10]))/10 for s,t in zip(scores,other)]))))
    write('normalization_sensitivity.csv',sensitivity)
    # Two score-blind disjoint 24-POI packets; participants/groups assigned to one split.
    selected=[]
    strata=defaultdict(list)
    for i,row in enumerate(rows):
        strata[(int(x[i].sum()),bool(described[i]))].append(i)
    for values in strata.values():
        rng.shuffle(values)
    while len(selected)<48 and any(strata.values()):
        for values in strata.values():
            if values and len(selected)<48:selected.append(values.pop())
    rng.shuffle(selected)
    write('labeling_template.csv',[dict(participant_id='',group_id='',split='heldout' if j%2 else 'development',presentation_order=j+1,place_id=ids[i],place_name=rows[i]['place_name'],category=rows[i]['category_name'],stored_description=clean(rows[i].get('description_summary')) if described[i] else '',source_url=clean(rows[i].get('description_source_url')) if described[i] else '',relevance_1_5='',willingness_to_visit_yes_no='',insufficient_evidence_yes_no='',notes='') for j,i in enumerate(selected)])
    # Preserve hashes before and after all work.
    changed_inputs=[p for p,h in protected.items() if not (ROOT/p).exists() or digest(ROOT/p)!=h]
    save('input_integrity.json',dict(before_sha256=protected,changed=changed_inputs))
    assert not changed_inputs
    save('manifest.json',dict(config=config,seed=config['seed'],git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),packages={d.metadata['Name']:d.version for d in importlib.metadata.distributions()},successful_models=len(config['models'])-len(failures),method_count=len(methods),label_status='No aligned independent labels used; synthetic profiles only',phase3='Diagnostic hybrids/group comparisons only; production adoption gate NOT MET'))
    # Simple static SVG: actual structured feature-count bias, no plotting dependency.
    counts=sorted(set(x.sum(1))); values=[float(sf[0,x.sum(1)==n].mean()) for n in counts]
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="700" height="330"><rect width="700" height="330" fill="white"/><text x="30" y="30" font-size="18">All-neutral survey: active-feature mean remains 3</text>']
    for j,(n,v) in enumerate(zip(counts,values)):
        px=70+j*140; height=v*40
        svg.append(f'<rect x="{px}" y="{260-height}" width="80" height="{height}" fill="#287e8d"/><text x="{px}" y="285">{int(n)} features</text><text x="{px}" y="{250-height}">{v:.1f}</text>')
    svg.append('</svg>');(OUT/'feature_count_bias.svg').write_text(''.join(svg))
    print('Complete:',len(methods),'methods;',len(groups),'group comparisons; protected inputs unchanged',flush=True)

if __name__=='__main__':
    main()
