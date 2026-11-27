import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("ortools")

from ourney_travelsync.provisional_schedules import provisional,validate_provisional
from ourney_travelsync.operating_schedules import day_windows
from ourney_travelsync.optimizer.contracts import PlanRequest
from ourney_travelsync.optimizer.eligibility import check_eligibility
from ourney_travelsync.optimizer.validation import validate_final
from ourney_travelsync.optimizer.solver import solve_prepared
from optimizer_fixtures import request_data,DAY

def ev(h='10:00~18:00',c=''):
 return [{'hours_text':h,'closure_text':c,'source':'official','source_id':'1','fetched_at_utc':'2026-11-26T00:00:00Z'}]
def test_unknown_closure_is_estimated_and_never_verified():
 s=provisional(ev(),{},'2026-11-26');assert s['weekly']['1']==[[600,1080]] and s['review_required'] and not s['independently_verified']
def test_cutoff_conservatively_limits_whole_visit():
 assert provisional(ev('10:00~18:00 (입장 마감 17:30)','연중무휴'),{},'2026-11-26')['weekly']['1']==[[600,1050]]
def test_current_break_preserved():
 assert provisional(ev('09:00~22:00 (휴게시간 12:00~13:00)','연중무휴'),{},'2026-11-26')['weekly']['1']==[[540,720],[780,1320]]
@pytest.mark.parametrize('h,c',[('동절기 10:00~18:00','연중무휴'),('부산타워 10:00~22:00','연중무휴'),('18:00~02:00','연중무휴'),('09:00~17:00','설·추석 당일'),('평일 09:00~18:00','매주 일요일')])
def test_unresolved_scope_rules_are_not_fabricated(h,c):
 s=provisional(ev(h,c),{},'2026-11-26');assert all(w is None for w in s['weekly'].values());assert day_windows({'schedule':s},'2026-11-26') is None

def test_expired_break_does_not_become_recurring():
 assert provisional(ev(),{'break_times_raw':'historical snapshot'},'2026-11-26')['weekly']['1'] is None

def request():
 d=request_data(1);d['allow_unknown_opening_hours']=True
 op=d['candidates'][0]['operations'][DAY]
 for field in ['openings','closed']:op[field].update(status='ESTIMATED',source='provisional-hours:v5:p',rule_version='provisional-hours-1.0')
 return d

def test_optimizer_respects_estimated_windows_and_strict_mode_rejects():
 d=request();req=PlanRequest.model_validate(d);es=check_eligibility(req)
 assert es[0].schedule_eligible and any('UNVERIFIED' in r for r in es[0].reasons)
 solved=solve_prepared(req,es);assert solved.status in ['OPTIMAL','FEASIBLE'];assert validate_final(req,solved.days).passed
 d['allow_unknown_opening_hours']=False;assert not check_eligibility(PlanRequest.model_validate(d))[0].schedule_eligible

def test_e3_independently_rejects_visit_outside_estimated_window():
 d=request();req=PlanRequest.model_validate(d);solved=solve_prepared(req,check_eligibility(req))
 d['candidates'][0]['operations'][DAY]['openings']['value'][0]['end_min']=610
 assert not validate_final(PlanRequest.model_validate(d),solved.days).passed
