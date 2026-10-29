"""Tests for duplicate detection mechanisms: order-independent in-batch and cross-batch repeat handling."""

from __future__ import annotations

from decimal import Decimal
from src.journal import validate_adjustments_batch
from src.models import JournalEntry, JournalEntryDisposition, JournalLine


def test_inbatch_duplicate_handling_is_order_independent(loaded_coa, loaded_tb, loaded_fx, default_config):
    """Confirms that reordering duplicate entries produces identical QUARANTINED dispositions for all copies."""
    entry_orig_1 = JournalEntry(
        id="JE-DUP-01",
        description="Duplicate entry copy 1",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="1110", debit=Decimal("100.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("100.00")),
        ],
    )
    entry_independent = JournalEntry(
        id="JE-INDEP-01",
        description="Independent single entry",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="1110", debit=Decimal("200.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("200.00")),
        ],
    )
    entry_orig_2 = JournalEntry(
        id="JE-DUP-01",
        description="Duplicate entry copy 2",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="1110", debit=Decimal("100.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("100.00")),
        ],
    )

    batch_forward = [entry_orig_1, entry_independent, entry_orig_2]
    batch_reversed = [entry_orig_2, entry_independent, entry_orig_1]

    results_fwd = validate_adjustments_batch(batch_forward, loaded_coa, loaded_tb, loaded_fx, default_config)
    results_rev = validate_adjustments_batch(batch_reversed, loaded_coa, loaded_tb, loaded_fx, default_config)

    # In both runs, JE-DUP-01 must be quarantined symmetrically
    assert results_fwd["JE-DUP-01"].disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_INBATCH_DUPLICATE" in results_fwd["JE-DUP-01"].issue_codes

    assert results_rev["JE-DUP-01"].disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_INBATCH_DUPLICATE" in results_rev["JE-DUP-01"].issue_codes

    # Independent entry remains ACCEPTED in both runs
    assert results_fwd["JE-INDEP-01"].disposition == JournalEntryDisposition.ACCEPTED
    assert results_rev["JE-INDEP-01"].disposition == JournalEntryDisposition.ACCEPTED


def test_cross_batch_prior_acceptance_assigns_advisory(loaded_coa, loaded_tb, loaded_fx, default_config):
    """An entry previously accepted in an earlier batch run receives reviewable advisory JE_PRIOR_ACCEPTANCE."""
    entry = JournalEntry(
        id="JE-001",
        description="Bonus accrual re-run",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="6100", debit=Decimal("850000.00"), credit=Decimal("0.00")),
            JournalLine(account="2120", debit=Decimal("0.00"), credit=Decimal("850000.00")),
        ],
    )
    prior_accepted = {"JE-001"}
    results = validate_adjustments_batch(
        [entry],
        loaded_coa,
        loaded_tb,
        loaded_fx,
        default_config,
        prior_accepted_ids=prior_accepted,
    )
    res = results["JE-001"]
    assert res.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_PRIOR_ACCEPTANCE" in res.issue_codes
