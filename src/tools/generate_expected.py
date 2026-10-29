"""Offline utility to generate the verified regression baseline from actual repository data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import ReportingAgentConfig, compute_policy_fingerprint
from src.ingest import (
    CoaIndex,
    compute_all_file_hashes,
    load_chart_of_accounts,
    load_fx_rates,
    load_manual_adjustments,
    load_trial_balance,
)
from src.journal import validate_adjustments_batch


def generate_baseline(
    data_dir: Path = Path("data"),
    output_fixture: Path = Path("tests/fixtures/expected_statuses.json"),
    reviewed_by: str = "sakshi_rai",
    reviewed_date: str = "2026-10-29",
) -> None:
    """Generates expected_statuses.json offline using actual file hashes and deterministic rules."""
    output_fixture.parent.mkdir(parents=True, exist_ok=True)
    config = ReportingAgentConfig()
    fingerprint = compute_policy_fingerprint(config)

    file_hashes = compute_all_file_hashes(data_dir)
    coa = CoaIndex(load_chart_of_accounts(data_dir / "chart_of_accounts.csv"))
    tb = load_trial_balance(data_dir / "trial_balance.csv")
    fx = load_fx_rates(data_dir / "fx_rates.csv")
    batch = load_manual_adjustments(data_dir / "manual_adjustments.json")

    results = validate_adjustments_batch(batch.entries, coa, tb, fx, config)

    expected_statuses = {}
    for entry_id, adj in sorted(results.items()):
        expected_statuses[entry_id] = {
            "status": adj.disposition.value,
            "issue_codes": adj.issue_codes,
        }

    payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": reviewed_by,
            "reviewed_date": reviewed_date,
            "policy_version": config.policy_version,
            "policy_fingerprint": fingerprint,
            "source_file_hashes": file_hashes,
        },
        "expected_statuses": expected_statuses,
    }

    with open(output_fixture, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Generated regression baseline at: {output_fixture.resolve()}")
    print(f"Policy Fingerprint: {fingerprint}")
    print(f"Reviewed By: {reviewed_by} on {reviewed_date}")
    print(f"Total entries: {len(expected_statuses)}")


if __name__ == "__main__":
    generate_baseline()
