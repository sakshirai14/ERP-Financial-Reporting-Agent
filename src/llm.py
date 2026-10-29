"""LLM explanation layer with deterministic offline mock default."""

from __future__ import annotations

import os
from typing import Any, Optional


class AccountingExplanationEngine:
    """Generates plain English narrative explanations for journal entry issues and accounting defects.

    Operates 100% offline with a deterministic mock by default.
    The LLM never makes validation decisions; all dispositions are strictly deterministic.
    """

    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None):
        self.provider = provider or os.getenv("LLM_PROVIDER", "mock")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def explain_issue(
        self,
        entry_id: str,
        issue_code: str,
        context: Optional[dict[str, Any]] = None,
        suggestions: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Returns a clear narrative explanation for an accounting finding."""
        ctx = context or {}

        if issue_code == "JE_UNBALANCED":
            return (
                f"Entry {entry_id} violates the fundamental double-entry equation (Debits != Credits). "
                "Total debit postings do not equal total credit postings. Under standard accounting rules, "
                "unbalanced adjustments cannot be accepted or posted to the general ledger."
            )

        if issue_code == "JE_FX_MISMATCH":
            fx_data = ctx.get("fx_context", ctx)
            booked = fx_data.get("booked_amount") or ctx.get("booked_amount")
            expected = fx_data.get("expected_usd_delta") or ctx.get("expected_usd_delta")
            discrepancy = fx_data.get("discrepancy") or ctx.get("discrepancy")
            if not booked or not expected or not discrepancy:
                raise ValueError(f"Missing required FX context for entry {entry_id}: {ctx}")
            return (
                f"Entry {entry_id} reflects an FX revaluation calculation discrepancy. "
                f"The entry books ${booked} USD, but the revaluation delta calculated from "
                f"the foreign cash balance and closing exchange rate is ${expected} USD (variance of ${discrepancy} USD). "
                "The entry is quarantined for finance team reconciliation."
            )

        if issue_code == "JE_CURRENCY_BASIS_INVALID":
            fx_data = ctx.get("fx_context", ctx)
            booked = fx_data.get("booked_amount") or ctx.get("booked_amount")
            native = fx_data.get("source_balance") or fx_data.get("native_balance") or ctx.get("source_balance") or ctx.get("native_balance")
            expected = fx_data.get("expected_usd_delta") or ctx.get("expected_usd_delta")
            if not booked or not native or not expected:
                raise ValueError(f"Missing required currency basis context for entry {entry_id}: {ctx}")
            return (
                f"Entry {entry_id} contains a suspected currency-basis error. "
                f"The booked functional amount of ${booked} USD matches the native foreign balance ({native} EUR) "
                f"rather than the expected functional revaluation delta (${expected} USD). "
                "Posting the raw foreign balance directly into functional reporting lines is rejected as an invalid basis."
            )

        if issue_code == "JE_FX_DIRECTION_INVALID":
            return (
                f"Entry {entry_id} has an inverted accounting direction for foreign currency revaluation. "
                "When a foreign monetary asset appreciates, the asset account must be debited and an unrealized gain credited. "
                "Inverted gain/loss direction is quarantined for correction."
            )

        if issue_code == "JE_ACCOUNT_UNMAPPED":
            unmapped = ctx.get("unmapped_account", "an unmapped code")
            suggestion_str = ""
            if suggestions:
                top = suggestions[0]
                suggestion_str = f" Closest candidate in active Chart of Accounts is {top['account_code']} ({top['account_name']})."
            return (
                f"Entry {entry_id} targets account {unmapped}, which is not present in the active Chart of Accounts. "
                f"The entry has been quarantined for reviewer disambiguation.{suggestion_str}"
            )

        if issue_code == "JE_SELF_OFFSETTING":
            acc = ctx.get("offsetting_account", "the target account")
            return (
                f"Entry {entry_id} debits and credits the exact same account ({acc}), netting to zero change. "
                "While mathematically balanced, self-offsetting journal lines distort line-item reporting. "
                "Quarantined for reviewer verification of business justification."
            )

        if issue_code == "JE_INBATCH_DUPLICATE":
            count = ctx.get("duplicate_count", "multiple")
            return (
                f"Entry ID {entry_id} appears {count} times within the current batch. "
                "All conflicting occurrences have been quarantined symmetrically to prevent accidental duplicate postings."
            )

        if issue_code == "JE_PRIOR_ACCEPTANCE":
            return (
                f"Entry ID {entry_id} was previously approved and accepted in an earlier batch run. "
                "Quarantined as a reviewable advisory to verify whether this is an intentional repeat submission or a duplicate."
            )

        if issue_code in ("JE_NON_POSTABLE_HEADER", "JE_NON_POSTABLE_PARENT"):
            return (
                f"Entry {entry_id} targets an account that is classified as a header or summary parent in the Chart of Accounts. "
                "Summary accounts cannot receive journal postings."
            )

        if issue_code == "JE_DATE_OUT_OF_PERIOD":
            date_err = ctx.get("date_error", {})
            entry_date = date_err.get("entry_date") or ctx.get("entry_date", "the entry date")
            period = date_err.get("batch_period") or ctx.get("period", "2024-Q4")
            return (
                f"Entry {entry_id} has date {entry_date}, which falls outside the active accounting period {period}. "
                "Quarantined for period assignment verification."
            )

        if issue_code == "JE_EXCEEDS_BALANCE":
            bal_err = ctx.get("balance_sufficiency_error", {})
            acc = bal_err.get("account") or ctx.get("account", "target account")
            against_amt = bal_err.get("against_amount") or ctx.get("against_amount", "adjustment amount")
            tb_bal = bal_err.get("tb_balance") or ctx.get("tb_balance", "current balance")
            return (
                f"Entry {entry_id} posts an adjustment of ${against_amt} against the normal balance of account {acc}, "
                f"which exceeds its current ledger balance of ${tb_bal}. "
                "Quarantined to prevent negative ledger balances."
            )

        return f"Entry {entry_id} triggered accounting validation issue {issue_code}. Quarantined for review."
