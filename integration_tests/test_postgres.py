"""Opt-in only: a disposable database, isolated random schema, rollback and cleanup."""
import os
from pathlib import Path
from uuid import uuid4
import pytest

URL = os.environ.get('TRAVELSYNC_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='No explicitly configured disposable PostgreSQL URL')


def test_snapshot_import_run_and_immutability():
    psycopg = pytest.importorskip('psycopg')
    from psycopg import sql
    from psycopg.rows import dict_row
    from importlib.resources import files, as_file
    from ourney_travelsync.db.poi_import import read_csv, import_rows, activate_dataset
    from ourney_travelsync.db.repository import (create_user, create_group, save_preference,
        save_constraints, get_run_inputs)
    from ourney_travelsync.db.adapter import recommend_database
    from ourney_travelsync.features import FEATURE_NAMES
    schema = 'travelsync_test_' + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(URL, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            with conn.transaction():
                conn.execute((root/'database/schema.sql').read_text(encoding='utf-8'))
                conn.execute((root/'database/migrations/002_recommendation_scale.sql').read_text(encoding='utf-8'))
            with as_file(files('ourney_travelsync').joinpath('data/travelsync_final_places_v2.csv')) as path:
                rows = read_csv(path)
            report = import_rows(conn, rows)
            assert report['imported_rows'] == 3570 and not report['activated']
            assert conn.execute('SELECT * FROM dataset_head').fetchone() is None
            activate_dataset(conn, report['dataset_version'])
            uid = create_user(conn, 'test-subject', 'Tester')
            gid = create_group(conn, 'Test group', uid)
            save_preference(conn, uid, dict.fromkeys(FEATURE_NAMES, 3))
            point = dict(latitude=35.1, longitude=129.1)
            cid = save_constraints(conn, gid, dict(daily_windows=[dict(date='2026-11-01',start_min=540,
                end_min=1080,start_point=point,end_point=point)],budget_total=100000,transport_mode='WALK'))
            config = dict(algorithm='gfar',tie_seed=0,candidate_top_n=20,gfar_depth=20,misery_threshold=2.5,top_k=3)
            rid, results = recommend_database(conn,gid,cid,config)
            save_preference(conn,uid,dict.fromkeys(FEATURE_NAMES,5))
            assert get_run_inputs(conn,rid)['preferences'][0]['version'] == 1
            assert len(results) == 3
            stored = conn.execute('SELECT * FROM group_scores WHERE run_id=%s',(rid,)).fetchall()
            assert all(r['mean_score']==3 and r['group_utility'] is None and r['feasible_dates'] is None for r in stored)
            with pytest.raises(psycopg.Error), conn.transaction():
                conn.execute('UPDATE poi SET food=1 WHERE dataset_version=%s',(report['dataset_version'],))
            with pytest.raises(ValueError):
                activate_dataset(conn,report['dataset_version'],str(uuid4()))
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
