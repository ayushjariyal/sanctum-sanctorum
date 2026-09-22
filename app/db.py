import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("SANCTUM_DATABASE_URL", "sqlite:///./sanctum.db")

# Hosted providers hand out ``postgres://`` or ``postgresql://``; both resolve to a
# driver we do not install, so pin them to psycopg 3 explicitly.
if DATABASE_URL.startswith(("postgres://", "postgresql://")):
    DATABASE_URL = f"postgresql+psycopg://{DATABASE_URL.partition('://')[2]}"

# check_same_thread is a SQLite-only connect argument.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
