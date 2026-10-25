"""Explicit schema operations; never run automatically during import or collection."""
from pathlib import Path
from .connection import connect


def apply_migrations(conn, directory):
    applied = {row['version'] for row in conn.execute('SELECT version FROM schema_migrations').fetchall()}
    for path in sorted((Path(directory) / 'migrations').glob('[0-9]*.sql')):
        if int(path.name.split('_')[0]) not in applied:
            conn.execute(path.read_text(encoding='utf-8'))


def initialize(directory):
    with connect() as conn:
        if conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchone():
            raise ValueError('SCHEMA_NOT_EMPTY: use explicit db-migrate for an existing TravelSync schema')
        conn.execute((Path(directory) / 'schema.sql').read_text(encoding='utf-8'))
        apply_migrations(conn, directory)
    return dict(initialized=True, seeded=False)


def migrate(directory):
    with connect() as conn:
        apply_migrations(conn, directory)
    return dict(migrated=True)
