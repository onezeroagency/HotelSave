"""Alembic environment — pulls the URL and metadata from the app."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import models so their tables register on Base.metadata.
from app import models  # noqa: F401
from app.config import settings
from app.database import Base

config = context.config
# `set_main_option` runs the value through configparser interpolation, so a
# literal % in a host-issued password would explode — escape it. Use the
# normalized URL so Render/Heroku `postgres://` URLs load a real dialect.
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
