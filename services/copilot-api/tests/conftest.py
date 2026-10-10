from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockcopilot.config import Settings
from stockcopilot.data.csv_repository import CsvRepository
from stockcopilot.domain.drafts import InMemoryDraftStore
from stockcopilot.domain.services import Services
from stockcopilot.domain.strategy.pipeline import StrategyEngine

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v2"
AS_OF = date(2026, 9, 16)


@pytest.fixture(scope="session")
def repo() -> CsvRepository:
    return CsvRepository(FIXTURE_DIR)


@pytest.fixture(scope="session")
def svc(repo: CsvRepository) -> Services:
    return Services.create(repo, AS_OF)


@pytest.fixture
def drafts() -> InMemoryDraftStore:
    return InMemoryDraftStore()


@pytest.fixture
def engine(svc: Services, drafts: InMemoryDraftStore) -> StrategyEngine:
    return StrategyEngine(svc, drafts)


@pytest.fixture
def client() -> Iterator[TestClient]:
    from stockcopilot.api.app import create_app

    settings = Settings(dataset_dir=FIXTURE_DIR, demo_as_of=AS_OF, llm_mode="offline", _env_file=None)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
