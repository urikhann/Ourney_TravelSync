"""Database engine and session. SQLite by default, PostgreSQL in production."""
from sqlalchemy import create_engine, inspect, text
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


def ensure_enriched_metadata_column():
    """Add enrichment storage to existing API databases without replacing their datasets."""
    with engine.begin() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("pois")}
        if "enriched_metadata" not in columns:
            conn.execute(text("ALTER TABLE pois ADD COLUMN enriched_metadata JSON"))
