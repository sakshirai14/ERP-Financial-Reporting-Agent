"""Human-in-the-loop review workflow, approval gates, atomic account mapping, and reclassification."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from src.config import ReportingAgentConfig, compute_policy_fingerprint
from src.dq_checks import check_posting_eligibility
from src.ingest import CoaIndex, parse_decimal
from src.journal import evaluate_fx_revaluation, validate_single_entry_full, validate_single_entry_hard_gates
from src.models import (
    FxRateRow,
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    ReviewDecisionRecord,
    ReviewOperationResult,
    TbRow,
)
from src.store import ReviewStore


class HITLReviewService:
    """Service governing human review actions, strict gate enforcement, and atomic state mutations."""

    def __init__(
        self,
        store: ReviewStore,
        coa_index: CoaIndex,
        config: ReportingAgentConfig,
        tb: Optional[list[TbRow]] = None,
        fx_rates: Optional[list[FxRateRow]] = None,
    ):
        self.store = store
        self.coa = coa_index
        self.config = config
        self.tb = tb or []
        self.fx_rates = fx_rates or []

    def approve_entry(
        self,
        entry_id: str,
        reviewer: str,
        notes: str,
        entry: Optional[JournalEntry] = None,
    ) -> ReviewOperationResult:
        """Approves a quarantined entry containing reviewable advisories.

        Cannot approve entries with hard validation failures, unmapped accounts,
        in-batch duplicate conflicts, or FX revaluation calculation discrepancies.
        Approval is only allowed if the remaining issue codes are a subset of
        reviewable advisories and no new issues have emerged since the entry was reviewed.
        """
        if not reviewer or not reviewer.strip():
            return ReviewOperationResult(success=False, error_message="Reviewer identity is required.")
        if not notes or not notes.strip():
            return ReviewOperationResult(success=False, error_message="Documented review notes are required.")

        target_entry = entry or self.store.get_entry(entry_id)
        if not target_entry:
            return ReviewOperationResult(success=False, error_message=f"Entry {entry_id} not found.")

        # 1. Fetch quarantined record to determine what issues reviewer was shown
        q_rec = self.store.get_quarantined_record(entry_id)
        seen_issues = set(q_rec["issue_codes"]) if q_rec else set()

        # 2. Run full revalidation across all policy gates
        val_res = validate_single_entry_full(
            entry=target_entry,
            coa_index=self.coa,
            tb=self.tb,
            fx_rates=self.fx_rates,
            config=self.config,
        )

        current_issues = set(val_res.issue_codes)
        reviewable_advisories = {"JE_SELF_OFFSETTING", "JE_PRIOR_ACCEPTANCE"}

        # 3. Guard against hard failures
        if val_res.disposition == JournalEntryDisposition.REJECTED or any(code in self.config.hard_validation_gates for code in current_issues):
            hard_detected = [c for c in current_issues if c in self.config.hard_validation_gates]
            return ReviewOperationResult(
                success=False,
                error_message=(
                    f"Cannot approve entry {entry_id}: contains hard validation failures ({hard_detected or val_res.issue_codes}). "
                    "Hard failures require correction and revalidation, not an approval override."
                ),
            )

        # 4. Guard against non-reviewable advisories (e.g. FX mismatch, unmapped account)
        non_advisories = current_issues - reviewable_advisories
        if non_advisories:
            return ReviewOperationResult(
                success=False,
                error_message=(
                    f"Cannot approve entry {entry_id}: contains non-reviewable issue(s) {sorted(list(non_advisories))}. "
                    "Only reviewable advisories (e.g. JE_SELF_OFFSETTING, JE_PRIOR_ACCEPTANCE) may be approved."
                ),
            )

        # 5. Guard against new issues not seen during initial review
        if seen_issues and not current_issues.issubset(seen_issues):
            new_issues = current_issues - seen_issues
            return ReviewOperationResult(
                success=False,
                error_message=(
                    f"Cannot approve entry {entry_id}: detected new issue(s) {sorted(list(new_issues))} not present during initial review."
                ),
            )

        fingerprint = compute_policy_fingerprint(self.config)
        self.store.record_decision(
            entry_id=entry_id,
            disposition=JournalEntryDisposition.ACCEPTED,
            reviewer=reviewer.strip(),
            notes=notes.strip(),
            policy_fingerprint=fingerprint,
            issue_codes=sorted(list(current_issues)),
            updated_entry=target_entry,
        )

        audit_rec = self.store.get_decision(entry_id)
        return ReviewOperationResult(
            success=True,
            disposition=JournalEntryDisposition.ACCEPTED,
            audit_entry=audit_rec,
        )

    def reject_entry(
        self,
        entry_id: str,
        reviewer: str,
        notes: str,
    ) -> ReviewOperationResult:
        """Explicitly rejects an entry during human review."""
        if not reviewer or not reviewer.strip():
            return ReviewOperationResult(success=False, error_message="Reviewer identity is required.")
        if not notes or not notes.strip():
            return ReviewOperationResult(success=False, error_message="Documented review notes are required.")

        fingerprint = compute_policy_fingerprint(self.config)
        self.store.record_decision(
            entry_id=entry_id,
            disposition=JournalEntryDisposition.REJECTED,
            reviewer=reviewer.strip(),
            notes=notes.strip(),
            policy_fingerprint=fingerprint,
            issue_codes=["REJECTED_BY_REVIEWER"],
        )
        audit_rec = self.store.get_decision(entry_id)
        return ReviewOperationResult(
            success=True,
            disposition=JournalEntryDisposition.REJECTED,
            audit_entry=audit_rec,
        )

    def map_and_revalidate(
        self,
        entry_id: str,
        from_account: str,
        to_account: str,
        reviewer: str,
        notes: str,
        entry: Optional[JournalEntry] = None,
    ) -> ReviewOperationResult:
        """Atomically maps an unmapped account, reruns full validation, and persists to store."""
        # 1. Pre-condition checks: Reviewer and notes non-empty
        if not reviewer or not reviewer.strip():
            return ReviewOperationResult(success=False, error_message="Reviewer identity is required.")
        if not notes or not notes.strip():
            return ReviewOperationResult(success=False, error_message="Documented review notes are required.")

        target_entry = entry or self.store.get_entry(entry_id)
        if not target_entry:
            return ReviewOperationResult(success=False, error_message=f"Entry {entry_id} not found.")

        # 2. Source account existence check on entry
        entry_accounts = {line.account for line in target_entry.lines}
        if from_account not in entry_accounts:
            return ReviewOperationResult(
                success=False,
                error_message=f"Source account {from_account} does not exist on entry {entry_id}.",
            )

        # 3. Target account existence & posting eligibility check
        if not self.coa.account_exists(to_account):
            return ReviewOperationResult(
                success=False,
                error_message=f"Target account {to_account} does not exist in Chart of Accounts.",
            )

        eligibility = check_posting_eligibility(to_account, self.coa)
        if not eligibility.eligible:
            return ReviewOperationResult(
                success=False,
                error_message=f"Target account {to_account} is not postable: {eligibility.reason} ({eligibility.failed_condition})",
            )

        # 4. Construct candidate entry with mapped account
        candidate_lines = []
        for line in target_entry.lines:
            new_acc = to_account if line.account == from_account else line.account
            candidate_lines.append(
                JournalLine(
                    account=new_acc,
                    debit=line.debit,
                    credit=line.credit,
                    memo=line.memo,
                    currency=line.currency,
                )
            )

        candidate_entry = JournalEntry(
            id=target_entry.id,
            description=target_entry.description,
            date=target_entry.date,
            source=target_entry.source,
            lines=candidate_lines,
            entry_type=target_entry.entry_type,
            source_currency=target_entry.source_currency,
        )

        # 5. Full in-memory revalidation across all policy gates
        full_val = validate_single_entry_full(
            entry=candidate_entry,
            coa_index=self.coa,
            tb=self.tb,
            fx_rates=self.fx_rates,
            config=self.config,
        )

        # If candidate entry suffers a hard failure, mapping cannot be accepted
        if full_val.disposition == JournalEntryDisposition.REJECTED:
            return ReviewOperationResult(
                success=False,
                error_message=f"Candidate entry failed hard validation after mapping: {full_val.issue_codes}",
            )

        # 6. Atomic Persistence in SQLite Transaction (verifies state inside transaction)
        fingerprint = compute_policy_fingerprint(self.config)
        try:
            self.store.persist_account_mapping(
                entry_id=entry_id,
                original_account=from_account,
                mapped_account=to_account,
                reviewer=reviewer.strip(),
                notes=notes.strip(),
                new_disposition=full_val.disposition,
                revalidation_result=full_val,
                policy_fingerprint=fingerprint,
                updated_entry=candidate_entry,
            )
            audit_rec = self.store.get_decision(entry_id)
            return ReviewOperationResult(
                success=True,
                disposition=full_val.disposition,
                audit_entry=audit_rec,
            )
        except Exception as e:
            return ReviewOperationResult(
                success=False,
                error_message=f"Database persistence failure: {str(e)}",
            )

    def reclassify_and_revalidate(
        self,
        entry_id: str,
        classification: str,
        reviewer: str,
        notes: str,
        entry: Optional[JournalEntry] = None,
    ) -> ReviewOperationResult:
        """Reclassifies an FX candidate as standard_adjustment and reruns full validation."""
        if not reviewer or not reviewer.strip():
            return ReviewOperationResult(success=False, error_message="Reviewer identity is required.")
        if not notes or not notes.strip():
            return ReviewOperationResult(success=False, error_message="Documented review notes are required.")

        target_entry = entry or self.store.get_entry(entry_id)
        if not target_entry:
            return ReviewOperationResult(success=False, error_message=f"Entry {entry_id} not found.")

        # Update classification
        candidate_entry = JournalEntry(
            id=target_entry.id,
            description=target_entry.description,
            date=target_entry.date,
            source=target_entry.source,
            lines=target_entry.lines,
            entry_type=classification,
            source_currency=target_entry.source_currency,
        )

        # Full revalidation across all policy gates
        full_val = validate_single_entry_full(
            entry=candidate_entry,
            coa_index=self.coa,
            tb=self.tb,
            fx_rates=self.fx_rates,
            config=self.config,
        )
        if full_val.disposition == JournalEntryDisposition.REJECTED:
            return ReviewOperationResult(
                success=False,
                error_message=f"Reclassified entry failed validation: {full_val.issue_codes}",
            )

        fingerprint = compute_policy_fingerprint(self.config)
        self.store.record_decision(
            entry_id=entry_id,
            disposition=full_val.disposition,
            reviewer=reviewer.strip(),
            notes=notes.strip(),
            policy_fingerprint=fingerprint,
            prior_classification=target_entry.entry_type or "candidate_fx",
            new_classification=classification,
            issue_codes=full_val.issue_codes,
            updated_entry=candidate_entry,
        )
        audit_rec = self.store.get_decision(entry_id)
        return ReviewOperationResult(
            success=True,
            disposition=full_val.disposition,
            audit_entry=audit_rec,
        )
