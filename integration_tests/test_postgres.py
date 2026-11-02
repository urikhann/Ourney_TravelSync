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
                from ourney_travelsync.db.operations import apply_migrations
                apply_migrations(conn, root/'database')
            with as_file(files('ourney_travelsync').joinpath('data/travelsync_poi_metadata_enriched_v1.csv')) as path:
                rows = read_csv(path)
            report = import_rows(conn, rows)
            assert report['imported_rows'] == 3570 and not report['activated']
            saved = conn.execute('SELECT enriched_metadata FROM poi WHERE dataset_version=%s AND place_id=%s',
                                 (report['dataset_version'], rows[0]['place_id'])).fetchone()
            assert saved['enriched_metadata'] == rows[0]['enriched_metadata']
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
            # Migration 004 accepts raw zero cosine while preserving historical scores.
            tfidf_config = config | dict(individual_method='tfidf', score_scale='0-1', misery_threshold=0)
            tfidf_run, tfidf_results = recommend_database(conn, gid, cid, tfidf_config)
            assert len(tfidf_results) == 3
            assert all(r['mean_score'] == 0 for r in conn.execute(
                'SELECT mean_score FROM group_scores WHERE run_id=%s', (tfidf_run,)).fetchall())
            save_preference(conn, uid, dict.fromkeys(FEATURE_NAMES, 5), preference_text='바다 산책')
            assert get_run_inputs(conn, tfidf_run)['preferences'][0]['preference_text'] == ''
            assert all(r['mean_score'] == 3 for r in conn.execute(
                'SELECT mean_score FROM group_scores WHERE run_id=%s', (rid,)).fetchall())
            with pytest.raises(psycopg.Error), conn.transaction():
                conn.execute('UPDATE poi SET food=1 WHERE dataset_version=%s',(report['dataset_version'],))
            with pytest.raises(ValueError):
                activate_dataset(conn,report['dataset_version'],str(uuid4()))
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
