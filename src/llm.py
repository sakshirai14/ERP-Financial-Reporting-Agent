"""Safe LLM layer with pluggable providers, structured JSON ranking, and deterministic offline mock default."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from src.dq_checks import check_posting_eligibility
from src.ingest import CoaIndex


@dataclass
class LLMRankingResult:
    """Structured result of LLM unmapped account ranking (Task A)."""
    selected_code: Optional[str]
    confidence: float
    rationale: str
    is_valid: bool
    escalated: bool
    escalation_reason: Optional[str] = None
    would_cause_self_offsetting: bool = False
    model_name: str = "mock"
    prompt: str = ""
    raw_response: str = ""


class LLMProvider(ABC):
    """Abstract provider wrapper for LLM generation."""
    name: str

    @abstractmethod
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generates a text completion under temperature 0."""
        pass


class MockLLMProvider(LLMProvider):
    """Deterministic offline mock provider (default). Operates without any API keys."""
    name = "mock"

    def __init__(self, mock_response: Optional[str] = None):
        self.mock_response = mock_response

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if self.mock_response is not None:
            return self.mock_response

        # Check if ranking prompt
        if "RANK_CANDIDATE_ACCOUNTS" in prompt or "candidates:" in prompt.lower():
            # Extract candidate codes from prompt
            lines = prompt.splitlines()
            candidates = []
            for line in lines:
                if line.strip().startswith("- Candidate:"):
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        candidates.append(parts[2].strip(":,()"))

            chosen_code = candidates[0] if candidates else "6310"
            return json.dumps({
                "selected_code": chosen_code,
                "confidence": 0.88,
                "rationale": f"Selected account {chosen_code} as the closest matching active COA account for this transaction.",
            })

        return json.dumps({
            "message": "Deterministic mock explanation.",
        })


class GeminiLLMProvider(LLMProvider):
    """Google Gemini LLM provider using direct HTTP request."""
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini provider.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text
        except Exception as e:
            raise RuntimeError(f"Gemini API request failed: {e}") from e


class OpenAILLMProvider(LLMProvider):
    """OpenAI LLM provider using direct HTTP request."""
    name = "openai"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI provider.")

        url = "https://api.openai.com/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"OpenAI API request failed: {e}") from e


class AnthropicLLMProvider(LLMProvider):
    """Anthropic LLM provider using direct HTTP request."""
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for Anthropic provider.")

        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["content"][0]["text"]
        except Exception as e:
            raise RuntimeError(f"Anthropic API request failed: {e}") from e


def get_llm_provider(name: Optional[str] = None) -> LLMProvider:
    """Factory creating an LLM provider based on name or LLM_PROVIDER environment variable."""
    prov_name = (name or os.getenv("LLM_PROVIDER", "mock")).lower().strip()
    if prov_name == "gemini":
        return GeminiLLMProvider()
    if prov_name == "openai":
        return OpenAILLMProvider()
    if prov_name == "anthropic":
        return AnthropicLLMProvider()
    return MockLLMProvider()


class AccountingExplanationEngine:
    """Generates plain English narrative explanations and evaluates AI account suggestions.

    Guarantees:
    - 100% offline and deterministic with MockLLMProvider by default.
    - Hallucinations outside candidate list are strictly rejected and escalated.
    - Suggestions are strictly advisory and never alter accounting dispositions.
    - All prompt/response pairs and validations are logged for auditor audit trails.
    """

    def __init__(
        self,
        provider: Optional[LLMProvider | str] = None,
        coa_index: Optional[CoaIndex] = None,
    ):
        if isinstance(provider, LLMProvider):
            self.provider = provider
        elif isinstance(provider, str):
            self.provider = get_llm_provider(provider)
        else:
            self.provider = get_llm_provider()

        self.coa = coa_index
        self.trace_events: list[dict[str, Any]] = []

    def rank_account_candidates(
        self,
        entry_id: str,
        unmapped_code: str,
        description: str,
        candidates: list[dict[str, Any]],
        other_accounts: Optional[set[str]] = None,
    ) -> LLMRankingResult:
        """Task A: Ranks RapidFuzz candidates using structured LLM evaluation.

        Validates that returned account is within candidate list and postable.
        Detects potential self-offsetting conditions and escalates low-confidence suggestions.
        """
        other = other_accounts or set()
        candidate_codes = [c["account_code"] for c in candidates]

        # Construct prompt
        candidates_str = "\n".join([
            f"- Candidate: {c['account_code']} | Name: {c['account_name']} | RapidFuzz Score: {c.get('score', 0)} | Self-Offsetting: {c['account_code'] in other}"
            for c in candidates
        ])
        prompt = (
            f"TASK: RANK_CANDIDATE_ACCOUNTS\n"
            f"Entry ID: {entry_id}\n"
            f"Unmapped Account Code: {unmapped_code}\n"
            f"Transaction Description: {description}\n"
            f"Allowed Candidates (You MUST select ONLY from this list):\n"
            f"{candidates_str}\n\n"
            "Respond ONLY with valid JSON in this exact schema:\n"
            "{\n"
            f'  "selected_code": "<one of {candidate_codes}>",\n'
            '  "confidence": <float between 0.0 and 1.0>,\n'
            '  "rationale": "<one-sentence explanation>"\n'
            "}"
        )

        raw_response = ""
        try:
            raw_response = self.provider.generate(prompt)
            data = json.loads(raw_response)
            selected = str(data.get("selected_code", "")).strip()
            confidence = float(data.get("confidence", 0.0))
            rationale = str(data.get("rationale", "")).strip()

            # Validation 1: Hallucination check (Must be in candidate list)
            if selected not in candidate_codes:
                result = LLMRankingResult(
                    selected_code=selected,
                    confidence=confidence,
                    rationale=rationale,
                    is_valid=False,
                    escalated=True,
                    escalation_reason=f"Selected code '{selected}' was not in the top-5 candidate set (hallucination rejected).",
                    would_cause_self_offsetting=False,
                    model_name=self.provider.name,
                    prompt=prompt,
                    raw_response=raw_response,
                )
                self._record_ranking_event(entry_id, prompt, raw_response, result)
                return result

            # Validation 2: Posting eligibility check
            if self.coa and not check_posting_eligibility(selected, self.coa).eligible:
                result = LLMRankingResult(
                    selected_code=selected,
                    confidence=confidence,
                    rationale=rationale,
                    is_valid=False,
                    escalated=True,
                    escalation_reason=f"Selected code '{selected}' is a non-postable summary or header account.",
                    would_cause_self_offsetting=False,
                    model_name=self.provider.name,
                    prompt=prompt,
                    raw_response=raw_response,
                )
                self._record_ranking_event(entry_id, prompt, raw_response, result)
                return result

            # Validation 3: Confidence threshold check
            is_self_offsetting = selected in other
            is_low_confidence = confidence < 0.60
            escalated = is_low_confidence
            escalation_reason = "Low confidence ranking (< 0.60). Human review required." if is_low_confidence else None

            result = LLMRankingResult(
                selected_code=selected,
                confidence=confidence,
                rationale=rationale,
                is_valid=True,
                escalated=escalated,
                escalation_reason=escalation_reason,
                would_cause_self_offsetting=is_self_offsetting,
                model_name=self.provider.name,
                prompt=prompt,
                raw_response=raw_response,
            )
            self._record_ranking_event(entry_id, prompt, raw_response, result)
            return result

        except Exception as e:
            result = LLMRankingResult(
                selected_code=None,
                confidence=0.0,
                rationale="",
                is_valid=False,
                escalated=True,
                escalation_reason=f"LLM parsing or generation failed: {e}",
                would_cause_self_offsetting=False,
                model_name=self.provider.name,
                prompt=prompt,
                raw_response=raw_response,
            )
            self._record_ranking_event(entry_id, prompt, raw_response, result)
            return result

    def _record_ranking_event(
        self,
        entry_id: str,
        prompt: str,
        response: str,
        result: LLMRankingResult,
    ) -> None:
        self.trace_events.append({
            "event": "LLM_ACCOUNT_MAPPING_RANKING",
            "entry_id": entry_id,
            "model": result.model_name,
            "prompt": prompt,
            "response": response,
            "validation_result": {
                "valid": result.is_valid,
                "selected_code": result.selected_code,
                "confidence": result.confidence,
                "escalated": result.escalated,
                "escalation_reason": result.escalation_reason,
                "would_cause_self_offsetting": result.would_cause_self_offsetting,
            },
        })

    def explain_issue(
        self,
        entry_id: str,
        issue_code: str,
        context: Optional[dict[str, Any]] = None,
        suggestions: Optional[list[dict[str, Any]]] = None,
        ranking_result: Optional[LLMRankingResult] = None,
    ) -> str:
        """Task B: Generates plain English narrative explanations for accounting findings."""
        ctx = context or {}

        if issue_code == "JE_UNBALANCED":
            return (
                f"Entry {entry_id} violates the fundamental double-entry equation (Debits != Credits). "
                "Total debit postings do not equal total credit postings. Under standard accounting rules, "
                "unbalanced adjustments cannot be accepted or posted to the general ledger."
            )

        if issue_code == "JE_FX_MISMATCH":
            fx_data = ctx.get("fx_context", ctx)
            booked = fx_data.get("booked_amount") or ctx.get("booked_amount")
            expected = fx_data.get("expected_usd_delta") or ctx.get("expected_usd_delta")
            discrepancy = fx_data.get("discrepancy") or ctx.get("discrepancy")
            if not booked or not expected or not discrepancy:
                raise ValueError(f"Missing required FX context for entry {entry_id}: {ctx}")
            return (
                f"Entry {entry_id} reflects an FX revaluation calculation discrepancy. "
                f"The entry books ${booked} USD, but the revaluation delta calculated from "
                f"the foreign cash balance and closing exchange rate is ${expected} USD (variance of ${discrepancy} USD). "
                "The entry is quarantined for finance team reconciliation."
            )

        if issue_code == "JE_CURRENCY_BASIS_INVALID":
            fx_data = ctx.get("fx_context", ctx)
            booked = fx_data.get("booked_amount") or ctx.get("booked_amount")
            native = fx_data.get("source_balance") or fx_data.get("native_balance") or ctx.get("source_balance") or ctx.get("native_balance")
            expected = fx_data.get("expected_usd_delta") or ctx.get("expected_usd_delta")
            if not booked or not native or not expected:
                raise ValueError(f"Missing required currency basis context for entry {entry_id}: {ctx}")
            return (
                f"Entry {entry_id} contains a suspected currency-basis error. "
                f"The booked functional amount of ${booked} USD matches the native foreign balance ({native} EUR) "
                f"rather than the expected functional revaluation delta (${expected} USD). "
                "Posting the raw foreign balance directly into functional reporting lines is rejected as an invalid basis."
            )

        if issue_code == "JE_FX_DIRECTION_INVALID":
            return (
                f"Entry {entry_id} has an inverted accounting direction for foreign currency revaluation. "
                "When a foreign monetary asset appreciates, the asset account must be debited and an unrealized gain credited. "
                "Inverted gain/loss direction is quarantined for correction."
            )

        if issue_code == "JE_ACCOUNT_UNMAPPED":
            unmapped = ctx.get("unmapped_account", "an unmapped code")
            suggestion_str = ""
            if ranking_result and ranking_result.is_valid and ranking_result.selected_code:
                warn_offset = " (WARNING: would cause self-offsetting balance)" if ranking_result.would_cause_self_offsetting else ""
                suggestion_str = (
                    f" AI suggested mapping: {ranking_result.selected_code} (Confidence: {ranking_result.confidence:.2f}){warn_offset}. "
                    f"Rationale: {ranking_result.rationale}"
                )
            elif suggestions:
                top = suggestions[0]
                suggestion_str = f" Closest candidate in active Chart of Accounts is {top['account_code']} ({top['account_name']})."

            return (
                f"Entry {entry_id} targets account {unmapped}, which is not present in the active Chart of Accounts. "
                f"The entry has been quarantined for reviewer disambiguation.{suggestion_str}"
            )

        if issue_code == "JE_SELF_OFFSETTING":
            acc = ctx.get("offsetting_account", "the target account")
            return (
                f"Entry {entry_id} debits and credits the exact same account ({acc}), netting to zero change. "
                "While mathematically balanced, self-offsetting journal lines distort line-item reporting. "
                "Quarantined for reviewer verification of business justification."
            )

        if issue_code == "JE_INBATCH_DUPLICATE":
            count = ctx.get("duplicate_count", "multiple")
            return (
                f"Entry ID {entry_id} appears {count} times within the current batch. "
                "All conflicting occurrences have been quarantined symmetrically to prevent accidental duplicate postings."
            )

        if issue_code == "JE_PRIOR_ACCEPTANCE":
            return (
                f"Entry ID {entry_id} was previously approved and accepted in an earlier batch run. "
                "Quarantined as a reviewable advisory to verify whether this is an intentional repeat submission or a duplicate."
            )

        if issue_code in ("JE_NON_POSTABLE_HEADER", "JE_NON_POSTABLE_PARENT"):
            return (
                f"Entry {entry_id} targets an account that is classified as a header or summary parent in the Chart of Accounts. "
                "Summary accounts cannot receive journal postings."
            )

        if issue_code == "JE_DATE_OUT_OF_PERIOD":
            date_err = ctx.get("date_error", {})
            entry_date = date_err.get("entry_date") or ctx.get("entry_date", "the entry date")
            period = date_err.get("batch_period") or ctx.get("period", "2024-Q4")
            return (
                f"Entry {entry_id} has date {entry_date}, which falls outside the active accounting period {period}. "
                "Quarantined for period assignment verification."
            )

        if issue_code == "JE_EXCEEDS_BALANCE":
            bal_err = ctx.get("balance_sufficiency_error", {})
            acc = bal_err.get("account") or ctx.get("account", "target account")
            against_amt = bal_err.get("against_amount") or ctx.get("against_amount", "adjustment amount")
            tb_bal = bal_err.get("tb_balance") or ctx.get("tb_balance", "current balance")
            return (
                f"Entry {entry_id} posts an adjustment of ${against_amt} against the normal balance of account {acc}, "
                f"which exceeds its current ledger balance of ${tb_bal}. "
                "Quarantined to prevent negative ledger balances."
            )

        return f"Entry {entry_id} triggered accounting validation issue {issue_code}. Quarantined for review."
