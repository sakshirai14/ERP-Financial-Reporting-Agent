# Financial Reporting Agent: Batch Integrity & Accounting Issues Report

**Generated At:** 2026-10-29T14:33:37.619614+00:00
**Policy Fingerprint:** `7dfb98445e6b81fb0ba022a2b6a011fd1ca91550cb0ef0510f08ea2b4748ca12`

## Executive Summary

- **Total Journal Entries Processed:** 10
- **Accepted:** 8
- **Rejected (Hard Structural Failures):** 1
- **Quarantined (Human Review Required):** 1
- **Quarantine Rate:** 10.0%
- **Data Quality Findings (Ledger Diagnostics):** 17

---

## Journal Entry Dispositions

| Entry ID | Disposition | Issue Codes | Summary Explanation |
|---|---|---|---|
| **JE-001** | `ACCEPTED` | None | All validation gates passed cleanly. |
| **JE-002** | `REJECTED` | `JE_UNBALANCED` | Entry JE-002 violates the fundamental double-entry equation (Debits != Credits). Total debit postings do not equal total credit postings. Under standard accounting rules, unbalanced adjustments cannot be accepted or posted to the general ledger. |
| **JE-003** | `QUARANTINED` | `JE_FX_MISMATCH` | Entry JE-003 reflects an FX revaluation calculation discrepancy. The entry books $11200.0 USD, but the revaluation delta calculated from the foreign cash balance and closing exchange rate is $19809.60 USD (variance of $-8609.60 USD). The entry is quarantined for finance team reconciliation. |
| **JE-004** | `ACCEPTED` | None | All validation gates passed cleanly. |
| **JE-005** | `ACCEPTED` | `JE_ACCOUNT_UNMAPPED` | Accepted after human review by controller: Reclassified software subscription to marketing expense |
| **JE-006** | `ACCEPTED` | None | All validation gates passed cleanly. |
| **JE-007** | `ACCEPTED` | None | All validation gates passed cleanly. |
| **JE-008** | `ACCEPTED` | `JE_SELF_OFFSETTING` | Accepted after human review by auditor: Verified legitimate intercompany clearing netting |
| **JE-009** | `ACCEPTED` | None | All validation gates passed cleanly. |
| **JE-010** | `ACCEPTED` | None | All validation gates passed cleanly. |

---

## Data Quality & Ledger Diagnostics

| Code | Severity | Diagnostic Message |
|---|---|---|
| `DQ_TB_DUPLICATE_ACCOUNT` | **WARNING** | Duplicate account 6310 (USD) found on rows 48, 49 with amounts $245,000.00, $38,500.00: duplicate or split - needs human confirmation. Do not silently sum. |
| `DQ_TB_ACCOUNT_ABSENT_COA` | **WARNING** | Trial balance account 9999 (Suspense - Unmapped) is missing from Chart of Accounts. |
| `DQ_RATE_MISSING` | **WARNING** | Required period_end FX rate missing for currency 'GBP'. |
| `DQ_TB_RECONCILIATION_BLOCKED` | **WARNING** | Full multi-currency trial balance reconciliation blocked due to missing period-end FX rates for: ['GBP']. Cannot verify full ledger balance without unverifiable rate assumptions. |
| `DQ_TB_IMBALANCE_PARTIAL` | **WARNING** | Non-conclusive partial comparison of translated USD and EUR accounts (gap $-338,687.00 USD; Debits: $70,738,313.00, Credits: $71,077,000.00). This is NOT evidence of a full ledger imbalance; complete reconciliation is blocked pending GBP/period_end FX rate. |
| `DQ_TB_CURRENCY_AMBIGUITY` | **WARNING** | Trial balance currency semantics ambiguous - ask controller: (a) Raw gap is $-4,800.00 USD (Debits: $71,072,200.00, Credits: $71,077,000.00). (b) Partial translated gap using EUR period_end rate (1.095) only is $-338,687.00 USD (clearly partial because GBP period_end rate is missing). Illustrative sensitivity assuming GBP at opening 1.251 (illustrative only): translated gap would be +$177,100.30 USD. Translating foreign cash balances moves the trial balance further from balance than the raw gap of -$4,800.00, suggesting foreign cash amounts may already be in USD. Interpretation is unresolved and needs controller input. |
| `DQ_TB_PRE_CLOSING_RETAINED_EARNINGS` | **INFO** | Retained Earnings (account 3200 balance $7,240,000.00) is presented on pre-closing trial balance while P&L accounts remain open (interim net income ~$5,994,100.00). SOCIE opening equity treatment needs a human decision. |
| `DQ_PPTB_ACCOUNT_ABSENT_COA` | **WARNING** | Prior-period account 6905 (Sundry Operating Expenses) is missing from current Chart of Accounts. |
| `DQ_PPTB_IMBALANCE` | **WARNING** | Prior-period trial balance extract imbalance: (a) Raw gap is $2,832,800.00 USD (Debits: $35,177,800.00, Credits: $32,345,000.00). (b) Translated gap is $2,980,009.80 USD (Debits: $35,325,009.80, Credits: $32,345,000.00) using opening FX rates (EUR: 1.071, GBP: 1.251, USD: 1.0). Currency ambiguity applies here as well (unclear whether foreign lines were pre-translated). Treated as partial balance-sheet extract with non-operating expenses; does not block current-period adjustments. |
| `DQ_PPE_COST_JUMP` | **INFO** | Significant period-over-period jump in Property, Plant & Equipment (account 1210): increased from $11,800,000.00 in prior period to $17,007,700.00 in current period (+$5,207,700.00 / +44.1%). Requires verification of capital additions. |
| `DQ_COA_CF_CATEGORY_TBD` | **INFO** | Account 1150 (Other Current Assets) has cf_category='TBD'. |
| `DQ_COA_LEAF_HEADER` | **INFO** | Header account 1290 (Other Assets) has no child accounts. |
| `DQ_COA_CF_CATEGORY_TBD` | **INFO** | Account 2170 (Intercompany Payable) has cf_category='TBD'. |
| `DQ_COA_NON_HEADER_PARENT` | **INFO** | Account 3300 (Accumulated OCI) has type='Equity' but acts as parent to child accounts. |
| `DQ_COA_EMPTY_NORMAL_BALANCE` | **INFO** | Header account 7000 (Non-Operating Items) has empty normal_balance. |
| `DQ_COA_NON_HEADER_PARENT` | **INFO** | Account 8000 (Income Tax Expense) has type='Expense' but acts as parent to child accounts. |
| `DQ_COA_CF_CATEGORY_MISMATCH` | **INFO** | Cash flow category mismatch in Chart of Accounts: P&L accounts (6500, 6510, 6600, 7300, 7310, 7400, 8100, 8200) carry cf_category metadata, whereas equity balance sheet accounts (3200, 3300) have none. |

---

## Input File Cryptographic Hashes

| Filename | SHA-256 Hash |
|---|---|
| `chart_of_accounts.csv` | `1139711bd276b2d443ab83b9a6461b3e1f62a26416962b0dcd0e0762028e49bf` |
| `fx_rates.csv` | `268c9b9b5a14ea5533e596ed8872f1c3b5515c92a46c9b197a10ef9f909d1893` |
| `manual_adjustments.json` | `26fcad0c0c7bda5306efea47e0c73238b2828b5d30afdd1bb0d54bb6a6704a0f` |
| `prior_period_tb.csv` | `aa1439622935eadb08ec1d0911c0c2f207fd9e71d32479326f5714a4c4180595` |
| `trial_balance.csv` | `6a5d38f4a7792e5a749d62ec34be8c2aa29f540ccf3c95caedf3e1f1b5203ca8` |
