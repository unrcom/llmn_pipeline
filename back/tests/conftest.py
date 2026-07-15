import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

VALID_API_KEY = "llmn_test_00000000000000000000000000000000"


def pytest_configure() -> None:
    os.environ.setdefault("API_KEYS", VALID_API_KEY)


@pytest.fixture(autouse=True)
async def _clean_projects_table():
    from app.db import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE rag.projects CASCADE"))
    yield


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
