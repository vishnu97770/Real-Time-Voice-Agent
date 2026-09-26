from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db import Base  # importing app.db registers every model on Base.metadata

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Autogenerate compares the database with this metadata. A model is only seen once its
# module has been imported: new model modules must be imported from app/models/__init__.py.
target_metadata = Base.metadata

# render_as_batch: SQLite cannot add constraints with ALTER TABLE, so migrations use batch
# operations. On PostgreSQL a batch operation is just the ordinary ALTER statements.
OPTIONS = {"target_metadata": target_metadata, "compare_type": True, "render_as_batch": True}


def database_url() -> str:
    """The URL passed to Alembic programmatically (tests), else the application's DATABASE_URL."""
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (`alembic upgrade head --sql`)."""
    context.configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"}, **OPTIONS)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)

    with engine.connect() as connection:
        context.configure(connection=connection, **OPTIONS)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
