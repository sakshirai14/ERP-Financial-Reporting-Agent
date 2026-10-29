"""Tests for ingestion layer, cryptographic hashes, and Decimal arithmetic parsing."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from src.ingest import (
    compute_file_hash,
    compute_all_file_hashes,
    parse_decimal,
    load_chart_of_accounts,
    load_trial_balance,
    load_fx_rates,
    load_prior_period_tb,
    load_manual_adjustments,
)


def test_decimal_parser_scale_and_safety():
    """Confirms Decimal parsing preserves exact scale without float corruption."""
    assert parse_decimal("100.50") == Decimal("100.50")
    assert parse_decimal("1,385,000.00") == Decimal("1385000.00")
    assert parse_decimal(825400.0) == Decimal("825400.0")
    assert parse_decimal("") == Decimal("0.00")
    assert parse_decimal(None) == Decimal("0.00")


def test_compute_file_hashes_exact_format(data_path: Path):
    """File hashes must be 64-character lowercase hexadecimal strings."""
    hashes = compute_all_file_hashes(data_path)
    assert len(hashes) == 5
    for fname, fhash in hashes.items():
        assert len(fhash) == 64
        assert fhash == fhash.lower()
        assert all(c in "0123456789abcdef" for c in fhash)


def test_ingest_row_counts(data_path: Path):
    """Verifies that all files parse into exact expected row counts."""
    coa = load_chart_of_accounts(data_path / "chart_of_accounts.csv")
    assert len(coa) == 72

    tb = load_trial_balance(data_path / "trial_balance.csv")
    assert len(tb) == 62

    fx = load_fx_rates(data_path / "fx_rates.csv")
    assert len(fx) == 7

    pp = load_prior_period_tb(data_path / "prior_period_tb.csv")
    assert len(pp) == 33

    ma = load_manual_adjustments(data_path / "manual_adjustments.json")
    assert len(ma.entries) == 10
