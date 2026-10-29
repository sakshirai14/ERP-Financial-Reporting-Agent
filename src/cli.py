"""Command Line Interface (CLI) for the ERP Financial Reporting Agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import ReportingAgentConfig
from src.hitl import HITLReviewService
from src.ingest import CoaIndex, load_chart_of_accounts
from src.models import JournalEntryDisposition
from src.store import ReviewStore
from src.workflow import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="ERP Financial Reporting Agent: Batch Integrity, Accounting Validation & HITL Review",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. run command
    run_parser = subparsers.add_parser("run", help="Execute the complete financial reporting pipeline.")
    run_parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Path to data directory.")
    run_parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Path to output directory.")
    run_parser.add_argument("--db-path", type=Path, default=Path("output/review_store.db"), help="Path to SQLite review store.")
    run_parser.add_argument("--force", action="store_true", help="Force rerun, bypassing cached results.")

    # 2. validate command
    val_parser = subparsers.add_parser("validate", help="Validate input batch and display summary table.")
    val_parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Path to data directory.")

    # 3. approve command
    appr_parser = subparsers.add_parser("approve", help="Approve a quarantined entry containing reviewable advisories.")
    appr_parser.add_argument("--entry-id", required=True, help="Entry ID to approve.")
    appr_parser.add_argument("--reviewer", required=True, help="Reviewer identity.")
    appr_parser.add_argument("--notes", required=True, help="Documented review notes.")
    appr_parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Path to data directory.")
    appr_parser.add_argument("--db-path", type=Path, default=Path("output/review_store.db"), help="Path to SQLite review store.")

    # 4. reject command
    rej_parser = subparsers.add_parser("reject", help="Explicitly reject a quarantined entry.")
    rej_parser.add_argument("--entry-id", required=True, help="Entry ID to reject.")
    rej_parser.add_argument("--reviewer", required=True, help="Reviewer identity.")
    rej_parser.add_argument("--notes", required=True, help="Documented review notes.")
    rej_parser.add_argument("--db-path", type=Path, default=Path("output/review_store.db"), help="Path to SQLite review store.")

    # 5. map-account command
    map_parser = subparsers.add_parser("map-account", help="Atomically map an unmapped account and revalidate.")
    map_parser.add_argument("--entry-id", required=True, help="Entry ID to map.")
    map_parser.add_argument("--from-account", required=True, help="Source unmapped account code.")
    map_parser.add_argument("--to-account", required=True, help="Target valid COA account code.")
    map_parser.add_argument("--reviewer", required=True, help="Reviewer identity.")
    map_parser.add_argument("--notes", required=True, help="Documented review notes.")
    map_parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Path to data directory.")
    map_parser.add_argument("--db-path", type=Path, default=Path("output/review_store.db"), help="Path to SQLite review store.")

    # 6. reclassify-entry command
    rec_parser = subparsers.add_parser("reclassify-entry", help="Reclassify an FX candidate to standard adjustment.")
    rec_parser.add_argument("--entry-id", required=True, help="Entry ID to reclassify.")
    rec_parser.add_argument("--classification", default="standard_adjustment", help="New classification.")
    rec_parser.add_argument("--reviewer", required=True, help="Reviewer identity.")
    rec_parser.add_argument("--notes", required=True, help="Documented review notes.")
    rec_parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Path to data directory.")
    rec_parser.add_argument("--db-path", type=Path, default=Path("output/review_store.db"), help="Path to SQLite review store.")

    args = parser.parse_args()

    if args.command == "run":
        print(f"Running Financial Reporting Pipeline on: {args.data_dir}...")
        res = run_pipeline(
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            db_path=args.db_path,
            force_rerun=args.force,
        )
        print("\n=== PIPELINE RUN COMPLETE ===")
        print(f"Total Entries:    {res.summary.total_entries}")
        print(f"Accepted:         {res.summary.accepted_count}")
        print(f"Rejected:         {res.summary.rejected_count}")
        print(f"Quarantined:      {res.summary.quarantined_count}")
        print(f"Quarantine Rate:  {res.summary.quarantine_rate:.1f}%")
        print(f"DQ Findings:      {res.summary.total_dq_warnings}")
        print(f"Policy Fingerprint: {res.policy_fingerprint}")
        print(f"Outputs written to: {args.output_dir.resolve()}/")

    elif args.command == "validate":
        res = run_pipeline(
            data_dir=args.data_dir,
            output_dir=Path("/tmp/financial_agent_temp"),
            db_path=":memory:",
            force_rerun=True,
        )
        print("\n=== ADJUSTMENT DISPOSITIONS ===")
        for entry_id, adj in sorted(res.adjustments.items()):
            codes = ", ".join(adj.issue_codes) if adj.issue_codes else "-"
            print(f"[{adj.disposition.value:11s}] {entry_id} -> {codes}")

    elif args.command == "approve":
        config = ReportingAgentConfig()
        coa = CoaIndex(load_chart_of_accounts(args.data_dir / "chart_of_accounts.csv"))
        store = ReviewStore(args.db_path)
        service = HITLReviewService(store, coa, config)
        op_res = service.approve_entry(args.entry_id, args.reviewer, args.notes)
        if op_res.success:
            print(f"SUCCESS: Entry {args.entry_id} approved. Disposition: {op_res.disposition.value}")
        else:
            print(f"ERROR: Approval refused for {args.entry_id}: {op_res.error_message}")
            sys.exit(1)

    elif args.command == "reject":
        config = ReportingAgentConfig()
        coa = CoaIndex(load_chart_of_accounts(Path("data/chart_of_accounts.csv")))
        store = ReviewStore(args.db_path)
        service = HITLReviewService(store, coa, config)
        op_res = service.reject_entry(args.entry_id, args.reviewer, args.notes)
        if op_res.success:
            print(f"SUCCESS: Entry {args.entry_id} rejected.")
        else:
            print(f"ERROR: {op_res.error_message}")
            sys.exit(1)

    elif args.command == "map-account":
        config = ReportingAgentConfig()
        coa = CoaIndex(load_chart_of_accounts(args.data_dir / "chart_of_accounts.csv"))
        store = ReviewStore(args.db_path)
        service = HITLReviewService(store, coa, config)
        op_res = service.map_and_revalidate(
            entry_id=args.entry_id,
            from_account=args.from_account,
            to_account=args.to_account,
            reviewer=args.reviewer,
            notes=args.notes,
        )
        if op_res.success:
            print(f"SUCCESS: Account {args.from_account} mapped to {args.to_account} on entry {args.entry_id}.")
            print(f"New disposition: {op_res.disposition.value}")
        else:
            print(f"ERROR: Mapping failed for {args.entry_id}: {op_res.error_message}")
            sys.exit(1)

    elif args.command == "reclassify-entry":
        config = ReportingAgentConfig()
        coa = CoaIndex(load_chart_of_accounts(args.data_dir / "chart_of_accounts.csv"))
        store = ReviewStore(args.db_path)
        service = HITLReviewService(store, coa, config)
        op_res = service.reclassify_and_revalidate(
            entry_id=args.entry_id,
            classification=args.classification,
            reviewer=args.reviewer,
            notes=args.notes,
        )
        if op_res.success:
            print(f"SUCCESS: Entry {args.entry_id} reclassified to {args.classification}.")
            print(f"New disposition: {op_res.disposition.value}")
        else:
            print(f"ERROR: Reclassification failed for {args.entry_id}: {op_res.error_message}")
            sys.exit(1)


if __name__ == "__main__":
    main()
