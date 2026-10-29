"""SQLite review store and atomic transaction manager for the ERP Financial Reporting Agent."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.models import (
    JournalEntry,
    JournalEntryDisposition,
    JournalLine,
    ReviewDecisionRecord,
)


class ReviewStore:
    """Manages persistent SQLite storage for review decisions, cached runs, and audit logs."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    original_account TEXT,
                    mapped_account TEXT,
                    prior_classification TEXT,
                    new_classification TEXT,
                    issue_codes_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cached_runs (
                    run_hash TEXT PRIMARY KEY,
                    policy_fingerprint TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantined_entries (
                    entry_id TEXT PRIMARY KEY,
                    entry_json TEXT NOT NULL,
                    issue_codes_json TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )

    def close(self) -> None:
        self._conn.close()

    def persist_quarantined_entries(
        self,
        entries: list[JournalEntry],
        results: dict[str, Any],
    ) -> None:
        """Stores or updates entries requiring review in the review queue."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            for entry in entries:
                res = results.get(entry.id)
                if res and res.disposition in (JournalEntryDisposition.QUARANTINED, JournalEntryDisposition.REJECTED):
                    entry_dict = entry.model_dump(mode="json")
                    self._conn.execute(
                        """
                        INSERT OR REPLACE INTO quarantined_entries (
                            entry_id, entry_json, issue_codes_json, disposition, timestamp
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            entry.id,
                            json.dumps(entry_dict),
                            json.dumps(res.issue_codes),
                            res.disposition.value,
                            now,
                        ),
                    )

    def get_entry(self, entry_id: str) -> Optional[JournalEntry]:
        """Retrieves a stored entry by ID."""
        cursor = self._conn.execute(
            "SELECT entry_json FROM quarantined_entries WHERE entry_id = ?",
            (entry_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = json.loads(row["entry_json"])
        lines = [JournalLine(**ld) for ld in data.get("lines", [])]
        return JournalEntry(
            id=data["id"],
            description=data.get("description", ""),
            date=data.get("date", ""),
            source=data.get("source", ""),
            lines=lines,
            entry_type=data.get("entry_type"),
            source_currency=data.get("source_currency"),
        )

    def get_decision(self, entry_id: str) -> Optional[ReviewDecisionRecord]:
        """Fetches the most recent decision record for an entry."""
        cursor = self._conn.execute(
            """
            SELECT * FROM review_decisions
            WHERE entry_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (entry_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ReviewDecisionRecord(
            entry_id=row["entry_id"],
            disposition=JournalEntryDisposition(row["disposition"]),
            reviewer=row["reviewer"],
            notes=row["notes"],
            timestamp=row["timestamp"],
            policy_fingerprint=row["policy_fingerprint"],
            original_account=row["original_account"],
            mapped_account=row["mapped_account"],
            prior_classification=row["prior_classification"],
            new_classification=row["new_classification"],
            issue_codes=json.loads(row["issue_codes_json"]),
        )

    def is_decision_valid(self, entry_id: str, current_fingerprint: str) -> bool:
        """Checks if the stored decision was evaluated under the current policy fingerprint."""
        decision = self.get_decision(entry_id)
        if not decision:
            return False
        return decision.policy_fingerprint == current_fingerprint

    def get_prior_accepted_ids(self) -> set[str]:
        """Returns set of all entry IDs that were accepted in prior runs."""
        cursor = self._conn.execute(
            """
            SELECT entry_id FROM review_decisions
            WHERE disposition = 'ACCEPTED'
            GROUP BY entry_id
            """
        )
        return {row["entry_id"] for row in cursor.fetchall()}

    def persist_account_mapping(
        self,
        entry_id: str,
        original_account: str,
        mapped_account: str,
        reviewer: str,
        notes: str,
        new_disposition: JournalEntryDisposition,
        revalidation_result: Any,
        policy_fingerprint: str,
        updated_entry: Optional[JournalEntry] = None,
    ) -> None:
        """Atomically records an account mapping, updates the entry, and logs the audit event."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            # Insert audit record
            self._conn.execute(
                """
                INSERT INTO review_decisions (
                    entry_id, disposition, reviewer, notes, policy_fingerprint,
                    original_account, mapped_account, issue_codes_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    new_disposition.value,
                    reviewer,
                    notes,
                    policy_fingerprint,
                    original_account,
                    mapped_account,
                    json.dumps(getattr(revalidation_result, "issue_codes", [])),
                    now,
                ),
            )

            # Update quarantined entry state
            if updated_entry:
                entry_dict = updated_entry.model_dump(mode="json")
                self._conn.execute(
                    """
                    UPDATE quarantined_entries
                    SET entry_json = ?, disposition = ?, issue_codes_json = '[]', timestamp = ?
                    WHERE entry_id = ?
                    """,
                    (json.dumps(entry_dict), new_disposition.value, now, entry_id),
                )

    def record_decision(
        self,
        entry_id: str,
        disposition: JournalEntryDisposition | str,
        reviewer: str,
        notes: str = "",
        policy_fingerprint: str = "",
        prior_classification: Optional[str] = None,
        new_classification: Optional[str] = None,
        issue_codes: Optional[list[str]] = None,
        updated_entry: Optional[JournalEntry] = None,
    ) -> None:
        """Atomically records a review decision and updates entry state."""
        now = datetime.now(timezone.utc).isoformat()
        disp_val = disposition.value if isinstance(disposition, JournalEntryDisposition) else disposition
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO review_decisions (
                    entry_id, disposition, reviewer, notes, policy_fingerprint,
                    prior_classification, new_classification, issue_codes_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    disp_val,
                    reviewer,
                    notes,
                    policy_fingerprint,
                    prior_classification,
                    new_classification,
                    json.dumps(issue_codes or []),
                    now,
                ),
            )
            if updated_entry:
                entry_dict = updated_entry.model_dump(mode="json")
                self._conn.execute(
                    """
                    UPDATE quarantined_entries
                    SET entry_json = ?, disposition = ?, issue_codes_json = ?, timestamp = ?
                    WHERE entry_id = ?
                    """,
                    (json.dumps(entry_dict), disp_val, json.dumps(issue_codes or []), now, entry_id),
                )

    def save_cached_run(
        self,
        run_hash: str,
        policy_fingerprint: str,
        results_data: dict[str, Any],
    ) -> None:
        """Caches results of a deterministic run under run_hash and policy_fingerprint."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cached_runs (
                    run_hash, policy_fingerprint, results_json, timestamp
                ) VALUES (?, ?, ?, ?)
                """,
                (run_hash, policy_fingerprint, json.dumps(results_data), now),
            )

    def get_cached_run(
        self,
        run_hash: str,
        current_fingerprint: str,
    ) -> Optional[dict[str, Any]]:
        """Retrieves cached run results if run_hash matches and policy_fingerprint is current."""
        cursor = self._conn.execute(
            """
            SELECT results_json FROM cached_runs
            WHERE run_hash = ? AND policy_fingerprint = ?
            """,
            (run_hash, current_fingerprint),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return json.loads(row["results_json"])
