"""Configuration dataclasses, policy fingerprinting, and field mapping."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class FxRevalPolicy:
    policy_version: str = "1.0.0"
    asset_account_code: str = "1110"               # Target monetary asset account
    target_currency: str = "EUR"                   # Target foreign transaction currency
    gain_loss_account_code: str = "7310"           # Target P&L revaluation gain/loss account
    rate_pair: tuple[str, str] = ("opening", "period_end")
    balance_source: str = "trial_balance"
    tolerance: Decimal = Decimal("0.01")           # Arithmetic tolerance in functional USD
    assumption_label: str = (
        "PROTOTYPE_ASSUMPTION: Monetary asset spot revaluation (period_end vs opening). "
        "Requires entity accounting policy confirmation before production use."
    )


@dataclass(frozen=True)
class CoaEligibilityPolicy:
    reject_headers: bool = True
    reject_parents: bool = True


@dataclass(frozen=True)
class ApprovalPolicy:
    allow_advisory_approval: bool = True
    require_notes: bool = True
    require_reviewer: bool = True
    require_mapping_for_unmapped_accounts: bool = True


@dataclass(frozen=True)
class ReportingPolicy:
    require_currency_safe_tb: bool = True
    block_on_missing_rates: bool = True


@dataclass(frozen=True)
class ReportingAgentConfig:
    policy_name: str = "ERP_REPORTING_AGENT_POLICY"
    policy_version: str = "1.0.0"
    policy_classification: str = (
        "PROTOTYPE_POLICY: Standardized financial validation ruleset. "
        "Requires management accounting sign-off."
    )
    functional_currency: str = "USD"
    fx: FxRevalPolicy = field(default_factory=FxRevalPolicy)
    coa: CoaEligibilityPolicy = field(default_factory=CoaEligibilityPolicy)
    approval: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    reporting: ReportingPolicy = field(default_factory=ReportingPolicy)
    hard_validation_gates: tuple[str, ...] = (
        "JE_UNBALANCED",
        "JE_NON_POSTABLE_HEADER",
        "JE_NON_POSTABLE_PARENT",
        "JE_INVALID_LINE_COUNT",
        "JE_NEGATIVE_AMOUNTS",
        "JE_ACCOUNT_PROHIBITED",
        "JE_CURRENCY_BASIS_INVALID",
        "JE_CURRENCY_METADATA_INCONSISTENT",
    )


# Explicit mapping of dataclass field paths to canonical payload paths
DECISION_RELEVANT_CONFIG_MAP: dict[str, tuple[str, ...]] = {
    "policy_name": ("policy_identity", "name"),
    "policy_version": ("policy_identity", "version"),
    "policy_classification": ("policy_identity", "policy_classification"),
    "functional_currency": ("functional_currency",),
    "fx.policy_version": ("fx_revaluation_policy", "version"),
    "fx.asset_account_code": ("fx_revaluation_policy", "asset_account_code"),
    "fx.target_currency": ("fx_revaluation_policy", "target_currency"),
    "fx.gain_loss_account_code": ("fx_revaluation_policy", "gain_loss_account_code"),
    "fx.rate_pair": ("fx_revaluation_policy", "rate_pair"),
    "fx.tolerance": ("fx_revaluation_policy", "tolerance"),
    "fx.balance_source": ("fx_revaluation_policy", "balance_source"),
    "fx.assumption_label": ("fx_revaluation_policy", "assumption_label"),
    "coa.reject_headers": ("coa_eligibility_policy", "reject_headers"),
    "coa.reject_parents": ("coa_eligibility_policy", "reject_parents"),
    "hard_validation_gates": ("hard_validation_gates",),
    "approval.allow_advisory_approval": ("review_approval_policy", "allow_advisory_approval"),
    "approval.require_notes": ("review_approval_policy", "require_notes"),
    "approval.require_reviewer": ("review_approval_policy", "require_reviewer"),
    "approval.require_mapping_for_unmapped_accounts": ("review_approval_policy", "require_mapping_for_unmapped_accounts"),
    "reporting.require_currency_safe_tb": ("reporting_policy", "require_currency_safe_tb"),
    "reporting.block_on_missing_rates": ("reporting_policy", "block_on_missing_rates"),
}

# Explicitly documented non-decision-relevant fields (none currently)
EXCLUDED_NON_DECISION_FIELDS: set[str] = set()


def get_canonical_fingerprint_dict(config: ReportingAgentConfig) -> dict[str, Any]:
    """Returns the explicit nested dictionary of all decision-relevant configuration settings."""
    return {
        "policy_identity": {
            "name": config.policy_name,
            "version": config.policy_version,
            "policy_classification": config.policy_classification,
        },
        "functional_currency": config.functional_currency,
        "fx_revaluation_policy": {
            "version": config.fx.policy_version,
            "asset_account_code": config.fx.asset_account_code,
            "target_currency": config.fx.target_currency,
            "gain_loss_account_code": config.fx.gain_loss_account_code,
            "rate_pair": list(config.fx.rate_pair),
            "tolerance": str(config.fx.tolerance),
            "balance_source": config.fx.balance_source,
            "assumption_label": config.fx.assumption_label,
        },
        "coa_eligibility_policy": {
            "reject_headers": config.coa.reject_headers,
            "reject_parents": config.coa.reject_parents,
        },
        "hard_validation_gates": sorted(config.hard_validation_gates),
        "review_approval_policy": {
            "allow_advisory_approval": config.approval.allow_advisory_approval,
            "require_notes": config.approval.require_notes,
            "require_reviewer": config.approval.require_reviewer,
            "require_mapping_for_unmapped_accounts": config.approval.require_mapping_for_unmapped_accounts,
        },
        "reporting_policy": {
            "require_currency_safe_tb": config.reporting.require_currency_safe_tb,
            "block_on_missing_rates": config.reporting.block_on_missing_rates,
        },
    }


def compute_policy_fingerprint(config: ReportingAgentConfig) -> str:
    """Computes a deterministic SHA-256 hash over the canonical JSON configuration."""
    payload = get_canonical_fingerprint_dict(config)
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()
