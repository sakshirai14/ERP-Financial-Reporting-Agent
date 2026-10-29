# AI Usage Disclosure & Agentic Integration Architecture

## 1. Executive Disclosure

This project was developed through pair programming between a human engineer and an agentic AI assistant (Antigravity IDE), acting as a senior financial-data systems architect and Python engineer. This document provides full disclosure of AI usage during both development and within the runtime application architecture, with particular focus on where generative AI failed and how deterministic guardrails corrected those failures.

---

## 2. AI in the Application Runtime Architecture

### 2.1 The Core Safety Mandate: Absolute Decoupling of LLM from Accounting Logic
In enterprise financial reporting, generative AI models must **never** make authoritative accounting decisions:
- Generative models are probabilistic, sensitive to prompt formatting, and susceptible to arithmetic errors and hallucinations.
- Consequently, this system enforces a strict architectural boundary:
  1. **100% Deterministic Accounting Engine:** All validation gates, double-entry equality checks, COA posting eligibility, FX revaluation calculations, duplicate lifecycles, and journal dispositions (`ACCEPTED`, `REJECTED`, `QUARANTINED`) are executed by deterministic Python code using fixed-scale `Decimal` arithmetic.
  2. **Bounded Advisory & Narrative Role:** The LLM is restricted to two narrow tasks:
     - **Task A (Advisory Candidate Ranking):** When an unmapped account is encountered (`JE_ACCOUNT_UNMAPPED`), deterministic algorithms (RapidFuzz and COA hierarchy) generate a pre-approved list of top-5 postable candidates. The LLM ranks only within this fixed enum. Software validation verifies the selected candidate is in the candidate list and postable; any invalid, hallucinated, or low-confidence (<0.60) response is discarded and escalated to human review. Suggestions are strictly advisory.
     - **Task B (Narrative Explanation):** Generates plain-English explanations for human auditors based on structured, verified context. The LLM output has zero write-back authority to change dispositions or ledger amounts.

### 2.2 Pluggable Provider Architecture & Offline-by-Default Operation
- The runtime uses a unified `LLMProvider` interface supporting `mock`, `gemini`, `anthropic`, and `openai` (controlled via `LLM_PROVIDER` environment variable).
- **Default Provider:** `MockLLMProvider`, an offline, deterministic mock operating at temperature 0 with zero external network or API key dependencies. This ensures immediate reproducibility in air-gapped CI/CD environments. Note: The `gemini`, `openai`, and `anthropic` providers are implemented for production readiness but remain untested in CI without live API keys.
- **Task B Explanations are Template-Based by Design:** Audit narrative generation uses verified structured context with deterministic template formatting. The LLM is strictly prohibited from deciding or altering journal dispositions.
- **Audit Logging:** Every LLM prompt, raw response, model identifier, and code validation result is recorded in `output/run_trace.jsonl` for auditor inspection.

---

## 3. Analysis of AI Tool Failures During Development

During early planning and analysis phases, generative AI tools produced multiple factual errors and hallucinations when reading the raw data files. These failures demonstrate why automated financial agents cannot rely on unconstrained AI text comprehension:

| Defect / Area | AI Hallucination / Error | Ground Truth in Data Files | Correction Mechanism |
|---|---|---|---|
| **Account 6310 Name** | Hallucinated `"Software & IT Services"` | `chart_of_accounts.csv` defines 6310 as `"Travel and Entertainment"`. | Created `src/tools/profile_data.py` to inspect actual CSV text. |
| **Suspense Account 9999** | Claimed Debit was `"$2,500.00"` | `trial_balance.csv` row 62 contains Debit `"$12,400.00"`. | Programmatic aggregation in `profile_data.py`. |
| **Adjustment JE-002 Accounts** | Claimed Line 1 used account `6320` | `manual_adjustments.json` uses `6300` (Dr $28,500) and `6310` (Cr $25,000). | JSON parser verification. |
| **Adjustment JE-005 Accounts** | Claimed Line 2 credited `2110` for `$45,000` | JE-005 Line 1 is Dr `6315` $18,500 and Line 2 is Cr `6310` $18,500. | Direct JSON inspection tool. |
| **Adjustment JE-008 Amount** | Claimed JE-008 was for `"$35,000.00"` | JE-008 Line 1 is Dr `2170` $320,000.00 and Line 2 is Cr `2170` $320,000.00. | Exact numeric verification in `profile_data.py`. |
| **GBP Trial Balance Rows** | Claimed GBP was `413,300 across 3 rows` | GBP cash `1110` is `$412,300.00` across exactly **one row**. | CSV row profiling. |
| **Prior Period Imbalance** | Claimed raw prior gap was `+$2,980,009.80` | Raw debits $35,177,800 vs credits $32,345,000 yield raw gap `+$2,832,800.00`. | Exact decimal summation script. |
| **Summary Parent Classification** | Classed `3300` (Equity) as an `"expense"` | `3300` is `account_type == "Equity"` with children `3310` and `3320`. | Dynamic account type in `DQ_COA_NON_HEADER_PARENT`. |

### Architectural Lesson Learned
Generative models frequently "fill in the blanks" from general corporate accounting patterns rather than reading exact file contents. To eliminate documentation and implementation drift, we instituted `src/tools/profile_data.py`, an automated Python profiling utility that extracts ground-truth figures directly from the source CSV and JSON files, guaranteeing that documentation and tests reflect actual data.

---

## 4. AI-Generated Code and Pair-Programming Artifacts

The Antigravity AI pair programming assistant was utilized across the project lifecycle for:
- Authoring strongly-typed domain schemas (`src/models.py`, `src/config.py`).
- Implementing the multi-currency data quality checks (`src/dq_checks.py`).
- Building the deterministic validation engine, period parser, and balance sufficiency rules (`src/journal.py`).
- Authoring the pluggable LLM provider hierarchy, candidate ranking engine, and run trace logger (`src/llm.py`, `src/resolver.py`).
- Implementing the SQLite transactional review store and HITL service (`src/store.py`, `src/hitl.py`).
- Developing the comprehensive pytest regression test suite (`tests/`).
- Authoring the ground-truth data profiling tool (`src/tools/profile_data.py`).
