# Clarifying Questions for Financial Leadership & Controller

Before commencing automated statutory financial statement publication from ERP extracts, an implementation engineer or architect must escalate unresolved accounting ambiguities to the Financial Controller. Below are the three critical questions that should be submitted before deployment, along with the operational assumption the prototype enforces while awaiting determination.

---

### Question 1: Ledger Currency Semantics, Imbalance Tolerance, and Foreign Exchange Rates

**The Question:**
1. In `trial_balance.csv`, raw debit and credit summation exhibits a small naive gap of $-\$4,800.00$ ($\text{Debits } \$71,072,200.00 \text{ vs Credits } \$71,077,000.00$). However, translating non-USD cash rows (EUR 825,400.00 and GBP 412,300.00) widens the imbalance to hundreds of thousands of dollars ($-\$338,687.00$ partial gap using EUR alone). Are the foreign cash amounts in the ERP extract already denominated in functional USD, or are they native balances requiring closing translation?
2. In `fx_rates.csv`, the period-end closing rate for `GBP` is missing. What is the approved closing rate for Q4 2024?
3. In `manual_adjustments.json`, entry `JE-003` books an FX gain of $\$11,200.00$ on EUR cash, whereas applying opening ($1.071$) to closing ($1.095$) rates on EUR 825,400.00 produces an expected delta of $\$19,809.60$ (variance of $-\$8,609.60$). What rate or methodology did the preparer use, or was this a calculation error?

**Prototype Assumption:**
- **Fail-Closed Multi-Currency:** The prototype assumes foreign cash balances are native denominations but refuses to invent a missing GBP closing rate. It emits `DQ_RATE_MISSING` and `DQ_TB_RECONCILIATION_BLOCKED`, blocking automated multi-currency trial balance closing fail-closed.
- **Explicit Currency Diagnostic:** Surfaces `DQ_TB_CURRENCY_AMBIGUITY`, reporting both the raw gap ($-\$4,800.00$) and partial translated gap ($-\$338,687.00$), labelling the gap unresolved without guessing.
- **FX Quarantine:** Quarantines `JE-003` with `JE_FX_MISMATCH` requiring controller recalculation; prohibits unchanged approval.

---

### Question 2: Failure Policy, Materiality Thresholds, and Provisional Publication

**The Question:**
When ledger extracts or unposted adjustments contain defects (e.g. suspense account `9999` carrying a debit of $\$12,400.00$, out-of-balance entries like `JE-002`, or unmapped accounts like `6315` in `JE-005`):
1. What is the organization's policy regarding fail-closed halts versus provisional statement generation? Is the agent permitted to publish draft financial statements with flagged line-item variances, or must output be strictly blocked until all items are reconciled?
2. What is the organization's materiality threshold for trial balance imbalances? Are small discrepancies (e.g. the $-\$4,800.00$ variance) permitted to post to an automated rounding/plug account, or is zero tolerance enforced?

**Prototype Assumption:**
- **Zero-Plugging & Fail-Closed Gates:** The prototype enforces a strict zero-tolerance failure policy. It never creates plug entries, synthetic adjustments, or rounding offsets.
- **Inviolable Rejection:** Fails unbalanced adjustments (`JE-002`) immediately at hard gates (`REJECTED`) and strictly refuses human approval overrides.
- **Human-in-the-Loop Quarantine:** Routes unmapped accounts (`JE-005`) and arithmetic mismatches (`JE-003`) to quarantine, requiring auditable human review with non-empty justification before acceptance.

---

### Question 3: Reporting Scope, Accounting Policies, and Intercompany Mechanics

**The Question:**
1. **Cash Flow Methodology:** Does the client mandate direct or indirect cash flow statement generation? In `chart_of_accounts.csv`, several P&L operating accounts carry `cf_category` metadata, while Equity accounts (`3200`, `3300`) carry none. What is the canonical cash flow mapping convention?
2. **Equity & SOCIE Articulation:** Account `3200` (`Retained Earnings`) has an active credit balance of $\$7,240,000.00$ on the current trial balance while nominal P&L accounts remain open with approximately $\$5.98\text{M}$ in net income. Furthermore, `prior_period_tb.csv` lacks P&L accounts. Does $\$7,240,000.00$ represent pre-closing opening retained earnings, and how should comparative restatements articulate into the Statement of Changes in Equity (SOCIE)?
3. **Consolidation & Intercompany Elimination:** Is this dataset a single legal entity or a consolidated multi-entity extract? Specifically, how should account `2170` (Intercompany Clearing) be governed when an adjustment (such as `JE-008`) posts equal debit and credit amounts to the same account ($320,000.00$)?

**Prototype Assumption:**
- **Slice Boundary:** Production prototype builds the Manual Adjustments Agent slice and Data Quality Diagnostic Gate; full financial statement generation (BS, P&L, Cash Flow, SOCIE) is formally architected but not automatically synthesized to avoid unverified accounting assumptions.
- **Self-Offsetting Quarantine:** Identifies identical-account debit/credit postings (`JE-008`) as `JE_SELF_OFFSETTING` (`QUARANTINED`). Requires human review with business justification to prevent artificial distortion of clearing accounts.
- **Diagnostic Transparency:** Flags pre-closing retained earnings (`DQ_TB_PRE_CLOSING_RETAINED_EARNINGS`) and cash flow category mismatches (`DQ_COA_CF_CATEGORY_MISMATCH`) as diagnostic findings for controller determination.
