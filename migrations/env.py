from alembic import context

import app.models  # noqa: F401 -- register models before accessing metadata
from app.database import Base, engine

target_metadata = Base.metadata

if context.is_offline_mode():
    from app.config import get_settings

    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
