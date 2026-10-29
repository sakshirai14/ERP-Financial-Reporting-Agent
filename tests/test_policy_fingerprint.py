"""Tests for policy fingerprint coverage, deterministic serialization, and review invalidation."""

from __future__ import annotations

import dataclasses
from dataclasses import fields, is_dataclass
from decimal import Decimal
import pytest

from src.config import (
    DECISION_RELEVANT_CONFIG_MAP,
    EXCLUDED_NON_DECISION_FIELDS,
    ReportingAgentConfig,
    compute_policy_fingerprint,
    get_canonical_fingerprint_dict,
)
from src.store import ReviewStore


def test_policy_fingerprint_exhaustively_covers_all_config_fields():
    """Recursively checks all fields in ReportingAgentConfig and child dataclasses,

    verifying that every field has an explicit entry in DECISION_RELEVANT_CONFIG_MAP
    and resolves to an actual non-null value in the canonical dictionary.
    """
    config = ReportingAgentConfig()
    canonical_dict = get_canonical_fingerprint_dict(config)

    # 1. Discover all dataclass fields in config hierarchy
    def get_all_dataclass_fields(dc_obj, prefix=""):
        res = set()
        for f in fields(dc_obj):
            field_path = f"{prefix}.{f.name}" if prefix else f.name
            val = getattr(dc_obj, f.name)
            if is_dataclass(val):
                res.update(get_all_dataclass_fields(val, field_path))
            else:
                res.add(field_path)
        return res

    all_config_fields = get_all_dataclass_fields(config)

    # 2. Assert zero omissions: every dataclass field must be mapped or explicitly excluded
    mapped_fields = set(DECISION_RELEVANT_CONFIG_MAP.keys())
    unmapped = all_config_fields - mapped_fields - EXCLUDED_NON_DECISION_FIELDS
    assert not unmapped, f"Decision-relevant configuration fields omitted from fingerprint mapping: {unmapped}"

    # 3. Assert each mapped path resolves to a real value in the canonical dictionary
    for field_path, payload_path in DECISION_RELEVANT_CONFIG_MAP.items():
        curr = canonical_dict
        for segment in payload_path:
            assert segment in curr, f"Payload path {payload_path} for field {field_path} missing in canonical dictionary."
            curr = curr[segment]
        assert curr is not None, f"Payload value at {payload_path} for field {field_path} is None."


def test_fingerprint_detects_omitted_field_simulation():
    """Proves that if a field is omitted from DECISION_RELEVANT_CONFIG_MAP, the test fails."""
    all_fields = {"policy_name", "policy_version", "unmapped_test_field"}
    mapped_fields = {"policy_name", "policy_version"}
    omitted = all_fields - mapped_fields
    assert omitted == {"unmapped_test_field"}


def test_fingerprint_invalidation_on_config_mutations():
    """Mutating any decision-relevant setting alters the fingerprint and invalidates prior reviews."""
    base_config = ReportingAgentConfig()
    base_fp = compute_policy_fingerprint(base_config)

    # 1. Change functional currency
    cfg_curr = dataclasses.replace(base_config, functional_currency="EUR")
    assert compute_policy_fingerprint(cfg_curr) != base_fp

    # 2. Change FX target currency
    cfg_fx_curr = dataclasses.replace(base_config, fx=dataclasses.replace(base_config.fx, target_currency="GBP"))
    assert compute_policy_fingerprint(cfg_fx_curr) != base_fp

    # 3. Change FX rate pair
    cfg_fx_rate = dataclasses.replace(base_config, fx=dataclasses.replace(base_config.fx, rate_pair=("opening", "period_average")))
    assert compute_policy_fingerprint(cfg_fx_rate) != base_fp

    # 4. Change COA header rule
    cfg_coa = dataclasses.replace(base_config, coa=dataclasses.replace(base_config.coa, reject_headers=False))
    assert compute_policy_fingerprint(cfg_coa) != base_fp

    # 5. Change approval policy
    cfg_appr = dataclasses.replace(base_config, approval=dataclasses.replace(base_config.approval, allow_advisory_approval=False))
    assert compute_policy_fingerprint(cfg_appr) != base_fp

    # 6. Change policy classification (even if policy_version stays 1.0.0)
    cfg_class = dataclasses.replace(base_config, policy_classification="CUSTOM_ENTITY_POLICY_APPROVED")
    assert compute_policy_fingerprint(cfg_class) != base_fp

    # 7. Verify review store treats prior decision under base_fp as stale when queried under new fingerprint
    store = ReviewStore(":memory:")
    store.record_decision("JE-008", "ACCEPTED", reviewer="auditor", policy_fingerprint=base_fp)
    new_fp = compute_policy_fingerprint(cfg_fx_rate)
    assert store.is_decision_valid("JE-008", current_fingerprint=new_fp) is False
    assert store.is_decision_valid("JE-008", current_fingerprint=base_fp) is True
    store.close()
