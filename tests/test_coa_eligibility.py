"""Tests for Chart of Accounts posting eligibility rules (Condition A and Condition B)."""

from __future__ import annotations

from decimal import Decimal
from src.dq_checks import check_posting_eligibility
from src.journal import validate_single_entry_hard_gates
from src.models import JournalEntry, JournalLine


def test_coa_condition_a_header_is_non_postable(loaded_coa):
    """Account 1290 is classified as 'Header' (even with 0 children) and cannot receive postings."""
    res_1290 = check_posting_eligibility("1290", loaded_coa)
    assert res_1290.eligible is False
    assert res_1290.failed_condition == "CONDITION_A_HEADER"
    assert "Header" in res_1290.reason

    # Also test top-level headers
    assert check_posting_eligibility("1000", loaded_coa).failed_condition == "CONDITION_A_HEADER"
    assert check_posting_eligibility("7000", loaded_coa).failed_condition == "CONDITION_A_HEADER"


def test_coa_condition_b_parent_is_non_postable(loaded_coa):
    """Account 8000 is typed 'Expense' but has children in COA, making it non-postable."""
    res_8000 = check_posting_eligibility("8000", loaded_coa)
    assert res_8000.eligible is False
    assert res_8000.failed_condition == "CONDITION_B_PARENT"
    assert "parent account" in res_8000.reason


def test_coa_valid_leaf_accounts_are_postable(loaded_coa):
    """Leaf accounts with valid non-header types and 0 children are postable."""
    postable_codes = ["1110", "1120", "2110", "2120", "6100", "7310"]
    for code in postable_codes:
        res = check_posting_eligibility(code, loaded_coa)
        assert res.eligible is True, f"Account {code} should be postable: {res.reason}"


def test_coa_absent_account(loaded_coa):
    """Non-existent account code fails eligibility check with CONDITION_ABSENT."""
    res = check_posting_eligibility("9999", loaded_coa)
    assert res.eligible is False
    assert res.failed_condition == "CONDITION_ABSENT"


def test_journal_posting_to_header_rejected(loaded_coa, default_config):
    """A journal entry posting to Header account 1290 is rejected with JE_NON_POSTABLE_HEADER."""
    entry = JournalEntry(
        id="JE-TEST-HDR",
        description="Invalid posting to header",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="1290", debit=Decimal("100.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("100.00")),
        ],
    )
    is_valid, issues = validate_single_entry_hard_gates(entry, loaded_coa, default_config)
    assert is_valid is False
    assert "JE_NON_POSTABLE_HEADER" in issues


def test_journal_posting_to_parent_rejected(loaded_coa, default_config):
    """A journal entry posting to parent account 8000 is rejected with JE_NON_POSTABLE_PARENT."""
    entry = JournalEntry(
        id="JE-TEST-PAR",
        description="Invalid posting to parent",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="8000", debit=Decimal("500.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("500.00")),
        ],
    )
    is_valid, issues = validate_single_entry_hard_gates(entry, loaded_coa, default_config)
    assert is_valid is False
    assert "JE_NON_POSTABLE_PARENT" in issues
