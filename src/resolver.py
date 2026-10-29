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
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Suggests closest postable leaf accounts based on code prefix and name similarity.

        Never suggests header accounts or parent accounts.
        """
        suggestions: list[dict[str, Any]] = []

        # 1. Code similarity match
        code_choices = {acc.account_code: acc for acc in self.postable_accounts}
        code_matches = process.extract(
            unmapped_code,
            code_choices.keys(),
            scorer=fuzz.ratio,
            limit=limit,
        )

        for match_code, score, _ in code_matches:
            acc = code_choices[match_code]
            suggestions.append({
                "account_code": acc.account_code,
                "account_name": acc.account_name,
                "score": round(score, 2),
                "statement": acc.statement,
                "match_type": "code_similarity",
            })

        # 2. Description similarity match if provided
        if description:
            name_choices = {acc.account_name: acc for acc in self.postable_accounts}
            name_matches = process.extract(
                description,
                name_choices.keys(),
                scorer=fuzz.token_set_ratio,
                limit=limit,
            )
            for match_name, score, _ in name_matches:
                acc = name_choices[match_name]
                if not any(s["account_code"] == acc.account_code for s in suggestions):
                    suggestions.append({
                        "account_code": acc.account_code,
                        "account_name": acc.account_name,
                        "score": round(score, 2),
                        "statement": acc.statement,
                        "match_type": "name_similarity",
                    })

        suggestions.sort(key=lambda x: x["score"], reverse=True)
        return suggestions[:limit]
