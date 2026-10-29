"""Manual adjustments engine: deterministic accounting validation, duplicate detection, and FX revaluation."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Optional

from src.config import FxRevalPolicy, ReportingAgentConfig
from src.dq_checks import check_posting_eligibility
from src.ingest import CoaIndex
from src.models import (
    FxRateRow,
    JournalEntry,
    JournalEntryDisposition,
    JournalEntryValidationResult,
    PostingEligibilityResult,
    TbRow,
)


def validate_single_entry_hard_gates(
    entry: JournalEntry,
    coa_index: CoaIndex,
    config: ReportingAgentConfig,
) -> tuple[bool, list[str]]:
    """Evaluates mandatory hard structural gates on an entry.

    Returns (is_valid, hard_failure_issue_codes).
    """
    issues: list[str] = []

    # 1. Line count check
    if len(entry.lines) < 2:
        issues.append("JE_INVALID_LINE_COUNT")

    # 2. Negative amount check
    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")
    has_negative = False
    for line in entry.lines:
        if line.debit < Decimal("0.00") or line.credit < Decimal("0.00"):
            has_negative = True
        total_debits += line.debit
        total_credits += line.credit

    if has_negative:
        issues.append("JE_NEGATIVE_AMOUNTS")

    # 3. Balance check (Debits must equal Credits within 0.00)
    if total_debits != total_credits:
        issues.append("JE_UNBALANCED")

    # 4. Currency metadata consistency
    for line in entry.lines:
        if line.currency and line.currency != config.functional_currency:
            issues.append("JE_CURRENCY_METADATA_INCONSISTENT")
            break

    # 5. Account existence & posting eligibility
    for line in entry.lines:
        if not coa_index.account_exists(line.account):
            # If account is structurally prohibited/invalid
            if not line.account.isdigit() or len(line.account) < 4:
                if "JE_ACCOUNT_PROHIBITED" not in issues:
                    issues.append("JE_ACCOUNT_PROHIBITED")
            # If valid code structure but absent from COA, it will be handled as JE_ACCOUNT_UNMAPPED (soft gate)
        else:
            eligibility: PostingEligibilityResult = check_posting_eligibility(line.account, coa_index)
            if not eligibility.eligible:
                if eligibility.failed_condition == "CONDITION_A_HEADER":
                    if "JE_NON_POSTABLE_HEADER" not in issues:
                        issues.append("JE_NON_POSTABLE_HEADER")
                elif eligibility.failed_condition == "CONDITION_B_PARENT":
                    if "JE_NON_POSTABLE_PARENT" not in issues:
                        issues.append("JE_NON_POSTABLE_PARENT")

    is_valid = len(issues) == 0
    return is_valid, issues


def evaluate_fx_revaluation(
    entry: JournalEntry,
    policy: FxRevalPolicy,
    tb: list[TbRow],
    fx_rates: list[FxRateRow],
    config: ReportingAgentConfig,
) -> tuple[Optional[str], dict[str, Any]]:
    """Evaluates the structured FX revaluation contract.

    Returns (issue_code_or_none, context_dict).
    """
    context: dict[str, Any] = {}

    # Confirm entry has lines matching asset and gain/loss accounts
    asset_lines = [line for line in entry.lines if line.account == policy.asset_account_code]
    gain_lines = [line for line in entry.lines if line.account == policy.gain_loss_account_code]

    if len(asset_lines) != 1 or len(gain_lines) != 1:
        return None, context

    asset_line = asset_lines[0]
    gain_line = gain_lines[0]

    # Currency ambiguity check
    if entry.source_currency and entry.source_currency != policy.target_currency:
        return "JE_FX_CURRENCY_AMBIGUOUS", {
            "reason": f"Entry source currency '{entry.source_currency}' conflicts with configured policy currency '{policy.target_currency}'"
        }

    # Resolve foreign source balance from Trial Balance
    matching_tb_rows = [
        row for row in tb
        if row.account_code == policy.asset_account_code and row.currency == policy.target_currency
    ]

    if not matching_tb_rows:
        return "JE_FX_BALANCE_UNRESOLVED", {"reason": f"No TB row for account {policy.asset_account_code} in {policy.target_currency}"}

    native_balance = matching_tb_rows[0].debit - matching_tb_rows[0].credit
    context["source_balance"] = native_balance

    # Resolve exchange rates
    open_rate: Optional[Decimal] = None
    close_rate: Optional[Decimal] = None
    for r in fx_rates:
        if r.currency == policy.target_currency:
            if r.rate_type == policy.rate_pair[0]:
                open_rate = r.rate
            elif r.rate_type == policy.rate_pair[1]:
                close_rate = r.rate

    if open_rate is None or close_rate is None:
        return "JE_FX_RATE_MISSING", {"missing_rate_pair": policy.rate_pair}

    context["opening_rate"] = open_rate
    context["period_end_rate"] = close_rate

    rate_change = close_rate - open_rate
    expected_delta = (native_balance * rate_change).quantize(Decimal("0.01"))
    context["expected_usd_delta"] = expected_delta

    # Direction check
    if expected_delta > Decimal("0.00"):
        # Asset gain: asset line must be debit, gain line must be credit
        if not (asset_line.debit > Decimal("0.00") and gain_line.credit > Decimal("0.00")):
            return "JE_FX_DIRECTION_INVALID", {"reason": "Asset appreciation requires debit to asset and credit to gain."}
    elif expected_delta < Decimal("0.00"):
        # Asset loss: asset line must be credit, gain line must be debit
        if not (asset_line.credit > Decimal("0.00") and gain_line.debit > Decimal("0.00")):
            return "JE_FX_DIRECTION_INVALID", {"reason": "Asset depreciation requires credit to asset and debit to loss."}

    # Derivation of booked functional amount
    booked_amount = max(asset_line.debit, asset_line.credit)
    context["booked_amount"] = booked_amount

    # Currency basis error check (Heuristic: did preparer post the native EUR balance?)
    if abs(booked_amount - native_balance) <= policy.tolerance and abs(booked_amount - expected_delta) > policy.tolerance:
        return "JE_CURRENCY_BASIS_INVALID", {
            "booked_amount": str(booked_amount),
            "native_balance": str(native_balance),
            "expected_delta": str(expected_delta),
        }

    # Calculation mismatch check
    if abs(booked_amount - expected_delta) > policy.tolerance:
        return "JE_FX_MISMATCH", {
            "booked_amount": str(booked_amount),
            "expected_delta": str(expected_delta),
            "discrepancy": str(booked_amount - expected_delta),
        }

    return None, context


def validate_adjustments_batch(
    entries: list[JournalEntry],
    coa_index: CoaIndex,
    tb: list[TbRow],
    fx_rates: list[FxRateRow],
    config: ReportingAgentConfig,
    prior_accepted_ids: Optional[set[str]] = None,
) -> dict[str, JournalEntryValidationResult]:
    """Validates an entire batch of manual journal adjustments deterministically.

    Implements:
    - Mechanism B: Order-independent in-batch duplicate handling (symmetrical quarantine)
    - Mechanism C: Cross-batch repeat submission detection
    - Chart of Accounts validation and posting eligibility
    - Self-offsetting entry detection
    - Structured FX revaluation calculation and direction
    """
    results: dict[str, JournalEntryValidationResult] = {}
    prior_accepted = prior_accepted_ids or set()

    # Step 1: Detect in-batch duplicates across the entire batch (Mechanism B)
    id_counts = Counter(entry.id for entry in entries)
    inbatch_duplicate_ids = {entry_id for entry_id, count in id_counts.items() if count > 1}

    # Step 2: Validate each entry
    for entry in entries:
        issues: list[str] = []
        details: dict[str, Any] = {}

        # Symmetrical in-batch duplicate assignment
        if entry.id in inbatch_duplicate_ids:
            issues.append("JE_INBATCH_DUPLICATE")
            details["duplicate_count"] = id_counts[entry.id]

        # Prior acceptance advisory (Mechanism C)
        if entry.id in prior_accepted:
            issues.append("JE_PRIOR_ACCEPTANCE")

        # Hard structural gates
        _, hard_issues = validate_single_entry_hard_gates(entry, coa_index, config)
        issues.extend(hard_issues)

        # Soft COA check: Unmapped account
        for line in entry.lines:
            if not coa_index.account_exists(line.account):
                if "JE_ACCOUNT_PROHIBITED" not in issues and "JE_ACCOUNT_UNMAPPED" not in issues:
                    issues.append("JE_ACCOUNT_UNMAPPED")
                    details["unmapped_account"] = line.account

        # Self-offsetting check (debit and credit hitting the exact same account)
        if len(entry.lines) >= 2:
            unique_accounts = {line.account for line in entry.lines}
            if len(unique_accounts) == 1:
                issues.append("JE_SELF_OFFSETTING")
                details["offsetting_account"] = next(iter(unique_accounts))

        # FX Revaluation candidate or confirmed check
        account_set = {line.account for line in entry.lines}
        is_fx_candidate = (
            entry.entry_type == "fx_revaluation" or
            (entry.entry_type is None and account_set == {config.fx.asset_account_code, config.fx.gain_loss_account_code})
        )

        if is_fx_candidate:
            fx_issue, fx_ctx = evaluate_fx_revaluation(entry, config.fx, tb, fx_rates, config)
            details["fx_context"] = fx_ctx
            if fx_issue:
                issues.append(fx_issue)

        # Deduplicate issues while preserving order
        unique_issues = list(dict.fromkeys(issues))

        # Determine disposition
        has_hard_failure = any(code in config.hard_validation_gates for code in unique_issues)
        if has_hard_failure:
            disposition = JournalEntryDisposition.REJECTED
        elif unique_issues:
            disposition = JournalEntryDisposition.QUARANTINED
        else:
            disposition = JournalEntryDisposition.ACCEPTED

        # Construct result
        is_balanced = "JE_UNBALANCED" not in unique_issues
        accounts_exist = not any(code in ("JE_ACCOUNT_PROHIBITED", "JE_ACCOUNT_UNMAPPED") for code in unique_issues)
        accounts_postable = not any(code in ("JE_NON_POSTABLE_HEADER", "JE_NON_POSTABLE_PARENT") for code in unique_issues)
        currencies_consistent = "JE_CURRENCY_METADATA_INCONSISTENT" not in unique_issues

        results[entry.id] = JournalEntryValidationResult(
            entry_id=entry.id,
            disposition=disposition,
            issue_codes=unique_issues,
            is_balanced=is_balanced,
            accounts_exist_in_coa=accounts_exist,
            accounts_are_postable=accounts_postable,
            line_currencies_consistent=currencies_consistent,
            details=details,
        )

    return results
