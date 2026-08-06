import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Required, deliberately with no default. A missing DATABASE_URL should stop
# the service from starting rather than fall back to some local file — a
# silent wrong-database is worse than a loud failure. The standalone/exe build
# sets this to SQLite in run_standalone.py before importing this module.
DATABASE_URL = os.environ["DATABASE_URL"]

# SQLite rejects cross-thread use of a connection by default, and FastAPI
# serves requests on a threadpool. Only applies to the standalone path;
# Postgres gets an empty dict and is unaffected.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
