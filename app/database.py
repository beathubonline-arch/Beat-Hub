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
    # PostgreSQL is the production database. Keep the pool deliberately
    # bounded so one Render instance cannot consume the whole database pool.
    # LIFO reuse favors warm connections while pre_ping detects stale ones.
    engine_options.update(
        pool_size=5,
        max_overflow=5,
        pool_timeout=15,
        pool_recycle=1800,
        pool_use_lifo=True,
    )
    # Never allow one accidental long-running SQL statement to hold a pooled
    # connection forever. This protects every route from a single stuck query.
    connect_args = {
        "options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=30000"
    }

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
