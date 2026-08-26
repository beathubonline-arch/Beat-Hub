"""
Database engine and session management.
Works with PostgreSQL in production and SQLite for local development.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

connect_args = {}
engine_options = {
    "pool_pre_ping": True,
    "future": True,
}

if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
else:
    # Reuse a small pool instead of opening a fresh PostgreSQL connection for
    # every request. Keep the pool bounded so a single Render instance cannot
    # exhaust the database connection limit.
    engine_options.update(
        pool_size=5,
        max_overflow=5,
        pool_timeout=15,
        pool_recycle=1800,
    )

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    **engine_options,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
