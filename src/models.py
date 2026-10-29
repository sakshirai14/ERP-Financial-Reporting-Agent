"""Domain models and data schemas for the ERP Financial Reporting Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class JournalEntryDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


class DiagnosticSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class DataQualityFinding:
    code: str
    severity: DiagnosticSeverity
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostingEligibilityResult:
    eligible: bool
    failed_condition: Optional[str] = None  # "CONDITION_A_HEADER" or "CONDITION_B_PARENT"
    reason: str = ""


class JournalLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")
    memo: Optional[str] = None
    currency: Optional[str] = None  # Denomination of posted amount (must be USD if specified)

    def is_debit(self) -> bool:
        return self.debit > Decimal("0.00") and self.credit == Decimal("0.00")

    def is_credit(self) -> bool:
        return self.credit > Decimal("0.00") and self.debit == Decimal("0.00")


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    description: str
    date: str
    source: str
    lines: list[JournalLine]
    entry_type: Optional[str] = None  # e.g., "fx_revaluation"
    source_currency: Optional[str] = None  # e.g., "EUR"


class ManualAdjustmentsBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    period: str
    functional_currency: str = "USD"
    entries: list[JournalEntry]


class CoaAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_code: str
    account_name: str
    account_type: str
    parent_account: Optional[str] = None
    statement: str
    cf_category: Optional[str] = None
    normal_balance: Optional[str] = None


class TbRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_code: str
    account_name: str
    currency: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")


class FxRateRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    currency: str
    rate_type: str
    rate: Decimal
    period: str


class PriorPeriodTbRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_code: str
    account_name: str
    currency: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class JournalEntryValidationResult:
    entry_id: str
    disposition: JournalEntryDisposition
    issue_codes: list[str]
    is_balanced: bool
    accounts_exist_in_coa: bool
    accounts_are_postable: bool
    line_currencies_consistent: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionSummary:
    total_entries: int
    accepted_count: int
    rejected_count: int
    quarantined_count: int
    quarantine_rate: float
    total_dq_warnings: int


@dataclass(frozen=True)
class PipelineExecutionResult:
    adjustments: dict[str, JournalEntryValidationResult]  # Keyed by entry_id
    dq_findings: list[DataQualityFinding]                 # Diagnostics & warnings
    policy_fingerprint: str
    input_file_hashes: dict[str, str]
    summary: ExecutionSummary
    entries: list[JournalEntry] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewDecisionRecord:
    entry_id: str
    disposition: JournalEntryDisposition
    reviewer: str
    notes: str
    timestamp: str
    policy_fingerprint: str
    original_account: Optional[str] = None
    mapped_account: Optional[str] = None
    prior_classification: Optional[str] = None
    new_classification: Optional[str] = None
    issue_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewOperationResult:
    success: bool
    disposition: Optional[JournalEntryDisposition] = None
    error_message: Optional[str] = None
    audit_entry: Optional[ReviewDecisionRecord] = None
