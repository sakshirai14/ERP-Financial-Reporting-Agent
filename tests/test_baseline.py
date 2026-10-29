"""Tests for the verified regression baseline, hash integrity, and review metadata."""

from __future__ import annotations

import json
from pathlib import Path

from src.config import ReportingAgentConfig, compute_policy_fingerprint
from src.ingest import compute_all_file_hashes
from src.journal import validate_adjustments_batch

FIXTURE_PATH = Path("tests/fixtures/expected_statuses.json")


def test_baseline_metadata_is_complete_and_reviewed():
    """Confirms that the committed baseline contains authentic human review metadata."""
    assert FIXTURE_PATH.exists(), f"Baseline fixture missing at {FIXTURE_PATH}"
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data["metadata"]
    assert meta["reviewed_by"] and meta["reviewed_by"] != "TODO"
    assert meta["reviewed_date"] and len(meta["reviewed_date"]) == 10  # YYYY-MM-DD
    assert meta["policy_version"] == "1.0.0"

    # Verify policy fingerprint in baseline matches current configuration
    current_fp = compute_policy_fingerprint(ReportingAgentConfig())
    assert meta["policy_fingerprint"] == current_fp


def test_baseline_hashes_match_actual_files(data_path: Path):
    """Recomputes SHA-256 hashes of actual files in data/ and asserts strict match with baseline."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    stored_hashes = data["metadata"]["source_file_hashes"]
    current_hashes = compute_all_file_hashes(data_path)

    for fname, current_hash in current_hashes.items():
        assert fname in stored_hashes, f"File {fname} missing from baseline hashes"
        assert (
            stored_hashes[fname] == current_hash
        ), f"Hash mismatch for {fname}: stored {stored_hashes[fname]} != current {current_hash}"


def test_baseline_statuses_match_pipeline_execution(
    loaded_coa, loaded_tb, loaded_fx, loaded_batch, default_config
):
    """Runs deterministic validation against real source data and asserts 100% match with baseline."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    expected = data["expected_statuses"]
    actual_results = validate_adjustments_batch(
        entries=loaded_batch.entries,
        coa_index=loaded_coa,
        tb=loaded_tb,
        fx_rates=loaded_fx,
        config=default_config,
    )

    counts = {"ACCEPTED": 0, "REJECTED": 0, "QUARANTINED": 0}
    for entry_id, exp in expected.items():
        assert entry_id in actual_results, f"Entry {entry_id} missing from actual results"
        actual = actual_results[entry_id]

        assert (
            actual.disposition.value == exp["status"]
        ), f"Disposition mismatch for {entry_id}: expected {exp['status']}, got {actual.disposition.value}"
        assert (
            actual.issue_codes == exp["issue_codes"]
        ), f"Issue codes mismatch for {entry_id}: expected {exp['issue_codes']}, got {actual.issue_codes}"

        counts[actual.disposition.value] += 1

    # Assert exact distribution: 6 Accepted, 1 Rejected, 3 Quarantined
    assert counts["ACCEPTED"] == 6
    assert counts["REJECTED"] == 1
    assert counts["QUARANTINED"] == 3
    assert counts["ACCEPTED"] + counts["REJECTED"] + counts["QUARANTINED"] == 10
