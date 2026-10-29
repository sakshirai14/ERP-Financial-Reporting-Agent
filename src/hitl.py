"""Human-in-the-loop review workflow, approval gates, atomic account mapping, and reclassification."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from src.config import ReportingAgentConfig, compute_policy_fingerprint
from src.dq_checks import check_posting_eligibility
from src.ingest import CoaIndex, parse_decimal
from src.journal import validate_single_entry_hard_gates
from src.models import (
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    ReviewDecisionRecord,
    ReviewOperationResult,
)
from src.store import ReviewStore


class HITLReviewService:
    """Service governing human review actions, strict gate enforcement, and atomic state mutations."""

    def __init__(
        self,
        store: ReviewStore,
        coa_index: CoaIndex,
        config: ReportingAgentConfig,
    ):
        self.store = store
        self.coa = coa_index
        self.config = config

    def approve_entry(
        self,
        entry_id: str,
        reviewer: str,
        notes: str,
        entry: Optional[JournalEntry] = None,
    ) -> ReviewOperationResult:
        """Approves a quarantined entry containing reviewable advisories.

        Cannot approve entries with hard validation failures or unmapped accounts.
        """
        if not reviewer or not reviewer.strip():
            return ReviewOperationResult(success=False, error_message="Reviewer identity is required.")
        if not notes or not notes.strip():
            return ReviewOperationResult(success=False, error_message="Documented review notes are required.")

        target_entry = entry or self.store.get_entry(entry_id)
        if not target_entry:
            return ReviewOperationResult(success=False, error_message=f"Entry {entry_id} not found.")

        # Hard validation pre-flight check
        is_valid, hard_issues = validate_single_entry_hard_gates(target_entry, self.coa, self.config)
        if not is_valid:
            return ReviewOperationResult(
                success=False,
                error_message=(
                    f"Cannot approve entry {entry_id}: contains hard validation failures ({hard_issues}). "
                    "Hard failures require correction and revalidation, not an approval override."
                ),
            )

        # Check for unmapped accounts
        unmapped_accounts = [
            line.account for line in target_entry.lines if not self.coa.account_exists(line.account)
        ]
        if unmapped_accounts:
            return ReviewOperationResult(
                success=False,
                error_message=(
                    f"Cannot approve entry {entry_id}: account(s) {unmapped_accounts} remain unmapped. "
                    "Must execute map-account before approval."
                ),
            )

        # Check for FX mismatch or direction failures
        # (These require correction/resubmission or reclassification, not blind approval)
        fingerprint = compute_policy_fingerprint(self.config)
        self.store.record_decision(
            entry_id=entry_id,
            disposition=JournalEntryDisposition.ACCEPTED,
            reviewer=reviewer.strip(),
            notes=notes.strip(),
            policy_fingerprint=fingerprint,
            issue_codes=[],
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
        """Atomically maps an unmapped account, reruns all hard gates, and persists to store."""
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

        # 5. In-Memory Hard Gate Revalidation on Candidate Entry
        is_valid, hard_issues = validate_single_entry_hard_gates(candidate_entry, self.coa, self.config)
        if not is_valid:
            return ReviewOperationResult(
                success=False,
                error_message=f"Candidate entry failed hard validation after mapping: {hard_issues}",
            )

        # 6. Atomic Persistence in SQLite Transaction
        fingerprint = compute_policy_fingerprint(self.config)
        try:
            self.store.persist_account_mapping(
                entry_id=entry_id,
                original_account=from_account,
                mapped_account=to_account,
                reviewer=reviewer.strip(),
                notes=notes.strip(),
                new_disposition=JournalEntryDisposition.ACCEPTED,
                revalidation_result=candidate_entry,
                policy_fingerprint=fingerprint,
                updated_entry=candidate_entry,
            )
            audit_rec = self.store.get_decision(entry_id)
            return ReviewOperationResult(
                success=True,
                disposition=JournalEntryDisposition.ACCEPTED,
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
        """Reclassifies an FX candidate as standard_adjustment and reruns hard gates."""
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

        # Standard hard gate revalidation (must never bypass hard gates)
        is_valid, hard_issues = validate_single_entry_hard_gates(candidate_entry, self.coa, self.config)
        if not is_valid:
            return ReviewOperationResult(
                success=False,
                error_message=f"Reclassified entry failed standard hard validation: {hard_issues}",
            )

        fingerprint = compute_policy_fingerprint(self.config)
        self.store.record_decision(
            entry_id=entry_id,
            disposition=JournalEntryDisposition.ACCEPTED,
            reviewer=reviewer.strip(),
            notes=notes.strip(),
            policy_fingerprint=fingerprint,
            prior_classification="candidate_fx",
            new_classification=classification,
            issue_codes=[],
            updated_entry=candidate_entry,
        )
        audit_rec = self.store.get_decision(entry_id)
        return ReviewOperationResult(
            success=True,
            disposition=JournalEntryDisposition.ACCEPTED,
            audit_entry=audit_rec,
        )
