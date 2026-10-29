"""Tests for HITL review, approval gate preservation, and FX candidate reclassification."""

from __future__ import annotations

from decimal import Decimal
import pytest

from src.hitl import HITLReviewService
from src.models import (
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    JournalEntryValidationResult,
)
from src.store import ReviewStore


def test_approval_cannot_bypass_hard_validation_failures(loaded_coa, default_config):
    """Attempting to approve an entry with hard validation failures (e.g. JE_UNBALANCED) is strictly refused."""
    store = ReviewStore(":memory:")
    unbalanced_entry = JournalEntry(
        id="JE-002",
        description="Unbalanced reclass",
        date="2024-12-28",
        source="Test",
        lines=[
            JournalLine(account="6310", debit=Decimal("28500.00"), credit=Decimal("0.00")),
            JournalLine(account="6310", debit=Decimal("0.00"), credit=Decimal("25000.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-002",
        disposition=JournalEntryDisposition.REJECTED,
        issue_codes=["JE_UNBALANCED"],
        is_balanced=False,
        accounts_exist_in_coa=True,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([unbalanced_entry], {"JE-002": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.approve_entry("JE-002", reviewer="auditor_jane", notes="Attempting override")
    assert res.success is False
    assert "hard validation failures" in res.error_message
    assert store.get_decision("JE-002") is None


def test_approval_of_reviewable_advisory_succeeds(loaded_coa, default_config):
    """An entry with reviewable advisory (JE_SELF_OFFSETTING) can be approved with reviewer and notes."""
    store = ReviewStore(":memory:")
    offset_entry = JournalEntry(
        id="JE-008",
        description="Clear internal clearing account",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="2170", debit=Decimal("35000.00"), credit=Decimal("0.00")),
            JournalLine(account="2170", debit=Decimal("0.00"), credit=Decimal("35000.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-008",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_SELF_OFFSETTING"],
        is_balanced=True,
        accounts_exist_in_coa=True,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([offset_entry], {"JE-008": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.approve_entry("JE-008", reviewer="auditor_jane", notes="Verified legitimate clearing netting")
    assert res.success is True
    assert res.disposition == JournalEntryDisposition.ACCEPTED

    decision = store.get_decision("JE-008")
    assert decision.disposition == JournalEntryDisposition.ACCEPTED
    assert decision.reviewer == "auditor_jane"


def test_reclassification_of_fx_candidate_reruns_hard_gates(loaded_coa, default_config):
    """Reclassifying an FX candidate runs standard hard gates and cannot bypass balance or COA rules."""
    store = ReviewStore(":memory:")
    candidate_entry = JournalEntry(
        id="JE-CAND-01",
        description="Candidate touching 1110 and 7310",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="1110", debit=Decimal("1000.00"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("1000.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-CAND-01",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_FX_MISMATCH"],
        is_balanced=True,
        accounts_exist_in_coa=True,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([candidate_entry], {"JE-CAND-01": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.reclassify_and_revalidate(
        "JE-CAND-01",
        classification="standard_adjustment",
        reviewer="auditor_jane",
        notes="Confirmed not an FX reval, direct cash adjustment",
    )
    assert res.success is True
    assert res.disposition == JournalEntryDisposition.ACCEPTED

    audit = store.get_decision("JE-CAND-01")
    assert audit.prior_classification == "candidate_fx"
    assert audit.new_classification == "standard_adjustment"
