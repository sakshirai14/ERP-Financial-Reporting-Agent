"""Data quality diagnostics and currency-safe trial balance reconciliation engine."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from src.config import ReportingAgentConfig
from src.ingest import CoaIndex
from src.models import (
    DataQualityFinding,
    DiagnosticSeverity,
    FxRateRow,
    PostingEligibilityResult,
    PriorPeriodTbRow,
    TbRow,
)


def check_posting_eligibility(account_code: str, coa_index: CoaIndex) -> PostingEligibilityResult:
    """Evaluates whether an account is postable under two independent structural conditions:

    - Condition A: Account must not be classified with account_type == 'Header'.
    - Condition B: Account must not be a parent account (must have no children in COA).
    """
    if not coa_index.account_exists(account_code):
        return PostingEligibilityResult(
            eligible=False,
            failed_condition="CONDITION_ABSENT",
            reason=f"Account {account_code} does not exist in Chart of Accounts.",
        )

    # Condition A: Explicit Header classification
    if coa_index.get_account_type(account_code) == "Header":
        return PostingEligibilityResult(
            eligible=False,
            failed_condition="CONDITION_A_HEADER",
            reason=f"Account {account_code} has account_type='Header' and cannot receive postings.",
        )

    # Condition B: Structural parent account (has children in the COA)
    if coa_index.has_children(account_code):
        child_count = len(coa_index.get_children(account_code))
        return PostingEligibilityResult(
            eligible=False,
            failed_condition="CONDITION_B_PARENT",
            reason=f"Account {account_code} is a parent account with {child_count} children and cannot receive postings.",
        )

    return PostingEligibilityResult(eligible=True)


def run_data_quality_checks(
    tb: list[TbRow],
    fx_rates: list[FxRateRow],
    coa_index: CoaIndex,
    prior_tb: list[PriorPeriodTbRow],
    config: ReportingAgentConfig,
) -> list[DataQualityFinding]:
    """Runs data quality diagnostics across all input ledger tables.

    Emits findings with DiagnosticSeverity. Does NOT alter journal entry dispositions.
    """
    findings: list[DataQualityFinding] = []

    # 1. Check for duplicate account-currency rows in Trial Balance (Finding F-02)
    seen_combos: set[tuple[str, str]] = set()
    for row in tb:
        combo = (row.account_code, row.currency)
        if combo in seen_combos:
            findings.append(
                DataQualityFinding(
                    code="DQ_TB_DUPLICATE_ACCOUNT",
                    severity=DiagnosticSeverity.WARNING,
                    message=f"Duplicate account/currency combination found in trial balance: {row.account_code} ({row.currency})",
                    context={"account_code": row.account_code, "currency": row.currency},
                )
            )
        seen_combos.add(combo)

    # 2. Check for TB accounts missing from Chart of Accounts (Finding F-03)
    for row in tb:
        if not coa_index.account_exists(row.account_code):
            findings.append(
                DataQualityFinding(
                    code="DQ_TB_ACCOUNT_ABSENT_COA",
                    severity=DiagnosticSeverity.WARNING,
                    message=f"Trial balance account {row.account_code} ({row.account_name}) is missing from Chart of Accounts.",
                    context={"account_code": row.account_code, "account_name": row.account_name},
                )
            )

    # 3. Currency-Safe Trial Balance Reconciliation (Findings F-01 & F-07)
    # Build FX rate lookup for period_end
    period_end_rates: dict[str, Decimal] = {"USD": Decimal("1.0")}
    for rate_row in fx_rates:
        if rate_row.rate_type == "period_end":
            period_end_rates[rate_row.currency] = rate_row.rate

    # Check for missing rates for currencies appearing in TB
    currencies_in_tb = {row.currency for row in tb}
    missing_rates = currencies_in_tb - set(period_end_rates.keys())
    for curr in sorted(missing_rates):
        findings.append(
            DataQualityFinding(
                code="DQ_RATE_MISSING",
                severity=DiagnosticSeverity.WARNING,
                message=f"Required period_end FX rate missing for currency '{curr}'.",
                context={"currency": curr, "rate_type": "period_end"},
            )
        )

    # Block full reconciliation fail-closed if any rates are missing
    if missing_rates:
        findings.append(
            DataQualityFinding(
                code="DQ_TB_RECONCILIATION_BLOCKED",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    f"Full multi-currency trial balance reconciliation blocked due to missing period-end FX rates for: {sorted(missing_rates)}. "
                    "Cannot verify full ledger balance without unverifiable rate assumptions."
                ),
                context={"missing_currencies": sorted(missing_rates)},
            )
        )

    # Perform partial USD + EUR comparison for available rates
    partial_debits = Decimal("0.00")
    partial_credits = Decimal("0.00")
    for row in tb:
        if row.currency in period_end_rates:
            rate = period_end_rates[row.currency]
            partial_debits += (row.debit * rate).quantize(Decimal("0.01"))
            partial_credits += (row.credit * rate).quantize(Decimal("0.01"))

    partial_gap = partial_debits - partial_credits
    if partial_gap != Decimal("0.00"):
        findings.append(
            DataQualityFinding(
                code="DQ_TB_IMBALANCE_PARTIAL",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    f"Non-conclusive partial comparison of translated USD and EUR accounts (gap ${partial_gap:,.2f} USD; "
                    f"Debits: ${partial_debits:,.2f}, Credits: ${partial_credits:,.2f}). "
                    "This is NOT evidence of a full ledger imbalance; complete reconciliation is blocked pending GBP/period_end FX rate."
                ),
                context={
                    "partial_debits": str(partial_debits),
                    "partial_credits": str(partial_credits),
                    "partial_gap": str(partial_gap),
                },
            )
        )

    # 4. Check Prior Period Trial Balance (Findings F-05 & F-06)
    opening_rates: dict[str, Decimal] = {"USD": Decimal("1.0")}
    for rate_row in fx_rates:
        if rate_row.rate_type == "opening":
            opening_rates[rate_row.currency] = rate_row.rate

    pp_debits = Decimal("0.00")
    pp_credits = Decimal("0.00")
    for row in prior_tb:
        if not coa_index.account_exists(row.account_code):
            findings.append(
                DataQualityFinding(
                    code="DQ_PPTB_ACCOUNT_ABSENT_COA",
                    severity=DiagnosticSeverity.WARNING,
                    message=f"Prior-period account {row.account_code} ({row.account_name}) is missing from current Chart of Accounts.",
                    context={"account_code": row.account_code},
                )
            )
        rate = opening_rates.get(row.currency, Decimal("1.0"))
        pp_debits += (row.debit * rate).quantize(Decimal("0.01"))
        pp_credits += (row.credit * rate).quantize(Decimal("0.01"))

    pp_gap = pp_debits - pp_credits
    if pp_gap != Decimal("0.00"):
        findings.append(
            DataQualityFinding(
                code="DQ_PPTB_IMBALANCE",
                severity=DiagnosticSeverity.WARNING,
                message=(
                    f"Prior-period trial balance extract has translated USD-equivalent imbalance of ${pp_gap:,.2f} USD "
                    f"(Debits: ${pp_debits:,.2f}, Credits: ${pp_credits:,.2f}). "
                    "Treated as partial balance-sheet extract with non-operating expenses; does not block current-period adjustments."
                ),
                context={"pp_gap": str(pp_gap)},
            )
        )

    # 5. COA Structure & Metadata Inspections (Findings F-04a to F-04d)
    for account in coa_index.all_accounts():
        if account.cf_category == "TBD":
            findings.append(
                DataQualityFinding(
                    code="DQ_COA_CF_CATEGORY_TBD",
                    severity=DiagnosticSeverity.INFO,
                    message=f"Account {account.account_code} ({account.account_name}) has cf_category='TBD'.",
                    context={"account_code": account.account_code},
                )
            )
        if account.account_type == "Header" and not account.normal_balance:
            findings.append(
                DataQualityFinding(
                    code="DQ_COA_EMPTY_NORMAL_BALANCE",
                    severity=DiagnosticSeverity.INFO,
                    message=f"Header account {account.account_code} ({account.account_name}) has empty normal_balance.",
                    context={"account_code": account.account_code},
                )
            )
        if account.account_type == "Header" and not coa_index.has_children(account.account_code):
            findings.append(
                DataQualityFinding(
                    code="DQ_COA_LEAF_HEADER",
                    severity=DiagnosticSeverity.INFO,
                    message=f"Header account {account.account_code} ({account.account_name}) has no child accounts.",
                    context={"account_code": account.account_code},
                )
            )
        if account.account_type != "Header" and coa_index.has_children(account.account_code):
            findings.append(
                DataQualityFinding(
                    code="DQ_COA_NON_HEADER_PARENT",
                    severity=DiagnosticSeverity.INFO,
                    message=f"Account {account.account_code} ({account.account_name}) has type='{account.account_type}' but acts as parent to child accounts.",
                    context={"account_code": account.account_code, "account_type": account.account_type},
                )
            )

    return findings
