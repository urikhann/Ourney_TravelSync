"""Experiment 2: source coverage and ranking robustness, no production writes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys
from time import perf_counter

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ROUND1_CODE = HERE.parent / 'enriched_poi_round1_20261028'
ROUND1 = ROOT / 'data/generated/evaluation/enriched_poi_round1_20261028'
PHASE1 = ROOT / 'data/generated/evaluation/human_evaluation_phase1_20261029_single_rater_verified'
sys.path.insert(0, str(ROUND1_CODE))
from prepare import BASE_FIELDS, EXPANDED_FIELDS, FEATURES, CANONICAL, IDENTITY_EXCLUSIONS, MODEL, clean, digest, read_csv, signature, write_csv, write_json, protected_snapshot
from run import Lexical, text_for, score_individuals, verify_frozen
from evaluate import audit_ratings, ndcg, pair_agreement

RATES = [0, 10, 25, 50, 75]
MASK_SEEDS = [11, 29, 47, 71, 101]
POOL_SEEDS = [11, 29, 47]
K = 10


def verify_original_content():
    """Keep old freeze checks; accept only two known subsequently tracked analyses."""
    try:
        verify_frozen(ROUND1)
        return {'legacy_exact_snapshot':'PASS','added_analysis_files':[]}
    except AssertionError as error:
        if str(error)!='Protected code/data changed':
            raise
        old=json.loads((ROUND1/'manifest.json').read_text(encoding='utf-8'))['protected_tracked_files']
        current=protected_snapshot()
        allowed={'notebooks/experiments/human_evaluation_phase1_20261029.py','notebooks/experiments/human_evaluation_phase1_20261029_audit.py'}
        assert old.keys() <= current.keys(), 'Originally protected file removed'
        assert all(current[p]==value for p,value in old.items()), 'Originally protected content changed'
        additions=current.keys()-old.keys()
        assert additions <= allowed, 'Unreviewed tracked additions: '+repr(sorted(additions))
        phase1=json.loads((PHASE1/'evaluation_validation.json').read_text(encoding='utf-8'))
        assert digest(ROOT/'notebooks/experiments/human_evaluation_phase1_20261029.py')==phase1['descriptive_wrapper']['sha256']
        return {'legacy_exact_snapshot':'REJECTS_KNOWN_TRACKED_ADDITIONS','original_protected_hashes':'ALL_UNCHANGED','added_analysis_files':sorted(additions),'explanation':'Two Phase 1 analysis scripts became tracked after Round 1 freeze; original verifier already checked frozen artifacts, inputs and canonical bytes before rejecting exact tracked-key equality. New experiment requires all original keys/hashes, permits only these additions, and separately protects the entire current tracked snapshot.'}


def average(values):
    values = [float(x) for x in values if x != 'N/A' and x is not None]
    return statistics.mean(values) if values else 'N/A'


def rank_info(scores, ids, k=K):
    """Exact ties; ascending stable IDs only for deterministic display."""
    scores = np.asarray(scores)
    valid = np.flatnonzero(np.isfinite(scores)).tolist()
    order = sorted(valid, key=lambda i: (-float(scores[i]), ids[i]))
    counts = Counter(float(scores[i]) for i in valid)
    n = len(valid)
    midranks = np.full(len(ids), np.nan)
    probability = np.zeros(len(ids))
    start = 0
    by_score = defaultdict(list)
    for i in valid:
        by_score[float(scores[i])].append(i)
    for value in sorted(by_score, reverse=True):
        band = by_score[value]
        size = len(band)
        midranks[band] = start + (size + 1) / 2
        probability[band] = max(0, min(size, k-start)) / size
        start += size
    pairs = n*(n-1)//2
    return {'order':order, 'top':order[:k], 'midranks':midranks, 'probability':probability,
            'rankable':n, 'bands':len(counts), 'ties':sum(v*(v-1)//2 for v in counts.values()),
            'pair_count':pairs, 'tie_rate':sum(v*(v-1)//2 for v in counts.values())/pairs if pairs else 'N/A'}


def stability(reference, observed, ids, k=K):
    base, changed = rank_info(reference, ids, k), rank_info(observed, ids, k)
    bt, ct = set(base['top']), set(changed['top'])
    eligible = np.isfinite(base['midranks']) & np.isfinite(changed['midranks'])
    x, y = base['midranks'][eligible], changed['midranks'][eligible]
    corr = float(np.corrcoef(x,y)[0,1]) if len(x)>1 and np.ptp(x)>0 and np.ptp(y)>0 else 'N/A'
    denominator = min(k, base['rankable'])
    return {'topk_id_retention':len(bt & ct)/len(bt) if bt else 'N/A',
            'topk_id_jaccard':len(bt & ct)/len(bt | ct) if bt | ct else 'N/A',
            'topk_membership_probability_overlap':float(np.minimum(base['probability'],changed['probability']).sum())/denominator if denominator else 'N/A',
            'spearman_average_tie_ranks':corr,
            'spearman_jointly_rankable':int(eligible.sum()), 'topk_returned':len(ct)}


def lexical_encode(model, texts):
    unique = sorted(set(texts))
    vectors = model.encode(unique)
    index = {t:i for i,t in enumerate(unique)}
    return vectors[[index[t] for t in texts]]


def load_lexical(spec):
    # Restore frozen vocabulary/IDF without calling fit/constructor.
    model = object.__new__(Lexical)
    model.vocab = spec['vocabulary']
    model.index = {v:i for i,v in enumerate(model.vocab)}
    model.idf = np.asarray(spec['idf'])
    return model


class SemanticCache:
    def __init__(self, texts, timings):
        import torch
        from sentence_transformers import SentenceTransformer
        recorded_hashes=json.loads((ROUND1/'results/semantic_metadata.json').read_text(encoding='utf-8'))['model_file_hashes']
        assert all(digest(ROUND1_CODE/'.model-cache'/name)==value for name,value in recorded_hashes.items()), 'Pinned local model cache changed'
        torch.set_num_threads(1)
        torch.manual_seed(20261029)
        torch.use_deterministic_algorithms(True)
        start = perf_counter()
        self.model = SentenceTransformer(MODEL['id'],revision=MODEL['revision'],cache_folder=str(ROUND1_CODE/'.model-cache'),local_files_only=True,device='cpu',trust_remote_code=False)
        self.model.eval()
        assert self.model.max_seq_length == 128
        timings.append({'stage':'D_model_load_offline','domain':'all','seconds':perf_counter()-start,'document_count':0,'notes':'CPU; pinned locally cached model; no download/training'})
        self.texts = sorted({t for t in texts if t})
        start = perf_counter()
        vectors = self.model.encode(self.texts,batch_size=16,normalize_embeddings=True,show_progress_bar=False)
        repeat = self.model.encode(self.texts,batch_size=16,normalize_embeddings=True,show_progress_bar=False)
        assert np.array_equal(vectors,repeat)
        self.lookup = {t:v for t,v in zip(self.texts,vectors)}
        self.lookup[''] = np.zeros(384,dtype=np.float32)
        lengths = [len(self.model.tokenizer(t,truncation=False)['input_ids']) for t in self.texts]
        self.metadata = {**MODEL,'pinned_model_file_hashes_verified':recorded_hashes,'unique_nonempty_texts':len(self.texts),'max_untruncated_tokens':max(lengths),'truncated_text_count':sum(n>128 for n in lengths),'repeat_embeddings_byte_identical':True,'execution':'CPU float32, one torch thread, deterministic eval, normalized embeddings; empty inputs mapped to zero rather than an empty-string semantic embedding'}
        timings.append({'stage':'D_unique_text_encode_and_repeat','domain':'all','seconds':perf_counter()-start,'document_count':len(self.texts),'notes':'Includes exact repeat check; shared deduplicated cache across domains'})

    def encode(self, texts):
        return np.asarray([self.lookup[t] for t in texts])


def audit(out):
    canonical = read_csv(CANONICAL)
    enriched = read_csv(ROUND1/'inputs/travelsync_demo_candidates_enriched_v1.csv')
    assert len(canonical)==3570 and len({r['place_id'] for r in canonical})==3570
    assert len(enriched)==40 and len({r['place_id'] for r in enriched})==40
    review = json.loads((HERE/'source_review.json').read_text(encoding='utf-8'))
    overlay = {r['place_id']:r for r in review['records'] if r['description_summary']}
    emap = {r['place_id']:r for r in enriched}
    rows, coverage = [], []
    for original in sorted(canonical,key=lambda r:r['place_id']):
        pid = original['place_id']
        enriched_row = emap.get(pid)
        r = dict(original)
        for f in EXPANDED_FIELDS:
            r[f] = enriched_row[f] if enriched_row else ''
        field_sources = json.loads(enriched_row['field_provenance_json']) if enriched_row else []
        has_desc = bool(clean(r['description_summary']))
        source_supported = has_desc and any('description_summary' in s['fields'] and s.get('url') and s.get('checked_at') for s in field_sources)
        provenance = ' | '.join(s['url'] for s in field_sources if 'description_summary' in s['fields'])
        original_desc = r['description_summary']
        if pid in overlay:
            r['description_summary'] = overlay[pid]['description_summary']
            source_supported = True
            provenance = overlay[pid]['source_url']
        excluded = pid in IDENTITY_EXCLUSIONS
        # Do not leak source-score, selection, review or operating fields into model text.
        if excluded or not source_supported:
            r['description_summary'] = ''
        if not enriched_row:
            for f in BASE_FIELDS:
                r[f] = ''
        rows.append(r)
        coverage.append({'place_id':pid,'place_name':r['place_name'],'source':r['source'],'category_name':r['category_name'],'feature_signature':signature(r),'in_40_poi_enrichment':bool(enriched_row),'canonical_has_narrative_column':False,'original_sidecar_description_present':has_desc,'overlay_description_added':pid in overlay,'source_supported_before_identity_exclusion':bool(source_supported),'usable_description':bool(clean(r['description_summary'])),'primary_excluded':excluded,'exclusion_reason':IDENTITY_EXCLUSIONS.get(pid,''),'description_source_url':provenance,'original_sidecar_description':original_desc,'usable_description_summary':r['description_summary'],'provenance_scope':'Existing dated field provenance; only eight targeted source issues reviewed live; no blanket current revalidation'})
    write_csv(out/'coverage_by_poi.csv',coverage)
    grouped = []
    for dimension in ['taxonomy_top','feature_signature',*FEATURES]:
        keys = sorted({r['category_name'].split('>')[0].strip() for r in rows}) if dimension=='taxonomy_top' else sorted({signature(r) for r in rows}) if dimension=='feature_signature' else [dimension]
        for key in keys:
            selected = [i for i,r in enumerate(rows) if (r['category_name'].split('>')[0].strip()==key if dimension=='taxonomy_top' else signature(r)==key if dimension=='feature_signature' else float(r[dimension])==1)]
            grouped.append({'group_dimension':dimension,'group_value':key,'canonical_pois':len(selected),'enriched_40_pois':sum(coverage[i]['in_40_poi_enrichment'] for i in selected),'original_sidecar_descriptions':sum(coverage[i]['original_sidecar_description_present'] for i in selected),'source_supported_descriptions_before_exclusion':sum(coverage[i]['source_supported_before_identity_exclusion'] for i in selected),'usable_descriptions':sum(coverage[i]['usable_description'] for i in selected),'usable_description_fraction':sum(coverage[i]['usable_description'] for i in selected)/len(selected) if selected else 'N/A','taxonomy_text_available':sum(bool(clean(rows[i]['category_name'])) for i in selected),'note':'Binary feature groups overlap; taxonomy_top and feature_signature groups partition the catalogue'})
    write_csv(out/'coverage_by_category.csv',grouped)
    write_json(out/'source_review.json',review)
    write_csv(out/'enrichment_issue_audit.csv',review['records'])
    write_csv(out/'description_overlay.csv',[{'place_id':r['place_id'],'description_summary':r['description_summary'],'source_url':r['source_url'],'review_date_korea':review['review_date_korea'],'identity_evidence':r['identity_evidence'],'limitations':r['limitations']} for r in overlay.values()])
    return rows,coverage


def construct_pools(rows):
    safe = [r for r in rows if r['place_id'] not in IDENTITY_EXCLUSIONS]
    enriched_ids = {r['place_id'] for r in read_csv(ROUND1/'inputs/travelsync_demo_candidates_enriched_v1.csv')} - IDENTITY_EXCLUSIONS.keys()
    strict = [r for r in safe if r['place_id'] in enriched_ids]
    all_ids = sorted(r['place_id'] for r in safe)
    pools = [('enriched38','original_fields',sorted(enriched_ids),'enriched file minus two disputed records; multiple feature signatures; natural missingness retained')]
    by_signature = defaultdict(list)
    for r in safe:
        by_signature[signature(r)].append(r['place_id'])
    for seed in POOL_SEEDS:
        rng = random.Random(seed)
        anchor = set(enriched_ids)
        for sig,ids in sorted(by_signature.items()):
            if not anchor.intersection(ids):
                anchor.add(rng.choice(sorted(ids)))
        remaining = [pid for pid in all_ids if pid not in anchor]
        rng.shuffle(remaining)
        order = sorted(anchor) + remaining
        for n in [100,500,1000]:
            pools.append((f'mixed{n}_seed{seed}','taxonomy_extension',sorted(order[:n]),'Nested seeded canonical sample; includes all 38 safe enriched records and at least one per feature signature; deliberately enrichment-anchored, not representative prevalence'))
    pools.append(('catalogue3568','taxonomy_extension',all_ids,'All canonical rows except the same two disputed records; no description-coverage inflation from selecting enriched anchors'))
    return strict,safe,pools


def make_masks(described_ids):
    masks = []
    for seed in MASK_SEEDS:
        ids = sorted(described_ids)
        random.Random(seed).shuffle(ids)
        for rate in RATES:
            if rate==0 and seed!=MASK_SEEDS[0]:
                continue
            count = math.floor(len(ids)*rate/100+0.5)
            masks.append({'mask_id':f'p{rate}_s{seed}','requested_percent':rate,'seed':seed,'masked_place_ids':sorted(ids[:count]),'masked_count':count,'available_description_count':len(ids),'actual_mask_fraction':count/len(ids)})
    return sorted(masks,key=lambda m:(m['requested_percent'],m['seed']))


def policy_scores(policy,b,c,available):
    if policy=='C_native':
        return c
    if policy=='B_global':
        return b
    if policy=='C_described_only':
        return np.where(available,c,np.nan)
    if policy=='B_backbone_C_ties':
        # Ordinal encoding of lexicographic (B,C) bands, never an arithmetic blend.
        keys = sorted(set(zip(b.tolist(),c.tolist())))
        index = {key:i for i,key in enumerate(keys)}
        return np.asarray([index[key] for key in zip(b.tolist(),c.tolist())],dtype=float)
    raise ValueError(policy)


def diagnostics(scores, ids):
    info = rank_info(scores,ids)
    finite = np.isfinite(scores)
    values = scores[finite]
    return {'candidates':len(ids),'k':min(K,len(ids)),'rankable_count':info['rankable'],'rankable_fraction':info['rankable']/len(ids),'distinct_score_bands':info['bands'],'exact_tied_pairs':info['ties'],'applicable_candidate_pairs':info['pair_count'],'exact_tied_pair_fraction':info['tie_rate'],'positive_score_fraction':float((values>0).sum())/len(ids),'zero_score_count':int((values==0).sum()),'score_min':float(values.min()) if len(values) else 'N/A','score_max':float(values.max()) if len(values) else 'N/A','score_std':float(values.std()) if len(values) else 'N/A','interpretation':'Ranking diagnostic, not relevance; compare raw scores only within same method. Composite policy uses ordinal band codes.'}


def frozen_relevance(out,masks,semantic):
    rankings = read_csv(ROUND1/'results/scores_ranks.csv')
    scenarios = json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
    original_rows = read_csv(ROUND1/'inputs/travelsync_demo_candidates_enriched_v1.csv')
    omap = {r['place_id']:r for r in original_rows}
    models = json.loads((ROUND1/'results/lexical_models.json').read_text(encoding='utf-8'))
    c = load_lexical(models['C'])
    queries = [clean(s['preference_ko']) for s in scenarios]
    query_vectors = c.encode(queries)
    sindex = {s['scenario_id']:i for i,s in enumerate(scenarios)}
    grouped = defaultdict(list)
    for r in rankings:
        grouped[(r['scenario_id'],r['method'])].append(r)
    ids = sorted({r['place_id'] for r in rankings})
    meta_vectors = c.encode([text_for(omap[pid],BASE_FIELDS) for pid in ids])
    c_meta = query_vectors @ meta_vectors.T
    iindex = {pid:i for i,pid in enumerate(ids)}
    d_meta = None
    if semantic:
        frozen_embeddings = np.load(ROUND1/'results/embeddings.npy',allow_pickle=False)
        assert frozen_embeddings.shape==(len(original_rows)+len(scenarios),384)
        d_meta = frozen_embeddings[-len(scenarios):] @ semantic.encode([text_for(omap[pid],BASE_FIELDS) for pid in ids]).T
    labels = {rid:audit_ratings(PHASE1/f'validated_adapter_{rid}.csv',ROUND1/f'results/annotation_{rid}.csv',rid) for rid in ['R1','R2']}
    results = []
    for mask in masks:
        masked = set(mask['masked_place_ids'])
        for (sid,method),rows in sorted(grouped.items()):
            if method=='D' and semantic is None:
                continue
            observed = [dict(r) for r in rows]
            for r in observed:
                if r['place_id'] in masked and method in ['C','D']:
                    r['score'] = float((c_meta if method=='C' else d_meta)[sindex[sid],iindex[r['place_id']]])
            observed.sort(key=lambda r:(-float(r['score']),r['place_id']))
            for rank,r in enumerate(observed,1):
                r['rank'] = rank
            for rid,judgments in labels.items():
                available = {r['place_id']:judgments[(sid,r['place_id'])] for r in observed if judgments[(sid,r['place_id'])] is not None}
                missing = len(available)!=len(observed)
                value = ndcg(observed,available,min(3,len(observed))) if not missing else None
                fallback = ndcg(observed,available,min(3,len(observed)),False) if not missing else None
                pair,count = pair_agreement(observed,available)
                results.append({'mask_id':mask['mask_id'],'requested_percent':mask['requested_percent'],'seed':mask['seed'],'scenario_id':sid,'sheet_id':rid,'actual_human_rater_count':1,'method':method,'candidates':len(observed),'k':min(3,len(observed)),'masked_in_this_scenario':sum(r['place_id'] in masked for r in observed),'ndcg_tie_expected':value if value is not None else 'N/A','ndcg_id_fallback':fallback if fallback is not None else 'N/A','pairwise_agreement':pair if pair is not None else 'N/A','human_strict_pairs':count,'ndcg_exclusion':'insufficient evidence' if missing else 'zero ideal DCG' if value is None else '', 'note':'Original frozen models/scores/candidate groups; fixed original IDF; no new human labels; two sheets from one person'})
    write_csv(out/'annotated_mask_metrics.csv',results)
    summary = []
    for rate,method,rid in itertools.product(RATES,sorted({r['method'] for r in results}),['R1','R2']):
        selected = [r for r in results if r['requested_percent']==rate and r['method']==method and r['sheet_id']==rid]
        seed_means = [average([r['ndcg_tie_expected'] for r in selected if r['seed']==seed]) for seed in sorted({r['seed'] for r in selected})]
        summary.append({'requested_percent':rate,'method':method,'sheet_id':rid,'mean_scenario_macro_ndcg':average(seed_means),'min_seed_macro_ndcg':min(seed_means),'max_seed_macro_ndcg':max(seed_means),'mean_id_fallback_ndcg':average([r['ndcg_id_fallback'] for r in selected]),'mean_scenario_macro_pairwise':average([r['pairwise_agreement'] for r in selected]),'evaluated_scenario_seed_rows':sum(r['ndcg_tie_expected']!='N/A' for r in selected),'excluded_rows':sum(r['ndcg_tie_expected']=='N/A' for r in selected),'seed_count':len(seed_means),'interpretation':'Descriptive masking sensitivity for one human; random masks are not independent relevance samples'})
    write_csv(out/'annotated_mask_summary.csv',summary)
    previous = read_csv(PHASE1/'per_scenario_metrics.csv')
    lookup = {(r['label_set'],r['scenario_id'],r['method']):r for r in previous if r['label_set']!='mean_R1_R2'}
    for r in results:
        if r['requested_percent']==0:
            old = lookup[(r['sheet_id'],r['scenario_id'],r['method'])]
            assert float(r['ndcg_tie_expected'])==float(old['ndcg_tie_expected'])
            assert float(r['ndcg_id_fallback'])==float(old['ndcg_id_fallback'])
    return summary


def run(out,with_semantic=True):
    if out.exists():
        raise FileExistsError('Choose a new result directory; never overwrite frozen runs')
    out.mkdir(parents=True)
    legacy_check = verify_original_content()
    tracked_before = protected_snapshot()
    protected = [p for directory in [ROUND1,PHASE1] for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    source_manifest = json.loads((ROUND1/'manifest.json').read_text(encoding='utf-8'))
    protected += [Path(r['original_path']) for r in source_manifest['inputs'] if Path(r['original_path']).exists()]
    hashes_before = {str(p):digest(p) for p in protected}
    config = {'experiment':'POI enrichment coverage and ranking robustness','version':'20261029_v1','description_mask_percentages':RATES,'mask_seeds':MASK_SEEDS,'pool_seeds':POOL_SEEDS,'pool_sizes':[38,100,500,1000,3568],'diagnostic_k':K,'annotated_k':'min(3, original group size)','scope':['original_fields','taxonomy_extension'],'fit_policy':'B/C each fit once per domain on all eligible domain documents; queries excluded; freeze IDF/vocabulary before masking. No fit per pool or mask. Annotated relevance uses separately restored original frozen models/scores only.','text_allowlist':{'original_B':list(BASE_FIELDS),'original_C':list(EXPANDED_FIELDS),'taxonomy_B':['category_name','food_type','representative_menu','activity_type'],'taxonomy_C':['category_name','food_type','representative_menu','activity_type','description_summary']},'fallback_policies':['C_native','B_global','B_backbone_C_ties','C_described_only'],'masking':'Additional MCAR removal among usable descriptions only; floor(n*p/100+0.5) counts; nested order per seed; natural missingness retained. Mask IDs fixed globally and shared across all pools/methods.','semantic_requested':with_semantic,'rank_correlation':'Pearson correlation of average ranks under exact score ties; constant rank vectors -> N/A; use jointly rankable items and report denominator','topk_ties':'Report ascending-ID fallback retention/Jaccard separately from probability-membership overlap sum(min(p_before,p_after))/min(k,baseline_rankable); identical all-tied rankings have overlap1 but no relevance evidence','no_relevance_labels_for_new_pools':True,'single_human_two_sheets':True,'identity_exclusions':IDENTITY_EXCLUSIONS,'no_gfar_integration':True}
    write_json(out/'config.json',config)
    write_json(out/'freeze_manifest.json',{'created_utc':datetime.now(timezone.utc).isoformat(),'client_date_korea':'2026-10-29','git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'config_sha256':digest(out/'config.json'),'source_review_sha256':digest(HERE/'source_review.json'),'code_sha256':{p.name:digest(p) for p in HERE.glob('*.py')},'canonical_sha256':digest(CANONICAL),'round1_manifest_sha256':digest(ROUND1/'manifest.json'),'phase1_validation_sha256':digest(PHASE1/'evaluation_validation.json'),'python':sys.version,'platform':platform.platform(),'packages':{name:importlib.metadata.version(name) for name in ['numpy','torch','sentence-transformers','transformers'] if name=='numpy' or with_semantic}})
    timings = []
    rows,coverage = audit(out)
    strict,full,pools = construct_pools(rows)
    domains = {'original_fields':strict,'taxonomy_extension':full}
    index = {domain:{r['place_id']:i for i,r in enumerate(rs)} for domain,rs in domains.items()}
    rowmap = {r['place_id']:r for r in rows}
    pool_summary = []
    memberships = []
    for name,domain,ids,note in pools:
        pool_summary.append({'pool_id':name,'domain':domain,'candidate_count':len(ids),'feature_signatures':len({signature(rowmap[p]) for p in ids}),'usable_descriptions':sum(bool(clean(rowmap[p]['description_summary'])) for p in ids),'description_fraction':sum(bool(clean(rowmap[p]['description_summary'])) for p in ids)/len(ids),'selection_policy':note})
        memberships.extend({'pool_id':name,'domain':domain,'place_id':pid,'feature_signature':signature(rowmap[pid])} for pid in ids)
    write_csv(out/'candidate_pools.csv',pool_summary)
    write_csv(out/'pool_memberships.csv',memberships)
    scenarios = json.loads((ROUND1/'scenarios.json').read_text(encoding='utf-8'))
    write_json(out/'scenarios.json',[{**s,'new_pool_relevance_labels':False,'scope_note':'Original preference text/vector reused on mixed-feature pools; synthetic query diagnostic, no new relevance claims'} for s in scenarios])
    described_ids = {r['place_id'] for r in full if clean(r['description_summary'])}
    masks = make_masks(described_ids)
    write_json(out/'description_masks.json',masks)
    docs = {}
    for domain,rs in domains.items():
        metadata = [text_for(r,BASE_FIELDS) if domain=='original_fields' else ' '.join(v for f in ['category_name','food_type','representative_menu','activity_type'] if (v:=clean(r[f]))) for r in rs]
        desc = [clean(r['description_summary']) for r in rs]
        docs[domain] = {'metadata':metadata,'expanded':[' '.join(p for p in [m,d] if p) for m,d in zip(metadata,desc)],'has_description':np.asarray([bool(d) for d in desc])}
    queries = [clean(s['preference_ko']) for s in scenarios]
    write_csv(out/'model_inputs.csv',[{'domain':domain,'place_id':r['place_id'],'B_text':docs[domain]['metadata'][i],'C_D_text':docs[domain]['expanded'][i],'usable_description':bool(docs[domain]['has_description'][i]),'feature_signature':signature(r)} for domain,rs in domains.items() for i,r in enumerate(rs)])
    # Freeze the actual candidate/input/mask data before fitting/scoring.
    write_json(out/'pre_scoring_freeze.json',{name:digest(out/name) for name in ['config.json','coverage_by_poi.csv','model_inputs.csv','pool_memberships.csv','candidate_pools.csv','scenarios.json','description_masks.json','source_review.json']})
    semantic = None
    if with_semantic:
        old = read_csv(ROUND1/'inputs/travelsync_demo_candidates_enriched_v1.csv')
        semantic = SemanticCache(queries+[t for d in docs.values() for name in ['metadata','expanded'] for t in d[name]]+[text_for(r,BASE_FIELDS) for r in old],timings)
        write_json(out/'semantic_metadata.json',semantic.metadata)
    matrices,models = {},{}
    for domain,rs in domains.items():
        start = perf_counter()
        b,c = Lexical(docs[domain]['metadata']),Lexical(docs[domain]['expanded'])
        timings.append({'stage':'BC_fit_once','domain':domain,'seconds':perf_counter()-start,'document_count':len(rs),'notes':'Separate B/C IDF; one shared domain corpus, queries never fit'})
        start = perf_counter()
        qb,qc = b.encode(queries),c.encode(queries)
        db,dc,dm = lexical_encode(b,docs[domain]['metadata']),lexical_encode(c,docs[domain]['expanded']),lexical_encode(c,docs[domain]['metadata'])
        preference = np.asarray([[s['preference_vector'][f] for f in FEATURES] for s in scenarios])
        features = np.asarray([[float(r[f]) for f in FEATURES] for r in rs])
        data = {'A':score_individuals(preference,features),'B':qb@db.T,'C':qc@dc.T,'C_metadata_projection':qc@dm.T}
        timings.append({'stage':'ABC_encode_and_score','domain':domain,'seconds':perf_counter()-start,'document_count':len(rs),'notes':'Includes unique-text encoding, query encoding and full query-document multiplication; no fitting'})
        if semantic:
            qd = semantic.encode(queries)
            data['D'] = qd@semantic.encode(docs[domain]['expanded']).T
            data['D_metadata_projection'] = qd@semantic.encode(docs[domain]['metadata']).T
        assert all(np.isfinite(v).all() for v in data.values())
        matrices[domain]=data
        models[domain]={'B':{'vocabulary':b.vocab,'idf':b.idf.tolist()},'C':{'vocabulary':c.vocab,'idf':c.idf.tolist()}}
        np.savez_compressed(out/f'scores_{domain}.npz',**data)
        # Warm-cache latency benchmark: fixed already-encoded queries/documents plus full stable sort.
        timing_matrices = {'A':(preference,features),'B':(qb,db),'C':(qc,dc)}
        if semantic:
            timing_matrices['D']=(qd,semantic.encode(docs[domain]['expanded']))
        for name,pool_domain,ids,note in pools:
            if pool_domain!=domain:
                continue
            indexes=[index[domain][pid] for pid in ids]
            for method,(query,document) in timing_matrices.items():
                for repeat in range(3):
                    for qi in range(len(queries)):
                        start=perf_counter()
                        vals = score_individuals(query[qi:qi+1],document[indexes])[0] if method=='A' else query[qi]@document[indexes].T
                        order = sorted(range(len(ids)),key=lambda i:(-float(vals[i]),ids[i]))[:min(K,len(ids))]
                        assert len(order)==min(K,len(ids))
                        timings.append({'stage':'warm_encoded_query_score_and_full_sort','domain':domain,'pool_id':name,'method':method,'repeat':repeat,'scenario_id':scenarios[qi]['scenario_id'],'seconds':perf_counter()-start,'document_count':len(ids),'notes':'Includes gathering candidate vectors, scoring, and deterministic full sort; excludes model load/fit/text/query encoding, network, DB, UI'})
    write_json(out/'lexical_models.json',models)
    # Record online query encoding cost separately from candidate-dependent score/sort latency.
    for domain in domains:
        for method in ['B','C']:
            model=load_lexical(models[domain][method])
            for query in queries:
                start=perf_counter(); model.encode([query]); seconds=perf_counter()-start
                timings.append({'stage':'single_query_encoding','domain':domain,'method':method,'seconds':seconds,'document_count':0,'notes':'Original lexical encoder; no scoring or fitting'})
    if semantic:
        for query in queries:
            start=perf_counter(); semantic.model.encode([query],normalize_embeddings=True,show_progress_bar=False); seconds=perf_counter()-start
            timings.append({'stage':'single_query_encoding','domain':'all','method':'D','seconds':seconds,'document_count':0,'notes':'Warm pinned CPU transformer inference, one query; no candidate scoring'})
    baseline,top_rows,changes,robust=[],[],[],[]
    for name,domain,ids,note in pools:
        positions=[index[domain][pid] for pid in ids]
        data=matrices[domain]
        for qi,s in enumerate(scenarios):
            sid=s['scenario_id']
            available=docs[domain]['has_description'][positions]
            method_scores={m:v[qi,positions] for m,v in data.items() if 'projection' not in m}
            for m,vals in method_scores.items():
                baseline.append({'pool_id':name,'domain':domain,'scenario_id':sid,'method':m,**diagnostics(vals,ids)})
                for rank,pi in enumerate(rank_info(vals,ids)['top'],1):
                    top_rows.append({'pool_id':name,'domain':domain,'scenario_id':sid,'method':m,'rank_id_fallback':rank,'place_id':ids[pi],'place_name':rowmap[ids[pi]]['place_name'],'score':float(vals[pi]),'usable_description':bool(available[pi]),'feature_signature':signature(rowmap[ids[pi]])})
            for a,b in itertools.combinations(method_scores,2):
                changes.append({'pool_id':name,'domain':domain,'scenario_id':sid,'reference_method':a,'comparison_method':b,**stability(method_scores[a],method_scores[b],ids)})
            policies = ['C_native','B_global','B_backbone_C_ties','C_described_only'] + (['D_native'] if semantic else [])
            reference={p:method_scores['D'] if p=='D_native' else policy_scores(p,method_scores['B'],method_scores['C'],available) for p in policies}
            for mask in masks:
                lost=np.asarray([pid in mask['masked_place_ids'] for pid in ids])
                c_masked=np.where(lost,data['C_metadata_projection'][qi,positions],method_scores['C'])
                for policy in policies:
                    vals = np.where(lost,data['D_metadata_projection'][qi,positions],method_scores['D']) if policy=='D_native' else policy_scores(policy,method_scores['B'],c_masked,available & ~lost)
                    metric=diagnostics(vals,ids)
                    if policy=='B_backbone_C_ties':
                        for field in ['positive_score_fraction','zero_score_count','score_min','score_max','score_std']:
                            metric[field]='N/A'
                    robust.append({'pool_id':name,'domain':domain,'scenario_id':sid,'mask_id':mask['mask_id'],'requested_percent':mask['requested_percent'],'seed':mask['seed'],'policy':policy,'natural_described_count':int(available.sum()),'masked_described_in_pool':int((available & lost).sum()),'remaining_described_in_pool':int((available & ~lost).sum()),'additional_mask_fraction_of_described_pool':float((available & lost).sum())/int(available.sum()),**metric,**stability(reference[policy],vals,ids),'zero_information_note':'A computable finite/zero score is not evidence of relevance or informative query matching'})
    write_csv(out/'baseline_ranking_diagnostics.csv',baseline)
    write_csv(out/'baseline_top10.csv',top_rows)
    write_csv(out/'between_method_ranking_changes.csv',changes)
    write_csv(out/'masking_robustness.csv',robust)
    summary=[]
    for name,domain,ids,note in pools:
        for rate,policy in itertools.product(RATES,sorted({r['policy'] for r in robust})):
            selected=[r for r in robust if r['pool_id']==name and r['requested_percent']==rate and r['policy']==policy]
            summary.append({'pool_id':name,'domain':domain,'requested_percent':rate,'policy':policy,'query_seed_rows':len(selected),'mean_rankable_fraction':average([r['rankable_fraction'] for r in selected]),'mean_positive_score_fraction':average([r['positive_score_fraction'] for r in selected]),'mean_exact_tied_pair_fraction':average([r['exact_tied_pair_fraction'] for r in selected]),'mean_topk_id_retention':average([r['topk_id_retention'] for r in selected]),'mean_topk_id_jaccard':average([r['topk_id_jaccard'] for r in selected]),'mean_topk_membership_probability_overlap':average([r['topk_membership_probability_overlap'] for r in selected]),'min_topk_id_retention':min(r['topk_id_retention'] for r in selected),'mean_spearman_average_tie_ranks':average([r['spearman_average_tie_ranks'] for r in selected]),'undefined_spearman_rows':sum(r['spearman_average_tie_ranks']=='N/A' for r in selected),'mean_topk_returned':average([r['topk_returned'] for r in selected]),'remaining_described_count':selected[0]['remaining_described_in_pool'],'note':'Average over 12 original queries and stated random masks; seed spread is perturbation variability, not a confidence interval'})
    write_csv(out/'masking_summary.csv',summary)
    relevance_summary=frozen_relevance(out,masks,semantic)
    fields=sorted(set().union(*(r.keys() for r in timings)))
    write_csv(out/'runtime_measurements.csv',timings,fields)
    runtime_summary=[]
    for name,domain,ids,note in pools:
        for method in ['A','B','C']+(['D'] if semantic else []):
            values=[r['seconds']*1000 for r in timings if r['stage']=='warm_encoded_query_score_and_full_sort' and r.get('pool_id')==name and r.get('method')==method]
            runtime_summary.append({'pool_id':name,'method':method,'candidates':len(ids),'warm_score_sort_median_ms':statistics.median(values),'warm_score_sort_p95_ms':float(np.percentile(values,95)),'measurements':len(values),'notes':'Warm preencoded query/documents; 12 queries x 3 repeats; no end-to-end service or cold-start claim'})
    write_csv(out/'runtime_summary.csv',runtime_summary)
    assert protected_snapshot()==tracked_before
    assert all(digest(p)==value for p,value in hashes_before.items())
    assert verify_original_content()==legacy_check
    # Pre-scoring artifacts must not be mutated during the run.
    assert all(digest(out/name)==value for name,value in json.loads((out/'pre_scoring_freeze.json').read_text()).items())
    verification={'status':'PASS','protected_input_file_count':len(hashes_before),'protected_source_hashes_unchanged':True,'canonical_and_production_tracked_snapshot_unchanged':True,'original_freeze_check_before_and_after':legacy_check,'original_NDCG_zero_mask_exactly_reproduced':True,'no_new_pool_NDCG':True,'mask_settings':len(masks),'scenario_count':len(scenarios),'pool_count':len(pools),'baseline_diagnostic_rows':len(baseline),'robustness_rows':len(robust),'source_hashes':hashes_before,'pre_scoring_artifacts_unchanged':True,'all_scores_finite':True,'model_code_sha256':{str(p):digest(p) for p in [ROUND1_CODE/'run.py',ROUND1_CODE/'evaluate.py',Path(__file__)]},'commands':{'run':subprocess.list2cmdline([sys.executable,str(Path(__file__).resolve()),'--out',str(out)]+([] if semantic else ['--no-semantic']))}}
    write_json(out/'verification.json',verification)
    from robustness_report_builder import build_report
    build_report(out,coverage,pool_summary,baseline,summary,relevance_summary,runtime_summary)
    write_json(out/'output_hashes.json',{str(p.relative_to(out)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps({'output':str(out),'canonical':len(rows),'source_descriptions':sum(r['original_sidecar_description_present'] for r in coverage),'usable_descriptions':len(described_ids),'pools':len(pools),'mask_settings':len(masks),'baseline_rows':len(baseline),'robustness_rows':len(robust),'semantic':bool(semantic),'preservation':'PASS'},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'data/generated/evaluation/poi_robustness_20261029')
    p.add_argument('--no-semantic',action='store_true')
    args=p.parse_args()
    run(args.out.resolve(),not args.no_semantic)
