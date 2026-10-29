"""Manual adjustments engine: deterministic accounting validation, duplicate detection, and FX revaluation."""

from __future__ import annotations

import calendar
from collections import Counter
from datetime import date, datetime
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


def parse_period_bounds(period_str: str) -> tuple[date, date]:
    """Derives start and end date bounds from a period identifier (e.g. '2024-Q4')."""
    if not period_str:
        return date.min, date.max
    parts = period_str.strip().split("-")
    if len(parts) == 2 and parts[1].upper().startswith("Q"):
        year = int(parts[0])
        quarter = int(parts[1][1:])
        start_month = (quarter - 1) * 3 + 1
        end_month = quarter * 3
        _, last_day = calendar.monthrange(year, end_month)
        return date(year, start_month, 1), date(year, end_month, last_day)
    if len(parts) == 2:
        try:
            year = int(parts[0])
            month = int(parts[1])
            _, last_day = calendar.monthrange(year, month)
            return date(year, month, 1), date(year, month, last_day)
        except ValueError:
            pass
    if len(parts) == 1 and len(parts[0]) == 4:
        try:
            year = int(parts[0])
            return date(year, 1, 1), date(year, 12, 31)
        except ValueError:
            pass
    return date.min, date.max


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

    # Standardized consistent context dict across all outcomes
    context = {
        "booked_amount": booked_amount,
        "expected_usd_delta": expected_delta,
        "discrepancy": booked_amount - expected_delta,
        "opening_rate": open_rate,
        "period_end_rate": close_rate,
        "rates_used": {
            "opening_rate": str(open_rate),
            "period_end_rate": str(close_rate),
        },
        "source_balance": native_balance,
        "native_balance": native_balance,
        "currency": policy.target_currency,
    }

    # Currency basis error check (Heuristic: did preparer post the native EUR balance?)
    if abs(booked_amount - native_balance) <= policy.tolerance and abs(booked_amount - expected_delta) > policy.tolerance:
        return "JE_CURRENCY_BASIS_INVALID", context

    # Calculation mismatch check
    if abs(booked_amount - expected_delta) > policy.tolerance:
        return "JE_FX_MISMATCH", context

    return None, context


def validate_single_entry_full(
    entry: JournalEntry,
    coa_index: CoaIndex,
    tb: list[TbRow],
    fx_rates: list[FxRateRow],
    config: ReportingAgentConfig,
    is_inbatch_duplicate: bool = False,
    is_prior_accepted: bool = False,
    duplicate_count: int = 1,
    batch_period: Optional[str] = "2024-Q4",
) -> JournalEntryValidationResult:
    """Performs complete validation on a single journal entry across all policy gates."""
    issues: list[str] = []
    details: dict[str, Any] = {}

    # Symmetrical in-batch duplicate assignment
    if is_inbatch_duplicate:
        issues.append("JE_INBATCH_DUPLICATE")
        details["duplicate_count"] = duplicate_count

    # Prior acceptance advisory (Mechanism C)
    if is_prior_accepted:
        issues.append("JE_PRIOR_ACCEPTANCE")

    # Hard structural gates
    _, hard_issues = validate_single_entry_hard_gates(entry, coa_index, config)
    issues.extend(hard_issues)

    # Date within period check (Amendment 4)
    if batch_period and entry.date:
        start_d, end_d = parse_period_bounds(batch_period)
        try:
            entry_d = datetime.strptime(entry.date.strip(), "%Y-%m-%d").date()
            if entry_d < start_d or entry_d > end_d:
                issues.append("JE_DATE_OUT_OF_PERIOD")
                details["date_error"] = {
                    "entry_date": entry.date,
                    "batch_period": batch_period,
                    "period_start": str(start_d),
                    "period_end": str(end_d),
                }
        except ValueError:
            issues.append("JE_DATE_OUT_OF_PERIOD")

    # Soft COA check: Unmapped account
    for line in entry.lines:
        if not coa_index.account_exists(line.account):
            if "JE_ACCOUNT_PROHIBITED" not in issues and "JE_ACCOUNT_UNMAPPED" not in issues:
                issues.append("JE_ACCOUNT_UNMAPPED")
                details["unmapped_account"] = line.account

    # Balance sufficiency rule (Amendment 1: uses normal_balance from COA)
    # Flag only when a line moves an account AGAINST its normal side by more than current TB balance.
    # Crediting contra accounts (normal Credit) is normal direction and must NOT be flagged.
    if tb:
        tb_net_balances: dict[str, Decimal] = {}
        for tb_row in tb:
            acc_info = coa_index.get_account(tb_row.account_code)
            norm = acc_info.normal_balance if acc_info else None
            if norm == "Debit":
                tb_net_balances[tb_row.account_code] = tb_net_balances.get(tb_row.account_code, Decimal("0.00")) + (tb_row.debit - tb_row.credit)
            elif norm == "Credit":
                tb_net_balances[tb_row.account_code] = tb_net_balances.get(tb_row.account_code, Decimal("0.00")) + (tb_row.credit - tb_row.debit)

        for line in entry.lines:
            acc_info = coa_index.get_account(line.account)
            if not acc_info or acc_info.statement != "BS":
                continue
            norm = acc_info.normal_balance
            if line.account in tb_net_balances:
                tb_bal = tb_net_balances[line.account]

                moves_against = False
                against_amount = Decimal("0.00")
                if norm == "Debit" and line.credit > Decimal("0.00"):
                    moves_against = True
                    against_amount = line.credit
                elif norm == "Credit" and line.debit > Decimal("0.00"):
                    moves_against = True
                    against_amount = line.debit

                if moves_against and against_amount > tb_bal:
                    issues.append("JE_EXCEEDS_BALANCE")
                    details["balance_sufficiency_error"] = {
                        "account": line.account,
                        "account_name": acc_info.account_name,
                        "normal_balance": norm,
                        "against_amount": str(against_amount),
                        "tb_balance": str(tb_bal),
                    }

    # Self-offsetting check (debit and credit hitting the exact same account)
    if len(entry.lines) >= 2:
        unique_accounts = {line.account for line in entry.lines}
        if len(unique_accounts) == 1:
            issues.append("JE_SELF_OFFSETTING")
            details["offsetting_account"] = next(iter(unique_accounts))

    # FX Revaluation candidate or confirmed check
    account_set = {line.account for line in entry.lines}
    is_fx_candidate = (
        entry.entry_type in ("fx_revaluation", "fx_candidate") or
        (entry.entry_type is None and account_set == {config.fx.asset_account_code, config.fx.gain_loss_account_code})
    )

    if is_fx_candidate:
        fx_issue, fx_ctx = evaluate_fx_revaluation(entry, config.fx, tb, fx_rates, config)
        details.update(fx_ctx)
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

    return JournalEntryValidationResult(
        entry_id=entry.id,
        disposition=disposition,
        issue_codes=unique_issues,
        is_balanced=is_balanced,
        accounts_exist_in_coa=accounts_exist,
        accounts_are_postable=accounts_postable,
        line_currencies_consistent=currencies_consistent,
        details=details,
    )


def validate_adjustments_batch(
    entries: list[JournalEntry],
    coa_index: CoaIndex,
    tb: list[TbRow],
    fx_rates: list[FxRateRow],
    config: ReportingAgentConfig,
    prior_accepted_ids: Optional[set[str]] = None,
    batch_period: Optional[str] = "2024-Q4",
) -> dict[str, JournalEntryValidationResult]:
    """Validates an entire batch of manual journal adjustments deterministically."""
    results: dict[str, JournalEntryValidationResult] = {}
    prior_accepted = prior_accepted_ids or set()

    # Step 1: Detect in-batch duplicates across the entire batch (Mechanism B)
    id_counts = Counter(entry.id for entry in entries)
    inbatch_duplicate_ids = {entry_id for entry_id, count in id_counts.items() if count > 1}

    # Step 2: Validate each entry
    for entry in entries:
        results[entry.id] = validate_single_entry_full(
            entry=entry,
            coa_index=coa_index,
            tb=tb,
            fx_rates=fx_rates,
            config=config,
            is_inbatch_duplicate=(entry.id in inbatch_duplicate_ids),
            is_prior_accepted=(entry.id in prior_accepted),
            duplicate_count=id_counts.get(entry.id, 1),
            batch_period=batch_period,
        )

    return results
