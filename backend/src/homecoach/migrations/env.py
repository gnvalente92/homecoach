"""Alembic environment: runs the migrations in versions/ against HC_DATABASE_URL."""

from alembic import context
from sqlalchemy import create_engine

from homecoach import models  # noqa: F401  (registers tables)
from homecoach.config import get_settings
from homecoach.db import Base

target_metadata = Base.metadata


def run() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            context.configure(connection=conn, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()  # don't leave an idle connection open for the rest of the process (the web app runs this first)


run()
