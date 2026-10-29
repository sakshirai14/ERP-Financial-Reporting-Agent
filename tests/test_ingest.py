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


def test_profile_all_data_ground_truth(data_path: Path):
    """Verifies profile_all_data computes verified ground-truth figures."""
    from src.tools.profile_data import profile_all_data

    profile = profile_all_data(data_path)
    tb = profile["trial_balance"]
    assert tb["total_debits"] == Decimal("71072200.00")
    assert tb["total_credits"] == Decimal("71077000.00")
    assert tb["raw_gap"] == Decimal("-4800.00")
    assert tb["account_6310"]["rows"] == [48, 49]
    assert tb["account_6310"]["total_debit"] == Decimal("283500.00")
    assert tb["account_9999"]["debit"] == Decimal("12400.00")

    coa = profile["chart_of_accounts"]
    assert coa["name_6310"] == "Travel and Entertainment"
    assert coa["name_6300"] == "Marketing and Advertising"
    assert coa["non_header_parents"] == ["3300", "8000"]

    pp = profile["prior_period_tb"]
    assert pp["total_debits"] == Decimal("35177800.00")
    assert pp["total_credits"] == Decimal("32345000.00")
    assert pp["raw_gap"] == Decimal("2832800.00")
    assert pp["ppe_1210_debit"] == Decimal("11800000.00")
    assert pp["has_6905"] is True

