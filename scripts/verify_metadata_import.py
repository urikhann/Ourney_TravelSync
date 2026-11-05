"""Exercise the actual team CSV in an isolated PostgreSQL schema; never changes dataset_head outside it.

Use TRAVELSYNC_TEST_DATABASE_URL for a disposable database. --keep-schema retains
the imported tables for inspection; otherwise the isolated schema is removed.
"""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from ourney_travelsync.db.metadata_import import read_metadata_csv, import_metadata
from ourney_travelsync.db.poi_import import register_mapping, insert_poi, finish_dataset, validate_rows, COLUMNS, MAPPING_VERSION, SCHEMA_VERSION
from ourney_travelsync.db.operations import apply_migrations
from ourney_travelsync.db.repository import create_user, create_group, save_preference, save_constraints, get_run_inputs
from ourney_travelsync.db.adapter import recommend_database, compute_snapshot
from ourney_travelsync.features import FEATURE_NAMES


def verify(path, keep=False):
    url = os.environ.get('TRAVELSYNC_TEST_DATABASE_URL')
    if not url:
        raise ValueError('Set TRAVELSYNC_TEST_DATABASE_URL to a disposable PostgreSQL database')
    rows, checksum = read_metadata_csv(path)
    version = UUID(rows[0]['dataset_version'])
    schema = 'metadata_verify_' + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            with conn.transaction():
                conn.execute((root / 'database/schema.sql').read_text(encoding='utf-8'))
                apply_migrations(conn, root / 'database')
                register_mapping(conn)
                conn.execute('INSERT INTO dataset_versions(dataset_version,mapping_version,feature_schema_version,input_sha256) VALUES(%s,%s,%s,%s)',
                             (version, MAPPING_VERSION, SCHEMA_VERSION, checksum))
                for row in validate_rows([{k: r[k] for k in COLUMNS} for r in rows]):
                    insert_poi(conn, row, version)
                finish_dataset(conn, version, True)
            before = conn.execute('SELECT md5(string_agg(row_to_json(p)::text,\'\' ORDER BY place_id)) hash FROM poi p').fetchone()['hash']
            report = import_metadata(conn, path, version)
            assert report['matched_rows'] == report['imported_rows'] == 3570
            assert import_metadata(conn, path, version)['imported_rows'] == 0
            after = conn.execute('SELECT md5(string_agg(row_to_json(p)::text,\'\' ORDER BY place_id)) hash FROM poi p').fetchone()['hash']
            assert before == after
            user = create_user(conn, 'metadata-verification', 'Metadata test')
            group = create_group(conn, 'Metadata test', user)
            save_preference(conn, user, dict.fromkeys(FEATURE_NAMES, 3), '해산물 바다 카페')
            point = dict(latitude=35.1, longitude=129.1)
            constraint = save_constraints(conn, group, dict(daily_windows=[dict(date='2026-11-02', start_min=540, end_min=1080,
                start_point=point, end_point=point)], budget_total=100000, transport_mode='WALK'))
            config = dict(algorithm='gfar', individual_method='tfidf', tie_seed=0, candidate_top_n=30,
                          gfar_depth=20, misery_threshold=0.25, top_k=5, score_scale='0-1')
            run, picks = recommend_database(conn, group, constraint, config, version)
            replay = compute_snapshot(get_run_inputs(conn, run))[0]
            assert picks == replay and len(picks) == 5
            assert max(r['score'] for r in conn.execute('SELECT score FROM individual_scores WHERE run_id=%s', (run,))) > 0
            # A newer active dataset must not change the captured run's JOIN.
            from ourney_travelsync.db.poi_import import import_rows, activate_dataset
            newer = import_rows(conn, [{k: r[k] for k in COLUMNS} for r in rows[:2]])
            activate_dataset(conn, newer['dataset_version'], str(version))
            assert compute_snapshot(get_run_inputs(conn, run))[0] == picks
            assert str(get_run_inputs(conn, run)['run']['dataset_version']) == str(version)
            conn.execute('UPDATE dataset_head SET dataset_version=%s WHERE singleton', (version,))
            for statement in ["UPDATE poi_metadata SET metadata='{}'", 'DELETE FROM poi_metadata']:
                try:
                    with conn.transaction(): conn.execute(statement)
                except psycopg.errors.CheckViolation:
                    pass
                else:
                    raise AssertionError('metadata mutation was not blocked')
            report.update(schema=schema, kept_schema=keep, poi_unchanged=before == after,
                          joined_rows=len(get_run_inputs(conn, run)['pois']), run_id=str(run),
                          replay_after_head_change=True, immutable_metadata=True, returned=len(picks))
            return report
        finally:
            conn.execute('SET search_path TO public')
            if not keep:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv'); parser.add_argument('--keep-schema', action='store_true'); parser.add_argument('--report')
    args = parser.parse_args()
    result = verify(args.csv, args.keep_schema)
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
