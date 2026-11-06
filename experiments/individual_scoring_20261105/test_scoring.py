import numpy as np
import pytest
from scoring import structured, semantic, document, normalize, hybrid, aggregate

def fixtures():
    x=np.eye(10)[:3]
    u=np.full((2,10),3.)
    return u,x,['a','b','c']

def test_neutral_and_single_feature_are_not_misery():
    u,x,ids=fixtures();scores,missing=structured(u,x)
    assert np.all(scores==3) and not missing.any()
    result,state=aggregate(u,x,ids,scores,'awm',scale='survey_feature_1_5')
    assert len(result)==3 and state['surviving']==3

def test_relevant_rating_and_multiple_features():
    u,x,_=fixtures();x[1]=x[0]+x[1];u[0,0]=5
    scores,_=structured(u,x)
    assert scores[0,0]==5 and scores[0,1]==4 and scores[1,0]==3

def test_unclassified_is_nan_not_satisfaction():
    u,x,_=fixtures();x[0]=0
    scores,missing=structured(u,x)
    assert missing[0] and np.isnan(scores[:,0]).all()

@pytest.mark.parametrize('method',['average','awm','gfar'])
def test_constraints_apply_to_all_aggregators(method):
    u,x,ids=fixtures();s,_=structured(u,x)
    result,_=aggregate(u,x,ids,s,method,scale='survey_feature_1_5',excluded={'a'},eligible={'a','b'})
    assert result==[1]

def test_missing_and_conflicting_description_not_fabricated():
    row={'place_name':'실제 장소','category_name':'카페','description_summary':'NULL'}
    assert document(row,'description')=='실제 장소 카페'
    row.update(description_summary='Unsupported text',description_source_url='https://example.org',detail_verification_status='CONFLICT')
    assert 'Unsupported' not in document(row,'description')

@pytest.mark.parametrize('scale',['cosine','uncalibrated_semantic','ranking_only','legacy_unvalidated'])
def test_awm_rejects_incompatible_scales(scale):
    u,x,ids=fixtures()
    with pytest.raises(ValueError,match='AWM requires'):
        aggregate(u,x,ids,np.full((2,3),.1),'awm',scale=scale)

def test_exhaustion_reported():
    u,x,ids=fixtures();u[:]=1;s,_=structured(u,x)
    result,state=aggregate(u,x,ids,s,'awm',scale='survey_feature_1_5')
    assert result==[] and state['reason']=='awm_threshold' and state['before_awm']==3
    _,state=aggregate(u,x,ids,s,'gfar',scale='survey_feature_1_5',eligible=set())
    assert state['reason']=='hard_constraints_or_unclassified'

def test_gfar_preserves_individual_rankings():
    u,x,ids=fixtures()
    scores=np.array([[5,1,3],[1,5,3]])
    selected,_=aggregate(u,x,ids,scores,'gfar',scale='survey_feature_1_5',k=2)
    assert set(selected)=={0,1}

def test_dislike_average_hiding_and_lambda_zero():
    u,x,_=fixtures();x[0,1]=1;u[0,:2]=[5,1]
    base,_=structured(u,x);pen,_=structured(u,x,.5)
    assert base[0,0]==3 and pen[0,0]==2
    assert np.array_equal(base,structured(u,x,0)[0])

def test_semantics_respects_neutral_negative_and_rating_strength():
    interests=np.eye(10);pois=np.eye(10)[:3]
    u=np.full((4,10),3.);u[1,0]=5;u[2,0]=1;u[3,[0,1]]=[5,4]
    scores=semantic(u,pois,interests)
    assert np.all(scores[0]==0) and scores[1,0]==1 and scores[2,0]==-1
    assert scores[3,0]>scores[3,1]>scores[3,2]
    assert np.all(semantic(u,pois,interests,beta=0)[2]==0)

def test_hybrid_endpoints_and_constant_profile_ties():
    a=np.array([[1,3,5],[3,3,3.]])
    b=np.array([[.9,.1,.5],[0,0,0.]])
    for mode in ['percentile','minmax']:
        assert np.array_equal(hybrid(a,b,0,mode),normalize(a,mode))
        assert np.array_equal(hybrid(a,b,1,mode),normalize(b,mode))
        assert np.all(hybrid(a,b,.5,mode)[1]==.5)

def test_invalid_inputs_fail_explicitly():
    u,x,_=fixtures();x[0,0]=.5
    with pytest.raises(ValueError):structured(u,x)

def test_evaluation_exact_ties_have_expected_gain():
    from evaluate_labels import metrics
    a=metrics([0,0,0],[1,3,5],[False,False,True],2)
    b=metrics([0,0,0],[5,1,3],[True,False,False],2)
    assert a['ndcg']==b['ndcg'] and a['willingness_at_k']==b['willingness_at_k']

def test_declared_rating_scale_also_checks_range():
    u,x,ids=fixtures()
    with pytest.raises(ValueError,match='scores must be'):
        aggregate(u,x,ids,np.full((2,3),.1),'awm',scale='survey_feature_1_5')
