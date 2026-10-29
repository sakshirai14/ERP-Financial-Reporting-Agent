"""Tests for the structured Foreign Exchange revaluation contract and precedence rules."""

from __future__ import annotations

from decimal import Decimal
from src.config import FxRevalPolicy, ReportingAgentConfig
from src.journal import evaluate_fx_revaluation, validate_adjustments_batch
from src.models import (
    FxRateRow,
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    TbRow,
)


def test_fx_correct_delta_and_direction_passes(loaded_tb, loaded_fx, default_config):
    """An FX revaluation entry booking exact expected delta $19,809.60 with correct direction passes amount check."""
    entry = JournalEntry(
        id="JE-FX-CLEAN",
        description="Clean EUR spot revaluation",
        date="2024-12-31",
        source="Reval calc",
        entry_type="fx_revaluation",
        source_currency="EUR",
        lines=[
            JournalLine(account="1110", currency="USD", debit=Decimal("19809.60"), credit=Decimal("0.00")),
            JournalLine(account="7310", currency="USD", debit=Decimal("0.00"), credit=Decimal("19809.60")),
        ],
    )
    issue, ctx = evaluate_fx_revaluation(entry, default_config.fx, loaded_tb, loaded_fx, default_config)
    assert issue is None
    assert ctx["source_balance"] == Decimal("825400.00")
    assert ctx["opening_rate"] == Decimal("1.071")
    assert ctx["period_end_rate"] == Decimal("1.095")
    assert ctx["expected_usd_delta"] == Decimal("19809.60")
    assert ctx["booked_amount"] == Decimal("19809.60")


def test_fx_currency_basis_invalid_when_native_balance_booked(loaded_tb, loaded_fx, default_config, loaded_coa):
    """When the booked functional amount is $825,400.00 (matching native balance), emit JE_CURRENCY_BASIS_INVALID."""
    entry = JournalEntry(
        id="JE-FX-BASIS",
        description="Mistakenly booked EUR native balance into USD lines",
        date="2024-12-31",
        source="Manual prep",
        entry_type="fx_revaluation",
        source_currency="EUR",
        lines=[
            JournalLine(account="1110", currency="USD", debit=Decimal("825400.00"), credit=Decimal("0.00")),
            JournalLine(account="7310", currency="USD", debit=Decimal("0.00"), credit=Decimal("825400.00")),
        ],
    )
    results = validate_adjustments_batch([entry], loaded_coa, loaded_tb, loaded_fx, default_config)
    res = results["JE-FX-BASIS"]
    assert res.disposition == JournalEntryDisposition.REJECTED
    assert "JE_CURRENCY_BASIS_INVALID" in res.issue_codes
    assert res.is_balanced is True
    assert res.accounts_exist_in_coa is True
    assert res.accounts_are_postable is True


def test_fx_mismatch_when_arbitrary_incorrect_amount(loaded_tb, loaded_fx, default_config, loaded_coa):
    """When booked functional amount is $11,200.00 (JE-003), emit JE_FX_MISMATCH (QUARANTINED)."""
    entry = JournalEntry(
        id="JE-003",
        description="FX revaluation of EUR cash balance to period-end rate",
        date="2024-12-31",
        source="FX reval calc",
        lines=[
            JournalLine(account="1110", debit=Decimal("11200.00"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("11200.00")),
        ],
    )
    results = validate_adjustments_batch([entry], loaded_coa, loaded_tb, loaded_fx, default_config)
    res = results["JE-003"]
    assert res.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_FX_MISMATCH" in res.issue_codes


def test_fx_inverted_direction_is_quarantined(loaded_tb, loaded_fx, default_config, loaded_coa):
    """When exchange rate appreciates but asset is credited and gain is debited, emit JE_FX_DIRECTION_INVALID."""
    entry = JournalEntry(
        id="JE-FX-INV",
        description="Inverted direction revaluation",
        date="2024-12-31",
        source="Test",
        entry_type="fx_revaluation",
        source_currency="EUR",
        lines=[
            JournalLine(account="1110", currency="USD", debit=Decimal("0.00"), credit=Decimal("19809.60")),
            JournalLine(account="7310", currency="USD", debit=Decimal("19809.60"), credit=Decimal("0.00")),
        ],
    )
    results = validate_adjustments_batch([entry], loaded_coa, loaded_tb, loaded_fx, default_config)
    res = results["JE-FX-INV"]
    assert res.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_FX_DIRECTION_INVALID" in res.issue_codes


def test_fx_tolerance_boundaries(loaded_tb, loaded_fx, default_config):
    """Verifies that tolerance boundary +/- 0.01 passes, while 0.02 discrepancy is quarantined."""
    # Delta is 19,809.60.
    # Boundary +0.01: 19,809.61 -> should pass
    entry_plus = JournalEntry(
        id="JE-TOL-PLUS",
        description="Tol plus 0.01",
        date="2024-12-31",
        source="Test",
        entry_type="fx_revaluation",
        lines=[
            JournalLine(account="1110", debit=Decimal("19809.61"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("19809.61")),
        ],
    )
    issue_plus, _ = evaluate_fx_revaluation(entry_plus, default_config.fx, loaded_tb, loaded_fx, default_config)
    assert issue_plus is None

    # Boundary -0.01: 19,809.59 -> should pass
    entry_minus = JournalEntry(
        id="JE-TOL-MINUS",
        description="Tol minus 0.01",
        date="2024-12-31",
        source="Test",
        entry_type="fx_revaluation",
        lines=[
            JournalLine(account="1110", debit=Decimal("19809.59"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("19809.59")),
        ],
    )
    issue_minus, _ = evaluate_fx_revaluation(entry_minus, default_config.fx, loaded_tb, loaded_fx, default_config)
    assert issue_minus is None

    # Beyond boundary: 19,809.62 -> should fail with JE_FX_MISMATCH
    entry_fail = JournalEntry(
        id="JE-TOL-FAIL",
        description="Tol plus 0.02",
        date="2024-12-31",
        source="Test",
        entry_type="fx_revaluation",
        lines=[
            JournalLine(account="1110", debit=Decimal("19809.62"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("19809.62")),
        ],
    )
    issue_fail, _ = evaluate_fx_revaluation(entry_fail, default_config.fx, loaded_tb, loaded_fx, default_config)
    assert issue_fail == "JE_FX_MISMATCH"


def test_fx_missing_rate_or_balance_fails_closed(default_config, loaded_coa):
    """Missing rates or unresolved balances are fail-closed and never silently accepted."""
    # 1. Missing rate simulation: policy looking for JPY
    policy_jpy = FxRevalPolicy(target_currency="JPY")
    cfg_jpy = ReportingAgentConfig(fx=policy_jpy)

    tb_jpy = [TbRow(account_code="1110", account_name="Cash", currency="JPY", debit=Decimal("1000000"), credit=Decimal("0"))]
    rates_empty = []

    entry = JournalEntry(
        id="JE-JPY",
        description="JPY cash reval",
        date="2024-12-31",
        source="Test",
        entry_type="fx_revaluation",
        lines=[
            JournalLine(account="1110", debit=Decimal("500.00"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("500.00")),
        ],
    )
    results = validate_adjustments_batch([entry], loaded_coa, tb_jpy, rates_empty, cfg_jpy)
    res = results["JE-JPY"]
    assert res.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_FX_RATE_MISSING" in res.issue_codes
    assert res.disposition != JournalEntryDisposition.ACCEPTED

    # 2. Missing source balance simulation: account 1110 JPY row missing from TB
    results_no_tb = validate_adjustments_batch([entry], loaded_coa, [], rates_empty, cfg_jpy)
    res_no_tb = results_no_tb["JE-JPY"]
    assert res_no_tb.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_FX_BALANCE_UNRESOLVED" in res_no_tb.issue_codes
    assert res_no_tb.disposition != JournalEntryDisposition.ACCEPTED


def test_fx_ambiguous_currency_is_quarantined(loaded_tb, loaded_fx, default_config, loaded_coa):
    """When entry specifies source_currency GBP but policy targets EUR, quarantine with JE_FX_CURRENCY_AMBIGUOUS."""
    entry = JournalEntry(
        id="JE-AMB-01",
        description="Ambiguous foreign currency reval",
        date="2024-12-31",
        source="Test",
        entry_type="fx_revaluation",
        source_currency="GBP",  # Conflicting with policy.target_currency='EUR'
        lines=[
            JournalLine(account="1110", debit=Decimal("1000.00"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("1000.00")),
        ],
    )
    results = validate_adjustments_batch([entry], loaded_coa, loaded_tb, loaded_fx, default_config)
    res = results["JE-AMB-01"]
    assert res.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_FX_CURRENCY_AMBIGUOUS" in res.issue_codes

