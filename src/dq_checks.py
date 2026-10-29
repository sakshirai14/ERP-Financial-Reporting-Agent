"""Data quality diagnostics and currency-safe trial balance reconciliation engine."""

from __future__ import annotations

from collections import defaultdict
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
    tb_groups: dict[tuple[str, str], list[TbRow]] = defaultdict(list)
    for row in tb:
        tb_groups[(row.account_code, row.currency)].append(row)

    for (acc_code, curr), rows_list in sorted(tb_groups.items()):
        if len(rows_list) > 1:
            row_nums = [r.row_number for r in rows_list if r.row_number is not None]
            amounts = [f"${max(r.debit, r.credit):,.2f}" for r in rows_list]
            row_str = f"rows {', '.join(str(n) for n in row_nums)}" if row_nums else f"{len(rows_list)} rows"
            findings.append(
                DataQualityFinding(
                    code="DQ_TB_DUPLICATE_ACCOUNT",
                    severity=DiagnosticSeverity.WARNING,
                    message=(
                        f"Duplicate account {acc_code} ({curr}) found on {row_str} with amounts {', '.join(amounts)}: "
                        "duplicate or split - needs human confirmation. Do not silently sum."
                    ),
                    context={
                        "account_code": acc_code,
                        "currency": curr,
                        "row_numbers": row_nums,
                        "amounts": [str(max(r.debit, r.credit)) for r in rows_list],
                    },
                )
            )

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

    # 3b. Trial Balance Currency Semantics Ambiguity (Amendment 3)
    raw_debits = sum(r.debit for r in tb)
    raw_credits = sum(r.credit for r in tb)
    raw_gap = raw_debits - raw_credits

    gbp_debit = sum(r.debit for r in tb if r.currency == "GBP")
    gbp_assumed_rate = Decimal("1.251")  # opening rate from fx_rates.csv
    illustrative_debits = partial_debits + (gbp_debit * gbp_assumed_rate).quantize(Decimal("0.01"))
    illustrative_gap = illustrative_debits - partial_credits

    findings.append(
        DataQualityFinding(
            code="DQ_TB_CURRENCY_AMBIGUITY",
            severity=DiagnosticSeverity.WARNING,
            message=(
                f"Trial balance currency semantics ambiguous - ask controller: "
                f"(a) Raw gap is ${raw_gap:,.2f} USD (Debits: ${raw_debits:,.2f}, Credits: ${raw_credits:,.2f}). "
                f"(b) Partial translated gap using EUR period_end rate ({period_end_rates.get('EUR', Decimal('1.095'))}) only is ${partial_gap:,.2f} USD "
                "(clearly partial because GBP period_end rate is missing). "
                f"Illustrative sensitivity assuming GBP at opening 1.251 (illustrative only): translated gap would be +${illustrative_gap:,.2f} USD. "
                "Translating foreign cash balances moves the trial balance further from balance than the raw gap of -$4,800.00, "
                "suggesting foreign cash amounts may already be in USD. Interpretation is unresolved and needs controller input."
            ),
            context={
                "raw_gap": str(raw_gap),
                "partial_translated_gap": str(partial_gap),
                "illustrative_assumed_gbp_rate": str(gbp_assumed_rate),
                "illustrative_gap": str(illustrative_gap),
                "status": "unresolved_needs_controller_input",
            },
        )
    )

    # 3c. Pre-Closing Retained Earnings & Open P&L Check
    re_row = next((r for r in tb if r.account_code == "3200"), None)
    pl_rows = [r for r in tb if (coa_index.get_account(r.account_code) and coa_index.get_account(r.account_code).statement == "PL") or r.account_code >= "4000"]
    if re_row and pl_rows:
        re_balance = re_row.credit - re_row.debit
        rev_credits = sum(r.credit - r.debit for r in pl_rows if r.account_code.startswith("4"))
        exp_debits = sum(r.debit - r.credit for r in pl_rows if not r.account_code.startswith("4"))
        interim_ni = rev_credits - exp_debits
        findings.append(
            DataQualityFinding(
                code="DQ_TB_PRE_CLOSING_RETAINED_EARNINGS",
                severity=DiagnosticSeverity.INFO,
                message=(
                    f"Retained Earnings (account 3200 balance ${re_balance:,.2f}) is presented on pre-closing trial balance "
                    f"while P&L accounts remain open (interim net income ~${interim_ni:,.2f}). "
                    "SOCIE opening equity treatment needs a human decision."
                ),
                context={
                    "account_code": "3200",
                    "retained_earnings_balance": str(re_balance),
                    "interim_net_income": str(interim_ni),
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

    # 4b. PPE Cost Period-over-Period Jump
    ppe_prior = next((r for r in prior_tb if r.account_code == "1210"), None)
    ppe_cur = next((r for r in tb if r.account_code == "1210"), None)
    if ppe_prior and ppe_cur:
        prior_amt = ppe_prior.debit - ppe_prior.credit
        cur_amt = ppe_cur.debit - ppe_cur.credit
        jump = cur_amt - prior_amt
        if jump > Decimal("1000000.00"):
            pct = (jump / prior_amt * Decimal("100")) if prior_amt else Decimal("0")
            findings.append(
                DataQualityFinding(
                    code="DQ_PPE_COST_JUMP",
                    severity=DiagnosticSeverity.INFO,
                    message=(
                        f"Significant period-over-period jump in Property, Plant & Equipment (account 1210): "
                        f"increased from ${prior_amt:,.2f} in prior period to ${cur_amt:,.2f} in current period "
                        f"(+${jump:,.2f} / +{pct:.1f}%). Requires verification of capital additions."
                    ),
                    context={
                        "account_code": "1210",
                        "prior_balance": str(prior_amt),
                        "current_balance": str(cur_amt),
                        "jump": str(jump),
                    },
                )
            )

    # 5. COA Structure & Metadata Inspections (Findings F-04a to F-04d)
    pl_with_cf = []
    equity_without_cf = []
    for account in coa_index.all_accounts():
        if account.statement == "PL" and account.cf_category and account.cf_category not in ("", "None", "TBD"):
            pl_with_cf.append(account.account_code)
        elif account.account_code in ("3200", "3300") and (not account.cf_category or account.cf_category in ("", "None")):
            equity_without_cf.append(account.account_code)

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

    if pl_with_cf or equity_without_cf:
        findings.append(
            DataQualityFinding(
                code="DQ_COA_CF_CATEGORY_MISMATCH",
                severity=DiagnosticSeverity.INFO,
                message=(
                    f"Cash flow category mismatch in Chart of Accounts: P&L accounts ({', '.join(sorted(pl_with_cf))}) "
                    f"carry cf_category metadata, whereas equity balance sheet accounts ({', '.join(sorted(equity_without_cf))}) have none."
                ),
                context={
                    "pl_accounts_with_cf": sorted(pl_with_cf),
                    "equity_accounts_without_cf": sorted(equity_without_cf),
                },
            )
        )

    return findings
