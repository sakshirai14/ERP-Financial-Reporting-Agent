"""Ingestion, cryptographic hashing, and parsing for ERP financial reporting data files."""

from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from src.models import (
    CoaAccount,
    FxRateRow,
    JournalEntry,
    JournalLine,
    ManualAdjustmentsBatch,
    PriorPeriodTbRow,
    TbRow,
)

REQUIRED_INPUT_FILES = [
    "trial_balance.csv",
    "chart_of_accounts.csv",
    "fx_rates.csv",
    "prior_period_tb.csv",
    "manual_adjustments.json",
]


def compute_file_hash(path: Path) -> str:
    """Computes a SHA-256 lowercase hexadecimal hash from file bytes."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_all_file_hashes(data_dir: Path) -> dict[str, str]:
    """Computes SHA-256 hashes for all required input files in the data directory."""
    hashes = {}
    for filename in REQUIRED_INPUT_FILES:
        filepath = data_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Required input file missing: {filepath}")
        hashes[filename] = compute_file_hash(filepath)
    return hashes


def parse_decimal(val: Any) -> Decimal:
    """Parses a numerical or string value into a Decimal safely."""
    if val is None or val == "":
        return Decimal("0.00")
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    cleaned = str(val).strip().replace(",", "")
    return Decimal(cleaned)


def load_chart_of_accounts(path: Path) -> list[CoaAccount]:
    """Loads and validates the Chart of Accounts CSV."""
    accounts = []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            accounts.append(
                CoaAccount(
                    account_code=row["account_code"].strip(),
                    account_name=row["account_name"].strip(),
                    account_type=row["account_type"].strip(),
                    parent_account=(row.get("parent_code", "").strip() or row.get("parent_account", "").strip()) or None,
                    statement=row.get("statement", "").strip(),
                    cf_category=row.get("cf_category", "").strip() or None,
                    normal_balance=row.get("normal_balance", "").strip() or None,
                )
            )
    return accounts


def load_trial_balance(path: Path) -> list[TbRow]:
    """Loads and validates the multi-currency trial balance CSV."""
    rows = []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                TbRow(
                    account_code=row["account_code"].strip(),
                    account_name=row["account_name"].strip(),
                    currency=row["currency"].strip(),
                    debit=parse_decimal(row.get("debit", "0.00")),
                    credit=parse_decimal(row.get("credit", "0.00")),
                    row_number=reader.line_num,
                )
            )
    return rows


def load_fx_rates(path: Path) -> list[FxRateRow]:
    """Loads and validates FX rates CSV."""
    rows = []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                FxRateRow(
                    currency=row["currency"].strip(),
                    rate_type=row["rate_type"].strip(),
                    rate=parse_decimal(row["rate"]),
                    period=row["period"].strip(),
                )
            )
    return rows


def load_prior_period_tb(path: Path) -> list[PriorPeriodTbRow]:
    """Loads and validates prior period trial balance CSV."""
    rows = []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                PriorPeriodTbRow(
                    account_code=row["account_code"].strip(),
                    account_name=row["account_name"].strip(),
                    currency=row["currency"].strip(),
                    debit=parse_decimal(row.get("debit", "0.00")),
                    credit=parse_decimal(row.get("credit", "0.00")),
                )
            )
    return rows


def load_manual_adjustments(path: Path) -> ManualAdjustmentsBatch:
    """Loads and validates manual journal adjustments JSON."""
    with open(path, mode="r", encoding="utf-8") as f:
        raw = json.load(f)

    entries = []
    for entry_dict in raw.get("entries", []):
        lines = []
        for line_dict in entry_dict.get("lines", []):
            lines.append(
                JournalLine(
                    account=str(line_dict["account"]).strip(),
                    debit=parse_decimal(line_dict.get("debit", 0.0)),
                    credit=parse_decimal(line_dict.get("credit", 0.0)),
                    memo=line_dict.get("memo"),
                    currency=line_dict.get("currency"),
                )
            )
        entries.append(
            JournalEntry(
                id=str(entry_dict["id"]).strip(),
                description=str(entry_dict.get("description", "")).strip(),
                date=str(entry_dict.get("date", "")).strip(),
                source=str(entry_dict.get("source", "")).strip(),
                lines=lines,
                entry_type=entry_dict.get("entry_type"),
                source_currency=entry_dict.get("source_currency"),
            )
        )

    return ManualAdjustmentsBatch(
        period=raw.get("period", "2024-Q4"),
        functional_currency=raw.get("functional_currency", "USD"),
        entries=entries,
    )


class CoaIndex:
    """High-performance in-memory index for Chart of Accounts lookups and hierarchy inspection."""

    def __init__(self, accounts: list[CoaAccount]):
        self._accounts: dict[str, CoaAccount] = {a.account_code: a for a in accounts}
        self._children: dict[str, list[str]] = {}
        for a in accounts:
            if a.parent_account:
                self._children.setdefault(a.parent_account, []).append(a.account_code)

    def account_exists(self, code: str) -> bool:
        return code in self._accounts

    def get_account(self, code: str) -> Optional[CoaAccount]:
        return self._accounts.get(code)

    def get_account_type(self, code: str) -> Optional[str]:
        account = self.get_account(code)
        return account.account_type if account else None

    def has_children(self, code: str) -> bool:
        return bool(self._children.get(code))

    def get_children(self, code: str) -> list[str]:
        return list(self._children.get(code, []))

    def all_accounts(self) -> list[CoaAccount]:
        return list(self._accounts.values())
