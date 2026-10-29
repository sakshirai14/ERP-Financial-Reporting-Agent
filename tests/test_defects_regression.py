"""Comprehensive regression tests covering verified defects P0/P1:
1. FX approval safety (JE-003, FX mismatch and direction gates)
2. Rerun idempotency (cache hit, cache invalidation, review reconciliation)
3. Persisted human-review changes (JE-005 mapping overlay, classification persistence, raw file immutability)
4. Atomic mapping (transactional concurrency, stale state rejection, rollback)
5. Cross-batch duplicate handling (same batch rerun vs. genuine resubmission)
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
import pytest

from src.config import FxRevalPolicy, ReportingAgentConfig, ReportingPolicy, compute_policy_fingerprint
from src.hitl import HITLReviewService
from src.ingest import CoaIndex, load_chart_of_accounts, load_manual_adjustments
from src.models import (
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    JournalEntryValidationResult,
)
from src.store import ReviewStore
from src.workflow import run_pipeline


# ==============================================================================
# 1. FX Approval Safety
# ==============================================================================

def test_fx_approval_safety_blocks_unchanged_je_003(loaded_coa, loaded_tb, loaded_fx, default_config, data_path):
    """Bug 1: HITLReviewService.approve_entry() must not clear JE_FX_MISMATCH on unchanged JE-003.

    Approval must fail, and no ACCEPTED decision must be persisted.
    """
    batch = load_manual_adjustments(data_path / "manual_adjustments.json")
    je_003 = next(e for e in batch.entries if e.id == "JE-003")

    store = ReviewStore(":memory:")
    val_res = JournalEntryValidationResult(
        entry_id="JE-003",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_FX_MISMATCH"],
        is_balanced=True,
        accounts_exist_in_coa=True,
        accounts_are_postable=True,
        line_currencies_consistent=True,
        details={"discrepancy": "-8609.60"},
    )
    store.persist_quarantined_entries([je_003], {"JE-003": val_res})

    service = HITLReviewService(store, loaded_coa, default_config, tb=loaded_tb, fx_rates=loaded_fx)
    
    # Attempting to approve unchanged JE-003 must fail
    res = service.approve_entry("JE-003", reviewer="auditor_jane", notes="Attempting to force-approve FX mismatch")
    assert res.success is False
    assert "JE_FX_MISMATCH" in res.error_message or "Accounting policy validation failed" in res.error_message
    assert store.get_decision("JE-003") is None


def test_fx_approval_safety_blocks_direction_invalid(loaded_coa, default_config):
    """Bug 1: HITLReviewService.approve_entry() must reject entries with JE_FX_DIRECTION_INVALID."""
    store = ReviewStore(":memory:")
    entry = JournalEntry(
        id="JE-FX-DIR",
        description="EUR Bank revaluation reversed",
        date="2024-12-31",
        source="Manual",
        entry_type="fx_revaluation",
        source_currency="EUR",
        lines=[
            JournalLine(account="1110", debit=Decimal("0.00"), credit=Decimal("19809.60")),
            JournalLine(account="7110", debit=Decimal("19809.60"), credit=Decimal("0.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-FX-DIR",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_FX_DIRECTION_INVALID"],
        is_balanced=True,
        accounts_exist_in_coa=True,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([entry], {"JE-FX-DIR": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.approve_entry("JE-FX-DIR", reviewer="auditor_jane", notes="Approve wrong direction")
    assert res.success is False
    assert store.get_decision("JE-FX-DIR") is None


def test_fx_reclassification_explicit_audited_workflow(loaded_coa, default_config):
    """Bug 1: Permit reclassification only through the explicit, audited reclassification workflow."""
    store = ReviewStore(":memory:")
    entry = JournalEntry(
        id="JE-CANDIDATE",
        description="Reclassification test entry",
        date="2024-12-31",
        source="Manual",
        entry_type="fx_candidate",
        lines=[
            JournalLine(account="1110", debit=Decimal("10000.00"), credit=Decimal("0.00")),
            JournalLine(account="7310", debit=Decimal("0.00"), credit=Decimal("10000.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-CANDIDATE",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_FX_MISMATCH"],
        is_balanced=True,
        accounts_exist_in_coa=True,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([entry], {"JE-CANDIDATE": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)
    res = service.reclassify_and_revalidate(
        entry_id="JE-CANDIDATE",
        classification="standard_adjustment",
        reviewer="auditor_jane",
        notes="Reclassified from candidate to standard adjustment",
    )
    assert res.success is True
    assert res.disposition == JournalEntryDisposition.ACCEPTED

    # Verify decision and audit event persisted
    dec = store.get_decision("JE-CANDIDATE")
    assert dec is not None
    assert dec.new_classification == "standard_adjustment"
    assert dec.prior_classification == "fx_candidate"

    audits = store.get_audit_events()
    assert any(a.new_classification == "standard_adjustment" and a.entry_id == "JE-CANDIDATE" for a in audits)


# ==============================================================================
# 2. Rerun Idempotency
# ==============================================================================

def test_rerun_idempotency_cache_reuse_and_invalidation(tmp_path, data_path):
    """Bug 2: run_pipeline() correctly reuses cache on identical run, and invalidates on source/policy change."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Run 1: initial run
    res1 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res1.is_cached is False
    assert res1.summary.total_entries == 10

    # Run 2: repeated identical run -> MUST hit cache
    res2 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res2.is_cached is True
    assert res2.summary.accepted_count == res1.summary.accepted_count
    assert res2.summary.quarantined_count == res1.summary.quarantined_count

    # Run 3: force_rerun -> MUST NOT hit cache
    res3 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file, force_rerun=True)
    assert res3.is_cached is False

    # Invalidation test A: Policy modification -> fingerprint changes -> cache miss
    custom_cfg = ReportingAgentConfig(fx=FxRevalPolicy(tolerance=Decimal("50.00")))
    res_pol = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file, config=custom_cfg)
    assert res_pol.is_cached is False
    assert res_pol.policy_fingerprint != res1.policy_fingerprint

    # Invalidation test B: Source modification -> run_hash changes -> cache miss
    mod_data = tmp_path / "mod_data"
    shutil.copytree(data_path, mod_data)
    # Touch or append newline to manual_adjustments.json
    with open(mod_data / "manual_adjustments.json", "a") as f:
        f.write("\n")
    res_src = run_pipeline(data_dir=mod_data, output_dir=out_dir, db_path=db_file)
    assert res_src.is_cached is False


def test_cached_results_do_not_bypass_review_decisions(tmp_path, data_path):
    """Bug 2: A newly approved review decision invalidates the previous cached run and reconciles correctly."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Run 1
    res1 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res1.adjustments["JE-008"].disposition == JournalEntryDisposition.QUARANTINED
    assert res1.is_cached is False

    # Reviewer approves JE-008 (self-offsetting advisory review)
    config = ReportingAgentConfig()
    coa = CoaIndex(load_chart_of_accounts(data_path / "chart_of_accounts.csv"))
    store = ReviewStore(db_file)
    service = HITLReviewService(store, coa, config)
    appr = service.approve_entry("JE-008", reviewer="auditor_jane", notes="Approved intentional intercompany offset")
    assert appr.success is True
    store.close()

    # Run 2: Cache key MUST incorporate overlays_hash, so this run revalidates and reflects JE-008 ACCEPTED
    res2 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res2.adjustments["JE-008"].disposition == JournalEntryDisposition.ACCEPTED


# ==============================================================================
# 3. Persisted Human-Review Changes
# ==============================================================================

def test_persisted_account_mapping_je_005_and_raw_file_immutability(tmp_path, data_path):
    """Bug 3: JE-005 mapping persists across subsequent runs as an overlay without mutating raw source file."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Compute hash of raw manual_adjustments.json before run
    raw_file = data_path / "manual_adjustments.json"
    with open(raw_file, "rb") as f:
        orig_raw_hash = hashlib.sha256(f.read()).hexdigest()

    # Run 1
    res1 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res1.adjustments["JE-005"].disposition == JournalEntryDisposition.QUARANTINED

    # Human maps account 6315 -> 6300 (Marketing & Advertising, clean leaf account)
    config = ReportingAgentConfig()
    coa = CoaIndex(load_chart_of_accounts(data_path / "chart_of_accounts.csv"))
    store = ReviewStore(db_file)
    service = HITLReviewService(store, coa, config)
    res_map = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6300",
        reviewer="auditor_jane",
        notes="Mapped travel to marketing expense 6300",
    )
    assert res_map.success is True
    assert res_map.disposition == JournalEntryDisposition.ACCEPTED
    store.close()

    # Verify original source file remains 100% UNCHANGED
    with open(raw_file, "rb") as f:
        post_map_hash = hashlib.sha256(f.read()).hexdigest()
    assert orig_raw_hash == post_map_hash, "Violation: Raw source data file was modified!"

    # Run 2: Subsequent pipeline run
    res2 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res2.adjustments["JE-005"].disposition == JournalEntryDisposition.ACCEPTED
    assert res2.adjustments["JE-005"].issue_codes == []

    # Verify source file STILL untouched
    with open(raw_file, "rb") as f:
        run2_raw_hash = hashlib.sha256(f.read()).hexdigest()
    assert orig_raw_hash == run2_raw_hash, "Violation: Raw source data file was modified during run 2!"


def test_persisted_classification_across_runs(tmp_path, data_path):
    """Bug 3: Reclassification overlay persists across subsequent pipeline runs."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Setup isolated test data directory
    test_data = tmp_path / "test_data"
    shutil.copytree(data_path, test_data)

    # Add a candidate entry to test_data
    with open(test_data / "manual_adjustments.json", "r") as f:
        batch_dict = json.load(f)
    
    candidate_entry = {
        "id": "JE-099",
        "date": "2024-12-31",
        "description": "Candidate reval",
        "source": "Manual",
        "entry_type": "fx_candidate",
        "lines": [
            {"account": "1110", "debit": "10000.00", "credit": "0.00", "currency": "USD"},
            {"account": "7310", "debit": "0.00", "credit": "10000.00", "currency": "USD"},
        ],
    }
    batch_dict["entries"].append(candidate_entry)
    with open(test_data / "manual_adjustments.json", "w") as f:
        json.dump(batch_dict, f, indent=2)

    # Run 1: JE-099 quarantined for FX mismatch
    res1 = run_pipeline(data_dir=test_data, output_dir=out_dir, db_path=db_file)
    assert res1.adjustments["JE-099"].disposition == JournalEntryDisposition.QUARANTINED

    # Reviewer reclassifies JE-099 to standard_adjustment
    config = ReportingAgentConfig()
    coa = CoaIndex(load_chart_of_accounts(test_data / "chart_of_accounts.csv"))
    store = ReviewStore(db_file)
    service = HITLReviewService(store, coa, config)
    res_rec = service.reclassify_and_revalidate(
        entry_id="JE-099",
        classification="standard_adjustment",
        reviewer="auditor_jane",
        notes="Reclassified after verifying non-revaluation business purpose",
    )
    assert res_rec.success is True
    assert res_rec.disposition == JournalEntryDisposition.ACCEPTED
    store.close()

    # Run 2: The reclassification overlay persists across subsequent runs
    res2 = run_pipeline(data_dir=test_data, output_dir=out_dir, db_path=db_file)
    assert res2.adjustments["JE-099"].disposition == JournalEntryDisposition.ACCEPTED


def test_policy_change_invalidates_stale_review_decisions(tmp_path, data_path):
    """Bug 3: Changing policy fingerprint invalidates stale review decisions."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Run 1
    res1 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)

    # Approve JE-008 under default policy
    config = ReportingAgentConfig()
    coa = CoaIndex(load_chart_of_accounts(data_path / "chart_of_accounts.csv"))
    store = ReviewStore(db_file)
    service = HITLReviewService(store, coa, config)
    service.approve_entry("JE-008", reviewer="auditor_jane", notes="Approved under policy A")
    store.close()

    # Verify active overlays exist under policy A
    store = ReviewStore(db_file)
    fp_a = compute_policy_fingerprint(config)
    assert len(store.get_active_review_overlays(fp_a)) == 1

    # Now run under policy B (different fingerprint)
    config_b = ReportingAgentConfig(reporting=ReportingPolicy(require_currency_safe_tb=False))
    fp_b = compute_policy_fingerprint(config_b)
    assert fp_a != fp_b

    # Under policy B, the decision from policy A is NOT active
    assert len(store.get_active_review_overlays(fp_b)) == 0
    store.close()

    # Running under policy B will revalidate from clean state
    res_b = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file, config=config_b)
    assert res_b.adjustments["JE-008"].disposition == JournalEntryDisposition.QUARANTINED


# ==============================================================================
# 4. Atomic Mapping & Concurrency Safety
# ==============================================================================

def test_atomic_mapping_stale_state_rejection(loaded_coa, default_config):
    """Bug 4: map_and_revalidate() rejects stale or concurrent attempts where source account changed."""
    store = ReviewStore(":memory:")
    entry = JournalEntry(
        id="JE-005",
        description="Software sub",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="6315", debit=Decimal("45000.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("45000.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-005",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_ACCOUNT_UNMAPPED"],
        is_balanced=True,
        accounts_exist_in_coa=False,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([entry], {"JE-005": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)

    # First mapping succeeds: 6315 -> 6310
    res1 = service.map_and_revalidate("JE-005", "6315", "6310", reviewer="auditor_1", notes="First mapping")
    assert res1.success is True

    # Second concurrent/stale mapping attempt targeting original account 6315 must be rejected
    res2 = service.map_and_revalidate("JE-005", "6315", "6320", reviewer="auditor_2", notes="Stale concurrent attempt")
    assert res2.success is False
    assert "no longer unmapped" in res2.error_message or "Conflict" in res2.error_message or "Source account" in res2.error_message or "does not exist" in res2.error_message


def test_atomic_mapping_rollback_on_failure(loaded_coa, default_config):
    """Bug 4: Persist mapping, decision, and audit event atomically; rollback on failure."""
    store = ReviewStore(":memory:")
    entry = JournalEntry(
        id="JE-005",
        description="Software sub",
        date="2024-12-31",
        source="Test",
        lines=[
            JournalLine(account="6315", debit=Decimal("45000.00"), credit=Decimal("0.00")),
            JournalLine(account="2110", debit=Decimal("0.00"), credit=Decimal("45000.00")),
        ],
    )
    val_res = JournalEntryValidationResult(
        entry_id="JE-005",
        disposition=JournalEntryDisposition.QUARANTINED,
        issue_codes=["JE_ACCOUNT_UNMAPPED"],
        is_balanced=True,
        accounts_exist_in_coa=False,
        accounts_are_postable=True,
        line_currencies_consistent=True,
    )
    store.persist_quarantined_entries([entry], {"JE-005": val_res})

    service = HITLReviewService(store, loaded_coa, default_config)

    # Attempt mapping to non-existent account 9999 -> fails validation
    res = service.map_and_revalidate("JE-005", "6315", "9999", reviewer="auditor_1", notes="Bad target")
    assert res.success is False

    # Verify complete rollback: no decision record or audit event persisted
    assert store.get_decision("JE-005") is None
    assert len(store.get_audit_events()) == 0


# ==============================================================================
# 5. Cross-Batch Duplicate Handling
# ==============================================================================

def test_cross_batch_duplicate_same_batch_repeat_not_quarantined(tmp_path, data_path):
    """Bug 5: An unchanged repeat run of the SAME batch must not convert previously accepted entries into quarantined."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Run 1
    res1 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res1.adjustments["JE-001"].disposition == JournalEntryDisposition.ACCEPTED

    # Run 2: Same batch rerun (force_rerun=True to exercise validation logic directly)
    res2 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file, force_rerun=True)
    assert res2.adjustments["JE-001"].disposition == JournalEntryDisposition.ACCEPTED
    assert "JE_PRIOR_ACCEPTANCE" not in res2.adjustments["JE-001"].issue_codes


def test_cross_batch_duplicate_genuine_resubmission_quarantined(tmp_path, data_path):
    """Bug 5: A genuinely resubmitted entry in a DIFFERENT batch (different run hash) flags JE_PRIOR_ACCEPTANCE."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Run 1: Batch 1 processed
    res1 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res1.adjustments["JE-001"].disposition == JournalEntryDisposition.ACCEPTED

    # Create Batch 2: Different batch run (e.g. modified batch containing resubmitted JE-001 and an unrelated new entry)
    batch2_dir = tmp_path / "batch2_data"
    shutil.copytree(data_path, batch2_dir)

    with open(batch2_dir / "manual_adjustments.json", "r") as f:
        batch2_dict = json.load(f)

    # Add a new entry to change the batch run hash
    batch2_dict["entries"].append({
        "id": "JE-101",
        "date": "2024-12-31",
        "description": "Unrelated new adjustment in batch 2",
        "source": "Manual",
        "lines": [
            {"account": "6110", "debit": "100.00", "credit": "0.00", "currency": "USD"},
            {"account": "2110", "debit": "0.00", "credit": "100.00", "currency": "USD"},
        ],
    })
    with open(batch2_dir / "manual_adjustments.json", "w") as f:
        json.dump(batch2_dict, f, indent=2)

    # Run Batch 2: JE-001 was accepted in Batch 1, so in Batch 2 it MUST be flagged with JE_PRIOR_ACCEPTANCE
    res2 = run_pipeline(data_dir=batch2_dir, output_dir=out_dir, db_path=db_file)
    assert res2.adjustments["JE-001"].disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_PRIOR_ACCEPTANCE" in res2.adjustments["JE-001"].issue_codes


# ==============================================================================
# FIX 2: Output Bugs & Reporting Integrity
# ==============================================================================

def test_fx_explanation_has_no_unknown_and_fails_on_missing_context():
    """FIX 2: Explanations must not contain '$unknown USD' or invent hardcoded numbers."""
    from src.llm import AccountingExplanationEngine

    engine = AccountingExplanationEngine()

    # Valid FX mismatch context
    valid_ctx = {
        "booked_amount": "11200.00",
        "expected_usd_delta": "19809.60",
        "discrepancy": "-8609.60",
    }
    expl = engine.explain_issue("JE-003", "JE_FX_MISMATCH", valid_ctx)
    assert "unknown" not in expl.lower()
    assert "$11200.00 USD" in expl
    assert "$19809.60 USD" in expl

    # Missing context must fail loudly with ValueError rather than inventing fallbacks
    with pytest.raises(ValueError):
        engine.explain_issue("JE-003", "JE_FX_MISMATCH", {})

    with pytest.raises(ValueError):
        engine.explain_issue("JE-003", "JE_CURRENCY_BASIS_INVALID", {})


def test_dq_finding_non_header_parent_code_and_type(loaded_coa, loaded_tb, loaded_fx, default_config):
    """FIX 2: DQ check must be named DQ_COA_NON_HEADER_PARENT and reflect true account type."""
    from src.dq_checks import run_data_quality_checks

    findings = run_data_quality_checks(loaded_tb, loaded_fx, loaded_coa, [], default_config)
    finding_3300 = next((f for f in findings if f.code == "DQ_COA_NON_HEADER_PARENT" and f.context.get("account_code") == "3300"), None)
    assert finding_3300 is not None
    assert "Equity" in finding_3300.message
    assert not any(f.code == "DQ_COA_EXPENSE_PARENT" for f in findings)


def test_issues_report_displays_human_review_notes_and_original_issues(data_path, tmp_path, default_config, loaded_coa, loaded_tb, loaded_fx):
    """FIX 2: In issues_report.md, reviewed accepted entries show reviewer note and original issue codes."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    # Step 1: Run baseline pipeline so JE-005 is quarantined
    res = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res.adjustments["JE-005"].disposition == JournalEntryDisposition.QUARANTINED

    # Step 2: Reviewer maps JE-005 to 6300 (which cleanly passes revalidation)
    store = ReviewStore(db_file)
    service = HITLReviewService(store, loaded_coa, default_config, tb=loaded_tb, fx_rates=loaded_fx)
    map_res = service.map_and_revalidate(
        entry_id="JE-005",
        from_account="6315",
        to_account="6300",
        reviewer="auditor_jane",
        notes="Reclassified unmapped travel sub-account to marketing expense",
    )
    assert map_res.success is True
    assert map_res.disposition == JournalEntryDisposition.ACCEPTED

    # Step 3: Re-run pipeline to generate fresh issues_report.md with overlay applied
    res2 = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res2.adjustments["JE-005"].disposition == JournalEntryDisposition.ACCEPTED

    report_path = out_dir / "issues_report.md"
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")

    # Locate JE-005 line in issues_report.md
    je005_lines = [line for line in content.splitlines() if "**JE-005**" in line]
    assert len(je005_lines) == 1
    je005_line = je005_lines[0]

    assert "Accepted after human review by auditor_jane: Reclassified unmapped travel sub-account to marketing expense" in je005_line
    assert "`JE_ACCOUNT_UNMAPPED`" in je005_line
    assert "All validation gates passed cleanly" not in je005_line


# ==============================================================================
# FIX 3: Balance Sufficiency, Period Bounds & Data Quality Diagnostics
# ==============================================================================

def test_balance_sufficiency_rule_and_contra_accounts(data_path, loaded_coa, loaded_tb, loaded_fx, default_config):
    """FIX 3: Balance sufficiency uses normal_balance from COA.
    
    JE-004, JE-006, JE-007, and JE-010 stay ACCEPTED.
    Crediting contra accounts (1121, 1211) is normal-direction move.
    Exceeding move against normal balance flags JE_EXCEEDS_BALANCE.
    """
    from src.journal import validate_adjustments_batch, validate_single_entry_full

    batch = load_manual_adjustments(data_path / "manual_adjustments.json")
    results = validate_adjustments_batch(
        entries=batch.entries,
        coa_index=loaded_coa,
        tb=loaded_tb,
        fx_rates=loaded_fx,
        config=default_config,
        batch_period=batch.period,
    )

    # JE-004 (Cr 1121 45k, contra asset Credit) stays ACCEPTED
    assert results["JE-004"].disposition == JournalEntryDisposition.ACCEPTED
    assert "JE_EXCEEDS_BALANCE" not in results["JE-004"].issue_codes

    # JE-006 (Cr 1211 215k, contra asset Credit) stays ACCEPTED
    assert results["JE-006"].disposition == JournalEntryDisposition.ACCEPTED
    assert "JE_EXCEEDS_BALANCE" not in results["JE-006"].issue_codes

    # JE-007 (Cr 1250 38k against 320k balance) stays ACCEPTED
    assert results["JE-007"].disposition == JournalEntryDisposition.ACCEPTED
    assert "JE_EXCEEDS_BALANCE" not in results["JE-007"].issue_codes

    # JE-010 (Dr 2210 200k against 6,500,000 balance) stays ACCEPTED
    assert results["JE-010"].disposition == JournalEntryDisposition.ACCEPTED
    assert "JE_EXCEEDS_BALANCE" not in results["JE-010"].issue_codes

    # Test an entry exceeding liability balance (Dr 2210 by 7,000,000 against 6,500,000 balance)
    exceeding_entry = JournalEntry(
        id="JE-EXCEED-LIAB",
        description="Reclass exceeding debt balance",
        date="2024-12-31",
        source="Manual",
        lines=[
            JournalLine(account="2210", debit=Decimal("7000000.00"), credit=Decimal("0.00")),
            JournalLine(account="2140", debit=Decimal("0.00"), credit=Decimal("7000000.00")),
        ],
    )
    res_exceed = validate_single_entry_full(
        entry=exceeding_entry,
        coa_index=loaded_coa,
        tb=loaded_tb,
        fx_rates=loaded_fx,
        config=default_config,
        batch_period="2024-Q4",
    )
    assert res_exceed.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_EXCEEDS_BALANCE" in res_exceed.issue_codes
    assert res_exceed.details["balance_sufficiency_error"]["account"] == "2210"

    # Test an entry exceeding asset balance (Cr 1140 by 250,000 against 180,000 balance)
    exceeding_asset = JournalEntry(
        id="JE-EXCEED-ASSET",
        description="Write-off exceeding prepaid balance",
        date="2024-12-31",
        source="Manual",
        lines=[
            JournalLine(account="6210", debit=Decimal("250000.00"), credit=Decimal("0.00")),
            JournalLine(account="1140", debit=Decimal("0.00"), credit=Decimal("250000.00")),
        ],
    )
    res_asset = validate_single_entry_full(
        entry=exceeding_asset,
        coa_index=loaded_coa,
        tb=loaded_tb,
        fx_rates=loaded_fx,
        config=default_config,
        batch_period="2024-Q4",
    )
    assert res_asset.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_EXCEEDS_BALANCE" in res_asset.issue_codes
    assert res_asset.details["balance_sufficiency_error"]["account"] == "1140"


def test_period_bounds_parsing_and_out_of_period_detection(loaded_coa, loaded_tb, loaded_fx, default_config):
    """FIX 3: Derive 2024-10-01 to 2024-12-31 from '2024-Q4' and flag out-of-period entries."""
    from datetime import date
    from src.journal import parse_period_bounds, validate_single_entry_full

    start, end = parse_period_bounds("2024-Q4")
    assert start == date(2024, 10, 1)
    assert end == date(2024, 12, 31)

    # Out of period entry before Q4 (2024-09-30)
    early_entry = JournalEntry(
        id="JE-EARLY",
        description="September accrual mistakenly in Q4 batch",
        date="2024-09-30",
        source="Manual",
        lines=[
            JournalLine(account="6100", debit=Decimal("1000.00"), credit=Decimal("0.00")),
            JournalLine(account="2120", debit=Decimal("0.00"), credit=Decimal("1000.00")),
        ],
    )
    res_early = validate_single_entry_full(
        entry=early_entry,
        coa_index=loaded_coa,
        tb=loaded_tb,
        fx_rates=loaded_fx,
        config=default_config,
        batch_period="2024-Q4",
    )
    assert res_early.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_DATE_OUT_OF_PERIOD" in res_early.issue_codes

    # Out of period entry after Q4 (2025-01-01)
    late_entry = JournalEntry(
        id="JE-LATE",
        description="Post-closing entry in Q4 batch",
        date="2025-01-01",
        source="Manual",
        lines=[
            JournalLine(account="6100", debit=Decimal("1000.00"), credit=Decimal("0.00")),
            JournalLine(account="2120", debit=Decimal("0.00"), credit=Decimal("1000.00")),
        ],
    )
    res_late = validate_single_entry_full(
        entry=late_entry,
        coa_index=loaded_coa,
        tb=loaded_tb,
        fx_rates=loaded_fx,
        config=default_config,
        batch_period="2024-Q4",
    )
    assert res_late.disposition == JournalEntryDisposition.QUARANTINED
    assert "JE_DATE_OUT_OF_PERIOD" in res_late.issue_codes


def test_fix3_dq_checks_all_findings(loaded_tb, loaded_fx, loaded_coa, loaded_pp_tb, default_config):
    """FIX 3: Verify all required DQ diagnostic findings on supplied real data."""
    from src.dq_checks import run_data_quality_checks

    findings = run_data_quality_checks(
        tb=loaded_tb,
        fx_rates=loaded_fx,
        coa_index=loaded_coa,
        prior_tb=loaded_pp_tb,
        config=default_config,
    )
    findings_by_code = {f.code: f for f in findings}

    # 1. Currency ambiguity
    assert "DQ_TB_CURRENCY_AMBIGUITY" in findings_by_code
    ambig = findings_by_code["DQ_TB_CURRENCY_AMBIGUITY"]
    assert "-4,800.00" in ambig.message
    assert "-338,687.00" in ambig.message
    assert "GBP at opening 1.251" in ambig.message
    assert "unresolved and needs controller input" in ambig.message

    # 2. Duplicate 6310 reporting
    assert "DQ_TB_DUPLICATE_ACCOUNT" in findings_by_code
    dup = findings_by_code["DQ_TB_DUPLICATE_ACCOUNT"]
    assert "6310" in dup.message
    assert "duplicate or split - needs human confirmation" in dup.message
    assert "245,000.00" in dup.message
    assert "38,500.00" in dup.message

    # 3. Pre-closing retained earnings
    assert "DQ_TB_PRE_CLOSING_RETAINED_EARNINGS" in findings_by_code
    re_finding = findings_by_code["DQ_TB_PRE_CLOSING_RETAINED_EARNINGS"]
    assert "3200" in re_finding.message
    assert "SOCIE opening equity" in re_finding.message

    # 4. PPE cost jump
    assert "DQ_PPE_COST_JUMP" in findings_by_code
    ppe = findings_by_code["DQ_PPE_COST_JUMP"]
    assert "1210" in ppe.message
    assert "11,800,000.00" in ppe.message
    assert "17,007,700.00" in ppe.message

    # 5. CF category mismatch
    assert "DQ_COA_CF_CATEGORY_MISMATCH" in findings_by_code
    cf = findings_by_code["DQ_COA_CF_CATEGORY_MISMATCH"]
    assert "3200" in cf.message or "3300" in cf.message


# ==============================================================================
# FIX 4: Safe LLM Layer, Ranking, Validation, and Run Trace
# ==============================================================================

def test_llm_hallucinated_code_is_rejected_and_escalated(loaded_coa):
    """FIX 4: An LLM response returning an account code not in candidate set is rejected and escalated."""
    from src.llm import AccountingExplanationEngine, MockLLMProvider

    # Provider returning hallucinated account code '9999'
    mock_hallucinated = MockLLMProvider(
        mock_response=json.dumps({
            "selected_code": "9999",
            "confidence": 0.95,
            "rationale": "I think suspense account 9999 is great.",
        })
    )
    engine = AccountingExplanationEngine(provider=mock_hallucinated, coa_index=loaded_coa)

    candidates = [
        {"account_code": "6310", "account_name": "Travel and Entertainment", "score": 90.0},
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 75.0},
    ]

    res = engine.rank_account_candidates(
        entry_id="JE-005",
        unmapped_code="6315",
        description="Travel booking adjustment",
        candidates=candidates,
        other_accounts={"6310"},
    )

    assert res.is_valid is False
    assert res.escalated is True
    assert "hallucination rejected" in res.escalation_reason.lower()


def test_llm_low_confidence_result_is_escalated(loaded_coa):
    """FIX 4: An LLM suggestion with low confidence (< 0.60) is escalated for human review."""
    from src.llm import AccountingExplanationEngine, MockLLMProvider

    mock_low_conf = MockLLMProvider(
        mock_response=json.dumps({
            "selected_code": "6300",
            "confidence": 0.45,
            "rationale": "Uncertain mapping suggestion.",
        })
    )
    engine = AccountingExplanationEngine(provider=mock_low_conf, coa_index=loaded_coa)

    candidates = [
        {"account_code": "6310", "account_name": "Travel and Entertainment", "score": 90.0},
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 75.0},
    ]

    res = engine.rank_account_candidates(
        entry_id="JE-005",
        unmapped_code="6315",
        description="Ambiguous travel or promo expense",
        candidates=candidates,
        other_accounts={"6310"},
    )

    assert res.is_valid is True
    assert res.escalated is True
    assert "low confidence" in res.escalation_reason.lower()


def test_llm_self_offsetting_candidate_is_flagged(loaded_coa):
    """FIX 4: If selected candidate matches other entry lines, self-offsetting warning is flagged."""
    from src.llm import AccountingExplanationEngine, MockLLMProvider

    mock_provider = MockLLMProvider(
        mock_response=json.dumps({
            "selected_code": "6310",
            "confidence": 0.88,
            "rationale": "Matches travel classification.",
        })
    )
    engine = AccountingExplanationEngine(provider=mock_provider, coa_index=loaded_coa)

    candidates = [
        {"account_code": "6310", "account_name": "Travel and Entertainment", "score": 90.0},
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 75.0},
    ]

    res = engine.rank_account_candidates(
        entry_id="JE-005",
        unmapped_code="6315",
        description="Travel adjustment",
        candidates=candidates,
        other_accounts={"6310"},  # Line 2 has 6310, so selecting 6310 causes self-offsetting
    )

    assert res.is_valid is True
    assert res.would_cause_self_offsetting is True


def test_llm_mock_mode_is_deterministic(loaded_coa):
    """FIX 4: Default mock mode produces deterministic results without requiring external API keys."""
    from src.llm import AccountingExplanationEngine

    engine = AccountingExplanationEngine(coa_index=loaded_coa)
    candidates = [
        {"account_code": "6310", "account_name": "Travel and Entertainment", "score": 90.0},
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 75.0},
    ]

    res1 = engine.rank_account_candidates("JE-005", "6315", "Travel adjustment", candidates)
    res2 = engine.rank_account_candidates("JE-005", "6315", "Travel adjustment", candidates)

    assert res1.selected_code == res2.selected_code
    assert res1.confidence == res2.confidence
    assert res1.rationale == res2.rationale


def test_llm_trace_events_logged_in_run_trace_jsonl(data_path, tmp_path):
    """FIX 4: Verify prompt, response, model name, and validation results are logged in run_trace.jsonl."""
    db_file = tmp_path / "review.db"
    out_dir = tmp_path / "output"

    res = run_pipeline(data_dir=data_path, output_dir=out_dir, db_path=db_file)
    assert res.summary.quarantined_count > 0

    trace_file = out_dir / "run_trace.jsonl"
    assert trace_file.exists()
    lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    llm_events = [l for l in lines if l.get("event") == "LLM_ACCOUNT_MAPPING_RANKING"]
    assert len(llm_events) >= 1
    ev = llm_events[0]
    assert ev["entry_id"] == "JE-005"
    assert "prompt" in ev
    assert "response" in ev
    assert "validation_result" in ev
    assert ev["model"] == "mock"


def test_je005_mock_suggestion_is_not_6310(loaded_coa):
    """FIX 4 Amendment: Mock provider chooses highest non-self-offsetting candidate (6300, NOT 6310)."""
    from src.llm import AccountingExplanationEngine

    engine = AccountingExplanationEngine(coa_index=loaded_coa)
    candidates = [
        {"account_code": "6310", "account_name": "Travel and Entertainment", "score": 88.89},
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 75.0},
        {"account_code": "6230", "account_name": "Office Supplies", "score": 50.0},
    ]

    res = engine.rank_account_candidates(
        entry_id="JE-005",
        unmapped_code="6315",
        description="Software subscription reclassification",
        candidates=candidates,
        other_accounts={"6310"},  # Line 2 has 6310
    )

    assert res.is_valid is True
    assert res.selected_code == "6300"
    assert res.selected_code != "6310"
    assert res.would_cause_self_offsetting is False
    assert "Marketing and Advertising" in res.rationale


def test_mock_confidence_varies_with_candidates(loaded_coa):
    """FIX 4 Amendment: Confidence varies based on candidate score and margin, not a hardcoded constant."""
    from src.llm import AccountingExplanationEngine

    engine = AccountingExplanationEngine(coa_index=loaded_coa)

    # High margin set: 92 vs 50 -> high confidence
    candidates_wide = [
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 92.0},
        {"account_code": "6230", "account_name": "Office Supplies", "score": 50.0},
    ]
    res_wide = engine.rank_account_candidates("JE-005", "6315", "Desc", candidates_wide)

    # Tight margin set: 70 vs 68 -> lower confidence
    candidates_tight = [
        {"account_code": "6300", "account_name": "Marketing and Advertising", "score": 70.0},
        {"account_code": "6230", "account_name": "Office Supplies", "score": 68.0},
    ]
    res_tight = engine.rank_account_candidates("JE-005", "6315", "Desc", candidates_tight)

    assert res_wide.confidence > res_tight.confidence
    assert res_wide.confidence != 0.88
    assert res_tight.confidence != 0.88


def test_all_self_offsetting_candidates_escalates(loaded_coa):
    """FIX 4 Amendment: If every candidate causes self-offsetting, return low confidence (<0.60) so it escalates."""
    from src.llm import AccountingExplanationEngine

    engine = AccountingExplanationEngine(coa_index=loaded_coa)
    candidates = [
        {"account_code": "6310", "account_name": "Travel and Entertainment", "score": 88.89},
        {"account_code": "2170", "account_name": "Intercompany Clearing", "score": 70.0},
    ]

    res = engine.rank_account_candidates(
        entry_id="JE-005",
        unmapped_code="6315",
        description="Software subscription reclassification",
        candidates=candidates,
        other_accounts={"6310", "2170"},  # Both candidates cause self-offsetting!
    )

    assert res.is_valid is True
    assert res.escalated is True
    assert res.confidence < 0.60
    assert "escalated" in res.rationale.lower()




