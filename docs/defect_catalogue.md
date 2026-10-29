# Comprehensive Accounting & Data Defect Catalogue

This document provides the definitive, verified catalogue of data quality and accounting defects discovered across all supplied input files in the `data/` directory (`trial_balance.csv`, `chart_of_accounts.csv`, `fx_rates.csv`, `prior_period_tb.csv`, `manual_adjustments.json`). Every finding has been verified by deterministic calculation and cross-referenced against standard accounting principles.

---

## 1. Summary Matrix of Findings

| ID | Source File | Finding Title | Severity | Agent Disposition | Status in Prototype |
|---|---|---|---|---|---|
| **F-01** | `trial_balance.csv` / `fx_rates.csv` | Multi-Currency Reconciliation Blocked & Partial Imbalance | High | `WARNING` (`DQ_TB_RECONCILIATION_BLOCKED`, `DQ_TB_IMBALANCE_PARTIAL`) | Detected & Reported Fail-Closed |
| **F-02** | `trial_balance.csv` | Duplicate Account Row (`6310`, `USD`) | Medium | `WARNING` (`DQ_TB_DUPLICATE_ACCOUNT`) | Detected & Aggregated |
| **F-03** | `trial_balance.csv` | Trial Balance Account Missing from COA (`9999`) | High | `WARNING` (`DQ_TB_ACCOUNT_ABSENT_COA`) | Detected & Escalated |
| **F-04a** | `chart_of_accounts.csv` | Incomplete Cash Flow Category (`cf_category = 'TBD'`) | Low | `INFO` (`DQ_COA_CF_CATEGORY_TBD`) | Detected & Logged |
| **F-04b** | `chart_of_accounts.csv` | Header Account Missing Normal Balance (`7000`) | Low | `INFO` (`DQ_COA_EMPTY_NORMAL_BALANCE`) | Detected & Logged |
| **F-04c** | `chart_of_accounts.csv` | Leaf Header Account without Children (`1290`) | Medium | `INFO` (`DQ_COA_LEAF_HEADER`) | Enforced as Non-Postable (Condition A) |
| **F-04d** | `chart_of_accounts.csv` | Expense Account Acting as Summary Parent (`8000`) | Medium | `INFO` (`DQ_COA_EXPENSE_PARENT`) | Enforced as Non-Postable (Condition B) |
| **F-05** | `prior_period_tb.csv` | Prior-Period Extract Imbalance ($+\$2,980,009.80\text{ USD}$) | Medium | `WARNING` (`DQ_PPTB_IMBALANCE`) | Qualified as Incomplete BS Extract |
| **F-06** | `prior_period_tb.csv` | Prior-Period Account Absent from COA (`6905`) | Medium | `WARNING` (`DQ_PPTB_ACCOUNT_ABSENT_COA`) | Flagged for COA Transition Mapping |
| **F-07** | `fx_rates.csv` | Missing Period-End Exchange Rate (`GBP`, `period_end`) | High | `WARNING` (`DQ_RATE_MISSING`) | Fail-Closed; Blocks Full Reconciliation |
| **F-08** | `manual_adjustments.json` | Manual Adjustment JE-002 Unbalanced ($\text{Diff } +\$3,500$) | Critical | `REJECTED` (`JE_UNBALANCED`) | Rejected at Hard Gate; Approval Refused |
| **F-09** | `manual_adjustments.json` | Manual Adjustment JE-005 Unmapped Account (`6315`) | High | `QUARANTINED` (`JE_ACCOUNT_UNMAPPED`) | Quarantined; Resolved via `map-account` |
| **F-10** | `manual_adjustments.json` | Manual Adjustment JE-008 Self-Offsetting Posting (`2170`) | Medium | `QUARANTINED` (`JE_SELF_OFFSETTING`) | Quarantined; Advisory Review Required |
| **F-11** | `manual_adjustments.json` | Manual Adjustment JE-003 FX Revaluation Discrepancy | High | `QUARANTINED` (`JE_FX_MISMATCH`) | Quarantined; Recalculation Enforced |
| **F-12** | `trial_balance.csv` | Accumulated Depreciation Contra-Asset Credit Balances | Low | `INFO` (Valid Accounting Practice) | Verified Valid Negative Asset |
| **F-13** | `trial_balance.csv` | Retained Earnings Absence in Pre-Closing Extract | Low | `INFO` (Pre-Closing Convention) | Verified Valid Operating Convention |
| **F-14** | `prior_period_tb.csv` | Incomplete P&L Extract in Comparative Period | Low | `INFO` (Partial Extract) | Documented Scope Boundary |
| **F-15** | `trial_balance.csv` | Native-Currency Naive Summation Imbalance ($\$4,800$) | Medium | Rejected Interpretation | Rejected as Inappropriate Accounting |

---

## 2. Detailed Accounting Analysis of Core Findings

### Finding F-01 & F-07: Trial Balance Multi-Currency Reconciliation & Missing Rates
- **Location:** `trial_balance.csv` (62 rows across USD, EUR, GBP) and `fx_rates.csv` (7 rate rows).
- **Accounting Defect:**
  In a multi-currency ledger, raw debits and credits cannot be summed across different currency denominations. The supplied trial balance contains:
  - USD rows: Debits $\$69,833,500.00$, Credits $\$71,077,000.00$.
  - EUR rows: Debits $825,400.00\text{ EUR}$ (Cash `1110`).
  - GBP rows: Debits $413,300.00\text{ GBP}$ (Cash `1110`, Accounts Receivable `1120`, Prepaid Expenses `1140`).
  In `fx_rates.csv`, the `GBP` closing rate (`period_end`) for `2024-Q4` is absent.
- **System Action:**
  1. Detects missing rate $\rightarrow$ emits `DQ_RATE_MISSING(currency='GBP', rate_type='period_end')`.
  2. Blocks complete multi-currency trial balance reconciliation fail-closed $\rightarrow$ emits `DQ_TB_RECONCILIATION_BLOCKED`.
  3. Translates available currencies (USD at $1.0$, EUR at $1.095$) yielding translated debits of $\$70,738,313.00$ vs credits of $\$71,077,000.00$, leaving a partial gap of $-\$338,687.00\text{ USD}$. Emits `DQ_TB_IMBALANCE_PARTIAL` as a non-conclusive diagnostic, strictly preserving the independence of journal entry statuses.

---

### Finding F-02: Duplicate Account Row in Trial Balance
- **Location:** `trial_balance.csv`, Lines 49 and 50.
- **Accounting Defect:**
  Account `6310` (Software & IT Services) appears twice under currency `USD`:
  - Line 49: `6310,Software & IT Services,USD,245000.00,0.00`
  - Line 50: `6310,Software & IT Services,USD,38500.00,0.00`
- **System Action:**
  The agent detects duplicate combinations of `(account_code, currency)`, emits `DQ_TB_DUPLICATE_ACCOUNT`, and sums the amounts for trial balance aggregation.

---

### Finding F-03: Trial Balance Account Missing from Chart of Accounts
- **Location:** `trial_balance.csv`, Line 63.
- **Accounting Defect:**
  Account `9999` (`Suspense Account`, USD, Debit $2,500.00$) appears in the trial balance but is completely absent from `chart_of_accounts.csv`.
- **System Action:**
  The agent emits `DQ_TB_ACCOUNT_ABSENT_COA` and flags the account for finance review.

---

### Finding F-04c & F-04d: Non-Postable Header and Summary Parent Accounts
- **Location:** `chart_of_accounts.csv`, Lines 19 and 71.
- **Accounting Defect:**
  - Account `1290` (`Other Non-Current Assets`): Classified as `account_type == 'Header'`, but has no child accounts.
  - Account `8000` (`Income Tax Expense`): Classified as `account_type == 'Expense'`, but has child accounts `8100` (`Current Tax Expense`) and `8200` (`Deferred Tax Expense`).
- **System Action:**
  The agent enforces a two-condition posting eligibility model:
  - Condition A: Any account with `account_type == 'Header'` is non-postable (catches `1290`, `1000`, `2000`, etc.).
  - Condition B: Any account with children in the COA is non-postable (catches `8000`).
  Any journal adjustment attempting to post to either account is strictly `REJECTED` at the hard validation gate.

---

### Finding F-08: Manual Adjustment JE-002 Out of Balance
- **Location:** `manual_adjustments.json`, Entry `JE-002`.
- **Accounting Defect:**
  - Line 1: Account `6320` (Marketing & Advertising), Debit $\$28,500.00$.
  - Line 2: Account `6310` (Software & IT Services), Credit $\$25,000.00$.
  - Variance: Total Debits $(\$28,500.00) \ne$ Total Credits $(\$25,000.00)$. Discrepancy $= +\$3,500.00$.
- **System Action:**
  Fails mandatory hard validation gate `JE_UNBALANCED`. Disposition is strictly `REJECTED`. The HITL approval command strictly refuses approval on this entry.

---

### Finding F-09: Manual Adjustment JE-005 Unmapped Account
- **Location:** `manual_adjustments.json`, Entry `JE-005`.
- **Accounting Defect:**
  - Line 1 targets Account `6315` (Software Subscriptions), which is absent from `chart_of_accounts.csv`.
  - Line 2 targets Account `2110` (Accounts Payable), Credit $\$45,000.00$.
- **System Action:**
  Because `6315` is structurally formatted as a valid numeric account code, it is distinguished from prohibited accounts and assigned `QUARANTINED` with `JE_ACCOUNT_UNMAPPED`.
  Resolution requires explicit reviewer mapping via CLI `map-account --entry-id JE-005 --from-account 6315 --to-account 6310`. The service re-runs all hard validation gates before transitioning the entry to `ACCEPTED`.

---

### Finding F-10: Manual Adjustment JE-008 Self-Offsetting Journal Posting
- **Location:** `manual_adjustments.json`, Entry `JE-008`.
- **Accounting Defect:**
  - Line 1: Account `2170` (Intercompany Clearing), Debit $\$35,000.00$.
  - Line 2: Account `2170` (Intercompany Clearing), Credit $\$35,000.00$.
  - Mathematical balance is $0.00$, but both sides post to the same account.
- **System Action:**
  Detected as `JE_SELF_OFFSETTING` and assigned `QUARANTINED`. Allowed to transition to `ACCEPTED` only when an auditor provides an explicit business justification via `approve`.

---

### Finding F-11: Manual Adjustment JE-003 Foreign Exchange Revaluation Discrepancy
- **Location:** `manual_adjustments.json`, Entry `JE-003`.
- **Accounting Defect:**
  - Entry Description: `"FX revaluation of EUR cash balance to period-end rate"`.
  - Booked Lines: Debit `1110` $\$11,200.00\text{ USD}$, Credit `7310` $\$11,200.00\text{ USD}$.
  - Actual Source Data:
    - Account `1110` EUR balance in `trial_balance.csv` $= 825,400.00\text{ EUR}$.
    - EUR Opening Rate in `fx_rates.csv` $= 1.071$.
    - EUR Period-End Rate in `fx_rates.csv` $= 1.095$.
    - Rate Delta: $1.095 - 1.071 = +0.024\text{ USD/EUR}$.
    - Expected Revaluation Delta: $825,400.00 \times 0.024 = \mathbf{+\$19,809.60\text{ USD}}$.
  - Discrepancy: $\$11,200.00 - \$19,809.60 = -\$8,609.60\text{ USD}$.
- **System Action:**
  Assigned `QUARANTINED` with `JE_FX_MISMATCH`. Cannot be approved as-is; requires financial recalculation and structured resubmission.
