from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from memory_demo.config import Settings
from memory_demo.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key=None,
        offline_demo_mode="true",
        database_path=tmp_path / "memory-demo.sqlite3",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client
