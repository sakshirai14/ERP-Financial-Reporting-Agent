"""Tests for atomic unmapped-account mapping workflow and failure mode rollback."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from src.hitl import HITLReviewService
from src.models import (
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    JournalEntryValidationResult,
)
from src.store import ReviewStore


def _seed_quarantined_entry(store: ReviewStore, entry_id: str, unmapped_acc: str = "6315", unbalanced: bool = False) -> JournalEntry:
    credit_amt = Decimal("45000.00") if not unbalanced else Decimal("40000.00")
    entry = JournalEntry(
        id=entry_id,
        description="Software subscription accrual",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account=unmapped_acc, debit=Decimal("45000.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=credit_amt),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id=entry_id,
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_ACCOUNT_UNMAPPED"],
        is_balanced=not unbalanced,
        accounts_exist_in_coa=False,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([entry], {entry_id: val_res})
    return entry


def test_map_account_missing_reviewer_aborts_without_mutation(loaded_coa, default_config):
    """Missing reviewer argument aborts execution before state mutation."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005")
    service = HITLReviewService(store, loaded_coa, default_config)

    res = service.map_and_revalidate("JE-005", "6315", "6310", reviewer="", notes="Some notes")
    assert res.success is False
    assert "reviewer" in res.error_message.lower()
    # Stored state unchanged
    assert store.get_decision("JE-005") is None
    entry = store.get_entry("JE-005")
    assert any(line.account == "6315" for line in entry.lines)


def test_map_account_missing_notes_aborts_without_mutation(loaded_coa, default_config):
    """Missing notes argument aborts execution before state mutation."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005")
    service = HITLReviewService(store, loaded_coa, default_config)

    res = service.map_and_revalidate("JE-005", "6315", "6310", reviewer="auditor_jane", notes="   ")
    assert res.success is False
    assert "notes" in res.error_message.lower()
    assert store.get_decision("JE-005") is None


def test_map_account_source_account_mismatch_aborts_without_mutation(loaded_coa, default_config):
    """Supplied from_account not on the entry aborts execution before state mutation."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005", unmapped_acc="6315")
    service = HITLReviewService(store, loaded_coa, default_config)

    res = service.map_and_revalidate("JE-005", from_account="9999", to_account="6310", reviewer="auditor_jane", notes="Valid notes")
    assert res.success is False
    assert "does not exist on entry" in res.error_message
    assert store.get_decision("JE-005") is None


def test_map_account_invalid_target_account_aborts_without_mutation(loaded_coa, default_config):
    """Target account absent from COA aborts execution before state mutation."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005")
    service = HITLReviewService(store, loaded_coa, default_config)

    res = service.map_and_revalidate("JE-005", from_account="6315", to_account="9999", reviewer="auditor_jane", notes="Valid notes")
    assert res.success is False
    assert "does not exist in Chart of Accounts" in res.error_message
    assert store.get_decision("JE-005") is None


def test_map_account_non_postable_target_account_aborts_without_mutation(loaded_coa, default_config):
    """Target account that is a Header or Parent aborts execution before state mutation."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005")
    service = HITLReviewService(store, loaded_coa, default_config)

    # 1. Target is Header 1290
    res_hdr = service.map_and_revalidate("JE-005", from_account="6315", to_account="1290", reviewer="auditor_jane", notes="Valid notes")
    assert res_hdr.success is False
    assert "not postable" in res_hdr.error_message
    assert store.get_decision("JE-005") is None

    # 2. Target is Parent 8000
    res_par = service.map_and_revalidate("JE-005", from_account="6315", to_account="8000", reviewer="auditor_jane", notes="Valid notes")
    assert res_par.success is False
    assert "not postable" in res_par.error_message
    assert store.get_decision("JE-005") is None


def test_map_account_failed_candidate_revalidation_aborts_without_mutation(loaded_coa, default_config):
    """If the mapped candidate entry still fails hard gates (e.g. unbalanced), mapping is refused."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005-UNBAL", unbalanced=True)
    service = HITLReviewService(store, loaded_coa, default_config)

    res = service.map_and_revalidate("JE-005-UNBAL", from_account="6315", to_account="6310", reviewer="auditor_jane", notes="Valid notes")
    assert res.success is False
    assert "failed hard validation" in res.error_message
    assert store.get_decision("JE-005-UNBAL") is None


def test_map_account_success_persists_full_audit_history(loaded_coa, default_config):
    """Valid mapping to postable account 6310 succeeds, transitions to ACCEPTED, and records audit trail."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005", unmapped_acc="6315")
    service = HITLReviewService(store, loaded_coa, default_config)

    res = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6310",
        reviewer="auditor_jane",
        notes="Reclassifying software sub to general IT services account",
    )
    assert res.success is True
    assert res.disposition == JournalEntryDisposition.ACCEPTED

    # Verify stored audit record
    audit = store.get_decision("JE-005")
    assert audit is not None
    assert audit.disposition == JournalEntryDisposition.ACCEPTED
    assert audit.reviewer == "auditor_jane"
    assert audit.notes == "Reclassifying software sub to general IT services account"
    assert audit.original_account == "6315"
    assert audit.mapped_account == "6310"
    assert audit.policy_fingerprint != ""

    # Verify entry in queue was updated
    updated = store.get_entry("JE-005")
    assert any(line.account == "6310" for line in updated.lines)
    assert not any(line.account == "6315" for line in updated.lines)


def test_map_account_persistence_failure_rolls_back_cleanly(loaded_coa, default_config, monkeypatch):
    """Simulated database failure during map persistence rolls back and leaves state unchanged."""
    store = ReviewStore(":memory:")
    _seed_quarantined_entry(store, "JE-005", unmapped_acc="6315")
    service = HITLReviewService(store, loaded_coa, default_config)

    def raise_db_error(*args, **kwargs):
        raise RuntimeError("Simulated SQLite write error")

    monkeypatch.setattr(store, "persist_account_mapping", raise_db_error)

    res = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6310",
        reviewer="auditor_jane",
        notes="Valid notes",
    )
    assert res.success is False
    assert "Database persistence failure" in res.error_message

    # Verify no decision was recorded
    assert store.get_decision("JE-005") is None

    # Verify entry remains with original unmapped account
    entry = store.get_entry("JE-005")
    assert any(line.account == "6315" for line in entry.lines)


def test_map_account_actual_je_005_to_6310_quarantines_for_self_offsetting(loaded_coa, default_config, data_path):
    """Mapping actual JE-005 (Dr 6315 / Cr 6310) to 6310 results in QUARANTINED with JE_SELF_OFFSETTING."""
    from src.ingest import load_manual_adjustments
    batch = load_manual_adjustments(data_path / "manual_adjustments.json")
    je_005 = next(e for e in batch.entries if e.id == "JE-005")

    store = ReviewStore(":memory:")
    val_res = JournalEntryValidationResult(
        entry_id="JE-005",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_ACCOUNT_UNMAPPED"],
        is_balanced=True,
        accounts_exist_in_coa=False,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([je_005], {"JE-005": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6310",
        reviewer="auditor_jane",
        notes="Attempt mapping to existing counterparty account 6310",
    )
    # Mapping succeeds in updating the account, but entry disposition must be QUARANTINED due to self-offsetting
    assert res.success is True
    assert res.disposition == JournalEntryDisposition.QUARANTINED

    # Verify decision stored reflects QUARANTINED and JE_SELF_OFFSETTING
    dec = store.get_decision("JE-005")
    assert dec is not None
    assert dec.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_SELF_OFFSETTING" in dec.issue_codes

    # Verify updated entry in review store
    updated = store.get_entry("JE-005")
    assert all(l.account == "6310" for l in updated.lines)


def test_map_account_actual_je_005_to_6300_accepts(loaded_coa, default_config, data_path):
    """Mapping actual JE-005 (Dr 6315 / Cr 6310) to valid leaf account 6300 results in ACCEPTED."""
    from src.ingest import load_manual_adjustments
    batch = load_manual_adjustments(data_path / "manual_adjustments.json")
    je_005 = next(e for e in batch.entries if e.id == "JE-005")

    store = ReviewStore(":memory:")
    val_res = JournalEntryValidationResult(
        entry_id="JE-005",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_ACCOUNT_UNMAPPED"],
        is_balanced=True,
        accounts_exist_in_coa=False,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([je_005], {"JE-005": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6300",
        reviewer="auditor_jane",
        notes="Mapped travel to marketing 6300",
    )
    assert res.success is True
    assert res.disposition == JournalEntryDisposition.ACCEPTED

    dec = store.get_decision("JE-005")
    assert dec is not None
    assert dec.disposition == JournalEntryDisposition.ACCEPTED
    assert dec.issue_codes == []


def test_map_account_to_header_or_parent_refused(loaded_coa, default_config, data_path):
    """Mapping to non-postable header account (1290) or parent account (6000) is refused."""
    from src.ingest import load_manual_adjustments
    batch = load_manual_adjustments(data_path / "manual_adjustments.json")
    je_005 = next(e for e in batch.entries if e.id == "JE-005")

    store = ReviewStore(":memory:")
    val_res = JournalEntryValidationResult(
        entry_id="JE-005",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_ACCOUNT_UNMAPPED"],
        is_balanced=True,
        accounts_exist_in_coa=False,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([je_005], {"JE-005": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)

    # 1. Header account 1290
    res_hdr = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="1290",
        reviewer="auditor_jane",
        notes="Try mapping to header",
    )
    assert res_hdr.success is False
    assert "not postable" in res_hdr.error_message

    # 2. Parent account 6000
    res_parent = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6000",
        reviewer="auditor_jane",
        notes="Try mapping to parent",
    )
    assert res_parent.success is False
    assert "not postable" in res_parent.error_message
