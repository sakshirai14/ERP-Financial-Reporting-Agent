"""Fuzzy account code and name resolver using RapidFuzz."""

from __future__ import annotations

from typing import Optional
from rapidfuzz import fuzz, process

from src.dq_checks import check_posting_eligibility
from src.ingest import CoaIndex
from src.models import CoaAccount


class AccountResolver:
    """Provides deterministic and fuzzy matching for unmapped account codes and descriptions."""

    def __init__(self, coa_index: CoaIndex):
        self.coa = coa_index
        # Pre-filter only postable leaf accounts for suggestions
        self.postable_accounts: list[CoaAccount] = [
            acc for acc in coa_index.all_accounts()
            if check_posting_eligibility(acc.account_code, coa_index).eligible
        ]

    def suggest_accounts(
        self,
        unmapped_code: str,
        description: str = "",
        limit: int = 5,
        other_accounts: Optional[set[str]] = None,
    ) -> list[dict[str, Any]]:
        """Suggests closest postable leaf accounts within the same statement and account family.

        Restricts candidates to postable leaf accounts with the same statement (PL/BS)
        and same first digit (e.g. 6000 family for 6315).
        Ranks primarily by token_set_ratio between description and account names,
        using shared code prefix length as a secondary signal.
        Always flags candidates that would cause self-offsetting journal lines.
        """
        other = other_accounts or set()
        clean_code = unmapped_code.strip()
        first_digit = clean_code[0] if clean_code else ""

        # Determine expected financial statement from first digit
        expected_statement = "BS" if first_digit in ("1", "2", "3") else "PL"

        # Restrict candidates to postable leaf accounts with the same statement AND same first digit
        matching_pool = [
            acc for acc in self.postable_accounts
            if acc.statement == expected_statement and acc.account_code.startswith(first_digit)
        ]

        # Fallback if no account matches the first digit
        if not matching_pool:
            matching_pool = [
                acc for acc in self.postable_accounts
                if acc.statement == expected_statement
            ]
        if not matching_pool:
            matching_pool = self.postable_accounts

        scored_candidates: list[dict[str, Any]] = []
        for acc in matching_pool:
            is_self_offsetting = acc.account_code in other

            # Name similarity (token_set_ratio)
            name_score = float(fuzz.token_set_ratio(description, acc.account_name)) if description else 0.0

            # Code proximity (shared prefix length)
            prefix_len = 0
            for c1, c2 in zip(clean_code, acc.account_code):
                if c1 == c2:
                    prefix_len += 1
                else:
                    break

            # Primary signal: name similarity (0-100). Secondary signal: code prefix length (2.0 points per matching prefix char)
            total_score = round(name_score + (prefix_len * 2.0), 2)

            scored_candidates.append({
                "account_code": acc.account_code,
                "account_name": acc.account_name,
                "name_score": round(name_score, 2),
                "prefix_len": prefix_len,
                "score": total_score,
                "statement": acc.statement,
                "match_type": "name_similarity_with_code_proximity",
                "would_cause_self_offsetting": is_self_offsetting,
            })

        # Sort primarily by total score descending, then by prefix_len descending
        scored_candidates.sort(key=lambda x: (x["score"], x["prefix_len"]), reverse=True)
        return scored_candidates[:limit]

