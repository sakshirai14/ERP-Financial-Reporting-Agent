"""Pipeline workflow orchestrator for the ERP Financial Reporting Agent."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from src.audit import (
    render_issues_report_md,
    write_adjustments_decisions_csv,
    write_review_queue_json,
    write_run_trace_jsonl,
)
from src.config import ReportingAgentConfig, compute_policy_fingerprint
from src.dq_checks import run_data_quality_checks
from src.ingest import (
    CoaIndex,
    compute_all_file_hashes,
    load_chart_of_accounts,
    load_fx_rates,
    load_manual_adjustments,
    load_prior_period_tb,
    load_trial_balance,
)
from src.journal import validate_adjustments_batch
from src.llm import AccountingExplanationEngine
from src.models import (
    DataQualityFinding,
    DiagnosticSeverity,
    ExecutionSummary,
    JournalEntry,
    JournalEntryDisposition,
    JournalEntryValidationResult,
    JournalLine,
    PipelineExecutionResult,
)
from src.resolver import AccountResolver
from src.store import ReviewStore


def compute_batch_run_hash(file_hashes: dict[str, str]) -> str:
    """Computes a composite hash of all input file hashes for rerun idempotency."""
    hasher = hashlib.sha256()
    for fname in sorted(file_hashes.keys()):
        hasher.update(f"{fname}:{file_hashes[fname]}".encode("utf-8"))
    return hasher.hexdigest()


def run_pipeline(
    data_dir: Path,
    output_dir: Path,
    db_path: Path | str = ":memory:",
    config: Optional[ReportingAgentConfig] = None,
    force_rerun: bool = False,
) -> PipelineExecutionResult:
    """Executes the full financial reporting agent pipeline."""
    cfg = config or ReportingAgentConfig()
    fingerprint = compute_policy_fingerprint(cfg)
    store = ReviewStore(db_path)

    # 1. Ingest files & compute cryptographic hashes
    file_hashes = compute_all_file_hashes(data_dir)
    run_hash = compute_batch_run_hash(file_hashes)

    # Check for active review overlays under the current policy fingerprint
    active_overlays = store.get_active_review_overlays(fingerprint)
    overlays_hash = store.compute_overlays_hash(active_overlays)
    effective_cache_key = f"{run_hash}:{fingerprint}:{overlays_hash}"

    # 2. Check for Rerun Idempotency (Mechanism A)
    if not force_rerun:
        cached_data = store.get_cached_run(effective_cache_key)
        if cached_data:
            # Rehydrate execution result from cache
            rehydrated_adjustments = {
                eid: JournalEntryValidationResult(
                    entry_id=eid,
                    disposition=JournalEntryDisposition(val["disposition"]),
                    issue_codes=val["issue_codes"],
                    is_balanced=val.get("is_balanced", True),
                    accounts_exist_in_coa=val.get("accounts_exist_in_coa", True),
                    accounts_are_postable=val.get("accounts_are_postable", True),
                    line_currencies_consistent=val.get("line_currencies_consistent", True),
                    details=val.get("details", {}),
                )
                for eid, val in cached_data["adjustments"].items()
            }
            rehydrated_dq = [
                DataQualityFinding(
                    code=f["code"],
                    severity=DiagnosticSeverity(f["severity"]),
                    message=f["message"],
                    context=f.get("context", {}),
                )
                for f in cached_data["dq_findings"]
            ]
            summary_dict = cached_data["summary"]
            rehydrated_summary = ExecutionSummary(
                total_entries=summary_dict["total_entries"],
                accepted_count=summary_dict["accepted_count"],
                rejected_count=summary_dict["rejected_count"],
                quarantined_count=summary_dict["quarantined_count"],
                quarantine_rate=summary_dict["quarantine_rate"],
                total_dq_warnings=summary_dict["total_dq_warnings"],
            )

            # Re-read raw entries and apply active overlays for reporting
            batch = load_manual_adjustments(data_dir / "manual_adjustments.json")
            cached_working_entries: list[JournalEntry] = []
            for raw_entry in batch.entries:
                if raw_entry.id in active_overlays:
                    overlay = active_overlays[raw_entry.id]
                    if overlay.get("mapped_account") and overlay.get("original_account"):
                        mapped_lines = [
                            JournalLine(
                                account=overlay["mapped_account"] if l.account == overlay["original_account"] else l.account,
                                debit=l.debit,
                                credit=l.credit,
                                memo=l.memo,
                                currency=l.currency,
                            )
                            for l in raw_entry.lines
                        ]
                        w_entry = JournalEntry(
                            id=raw_entry.id,
                            description=raw_entry.description,
                            date=raw_entry.date,
                            source=raw_entry.source,
                            lines=mapped_lines,
                            entry_type=overlay.get("new_classification") or raw_entry.entry_type,
                            source_currency=raw_entry.source_currency,
                        )
                    elif overlay.get("new_classification"):
                        w_entry = JournalEntry(
                            id=raw_entry.id,
                            description=raw_entry.description,
                            date=raw_entry.date,
                            source=raw_entry.source,
                            lines=raw_entry.lines,
                            entry_type=overlay["new_classification"],
                            source_currency=raw_entry.source_currency,
                        )
                    else:
                        w_entry = raw_entry
                    cached_working_entries.append(w_entry)
                else:
                    cached_working_entries.append(raw_entry)

            cached_explanations = cached_data.get("explanations", {})
            cached_result = PipelineExecutionResult(
                adjustments=rehydrated_adjustments,
                dq_findings=rehydrated_dq,
                policy_fingerprint=fingerprint,
                input_file_hashes=file_hashes,
                summary=rehydrated_summary,
                entries=cached_working_entries,
                is_cached=True,
            )

            # Ensure output deliverables are present even on cache hit
            output_dir.mkdir(parents=True, exist_ok=True)
            write_run_trace_jsonl(output_dir / "run_trace.jsonl", cached_result)
            write_adjustments_decisions_csv(output_dir / "adjustments_decisions.csv", rehydrated_adjustments)
            write_review_queue_json(output_dir / "review_queue.json", cached_working_entries, rehydrated_adjustments, cached_explanations)
            render_issues_report_md(output_dir / "issues_report.md", cached_result, cached_explanations)

            return cached_result

    # Load source files
    coa_accounts = load_chart_of_accounts(data_dir / "chart_of_accounts.csv")
    coa_index = CoaIndex(coa_accounts)
    tb_rows = load_trial_balance(data_dir / "trial_balance.csv")
    fx_rows = load_fx_rates(data_dir / "fx_rates.csv")
    prior_tb_rows = load_prior_period_tb(data_dir / "prior_period_tb.csv")
    batch = load_manual_adjustments(data_dir / "manual_adjustments.json")

    # 3. Apply active review overlays to working entries (persisting human review across runs)
    working_entries: list[JournalEntry] = []
    for raw_entry in batch.entries:
        if raw_entry.id in active_overlays:
            overlay = active_overlays[raw_entry.id]
            # Account mapping overlay
            if overlay.get("mapped_account") and overlay.get("original_account"):
                mapped_lines = [
                    JournalLine(
                        account=overlay["mapped_account"] if l.account == overlay["original_account"] else l.account,
                        debit=l.debit,
                        credit=l.credit,
                        memo=l.memo,
                        currency=l.currency,
                    )
                    for l in raw_entry.lines
                ]
                w_entry = JournalEntry(
                    id=raw_entry.id,
                    description=raw_entry.description,
                    date=raw_entry.date,
                    source=raw_entry.source,
                    lines=mapped_lines,
                    entry_type=overlay.get("new_classification") or raw_entry.entry_type,
                    source_currency=raw_entry.source_currency,
                )
            elif overlay.get("new_classification"):
                w_entry = JournalEntry(
                    id=raw_entry.id,
                    description=raw_entry.description,
                    date=raw_entry.date,
                    source=raw_entry.source,
                    lines=raw_entry.lines,
                    entry_type=overlay["new_classification"],
                    source_currency=raw_entry.source_currency,
                )
            else:
                w_entry = raw_entry
            working_entries.append(w_entry)
        else:
            working_entries.append(raw_entry)

    # 4. Run Data Quality Diagnostics on Ledger Tables
    dq_findings = run_data_quality_checks(tb_rows, fx_rows, coa_index, prior_tb_rows, cfg)

    # 5. Check for cross-batch prior accepted entry IDs (Mechanism C)
    # Excludes entries from the same batch to ensure rerun idempotency
    prior_accepted_ids = store.get_prior_accepted_ids(current_run_hash=run_hash)

    # 6. Validate Adjustments Batch
    adjustments = validate_adjustments_batch(
        entries=working_entries,
        coa_index=coa_index,
        tb=tb_rows,
        fx_rates=fx_rows,
        config=cfg,
        prior_accepted_ids=prior_accepted_ids,
    )

    # 7. Reconcile review decisions with active overlays
    for entry_id, overlay in active_overlays.items():
        if entry_id in adjustments:
            cur_adj = adjustments[entry_id]
            # If human reviewer approved an advisory and hard gates still pass
            if overlay["disposition"] == JournalEntryDisposition.ACCEPTED:
                approved_codes = set(overlay.get("issue_codes", []))
                reviewable_advisories = {"JE_SELF_OFFSETTING", "JE_PRIOR_ACCEPTANCE"}
                cur_issues = set(cur_adj.issue_codes)
                has_hard_fail = any(code in cfg.hard_validation_gates for code in cur_issues)
                if not has_hard_fail and (cur_issues.issubset(approved_codes | reviewable_advisories) or not cur_issues):
                    orig_issues = list(approved_codes or cur_issues)
                    if overlay.get("mapped_account") and not orig_issues:
                        orig_issues = ["JE_ACCOUNT_UNMAPPED"]
                    adjustments[entry_id] = JournalEntryValidationResult(
                        entry_id=entry_id,
                        disposition=JournalEntryDisposition.ACCEPTED,
                        issue_codes=[],
                        is_balanced=cur_adj.is_balanced,
                        accounts_exist_in_coa=cur_adj.accounts_exist_in_coa,
                        accounts_are_postable=cur_adj.accounts_are_postable,
                        line_currencies_consistent=cur_adj.line_currencies_consistent,
                        details={
                            **cur_adj.details,
                            "reviewed_by": overlay["reviewer"],
                            "notes": overlay["notes"],
                            "original_issues": orig_issues,
                        },
                    )
            elif overlay["disposition"] == JournalEntryDisposition.QUARANTINED:
                adjustments[entry_id] = JournalEntryValidationResult(
                    entry_id=entry_id,
                    disposition=JournalEntryDisposition.QUARANTINED,
                    issue_codes=cur_adj.issue_codes,
                    is_balanced=cur_adj.is_balanced,
                    accounts_exist_in_coa=cur_adj.accounts_exist_in_coa,
                    accounts_are_postable=cur_adj.accounts_are_postable,
                    line_currencies_consistent=cur_adj.line_currencies_consistent,
                    details={
                        **cur_adj.details,
                        "reviewed_by": overlay["reviewer"],
                        "notes": overlay["notes"],
                        "mapped_account": overlay.get("mapped_account"),
                    },
                )
            elif overlay["disposition"] == JournalEntryDisposition.REJECTED:
                adjustments[entry_id] = JournalEntryValidationResult(
                    entry_id=entry_id,
                    disposition=JournalEntryDisposition.REJECTED,
                    issue_codes=["REJECTED_BY_REVIEWER"],
                    is_balanced=cur_adj.is_balanced,
                    accounts_exist_in_coa=cur_adj.accounts_exist_in_coa,
                    accounts_are_postable=cur_adj.accounts_are_postable,
                    line_currencies_consistent=cur_adj.line_currencies_consistent,
                    details={**cur_adj.details, "reviewed_by": overlay["reviewer"], "notes": overlay["notes"]},
                )

    # 8. Record batch run in ReviewStore for cross-batch tracking
    store.record_batch_run(run_hash, fingerprint, adjustments)

    # 9. Generate narrative explanations and fuzzy suggestions
    resolver = AccountResolver(coa_index)
    explainer = AccountingExplanationEngine()
    explanations: dict[str, str] = {}

    for entry in working_entries:
        adj = adjustments[entry.id]
        if adj.issue_codes:
            top_issue = adj.issue_codes[0]
            suggestions = None
            if "unmapped_account" in adj.details:
                suggestions = resolver.suggest_accounts(
                    adj.details["unmapped_account"],
                    description=entry.description,
                )
            expl = explainer.explain_issue(
                entry_id=entry.id,
                issue_code=top_issue,
                context=adj.details,
                suggestions=suggestions,
            )
            explanations[entry.id] = expl
        else:
            explanations[entry.id] = "All validation gates passed cleanly."

    # 10. Persist Quarantined Entries in ReviewStore
    store.persist_quarantined_entries(working_entries, adjustments)

    # 11. Compute Execution Summary
    accepted = sum(1 for a in adjustments.values() if a.disposition == JournalEntryDisposition.ACCEPTED)
    rejected = sum(1 for a in adjustments.values() if a.disposition == JournalEntryDisposition.REJECTED)
    quarantined = sum(1 for a in adjustments.values() if a.disposition == JournalEntryDisposition.QUARANTINED)
    total = len(adjustments)
    q_rate = (quarantined / total * 100) if total > 0 else 0.0

    summary = ExecutionSummary(
        total_entries=total,
        accepted_count=accepted,
        rejected_count=rejected,
        quarantined_count=quarantined,
        quarantine_rate=q_rate,
        total_dq_warnings=len(dq_findings),
    )

    result = PipelineExecutionResult(
        adjustments=adjustments,
        dq_findings=dq_findings,
        policy_fingerprint=fingerprint,
        input_file_hashes=file_hashes,
        summary=summary,
        entries=working_entries,
    )

    # 12. Save Cached Run under effective_cache_key
    cached_payload = {
        "adjustments": {
            k: {
                "disposition": v.disposition.value,
                "issue_codes": v.issue_codes,
                "is_balanced": v.is_balanced,
                "accounts_exist_in_coa": v.accounts_exist_in_coa,
                "accounts_are_postable": v.accounts_are_postable,
                "line_currencies_consistent": v.line_currencies_consistent,
                "details": v.details,
            }
            for k, v in adjustments.items()
        },
        "dq_findings": [
            {
                "code": f.code,
                "severity": f.severity.value,
                "message": f.message,
                "context": f.context,
            }
            for f in dq_findings
        ],
        "summary": {
            "total_entries": summary.total_entries,
            "accepted_count": summary.accepted_count,
            "rejected_count": summary.rejected_count,
            "quarantined_count": summary.quarantined_count,
            "quarantine_rate": summary.quarantine_rate,
            "total_dq_warnings": summary.total_dq_warnings,
        },
        "explanations": explanations,
    }
    store.save_cached_run(effective_cache_key, run_hash, fingerprint, cached_payload)

    # 13. Write Output Files
    output_dir.mkdir(parents=True, exist_ok=True)
    write_run_trace_jsonl(output_dir / "run_trace.jsonl", result)
    write_adjustments_decisions_csv(output_dir / "adjustments_decisions.csv", adjustments)
    write_review_queue_json(output_dir / "review_queue.json", working_entries, adjustments, explanations)
    render_issues_report_md(output_dir / "issues_report.md", result, explanations)

    return result
