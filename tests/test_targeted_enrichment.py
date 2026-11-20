"""Protect provenance and conservative matching in the isolated enrichment pilot."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.targeted_enrichment import (
    classify_schedule, match_record, make_triage, balanced, Collector,
)


def poi(**overrides):
    return {"place_id":"tour:1", "place_name":"부산카페 본점", "source":"TOUR_API",
            "source_place_id":"1", "latitude":"35.1", "longitude":"129.1",
            "address":"부산광역시 중구 중앙대로 1", "category_name":"카페",
            "cafe":"1", "food":"0", "festival":"0", "description_summary":"A description",
            "description_source_url":"https://example.org/place", **overrides}


def test_old_snapshot_never_becomes_regular_or_zero_frequency():
    p = poi()
    h = {"place_id":p["place_id"], "collection_status":"FOUND", "hours_scope":"SNAPSHOT_WEEK",
         "hours_valid_from":"2026-10-26", "hours_valid_until":"2026-11-01",
         "operating_hours_raw":'{"week_from_today":{"week_periods":[{"days":[{"day_of_the_week_desc":"월(10/26)","on_days":{"start_end_time_desc":"11:00 ~ 21:30"}}]}]}}'}
    r = make_triage([p], [h], {}, {"status":"UNAVAILABLE"})[0]
    assert r["parsed_hours"] == "SNAPSHOT" and r["snapshot_expired"]
    assert r["missing_regular_hours"] and r["recommendation_run_count"] == ""
    assert not r["verified_for_planning"]


def test_inferred_all_day_remains_flagged_not_verified():
    p = poi()
    h = {"place_id":p["place_id"], "collection_status":"FOUND", "hours_scope":"PUBLISHED_SCHEDULE",
         "operating_hours_raw":"상시 개방", "closed_day":"연중무휴", "break_times_raw":"12:00-13:00"}
    r = make_triage([p], [h], {p["place_id"]:3}, {"status":"AVAILABLE"})[0]
    assert r["inferred_all_day"] and r["recommendation_run_count"] == 3
    assert r["break_times_raw"] == "12:00-13:00" and not r["verified_for_planning"]
    assert "INFERRED_ALL_DAY" in r["review_flags"]


def test_same_name_different_branch_and_ambiguous_matches_are_not_resolved():
    p = poi(place_id="kakao:1", source="KAKAO")
    a = {"UC_SEQ":10, "MAIN_TITLE":"부산카페 본점", "LAT":35.1, "LNG":129.1}
    b = {**a, "UC_SEQ":11}
    assert match_record(p,[a,b],"busan")[1] == "AMBIGUOUS"
    assert match_record(p,[{**a,"MAIN_TITLE":"부산카페 해운대점"}],"busan")[0] is None
    assert match_record(p,[{**a,"LAT":35.5}],"busan")[0] is None
    assert match_record(p,[a],"busan")[1] == "EXACT_NAME_AND_DISTANCE"


def test_direct_tour_identity_matches_existing_source_id():
    a = {"contentid":"1", "title":"Renamed venue", "mapy":35.1, "mapx":129.1}
    assert match_record(poi(),[a],"tour")[1] == "EXACT_SOURCE_ID"


def test_candidate_requires_closure_and_preserves_cutoff_warning():
    assert classify_schedule("10:00-20:00")['schedule_evidence_status'] == 'TIME_RANGE_FOUND_CLOSURE_UNRESOLVED'
    assert classify_schedule("상시 개방", "연중무휴")['schedule_evidence_status'] == 'ALL_DAY_TEXT_REQUIRES_VENUE_CHECK'
    r = classify_schedule("09:00~18:00 (입장 마감 17:00)", "매주 월요일")
    assert r['schedule_evidence_status'] == 'REGULAR_SCHEDULE_CANDIDATE_REVIEW_REQUIRED'
    assert r['break_or_cutoff_present'] and not r['verified_for_planning']
    assert classify_schedule("동절기 09:00-17:00 하절기 09:00-18:00", "연중무휴")['schedule_evidence_status'] == 'VARIABLE_OR_CONDITIONAL_REQUIRES_REVIEW'


def test_balanced_selection_preserves_bucket_order():
    rows = [{"district":d,"category":c,"place_id":f"{d}-{c}-{i}"}
            for i in range(3) for d in ['A','B'] for c in ['cafe','attraction']]
    selected = balanced(rows, 5)
    assert len({r['place_id'] for r in selected}) == 5
    assert {r['district'] for r in selected[:4]} == {'A','B'}
    assert selected[-1]['place_id'] == 'A-attraction-1'


def test_observed_frequency_controls_global_rank_without_changing_hours():
    a, b = poi(place_id='tour:1'), poi(place_id='tour:2', source_place_id='2')
    hours = [{'place_id':p['place_id'], 'collection_status':'NOT_FOUND'} for p in (a,b)]
    ranked = make_triage([a,b],hours,{'tour:2':18,'tour:1':2},{'status':'AVAILABLE'})
    assert [r['place_id'] for r in ranked] == ['tour:2','tour:1']
    assert [r['recommendation_run_count'] for r in ranked] == [18,2]
    assert [r['rank'] for r in ranked] == [1,2]
    assert all(r['parsed_hours']=='UNKNOWN' and not r['verified_for_planning'] for r in ranked)


def test_live_response_and_request_log_do_not_save_key(monkeypatch, tmp_path):
    import json
    requests = pytest.importorskip('requests')
    sentinel = 'PRIVATE_API_KEY_SENTINEL_1234567890'
    payload = {'response': {'header': {'resultCode':'0000'}, 'body': {
        'items': {'item': [{'overview':sentinel}]}, 'totalCount':1}}}
    class Response:
        status_code = 200
        text = json.dumps(payload)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response())
    c = Collector(tmp_path, {'TOUR_API_KEY':sentinel})
    result = c.get('https://example.org/detailIntro2', {'serviceKey':sentinel, 'contentId':'1'}, 'tour_intro')
    assert result[0]['overview'] == '[REDACTED]'
    assert all(sentinel not in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.txt'))
    assert sentinel not in (tmp_path/'requests.json').read_text(encoding='utf-8')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Cache should be used')))
    assert c.get('https://example.org/detailIntro2', {'serviceKey':sentinel, 'contentId':'1'}, 'tour_intro') == result
    assert len(c.log) == 1
