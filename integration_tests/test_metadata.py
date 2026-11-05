"""Metadata imports, LEFT JOINs, immutable snapshots and SQL constraints in PostgreSQL."""
import csv
import os
from pathlib import Path
from uuid import uuid4

import pytest

URL = os.getenv('TRAVELSYNC_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not URL, reason='Disposable PostgreSQL URL required')


def test_metadata_sidecar_validation_and_versioned_join(tmp_path):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from ourney_travelsync.db.operations import apply_migrations
    from ourney_travelsync.db.poi_import import read_csv, import_rows, COLUMNS
    from ourney_travelsync.db.metadata_import import import_metadata
    from ourney_travelsync.db.repository import get_active_pois
    root = Path(__file__).resolve().parents[1]
    rows = read_csv(root / 'src/ourney_travelsync/data/travelsync_poi_metadata_enriched_v1.csv')[:2]
    core = [{k: r[k] for k in COLUMNS} for r in rows]
    schema = 'metadata_test_' + uuid4().hex
    with psycopg.connect(URL, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            with conn.transaction():
                conn.execute((root / 'database/schema.sql').read_text(encoding='utf-8'))
                apply_migrations(conn, root / 'database')
            v1 = import_rows(conn, core)['dataset_version']
            v2 = import_rows(conn, core)['dataset_version']
            assert len(get_active_pois(conn, v1)) == 2
            assert all(p['enriched_metadata'] == {} for p in get_active_pois(conn, v1))
            path = tmp_path / 'metadata.csv'

            def write(version, summaries):
                with path.open('w', encoding='utf-8', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=['place_id', 'dataset_version', 'description_summary'])
                    writer.writeheader()
                    for row, summary in zip(rows, summaries):
                        writer.writerow(dict(place_id=row['place_id'], dataset_version=version, description_summary=summary))

            write(v1, ['old', 'old'])
            with pytest.raises(ValueError, match='DATASET_VERSION'):
                import_metadata(conn, path, v2, expected_count=2)
            assert conn.execute('SELECT count(*) n FROM poi_metadata').fetchone()['n'] == 0
            assert import_metadata(conn, path, v1, expected_count=2, validate_only=True)['imported_rows'] == 0
            assert conn.execute('SELECT count(*) n FROM poi_metadata').fetchone()['n'] == 0
            assert import_metadata(conn, path, v1, expected_count=2)['imported_rows'] == 2
            assert import_metadata(conn, path, v1, expected_count=2)['imported_rows'] == 0
            write(v2, ['new', 'new'])
            import_metadata(conn, path, v2, expected_count=2)
            assert [p['enriched_metadata']['description_summary'] for p in get_active_pois(conn, v1)] == ['old', 'old']
            assert [p['enriched_metadata']['description_summary'] for p in get_active_pois(conn, v2)] == ['new', 'new']
            write(v1, ['overwrite', 'overwrite'])
            with pytest.raises(ValueError, match='IMMUTABLE'):
                import_metadata(conn, path, v1, expected_count=2)
            for query in ["UPDATE poi_metadata SET metadata='{}'", 'DELETE FROM poi_metadata']:
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(query)
            with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
                conn.execute('INSERT INTO poi_metadata SELECT * FROM poi_metadata LIMIT 1')
            with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute("INSERT INTO poi_metadata VALUES('missing',%s,'{}')", (v1,))
        finally:
            conn.execute('SET search_path TO public')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
