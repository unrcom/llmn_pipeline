import os
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings

VALID_API_KEY = "llmn_test_00000000000000000000000000000000"

# get_settings() は lru_cache されるため、それを呼び出す(test_engine の構築を含む)前に確定させる。
os.environ.setdefault("API_KEYS", VALID_API_KEY)

# 開発 DB でテストが走る事故を構造的に排除するため、接続先 DB 名は環境変数に依存せずここで固定する。
TEST_DB_NAME = "llmn_pipeline_test"
DB_PROJECT_DIR = Path(__file__).resolve().parents[2] / "db"


def _test_database_url(scheme: str) -> str:
    settings = get_settings()
    return (
        f"{scheme}://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{TEST_DB_NAME}"
    )


test_engine = create_async_engine(_test_database_url("postgresql+asyncpg"), pool_pre_ping=True)
test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)


async def _override_get_db_session():
    async with test_session_factory() as session:
        yield session


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_database():
    # node-pg-migrate を DATABASE_URL 上書きで test DB に向けて実行する(dotenv は
    # 既存の環境変数を上書きしないため、ここで渡した値が db/.env より優先される)。
    subprocess.run(
        ["npm", "run", "migrate:up"],
        cwd=DB_PROJECT_DIR,
        env={**os.environ, "DATABASE_URL": _test_database_url("postgresql")},
        check=True,
    )


@pytest.fixture(autouse=True)
async def _clean_projects_table():
    async with test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE rag.projects CASCADE"))
    yield


@pytest.fixture
async def client():
    from app.db import get_db_session
    from app.main import app

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
