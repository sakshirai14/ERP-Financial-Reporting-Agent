# ERP Financial Reporting Agent (Manual Adjustments Prototype)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests Passing](https://img.shields.io/badge/tests-37%2F37%20passed-brightgreen.svg)]()
[![Deterministic Accounting](https://img.shields.io/badge/accounting-deterministic%20Decimal-success.svg)]()
[![Zero False Accepts](https://img.shields.io/badge/false%20accepts-0.0%25-brightgreen.svg)]()

A production-minded, deterministic financial reporting agent designed to ingest, validate, diagnose, and govern unposted manual journal adjustments against enterprise ERP ledger extracts.

---

## 1. Key Capabilities & Architectural Principles

- **100% Deterministic Accounting Enforcement:** All financial calculations are implemented using Python fixed-scale `Decimal` arithmetic. The LLM is strictly decoupled from accounting decisions and functions solely as a narrative explanation layer.
- **Fail-Closed Verification:** Missing exchange rates, incomplete ledger extracts, or ambiguous currency rows block full reconciliation fail-closed rather than inventing synthetic numbers.
- **Inviolable Hard Integrity Gates:** Broken double-entry equations, non-existent accounts, and postings to non-postable Header/Summary accounts cannot be bypassed by reviewer approval commands.
- **Order-Independent Duplicate Handling:** In-batch duplicate detection scans batches symmetrically; all conflicting occurrences are quarantined symmetrically with code `JE_INBATCH_DUPLICATE`, eliminating row-order dependency.
- **Atomic HITL Review & Audit Trails:** SQLite review store ensures ACID-compliant transaction rollbacks on failure, maintaining immutable audit logs of all reviewer actions, notes, and account mappings.
- **Enforceable Recursive Policy Fingerprinting:** Every decision-relevant configuration setting is hashed into a canonical SHA-256 fingerprint, automatically invalidating stale review decisions if policies drift.
- **Offline-by-Default:** Operates 100% offline with a deterministic mock explanation engine; optional Gemini API integration available via environment variables.

---

## 2. Directory Structure

```
ERP Financial Reporting Agent/
├── data/                                 # Pristine original data files (strictly read-only)
│   ├── chart_of_accounts.csv             # Hierarchical COA (72 accounts)
│   ├── fx_rates.csv                      # Period FX rates (opening, average, closing)
│   ├── manual_adjustments.json           # Batch of 10 unposted manual adjustments
│   ├── prior_period_tb.csv               # Prior period comparative trial balance
│   ├── trial_balance.csv                 # Period-end multi-currency trial balance
│   └── README.md                         # Input data conventions and seeded defect notes
├── src/
│   ├── __init__.py
│   ├── audit.py                          # Audit logging, report rendering, run trace generation
│   ├── cli.py                            # Unified CLI interface (run, validate, approve, map-account, etc.)
│   ├── config.py                         # Configuration dataclasses, canonical fingerprint dict, SHA-256 hasher
│   ├── dq_checks.py                      # Data quality diagnostics and currency-safe TB reconciliation
│   ├── hitl.py                           # Human-in-the-Loop review service and atomic mapping workflow
│   ├── ingest.py                         # SHA-256 binary hashing, Decimal parsing, CoaIndex
│   ├── journal.py                        # Manual adjustments engine: hard gates, duplicate lifecycle, FX contract
│   ├── llm.py                            # Narrative explanation layer (deterministic mock default)
│   ├── models.py                         # Domain models, enums, Pydantic schemas, execution results
│   ├── resolver.py                       # RapidFuzz fuzzy account suggestion engine
│   ├── store.py                          # SQLite review store and atomic transaction manager
│   ├── workflow.py                       # Pipeline orchestrator and rerun idempotency manager
│   └── tools/
│       ├── __init__.py
│       └── generate_expected.py          # Offline utility for baseline generation (manual invocation only)
├── tests/
│   ├── __init__.py
│   ├── conftest.py                       # Pytest fixtures and test data loaders
│   ├── fixtures/
│   │   └── expected_statuses.json        # Committed, reviewed regression baseline
│   ├── test_baseline.py                  # Baseline integrity and hash match tests
│   ├── test_coa_eligibility.py           # Posting eligibility Condition A and B tests
│   ├── test_dq_checks.py                 # Currency-safe TB tests, diagnostic independence tests
│   ├── test_duplicate_lifecycle.py       # Order-independent in-batch duplicate tests
│   ├── test_fx_revaluation.py            # FX contract, precedence, tolerance, and direction tests
│   ├── test_hitl_mapping.py              # Atomic mapping, failure mode, and audit history tests
│   ├── test_hitl_review.py               # Approval gates, hard-gate bypass prevention tests
│   ├── test_ingest.py                    # File parsing, hash computation, Decimal scale tests
│   └── test_policy_fingerprint.py        # Recursive fingerprint coverage and invalidation tests
├── docs/
│   ├── AI_USAGE.md                       # Comprehensive disclosure of AI tools and prompts
│   ├── ARCHITECTURE.md                   # System architecture, data flow diagrams, domain models
│   ├── REFLECTION.md                     # Technical reflection, engineering trade-offs, production roadmap
│   └── defect_catalogue.md               # 18 verified data and accounting defect findings
├── output/                               # Generated run artifacts (gitignored)
│   ├── adjustments_decisions.csv         # Machine-readable validation decisions
│   ├── evaluation_report.md              # System evaluation and benchmark report
│   ├── issues_report.md                  # Comprehensive auditor markdown report
│   ├── review_queue.json                 # Quarantined entries formatted for review
│   ├── review_store.db                   # Persistent SQLite review store
│   └── run_trace.jsonl                   # Machine-readable JSONL audit event trace
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

---

## 3. Quickstart & Installation

### Prerequisites
- Python 3.11+
- Standard Unix/macOS environment

### Environment Setup
```bash
# Clone or navigate to the repository
cd "/Users/sakshirai/ERP Financial Reporting Agent"

# Initialize virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Running the Agent

### A. Run Full Pipeline
Executes ingestion, hashing, data quality diagnostics, adjustments validation, narrative explanation, and artifact generation:
```bash
python -m src.cli run
```

### B. Validate Adjustments Batch
Validates the adjustments batch and prints a terminal summary table:
```bash
python -m src.cli validate
```

### C. Human Review & Workflow Commands
```bash
# 1. Map an unmapped account (e.g. JE-005 referencing unmapped 6315):
python -m src.cli map-account \
  --entry-id JE-005 \
  --from-account 6315 \
  --to-account 6310 \
  --reviewer "sakshi_rai" \
  --notes "Reclassified software subscription to IT expense account"

# 2. Approve a quarantined reviewable advisory (e.g. JE-008 self-offsetting clearing):
python -m src.cli approve \
  --entry-id JE-008 \
  --reviewer "sakshi_rai" \
  --notes "Verified legitimate intercompany clearing netting"

# 3. Reject an invalid or unreconciled entry:
python -m src.cli reject \
  --entry-id JE-003 \
  --reviewer "sakshi_rai" \
  --notes "Awaiting recalculation by finance team"

# 4. Attempting to approve a hard failure (e.g. JE-002 unbalanced) is strictly refused:
python -m src.cli approve \
  --entry-id JE-002 \
  --reviewer "sakshi_rai" \
  --notes "Attempting override"
# Output: ERROR: Approval refused for JE-002: contains hard validation failures (['JE_UNBALANCED']).
```

---

## 5. Automated Testing

Run the full pytest suite (37 automated tests covering baseline integrity, FX contracts, COA rules, duplicate detection, and atomic rollbacks):
```bash
pytest tests/ -v
```

---

## 6. Baseline Verification Contract

The regression baseline in `tests/fixtures/expected_statuses.json` contains:
- Full 64-character SHA-256 hashes of all 5 source data files.
- Authentic human review metadata: `"reviewed_by": "sakshi_rai"`, `"reviewed_date": "2026-10-29"`.
- Canonical `policy_fingerprint` matching the active configuration payload.
- Expected baseline status breakdown:
  - **6 ACCEPTED:** JE-001, JE-004, JE-006, JE-007, JE-009, JE-010
  - **1 REJECTED:** JE-002 (`JE_UNBALANCED`)
  - **3 QUARANTINED:** JE-003 (`JE_FX_MISMATCH`), JE-005 (`JE_ACCOUNT_UNMAPPED`), JE-008 (`JE_SELF_OFFSETTING`)
  - **Zero False Accepts** (0.0%).

*Note: Normal test runs (`pytest`) execute in read-only mode and NEVER regenerate the baseline. Offline baseline regeneration is strictly restricted to manual execution of `python -m src.tools.generate_expected`.*

---

## 7. Documentation Index

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): Detailed subsystem designs, data flow diagrams, and production scaling roadmap.
- [docs/defect_catalogue.md](docs/defect_catalogue.md): Comprehensive analysis of all 18 verified accounting and ledger defect findings.
- [docs/REFLECTION.md](docs/REFLECTION.md): Engineering analysis, architectural trade-offs, and lessons learned.
- [docs/AI_USAGE.md](docs/AI_USAGE.md): Complete disclosure of agentic AI pair programming and runtime LLM decoupling.
- [output/evaluation_report.md](output/evaluation_report.md): Benchmark results, accuracy metrics, and verification logs.
