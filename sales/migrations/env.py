"""Alembic 실행 환경. DB 주소는 core.database 가 정한다(명령행 alembic 에서도 같은 규칙)."""
import os
import sys

from alembic import context
from sqlalchemy import create_engine, pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                          # schema_v2_* 도우미 모듈
sys.path.insert(0, os.path.dirname(HERE))         # sales/ (core 패키지)

config = context.config
url = config.get_main_option("sqlalchemy.url")
if not url or url == "driver://":
    from core import database
    url = database.sqlalchemy_url()


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, render_as_batch=url.startswith("sqlite"))
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
