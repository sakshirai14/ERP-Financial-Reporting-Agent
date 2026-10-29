"""Tests for data quality diagnostics, currency-safe reconciliation, and disposition independence."""

from __future__ import annotations

from decimal import Decimal
from src.dq_checks import run_data_quality_checks
from src.models import (
    DiagnosticSeverity,
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
)
from src.workflow import run_pipeline


def test_dq_checks_on_supplied_files(loaded_tb, loaded_fx, loaded_coa, loaded_pp_tb, default_config):
    """Verifies that DQ checks detect all seeded ledger anomalies without crashing."""
    findings = run_data_quality_checks(
        tb=loaded_tb,
        fx_rates=loaded_fx,
        coa_index=loaded_coa,
        prior_tb=loaded_pp_tb,
        config=default_config,
    )
    codes = {f.code for f in findings}

    # Finding F-01 & F-07: GBP period-end missing & full reconciliation blocked
    assert "DQ_RATE_MISSING" in codes
    assert "DQ_TB_RECONCILIATION_BLOCKED" in codes
    assert "DQ_TB_IMBALANCE_PARTIAL" in codes

    # Finding F-02: Duplicate account 6310 in TB
    assert "DQ_TB_DUPLICATE_ACCOUNT" in codes

    # Finding F-03: Account 9999 missing from COA
    assert "DQ_TB_ACCOUNT_ABSENT_COA" in codes

    # Finding F-05 & F-06: Prior period TB anomalies
    assert "DQ_PPTB_IMBALANCE" in codes
    assert "DQ_PPTB_ACCOUNT_ABSENT_COA" in codes


def test_dq_warning_does_not_mutate_journal_disposition(data_path, tmp_path):
    """Asserts that trial balance warnings cannot alter or downgrade an entry's ACCEPTED disposition."""
    result = run_pipeline(
        data_dir=data_path,
        output_dir=tmp_path / "out",
        db_path=":memory:",
        force_rerun=True,
    )

    # Confirm trial balance emitted warnings
    assert any(f.code == "DQ_TB_IMBALANCE_PARTIAL" for f in result.dq_findings)
    assert any(f.code == "DQ_TB_RECONCILIATION_BLOCKED" for f in result.dq_findings)
    assert all(f.severity in (DiagnosticSeverity.WARNING, DiagnosticSeverity.INFO) for f in result.dq_findings)

    # Confirm clean entries remain ACCEPTED
    assert result.adjustments["JE-001"].disposition == JournalEntryDisposition.ACCEPTED
    assert result.adjustments["JE-004"].disposition == JournalEntryDisposition.ACCEPTED
    assert result.adjustments["JE-006"].disposition == JournalEntryDisposition.ACCEPTED
    assert result.adjustments["JE-007"].disposition == JournalEntryDisposition.ACCEPTED
    assert result.adjustments["JE-009"].disposition == JournalEntryDisposition.ACCEPTED
    assert result.adjustments["JE-010"].disposition == JournalEntryDisposition.ACCEPTED
