# Technical Reflection & Engineering Analysis

### 1. What I'd Build Differently with 3 Months
Given three months, I would pivot from a file-based batch script to an event-driven ledger streaming architecture. I would replace SQLite with distributed PostgreSQL featuring row-level locking (`SELECT ... FOR UPDATE`) and temporal tables to support concurrent auditor reviews and retroactive restatements. Rather than inspecting static trial balance snapshots, the agent would connect directly to ERP subledgers via CDC pipelines, validating adjustments against live invoice and asset IDs. Finally, I would build an automated SOCIE roll-forward engine to programmatically articulate interim P&L closings into retained earnings.

### 2. Where the Prototype Breaks at Scale
At enterprise scale, this prototype encounters four concrete bottlenecks:
- **COA Complexity:** In-memory tree traversals and fuzzy candidate searches degrade as the Chart of Accounts scales from 72 accounts to tens of thousands across subsidiaries.
- **Multi-Entity Consolidation:** The engine handles a single ledger; multi-tier parent-subsidiary hierarchies with minority interests, statutory-to-GAAP bridges, and cumulative translation adjustments (CTA) require dedicated consolidation pipelines.
- **Intercompany Matching:** Flagging intra-entry netting (JE-008) is insufficient for cross-entity transactions, which require bilateral invoice matching and elimination matrices.
- **LLM Latency & Cost:** Calling LLMs on tens of thousands of monthly adjustments causes latency and cost spikes, reinforcing why audit explanations must remain template-driven.

### 3. How I Used AI Tools: Strengths and Critical Failures
I utilized AI pair-programming to accelerate authoring Pydantic schemas, data models, and pytest fixtures. However, generative AI repeatedly failed on raw accounting data. Early AI drafts hallucinated account names (calling 6310 "Software & IT Services" instead of "Travel and Entertainment"), invented balances (claiming suspense 9999 was $2,500 instead of $12,400), misstated ledger structure (claiming GBP cash spanned three rows instead of one), and created the original JE-005 bug by accepting a self-offsetting entry after mapping 6315 to 6310. These failures proved that AI must be bounded: deterministic profiling (`profile_data.py`) and Python `Decimal` arithmetic must govern accounting truth.

### 4. What the Problem Setters Are Underestimating
The problem setters underestimate the **accounting judgment layer**. Double-entry arithmetic ($\text{Debits} = \text{Credits}$) is straightforward to automate. The true difficulty lies in ambiguous accounting semantics: determining whether foreign ledger balances were pre-translated, deciding whether pre-closing trial balances require interim equity closing before feeding SOCIE, and handling missing market rates without guessing. Above all, trust and explainability matter far more than raw arithmetic. In enterprise reporting, an agent that fails closed and provides transparent audit trails for the controller is infinitely more valuable than an autonomous model that silently plugs numbers.
