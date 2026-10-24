"""Backend-only repositories. Caller authenticates actor; never expose DB credentials to browsers."""
import hashlib
import json
from datetime import date
from uuid import uuid4
from psycopg.types.json import Jsonb
from ..features import FEATURE_NAMES as FEATURES


def get_active_pois(conn,dataset_version=None):
    if dataset_version is None:
        head=conn.execute('SELECT dataset_version FROM dataset_head WHERE singleton').fetchone()
        if head is None:return []
        dataset_version=head['dataset_version']
    return conn.execute("SELECT p.* FROM poi p JOIN dataset_versions d USING(dataset_version) WHERE p.dataset_version=%s AND p.active AND d.status='READY' ORDER BY p.place_id",(dataset_version,)).fetchall()


def get_latest_preference(conn,user_id):
    return conn.execute('SELECT * FROM preferences WHERE user_id=%s ORDER BY version DESC LIMIT 1',(user_id,)).fetchone()


def get_group_members(conn,group_id):
    return conn.execute('SELECT user_id,role,joined_at FROM group_members WHERE group_id=%s ORDER BY user_id',(group_id,)).fetchall()


def create_user(conn,auth_subject,display_name):
    uid=uuid4()
    conn.execute('INSERT INTO users(user_id,auth_subject,display_name) VALUES(%s,%s,%s)',(uid,auth_subject,display_name))
    return uid


def create_group(conn,name,owner_id):
    with conn.transaction():
        gid=uuid4()
        conn.execute('INSERT INTO travel_groups(group_id,name,owner_id) VALUES(%s,%s,%s)',(gid,name,owner_id))
        conn.execute("INSERT INTO group_members(group_id,user_id,role) VALUES(%s,%s,'OWNER')",(gid,owner_id))
        return gid


def add_group_member(conn,group_id,user_id):
    with conn.transaction():
        row=conn.execute("SELECT group_id FROM travel_groups WHERE group_id=%s AND status='ACTIVE' FOR UPDATE",(group_id,)).fetchone()
        if not row:raise ValueError('GROUP_NOT_ACTIVE')
        n=conn.execute('SELECT count(*) n FROM group_members WHERE group_id=%s',(group_id,)).fetchone()['n']
        if n>=10:raise ValueError('GROUP_SIZE_LIMIT')
        conn.execute("INSERT INTO group_members(group_id,user_id,role) VALUES(%s,%s,'MEMBER')",(group_id,user_id))


def save_preference(conn,user_id,scores):
    if set(scores)!=set(FEATURES) or any(type(scores[k]) is not int or not 1<=scores[k]<=5 for k in FEATURES):
        raise ValueError('PREFERENCE_1_TO_5_INTEGER_REQUIRED')
    with conn.transaction():
        if not conn.execute('SELECT user_id FROM users WHERE user_id=%s FOR UPDATE',(user_id,)).fetchone():
            raise ValueError('USER_NOT_FOUND')
        row=get_latest_preference(conn,user_id);version=1 if row is None else row['version']+1
        conn.execute('INSERT INTO preferences(user_id,version,'+','.join(FEATURES)+') VALUES('+','.join(['%s']*12)+')',
                     [user_id,version]+[scores[k] for k in FEATURES])
        return version


def validate_constraints(values):
    windows=values.get('daily_windows')
    if not isinstance(windows,list) or not 1<=len(windows)<=3:raise ValueError('DAILY_WINDOWS_REQUIRED')
    seen=set()
    for w in windows:
        if not isinstance(w,dict) or set(w)!={'date','start_min','end_min','start_point','end_point'}:raise ValueError('WINDOW_FIELDS')
        try:d=date.fromisoformat(w['date'])
        except (ValueError,TypeError):raise ValueError('INVALID_TRAVEL_DATE') from None
        if d in seen:raise ValueError('DUPLICATE_TRAVEL_DATE')
        seen.add(d)
        if any(type(w[k]) is not int for k in ('start_min','end_min')) or not 0<=w['start_min']<w['end_min']<=1440:
            raise ValueError('INVALID_TIME_WINDOW')
        for k in ('start_point','end_point'):
            point=w[k]
            if not isinstance(point,dict) or set(point)!={'latitude','longitude'}:raise ValueError('POINT_FIELDS')
            for field,lo,hi in [('latitude',-90,90),('longitude',-180,180)]:
                v=point[field]
                if isinstance(v,bool) or not isinstance(v,(int,float)) or not lo<=v<=hi:raise ValueError('INVALID_POINT')
    if type(values.get('budget_total')) is not int or values['budget_total']<0:raise ValueError('INVALID_BUDGET')
    if values.get('transport_mode') not in ('WALK','CAR','TRANSIT'):raise ValueError('INVALID_TRANSPORT')
    for k in ('max_leg_minutes','max_total_walk_minutes'):
        v=values.get(k)
        if v is not None and (type(v) is not int or v < (1 if k=='max_leg_minutes' else 0)):raise ValueError('INVALID_MOVEMENT_LIMIT')
    exclusions=values.get('member_exclusions',{})
    if not isinstance(exclusions,dict):raise ValueError('EXCLUSIONS_OBJECT_REQUIRED')
    for v in exclusions.values():
        if not isinstance(v,dict) or set(v)-{'poi_ids','categories'}:raise ValueError('EXCLUSIONS_FIELDS')
        if any(not isinstance(a,list) or any(not isinstance(i,str) for i in a) for a in v.values()):raise ValueError('EXCLUSIONS_LIST_REQUIRED')


def save_constraints(conn,group_id,values):
    validate_constraints(values)
    with conn.transaction():
        if not conn.execute('SELECT group_id FROM travel_groups WHERE group_id=%s FOR UPDATE',(group_id,)).fetchone():raise ValueError('GROUP_NOT_FOUND')
        members={str(r['user_id']) for r in get_group_members(conn,group_id)}
        if set(values.get('member_exclusions',{}))-members:raise ValueError('EXCLUSION_USER_NOT_MEMBER')
        version=conn.execute('SELECT coalesce(max(version),0)+1 v FROM trip_constraints WHERE group_id=%s',(group_id,)).fetchone()['v']
        cid=uuid4()
        conn.execute('''INSERT INTO trip_constraints(constraint_id,group_id,version,daily_windows,budget_total,transport_mode,max_leg_minutes,max_total_walk_minutes,member_exclusions,accessibility_requirements)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(cid,group_id,version,Jsonb(values['daily_windows']),values['budget_total'],values['transport_mode'],values.get('max_leg_minutes'),values.get('max_total_walk_minutes'),Jsonb(values.get('member_exclusions',{})),Jsonb(values.get('accessibility_requirements'))))
        return cid


def create_recommendation_run(conn,group_id,constraint_id,config_snapshot,dataset_version=None):
    with conn.transaction():
        if not conn.execute("SELECT group_id FROM travel_groups WHERE group_id=%s AND status='ACTIVE' FOR SHARE",(group_id,)).fetchone():raise ValueError('GROUP_NOT_ACTIVE')
        if not conn.execute('SELECT 1 FROM trip_constraints WHERE constraint_id=%s AND group_id=%s',(constraint_id,group_id)).fetchone():raise ValueError('CONSTRAINT_GROUP_MISMATCH')
        if dataset_version is None:
            head=conn.execute('SELECT dataset_version FROM dataset_head WHERE singleton').fetchone()
            if not head:raise ValueError('NO_ACTIVE_DATASET')
            dataset_version=head['dataset_version']
        ds=conn.execute("SELECT * FROM dataset_versions WHERE dataset_version=%s AND status='READY'",(dataset_version,)).fetchone()
        if not ds:raise ValueError('DATASET_NOT_READY')
        members=get_group_members(conn,group_id)
        if not 1<=len(members)<=10:raise ValueError('GROUP_SIZE_LIMIT')
        # Lock users in deterministic order: save_preference uses the same locks.
        for m in members:conn.execute('SELECT user_id FROM users WHERE user_id=%s FOR SHARE',(m['user_id'],))
        snapshot=[]
        for m in members:
            pref=get_latest_preference(conn,m['user_id'])
            if not pref:raise ValueError('MEMBER_PREFERENCE_MISSING')
            snapshot.append({'user_id':str(m['user_id']),'preference_version':pref['version']})
        payload={'group':str(group_id),'constraint':str(constraint_id),'dataset':str(dataset_version),'members':snapshot,'config':config_snapshot,'mapping':ds['mapping_version']}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        rid=uuid4()
        conn.execute('''INSERT INTO recommendation_runs(run_id,group_id,constraint_id,dataset_version,mapping_version,feature_schema_version,member_snapshot,config_snapshot,input_hash)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(rid,group_id,constraint_id,dataset_version,ds['mapping_version'],ds['feature_schema_version'],Jsonb(snapshot),Jsonb(config_snapshot),digest))
        for m in snapshot:conn.execute('INSERT INTO run_preferences VALUES(%s,%s,%s)',(rid,m['user_id'],m['preference_version']))
        return rid


def get_run_inputs(conn,run_id):
    run=conn.execute('SELECT * FROM recommendation_runs WHERE run_id=%s',(run_id,)).fetchone()
    if not run:raise ValueError('RUN_NOT_FOUND')
    preferences=conn.execute('SELECT p.* FROM run_preferences r JOIN preferences p ON p.user_id=r.user_id AND p.version=r.preference_version WHERE r.run_id=%s ORDER BY p.user_id',(run_id,)).fetchall()
    constraints=conn.execute('SELECT * FROM trip_constraints WHERE constraint_id=%s',(run['constraint_id'],)).fetchone()
    return {'run':run,'pois':get_active_pois(conn,run['dataset_version']),'preferences':preferences,'constraints':constraints}


def _dataset(conn,run_id):
    row=conn.execute('SELECT dataset_version FROM recommendation_runs WHERE run_id=%s',(run_id,)).fetchone()
    if not row:raise ValueError('RUN_NOT_FOUND')
    return row['dataset_version']


def save_individual_scores(conn,run_id,rows):
    with conn.transaction():
        ds=_dataset(conn,run_id)
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO individual_scores(run_id,user_id,poi_id,dataset_version,score,rank,eligible) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                            [(run_id,r['user_id'],r['poi_id'],ds,r['score'],r.get('rank'),r['eligible']) for r in rows])


def save_group_scores(conn,run_id,rows):
    with conn.transaction():
        ds=_dataset(conn,run_id)
        n=conn.execute('SELECT count(*) n FROM run_preferences WHERE run_id=%s',(run_id,)).fetchone()['n']
        for r in rows:
            actual=conn.execute('SELECT count(*) n,avg(score) mean FROM individual_scores WHERE run_id=%s AND poi_id=%s AND eligible',(run_id,r['poi_id'])).fetchone()
            if actual['n']!=n or abs(actual['mean']-r['mean_score'])>1e-9:raise ValueError('GROUP_SCORE_REQUIRES_ALL_MEMBER_SCORES')
            conn.execute('INSERT INTO group_scores VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(run_id,r['poi_id'],ds,r['mean_score'],r['group_rank'],r['selection_gain'],r['group_utility'],None if r['feasible_dates'] is None else Jsonb(r['feasible_dates'])))


def save_itinerary(conn,run_id,*,solver_status,validation_status,total_cost,metrics,routing_version,items):
    """Persist optimizer output atomically. PASSED requires upstream full constraint validation.
    This adapter checks structural consistency; it does not implement OR-Tools or routing.
    """
    with conn.transaction():
        ds=_dataset(conn,run_id)
        if validation_status=='PASSED' and not items:raise ValueError('EMPTY_PASSED_ITINERARY')
        iid=uuid4()
        conn.execute('INSERT INTO itineraries(itinerary_id,run_id,dataset_version,solver_status,validation_status,total_cost,metrics,routing_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(iid,run_id,ds,solver_status,validation_status,total_cost,Jsonb(metrics),routing_version))
        for r in items:
            conn.execute('INSERT INTO itinerary_items VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(iid,run_id,ds,r['travel_date'],r['sequence'],r['poi_id'],r['arrival_min'],r['start_min'],r['departure_min'],r['travel_minutes'],r['stay_minutes'],r['group_cost']))
        return iid
