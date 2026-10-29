"""Pytest fixtures for the ERP Financial Reporting Agent."""

from __future__ import annotations

from pathlib import Path
import pytest

from src.config import ReportingAgentConfig
from src.ingest import (
    CoaIndex,
    compute_all_file_hashes,
    load_chart_of_accounts,
    load_fx_rates,
    load_manual_adjustments,
    load_prior_period_tb,
    load_trial_balance,
)
from src.store import ReviewStore

DATA_DIR = Path("data")


@pytest.fixture(scope="session")
def data_path() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def default_config() -> ReportingAgentConfig:
    return ReportingAgentConfig()


@pytest.fixture(scope="session")
def loaded_coa(data_path: Path) -> CoaIndex:
    accounts = load_chart_of_accounts(data_path / "chart_of_accounts.csv")
    return CoaIndex(accounts)


@pytest.fixture(scope="session")
def loaded_tb(data_path: Path):
    return load_trial_balance(data_path / "trial_balance.csv")


@pytest.fixture(scope="session")
def loaded_fx(data_path: Path):
    return load_fx_rates(data_path / "fx_rates.csv")


@pytest.fixture(scope="session")
def loaded_pp_tb(data_path: Path):
    return load_prior_period_tb(data_path / "prior_period_tb.csv")


@pytest.fixture(scope="session")
def loaded_batch(data_path: Path):
    return load_manual_adjustments(data_path / "manual_adjustments.json")


@pytest.fixture
def memory_store() -> ReviewStore:
    store = ReviewStore(":memory:")
    yield store
    store.close()
