"""Audit trail generation, report rendering, and run trace persistence."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.models import (
    DataQualityFinding,
    ExecutionSummary,
    JournalEntry,
    JournalEntryValidationResult,
    PipelineExecutionResult,
)


def write_run_trace_jsonl(
    output_path: Path,
    result: PipelineExecutionResult,
) -> None:
    """Writes a machine-readable JSONL audit trace of the pipeline execution."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        # Header event
        header_event = {
            "event": "PIPELINE_RUN_START",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "policy_fingerprint": result.policy_fingerprint,
            "input_file_hashes": result.input_file_hashes,
        }
        f.write(json.dumps(header_event) + "\n")

        # Entry validation events
        for entry_id, adj in sorted(result.adjustments.items()):
            entry_event = {
                "event": "ADJUSTMENT_VALIDATED",
                "entry_id": entry_id,
                "disposition": adj.disposition.value,
                "issue_codes": adj.issue_codes,
                "is_balanced": adj.is_balanced,
                "accounts_exist": adj.accounts_exist_in_coa,
                "accounts_postable": adj.accounts_are_postable,
                "details": adj.details,
            }
            f.write(json.dumps(entry_event) + "\n")

        # DQ events
        for finding in result.dq_findings:
            dq_event = {
                "event": "DATA_QUALITY_FINDING",
                "code": finding.code,
                "severity": finding.severity.value,
                "message": finding.message,
                "context": finding.context,
            }
            f.write(json.dumps(dq_event) + "\n")

        # Summary event
        summary_event = {
            "event": "PIPELINE_RUN_COMPLETED",
            "summary": {
                "total_entries": result.summary.total_entries,
                "accepted": result.summary.accepted_count,
                "rejected": result.summary.rejected_count,
                "quarantined": result.summary.quarantined_count,
                "quarantine_rate": result.summary.quarantine_rate,
                "total_dq_warnings": result.summary.total_dq_warnings,
            },
        }
        f.write(json.dumps(summary_event) + "\n")


def write_adjustments_decisions_csv(
    output_path: Path,
    adjustments: dict[str, JournalEntryValidationResult],
) -> None:
    """Writes adjustments decisions to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["entry_id", "disposition", "issue_codes", "is_balanced", "details"])
        for entry_id, adj in sorted(adjustments.items()):
            writer.writerow([
                entry_id,
                adj.disposition.value,
                ";".join(adj.issue_codes),
                adj.is_balanced,
                json.dumps(adj.details),
            ])


def write_review_queue_json(
    output_path: Path,
    entries: list[JournalEntry],
    adjustments: dict[str, JournalEntryValidationResult],
    explanations: dict[str, str],
) -> None:
    """Writes quarantined entries and reviewer context to review_queue.json."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    queue = []
    for entry in entries:
        adj = adjustments.get(entry.id)
        if adj and adj.disposition.value == "QUARANTINED":
            queue.append({
                "entry_id": entry.id,
                "description": entry.description,
                "source": entry.source,
                "date": entry.date,
                "issue_codes": adj.issue_codes,
                "explanation": explanations.get(entry.id, ""),
                "details": adj.details,
                "lines": [line.model_dump(mode="json") for line in entry.lines],
            })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)


def render_issues_report_md(
    output_path: Path,
    result: PipelineExecutionResult,
    explanations: dict[str, str],
) -> None:
    """Renders a comprehensive markdown issues report for auditors and finance management."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Financial Reporting Agent: Batch Integrity & Accounting Issues Report",
        "",
        f"**Generated At:** {datetime.now(timezone.utc).isoformat()}",
        f"**Policy Fingerprint:** `{result.policy_fingerprint}`",
        "",
        "## Executive Summary",
        "",
        f"- **Total Journal Entries Processed:** {result.summary.total_entries}",
        f"- **Accepted:** {result.summary.accepted_count}",
        f"- **Rejected (Hard Structural Failures):** {result.summary.rejected_count}",
        f"- **Quarantined (Human Review Required):** {result.summary.quarantined_count}",
        f"- **Quarantine Rate:** {result.summary.quarantine_rate:.1f}%",
        f"- **Data Quality Findings (Ledger Diagnostics):** {result.summary.total_dq_warnings}",
        "",
        "---",
        "",
        "## Journal Entry Dispositions",
        "",
        "| Entry ID | Disposition | Issue Codes | Summary Explanation |",
        "|---|---|---|---|",
    ]

    for entry_id, adj in sorted(result.adjustments.items()):
        codes_str = ", ".join(f"`{c}`" for c in adj.issue_codes) if adj.issue_codes else "None"
        expl = explanations.get(entry_id, "All validation gates passed cleanly.")
        lines.append(f"| **{entry_id}** | `{adj.disposition.value}` | {codes_str} | {expl} |")

    lines.extend([
        "",
        "---",
        "",
        "## Data Quality & Ledger Diagnostics",
        "",
        "| Code | Severity | Diagnostic Message |",
        "|---|---|---|",
    ])

    for finding in result.dq_findings:
        lines.append(f"| `{finding.code}` | **{finding.severity.value}** | {finding.message} |")

    lines.extend([
        "",
        "---",
        "",
        "## Input File Cryptographic Hashes",
        "",
        "| Filename | SHA-256 Hash |",
        "|---|---|",
    ])

    for fname, fhash in sorted(result.input_file_hashes.items()):
        lines.append(f"| `{fname}` | `{fhash}` |")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
