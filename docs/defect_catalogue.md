# Comprehensive Accounting & Data Defect Catalogue

This document provides the definitive, verified catalogue of data quality and accounting defects discovered across all supplied input files in the `data/` directory (`trial_balance.csv`, `chart_of_accounts.csv`, `fx_rates.csv`, `prior_period_tb.csv`, `manual_adjustments.json`). Every finding has been verified by deterministic calculation using `src/tools/profile_data.py` and cross-referenced against standard accounting principles.

---

## 1. Summary Matrix of Findings

| ID | Source File | Finding Title | Severity | Agent Disposition | Status in Prototype |
|---|---|---|---|---|---|
| **F-01** | `trial_balance.csv` / `fx_rates.csv` | Multi-Currency Reconciliation Blocked & Partial Imbalance | High | `WARNING` (`DQ_TB_RECONCILIATION_BLOCKED`, `DQ_TB_IMBALANCE_PARTIAL`) | Detected & Reported Fail-Closed |
| **F-02** | `trial_balance.csv` | Duplicate Account Row (`6310`, `USD`, Rows 48 & 49) | Medium | `WARNING` (`DQ_TB_DUPLICATE_ACCOUNT`) | Flagged: Needs Human Confirmation |
| **F-03** | `trial_balance.csv` | Trial Balance Account Missing from COA (`9999`, Debit $12,400.00) | High | `WARNING` (`DQ_TB_ACCOUNT_ABSENT_COA`) | Detected & Escalated |
| **F-04a** | `chart_of_accounts.csv` | Incomplete Cash Flow Category (`cf_category = 'TBD'`) | Low | `INFO` (`DQ_COA_CF_CATEGORY_TBD`) | Detected & Logged |
| **F-04b** | `chart_of_accounts.csv` | Header Account Missing Normal Balance (`7000`) | Low | `INFO` (`DQ_COA_EMPTY_NORMAL_BALANCE`) | Detected & Logged |
| **F-04c** | `chart_of_accounts.csv` | Leaf Header Account without Children (`1290`) | Medium | `INFO` (`DQ_COA_LEAF_HEADER`) | Enforced as Non-Postable (Condition A) |
| **F-04d** | `chart_of_accounts.csv` | Non-Header Accounts Acting as Summary Parents (`3300`, `8000`) | Medium | `INFO` (`DQ_COA_NON_HEADER_PARENT`) | Enforced as Non-Postable (Condition B) |
| **F-04e** | `chart_of_accounts.csv` | Cash Flow Category Mismatches (P&L with CF, Equity without CF) | Low | `INFO` (`DQ_COA_CF_CATEGORY_MISMATCH`) | Detected & Logged |
| **F-05** | `prior_period_tb.csv` | Prior-Period Extract Imbalance ($+\$2,832,800.00\text{ USD}$) | Medium | `WARNING` (`DQ_PPTB_IMBALANCE`) | Qualified as Incomplete BS Extract |
| **F-06** | `prior_period_tb.csv` | Prior-Period Account Absent from COA (`6905` "Sundry Operating Expenses") | Medium | `WARNING` (`DQ_PPTB_ACCOUNT_ABSENT_COA`) | Flagged for COA Transition Mapping |
| **F-07** | `fx_rates.csv` | Missing Period-End Exchange Rate (`GBP`, `period_end`) | High | `WARNING` (`DQ_RATE_MISSING`) | Fail-Closed; Blocks Full Reconciliation |
| **F-08** | `manual_adjustments.json` | Manual Adjustment JE-002 Unbalanced ($\text{Diff } +\$3,500.00$) | Critical | `REJECTED` (`JE_UNBALANCED`) | Rejected at Hard Gate; Approval Refused |
| **F-09** | `manual_adjustments.json` | Manual Adjustment JE-005 Unmapped Account (`6315`) | High | `QUARANTINED` (`JE_ACCOUNT_UNMAPPED`) | Quarantined; Resolved via `map-account` |
| **F-10** | `manual_adjustments.json` | Manual Adjustment JE-008 Self-Offsetting Posting (`2170`, $\$320,000.00$) | Medium | `QUARANTINED` (`JE_SELF_OFFSETTING`) | Quarantined; Advisory Review Required |
| **F-11** | `manual_adjustments.json` | Manual Adjustment JE-003 FX Revaluation Discrepancy ($-\$8,609.60$) | High | `QUARANTINED` (`JE_FX_MISMATCH`) | Quarantined; Recalculation Enforced |
| **F-12** | `trial_balance.csv` / `prior_period_tb.csv` | PPE Cost Jump (+$5,207,700.00 from $11.8M to $17.0M) | Medium | `WARNING` (`DQ_PPE_COST_JUMP`) | Diagnostic Flag for Fixed Asset Audit |
| **F-13** | `trial_balance.csv` | Pre-Closing Retained Earnings Present with Open P&L | Medium | `WARNING` (`DQ_TB_PRE_CLOSING_RETAINED_EARNINGS`) | Diagnostic Flag for SOCIE Opening Equity |
| **F-14** | `trial_balance.csv` | Currency Semantics Ambiguity (Raw Gap -$4,800 vs Translated Gap) | High | `WARNING` (`DQ_TB_CURRENCY_AMBIGUITY`) | Escalated for Controller Decision |
| **F-15** | `manual_adjustments.json` | Out-of-Period Adjustment Date Check | High | `QUARANTINED` (`JE_DATE_OUT_OF_PERIOD`) | Enforced Dynamically from Period Header |
| **F-16** | `manual_adjustments.json` | Balance-Sufficiency Rule & Contra-Account Preservation | High | `QUARANTINED` (`JE_EXCEEDS_BALANCE`) | Enforced using COA Normal Balance |

---

## 2. Detailed Accounting Analysis of Core Findings

### Finding F-01 & F-07: Trial Balance Multi-Currency Reconciliation & Missing Rates
- **Location:** `trial_balance.csv` (62 rows across USD, EUR, GBP) and `fx_rates.csv` (7 rate rows).
- **Verified Source Data:**
  - Raw Debits: $\$71,072,200.00$
  - Raw Credits: $\$71,077,000.00$
  - Raw Naive Gap: $-\$4,800.00$
  - Breakdown by currency:
    - USD rows: Debits $\$69,833,500.00$, Credits $\$71,077,000.00$.
    - EUR rows: Debits $825,400.00\text{ EUR}$ (Cash `1110`).
    - GBP rows: Debits $412,300.00\text{ GBP}$ (Cash `1110` - one single row, not three rows).
  - In `fx_rates.csv`, the `GBP` closing rate (`period_end`) for `2024-Q4` is absent.
- **Accounting Defect & System Action:**
  1. Detects missing rate $\rightarrow$ emits `DQ_RATE_MISSING(currency='GBP', rate_type='period_end')`.
  2. Blocks complete multi-currency trial balance reconciliation fail-closed $\rightarrow$ emits `DQ_TB_RECONCILIATION_BLOCKED`.
  3. Translates available currencies (USD at $1.0$, EUR at $1.095$) yielding translated debits of $\$70,738,313.00$ vs credits of $\$71,077,000.00$, leaving a partial gap of $-\$338,687.00\text{ USD}$. Emits `DQ_TB_IMBALANCE_PARTIAL` as a non-conclusive diagnostic, strictly preserving the independence of journal entry statuses.

---

### Finding F-02: Duplicate Account Row in Trial Balance
- **Location:** `trial_balance.csv`, Lines 48 and 49.
- **Verified Source Data:**
  Account `6310` ("Travel and Entertainment", NOT "Software & IT Services") appears twice under currency `USD`:
  - Line 48: `6310,Travel and Entertainment,USD,245000.00,0.00`
  - Line 49: `6310,Travel and Entertainment,USD,38500.00,0.00`
  - Total debits: $\$283,500.00$.
- **Accounting Defect & System Action:**
  The agent detects duplicate combinations of `(account_code, currency)`, emits `DQ_TB_DUPLICATE_ACCOUNT`, and reports both row numbers (48 and 49) and amounts ($245,000.00 and $38,500.00) with diagnostic note: `"duplicate or split - needs human confirmation"`. It does not silently aggregate without warning.

---

### Finding F-03: Trial Balance Account Missing from Chart of Accounts
- **Location:** `trial_balance.csv`, Line 62.
- **Verified Source Data:**
  Account `9999` (`Suspense - Unmapped`, USD, Debit $\$12,400.00$, NOT $\$2,500.00$) appears in the trial balance but is completely absent from `chart_of_accounts.csv`.
- **System Action:**
  The agent emits `DQ_TB_ACCOUNT_ABSENT_COA` and flags the account for finance review.

---

### Finding F-04c & F-04d: Non-Postable Header and Summary Parent Accounts
- **Location:** `chart_of_accounts.csv`, Lines 19, 39, and 71.
- **Verified Source Data:**
  - Account `1290` (`Other Non-Current Assets`): Classified as `account_type == 'Header'`, but has no child accounts.
  - Account `3300` (`Other Comprehensive Income`): Classified as `account_type == 'Equity'`, but has child accounts `3310` and `3320`.
  - Account `8000` (`Income Tax Expense`): Classified as `account_type == 'Expense'`, but has child accounts `8100` (`Current Tax Expense`) and `8200` (`Deferred Tax Expense`).
- **System Action:**
  The agent enforces a two-condition posting eligibility model:
  - Condition A: Any account with `account_type == 'Header'` is non-postable (catches `1290`, `1000`, `2000`, etc.).
  - Condition B: Any account with children in the COA is non-postable (catches `3300` and `8000`).
  Emits `DQ_COA_NON_HEADER_PARENT` for `3300` (Equity) and `8000` (Expense) using their dynamic account types. Any journal adjustment attempting to post to either account is strictly `REJECTED` at the hard validation gate.

---

### Finding F-04e: Cash Flow Category Inconsistencies in COA
- **Location:** `chart_of_accounts.csv`.
- **Verified Source Data:**
  - P&L accounts carrying a `cf_category`: `6500`, `6510`, `6600`, `7300`, `7310`, `7400`, `8100`, `8200`.
  - Equity accounts without a `cf_category`: `3200` (`Retained Earnings`), `3300` (`Other Comprehensive Income`).
- **System Action:**
  Emits `DQ_COA_CF_CATEGORY_MISMATCH` flagging that cash flow statement classification should align with statement type conventions.

---

### Finding F-05: Prior-Period Trial Balance Imbalance
- **Location:** `prior_period_tb.csv` (30 rows).
- **Verified Source Data:**
  - Raw Debits: $\$35,177,800.00$
  - Raw Credits: $\$32,345,000.00$
  - Raw Gap: $+\$2,832,800.00$ (NOT $\$2,980,009.80$).
- **Accounting Defect & System Action:**
  The extract contains only balance sheet accounts with zero revenue or expense rows. The agent emits `DQ_PPTB_IMBALANCE` and notes that comparative equity analysis requires post-closing retained earnings adjustments.

---

### Finding F-06: Prior-Period Account Absent from COA
- **Location:** `prior_period_tb.csv`, Line 30.
- **Verified Source Data:**
  Account `6905` (`Sundry Operating Expenses`, Debit $\$45,200.00$) appears in the prior period extract but is absent from the current COA. Cross-referencing reveals it was renumbered to `6900` (`Other Operating Expenses`).
- **System Action:**
  Emits `DQ_PPTB_ACCOUNT_ABSENT_COA` to alert the reporting agent to map comparative balances from `6905` to `6900`.

---

### Finding F-08: Manual Adjustment JE-002 Out of Balance
- **Location:** `manual_adjustments.json`, Entry `JE-002`.
- **Verified Source Data:**
  - Line 1: Account `6300` (Marketing and Advertising), Debit $\$28,500.00$.
  - Line 2: Account `6310` (Travel and Entertainment), Credit $\$25,000.00$.
  - Variance: Total Debits $(\$28,500.00) \ne$ Total Credits $(\$25,000.00)$. Discrepancy $= +\$3,500.00$.
- **System Action:**
  Fails mandatory hard validation gate `JE_UNBALANCED`. Disposition is strictly `REJECTED`. The HITL approval command strictly refuses approval on this entry.

---

### Finding F-09: Manual Adjustment JE-005 Unmapped Account
- **Location:** `manual_adjustments.json`, Entry `JE-005`.
- **Verified Source Data:**
  - Line 1: Account `6315` (Software Subscriptions), Debit $\$18,500.00$ (`6315` not in COA).
  - Line 2: Account `6310` (Travel and Entertainment), Credit $\$18,500.00$.
  - (Note: Does not touch account 2110 or $45,000).
- **System Action:**
  Assigned `QUARANTINED` with `JE_ACCOUNT_UNMAPPED`.
  Resolution workflow:
  - Task A generates top-5 postable candidates (`6310`, `6300`, `6230`, `6220`, `6210`).
  - Mapping to `6310` creates self-offsetting entry (`6310` Dr $18.5k / `6310` Cr $18.5k) and remains `QUARANTINED` with `JE_SELF_OFFSETTING`.
  - Mapping to valid alternative `6300` passes full revalidation and transitions to `ACCEPTED`.

---

### Finding F-10: Manual Adjustment JE-008 Self-Offsetting Journal Posting
- **Location:** `manual_adjustments.json`, Entry `JE-008`.
- **Verified Source Data:**
  - Line 1: Account `2170` (Intercompany Clearing), Debit $\$320,000.00$.
  - Line 2: Account `2170` (Intercompany Clearing), Credit $\$320,000.00$.
  - (Note: Amount is $320,000.00, NOT $35,000).
- **System Action:**
  Detected as `JE_SELF_OFFSETTING` and assigned `QUARANTINED`. Allowed to transition to `ACCEPTED` only when an auditor provides an explicit business justification via `approve`.

---

### Finding F-11: Manual Adjustment JE-003 Foreign Exchange Revaluation Discrepancy
- **Location:** `manual_adjustments.json`, Entry `JE-003`.
- **Verified Source Data:**
  - Description: `"FX revaluation of EUR cash balance to period-end rate"`.
  - Booked Lines: Debit `1110` $\$11,200.00\text{ USD}$, Credit `7310` $\$11,200.00\text{ USD}$.
  - Source Balance: Account `1110` EUR in `trial_balance.csv` $= 825,400.00\text{ EUR}$.
  - Rates: Opening $1.071$, Period-End $1.095$. Delta $= +0.024\text{ USD/EUR}$.
  - Expected Revaluation Delta: $825,400.00 \times 0.024 = \mathbf{+\$19,809.60\text{ USD}}$.
  - Discrepancy: $\$11,200.00 - \$19,809.60 = -\$8,609.60\text{ USD}$.
- **System Action:**
  Assigned `QUARANTINED` with `JE_FX_MISMATCH`. Cannot be approved as-is; requires financial recalculation and structured resubmission.

---

### Finding F-12: PPE Cost Jump
- **Location:** `trial_balance.csv` and `prior_period_tb.csv`, Account `1210` (`Property, Plant and Equipment - Cost`).
- **Verified Source Data:**
  - Prior Period: $\$11,800,000.00$
  - Current Period: $\$17,007,700.00$
  - Jump: $+\$5,207,700.00$ ($+44.1\%$).
- **System Action:**
  Emits `DQ_PPE_COST_JUMP` diagnostic for fixed asset additions audit.

---

### Finding F-13: Pre-Closing Retained Earnings in Active Trial Balance
- **Location:** `trial_balance.csv`, Account `3200` (`Retained Earnings`).
- **Verified Source Data:**
  - Account `3200` credit balance: $\$7,240,000.00$.
  - Current Period P&L Accounts are still open with interim net income of approximately $\$5.98\text{M}$.
- **System Action:**
  Emits `DQ_TB_PRE_CLOSING_RETAINED_EARNINGS` diagnostic noting that SOCIE opening equity requires human confirmation on whether 3200 represents pre-closing or post-closing retained earnings.

---

### Finding F-14: Trial Balance Currency Semantics Ambiguity
- **Location:** `trial_balance.csv` multi-currency cash balances.
- **Verified Source Data:**
  - Naive raw summation gap: $-\$4,800.00$
  - Partial translated gap (using EUR period_end rate 1.095 only): $-\$338,687.00$
  - Illustrative sensitivity (assuming GBP at opening rate 1.251, illustrative only): $+\$177,100.30$
- **System Action:**
  Emits `DQ_TB_CURRENCY_AMBIGUITY` reporting both raw gap ($-4,800$) and partial translated gap ($-338,687$), clearly labelling that GBP period_end rate is missing and any sensitivity rate is illustrative. Escalates finding for controller input.
