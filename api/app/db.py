"""Database engine and session. SQLite by default, PostgreSQL in production."""
from sqlalchemy import create_engine, inspect, text, select, and_
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_enriched_metadata_column(bind=None):
    """Migrate legacy payloads unchanged and initialize immutable versioned sidecars."""
    with (bind or engine).begin() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("pois")}
        if "enriched_metadata" not in columns:
            conn.execute(text("ALTER TABLE pois ADD COLUMN enriched_metadata JSON"))
        # Copy historical payloads unchanged before enabling immutable sidecar storage.
        from .models import Poi, PoiMetadata
        legacy, sidecar = Poi.__table__, PoiMetadata.__table__
        rows = conn.execute(select(legacy.c.id, legacy.c.dataset_version, legacy.c.enriched_metadata)
                            .select_from(legacy.outerjoin(sidecar, and_(legacy.c.id == sidecar.c.place_id,
                                                                      legacy.c.dataset_version == sidecar.c.dataset_version)))
                            .where(sidecar.c.place_id.is_(None))).mappings().all()
        for row in rows:
            payload = row['enriched_metadata']
            if payload:
                conn.execute(PoiMetadata.__table__.insert().values(
                    place_id=row['id'], dataset_version=row['dataset_version'], metadata=payload))
        if conn.dialect.name == 'postgresql':
            conn.execute(text("""CREATE OR REPLACE FUNCTION immutable_api_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'METADATA_IMMUTABLE'; END $$"""))
            conn.execute(text('DROP TRIGGER IF EXISTS immutable_api_metadata ON poi_metadata'))
            conn.execute(text('CREATE TRIGGER immutable_api_metadata BEFORE UPDATE OR DELETE ON poi_metadata FOR EACH ROW EXECUTE FUNCTION immutable_api_metadata()'))
            conn.execute(text("""CREATE OR REPLACE FUNCTION guard_api_metadata_insert() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  PERFORM 1 FROM dataset_versions WHERE version=NEW.dataset_version FOR UPDATE;
                  IF EXISTS(SELECT 1 FROM recommendation_runs WHERE dataset_version=NEW.dataset_version) THEN
                    RAISE EXCEPTION 'METADATA_RUN_EXISTS';
                  END IF;
                  RETURN NEW;
                END $$"""))
            conn.execute(text('DROP TRIGGER IF EXISTS guard_api_metadata_insert ON poi_metadata'))
            conn.execute(text('CREATE TRIGGER guard_api_metadata_insert BEFORE INSERT ON poi_metadata FOR EACH ROW EXECUTE FUNCTION guard_api_metadata_insert()'))
        else:
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_metadata_{operation.lower()} BEFORE {operation} ON poi_metadata BEGIN SELECT RAISE(ABORT, 'METADATA_IMMUTABLE'); END"))
            conn.execute(text("""CREATE TRIGGER IF NOT EXISTS guard_metadata_insert BEFORE INSERT ON poi_metadata
                WHEN EXISTS(SELECT 1 FROM recommendation_runs WHERE dataset_version=NEW.dataset_version)
                BEGIN SELECT RAISE(ABORT, 'METADATA_RUN_EXISTS'); END"""))
