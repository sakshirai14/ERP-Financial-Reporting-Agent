# AI Usage Disclosure & Agentic Integration Architecture

## 1. Executive Disclosure

This project was developed by pair-programming with an advanced agentic AI assistant (Antigravity IDE) serving as a senior financial-data systems architect and Python engineer. This document details the role of AI both during development and within the runtime system architecture.

---

## 2. AI in the Application Runtime Architecture

### 2.1 The Core Safety Mandate: Separation of LLM from Accounting Decisions
In mission-critical enterprise financial reporting, generative AI models must **never** make authoritative accounting decisions:
- Large Language Models are probabilistic and prone to hallucination, subtle arithmetic drifts, and prompt injection.
- Consequently, this prototype enforces an absolute architectural boundary:
  1. **Deterministic Accounting Engine:** 100% of accounting decisions (balancing equations, COA postability, duplicate detection, FX revaluation deltas, and journal dispositions) are executed by deterministic Python algorithms using fixed-scale `Decimal` arithmetic.
  2. **Decoupled Narrative Explanations:** The LLM's role is strictly confined to generating plain-English explanations and diagnostic summaries for human auditors. The LLM output is purely explanatory and has no write-back path to alter dispositions, amounts, or account codes.

### 2.2 Offline-by-Default Operation & Deterministic Mock Engine
- To ensure immediate, zero-dependency reproducibility in air-gapped auditor environments or CI/CD pipelines without API keys, the prototype defaults to an offline deterministic mock (`AccountingExplanationEngine`).
- If an enterprise operator configures `GEMINI_API_KEY` and sets `LLM_PROVIDER=gemini`, the system connects to the Google Generative AI API (`gemini-1.5-flash`) operating at `temperature=0` with structured output schemas to enrich audit narratives.

---

## 3. AI Usage During the Engineering Process

### 3.1 Planning & Architectural Iteration
- The implementation specification underwent 11 iterative architectural reviews (v1 through v11) to eliminate all edge-case ambiguities:
  - Reconciling genuine source data ($825,400.00\text{ EUR}$, rates $1.071/1.095$, delta $\$19,809.60$).
  - Establishing order-independent in-batch duplicate handling (symmetrical quarantine).
  - Enforcing non-bypassable hard gates during human approval.
  - Designing recursive policy fingerprinting with zero omitted fields.
  - Formalizing atomic unmapped-account mapping and candidate reclassification.

### 3.2 Automated Code & Test Generation
- AI pair programming was utilized to author:
  - Strongly-typed Pydantic schemas and dataclass models (`src/models.py`, `src/config.py`).
  - Ingestion, Decimal parsing, and SHA-256 binary hashing (`src/ingest.py`).
  - Data quality diagnostics and multi-currency reconciliation (`src/dq_checks.py`).
  - Deterministic adjustments validation engine (`src/journal.py`).
  - ACID-compliant SQLite review store and HITL service (`src/store.py`, `src/hitl.py`).
  - Comprehensive automated pytest suite covering 37 test cases (`tests/`).
  - CLI commands and markdown reporting utilities (`src/cli.py`, `src/audit.py`).
