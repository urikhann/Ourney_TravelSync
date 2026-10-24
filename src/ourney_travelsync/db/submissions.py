"""Pending submissions cannot select features. Review is trusted backend/admin only."""
import hashlib
import json
import re
from uuid import uuid4
from difflib import SequenceMatcher
from types import SimpleNamespace
from psycopg.types.json import Jsonb
from ..data_schema import BASE, EXTRA, FEATURES
from .poi_import import lock_head,begin_dataset,clone_dataset,insert_poi,finish_dataset
from .repository import get_active_pois


def submit_poi(conn,submitted_by,*,place_name,category_name,address,latitude,longitude,description=None):
    # No feature kwargs accepted, not even for advanced clients.
    sid=uuid4()
    conn.execute('''INSERT INTO poi_submission(submission_id,submitted_by,place_name,category_name,address,latitude,longitude,description)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(sid,submitted_by,place_name,category_name,address,latitude,longitude,description))
    return sid


def review_submission(conn,submission_id,reviewer_id,*,approve,evidence,review_note):
    """Evidence from administrator verification, never copied from public submission payload.
    Requires Busan address AND explicit admin coordinate/address verification, since no GIS
    boundary data/geocoder is supplied. Possible duplicates block approval for manual resolution.
    """
    import pandas as pd
    from ..ingestion.travelsync_pipeline_rule_v2 import classify, normal_name, distance
    with conn.transaction():
        parent=lock_head(conn)
        if not conn.execute('SELECT 1 FROM users WHERE user_id=%s AND is_admin',(reviewer_id,)).fetchone():raise ValueError('ADMIN_REQUIRED')
        sub=conn.execute('SELECT * FROM poi_submission WHERE submission_id=%s FOR UPDATE',(submission_id,)).fetchone()
        if not sub or sub['status']!='PENDING':raise ValueError('SUBMISSION_NOT_PENDING')
        if not isinstance(review_note,str) or not review_note.strip():raise ValueError('REVIEW_NOTE_REQUIRED')
        if not approve:
            conn.execute("UPDATE poi_submission SET status='REJECTED',review_note=%s,reviewed_by=%s,reviewed_at=now() WHERE submission_id=%s",(review_note,reviewer_id,submission_id))
            return {'status':'REJECTED'}
        required=('busan_verified','coordinates_verified','tourism_verified')
        if not isinstance(evidence,dict) or any(evidence.get(k) is not True for k in required):raise ValueError('ADMIN_VERIFICATION_REQUIRED')
        if not re.match(r'^부산(?:광역시)?\s',sub['address'].strip()):raise ValueError('BUSAN_ADDRESS_REQUIRED')
        name=normal_name(sub['place_name'])
        if not name:raise ValueError('INVALID_PLACE_NAME')
        existing=get_active_pois(conn,parent) if parent else []
        for row in existing:
            other=normal_name(row['place_name'])
            proximity=distance(SimpleNamespace(**sub),SimpleNamespace(**row))
            if proximity<=150 and (name==other or SequenceMatcher(None,name,other).ratio()>=.9):
                raise ValueError('POSSIBLE_DUPLICATE: reject this submission or resolve source records separately')
        r={k:'' for k in BASE+EXTRA}
        r.update(place_id='user:'+str(submission_id),source_place_id=str(submission_id),source='USER',
                 place_name=sub['place_name'],category=sub['category_name'],category_name=sub['category_name'],
                 address=sub['address'],latitude=float(sub['latitude']),longitude=float(sub['longitude']))
        r.update({f:0 for f in FEATURES})
        mapped=classify(pd.DataFrame([r])).iloc[0].to_dict()
        if mapped['exclude_reason'] or not sum(mapped[f] for f in FEATURES):raise ValueError('NOT_TOURISM_OR_UNCLASSIFIED')
        mapped.update(description=sub['description'])
        verified=dict(evidence,duplicates_checked=True)
        conn.execute("UPDATE poi_submission SET status='APPROVED',review_note=%s,validation_evidence=%s,reviewed_by=%s,reviewed_at=now() WHERE submission_id=%s",(review_note,Jsonb(verified),reviewer_id,submission_id))
        digest=hashlib.sha256(json.dumps({'submission':str(submission_id),'parent':str(parent),'mapped':mapped},sort_keys=True,default=str).encode()).hexdigest()
        version=begin_dataset(conn,parent,digest)
        clone_dataset(conn,parent,version)
        insert_poi(conn,mapped,version,submission_id)
        count=finish_dataset(conn,version,True)
        return {'status':'APPROVED','dataset_version':str(version),'total_rows':count}
