"""Ground-truth data profiler script for ERP Financial Reporting Agent.

Computes exact numbers directly from data/ CSV and JSON files so that documentation
and defect catalogue never drift from the underlying ledger files.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path


def profile_all_data(data_dir: Path) -> dict[str, any]:
    """Inspects all input files and computes verified ground-truth figures."""
    results = {}

    # 1. Trial Balance (trial_balance.csv)
    tb_file = data_dir / "trial_balance.csv"
    tb_rows = []
    with open(tb_file, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["line_num"] = reader.line_num
            tb_rows.append(row)

    tb_debits = sum(Decimal(r["debit"]) for r in tb_rows)
    tb_credits = sum(Decimal(r["credit"]) for r in tb_rows)
    tb_gap = tb_debits - tb_credits

    tb_6310_rows = [r for r in tb_rows if r["account_code"] == "6310"]
    tb_9999_rows = [r for r in tb_rows if r["account_code"] == "9999"]
    tb_1110_rows = [r for r in tb_rows if r["account_code"] == "1110"]
    tb_1210_row = next((r for r in tb_rows if r["account_code"] == "1210"), None)
    tb_3200_row = next((r for r in tb_rows if r["account_code"] == "3200"), None)

    results["trial_balance"] = {
        "total_rows": len(tb_rows),
        "total_debits": tb_debits,
        "total_credits": tb_credits,
        "raw_gap": tb_gap,
        "account_6310": {
            "occurrences": len(tb_6310_rows),
            "rows": [r["line_num"] for r in tb_6310_rows],
            "amounts": [Decimal(r["debit"]) for r in tb_6310_rows],
            "total_debit": sum(Decimal(r["debit"]) for r in tb_6310_rows),
        },
        "account_9999": {
            "name": tb_9999_rows[0]["account_name"] if tb_9999_rows else "",
            "debit": Decimal(tb_9999_rows[0]["debit"]) if tb_9999_rows else Decimal("0"),
        },
        "cash_1110": {
            r["currency"]: Decimal(r["debit"]) for r in tb_1110_rows
        },
        "ppe_1210_debit": Decimal(tb_1210_row["debit"]) if tb_1210_row else Decimal("0"),
        "re_3200_credit": Decimal(tb_3200_row["credit"]) if tb_3200_row else Decimal("0"),
    }

    # 2. Chart of Accounts (chart_of_accounts.csv)
    coa_file = data_dir / "chart_of_accounts.csv"
    coa_rows = []
    with open(coa_file, encoding="utf-8-sig") as f:
        coa_rows = list(csv.DictReader(f))

    coa_map = {r["account_code"]: r for r in coa_rows}
    parents = {r.get("parent_code", "").strip() for r in coa_rows if r.get("parent_code")}

    non_header_parents = [
        r["account_code"] for r in coa_rows
        if r["account_type"] != "Header" and r["account_code"] in parents
    ]

    pl_with_cf = [
        r["account_code"] for r in coa_rows
        if r["statement"] == "PL" and r.get("cf_category") and r.get("cf_category") not in ("", "None", "TBD")
    ]
    equity_without_cf = [
        r["account_code"] for r in coa_rows
        if r["account_code"] in ("3200", "3300") and (not r.get("cf_category") or r.get("cf_category") in ("", "None"))
    ]

    results["chart_of_accounts"] = {
        "total_accounts": len(coa_rows),
        "name_6310": coa_map.get("6310", {}).get("account_name", ""),
        "name_6300": coa_map.get("6300", {}).get("account_name", ""),
        "type_3300": coa_map.get("3300", {}).get("account_type", ""),
        "type_8000": coa_map.get("8000", {}).get("account_type", ""),
        "non_header_parents": sorted(non_header_parents),
        "pl_with_cf_category": sorted(pl_with_cf),
        "equity_without_cf_category": sorted(equity_without_cf),
    }

    # 3. Manual Adjustments (manual_adjustments.json)
    adj_file = data_dir / "manual_adjustments.json"
    with open(adj_file, encoding="utf-8") as f:
        adj_data = json.load(f)

    entries = adj_data.get("entries", [])
    results["manual_adjustments"] = {
        "period": adj_data.get("period", ""),
        "functional_currency": adj_data.get("functional_currency", ""),
        "total_entries": len(entries),
        "entries": {
            e["id"]: {
                "description": e.get("description", ""),
                "date": e.get("date", ""),
                "lines": [
                    {
                        "account": l["account"],
                        "debit": Decimal(str(l.get("debit", 0))),
                        "credit": Decimal(str(l.get("credit", 0))),
                    }
                    for l in e.get("lines", [])
                ],
            }
            for e in entries
        },
    }

    # 4. Prior Period Trial Balance (prior_period_tb.csv)
    pp_file = data_dir / "prior_period_tb.csv"
    pp_rows = []
    with open(pp_file, encoding="utf-8-sig") as f:
        pp_rows = list(csv.DictReader(f))

    pp_debits = sum(Decimal(r["debit"]) for r in pp_rows)
    pp_credits = sum(Decimal(r["credit"]) for r in pp_rows)
    pp_gap = pp_debits - pp_credits
    pp_1210_row = next((r for r in pp_rows if r["account_code"] == "1210"), None)
    pp_6905_row = next((r for r in pp_rows if r["account_code"] == "6905"), None)

    results["prior_period_tb"] = {
        "total_rows": len(pp_rows),
        "total_debits": pp_debits,
        "total_credits": pp_credits,
        "raw_gap": pp_gap,
        "ppe_1210_debit": Decimal(pp_1210_row["debit"]) if pp_1210_row else Decimal("0"),
        "has_6905": pp_6905_row is not None,
        "name_6905": pp_6905_row.get("account_name", "") if pp_6905_row else "",
    }

    # 5. FX Rates (fx_rates.csv)
    fx_file = data_dir / "fx_rates.csv"
    fx_rows = []
    with open(fx_file, encoding="utf-8-sig") as f:
        fx_rows = list(csv.DictReader(f))

    fx_rates_map = {(r["currency"], r["rate_type"]): Decimal(r["rate"]) for r in fx_rows}
    results["fx_rates"] = {
        "eur_opening": fx_rates_map.get(("EUR", "opening")),
        "eur_period_average": fx_rates_map.get(("EUR", "period_average")),
        "eur_period_end": fx_rates_map.get(("EUR", "period_end")),
        "gbp_opening": fx_rates_map.get(("GBP", "opening")),
        "gbp_period_average": fx_rates_map.get(("GBP", "period_average")),
        "gbp_period_end": fx_rates_map.get(("GBP", "period_end")),  # None!
    }

    return results


def print_profile_summary(profile: dict[str, any]) -> None:
    """Prints a clear summary of ground truth numbers."""
    print("=" * 70)
    print("VERIFIED SOURCE DATA PROFILE (GROUND TRUTH)")
    print("=" * 70)
    tb = profile["trial_balance"]
    print(f"Trial Balance:")
    print(f"  Raw Debits:  ${tb['total_debits']:,.2f}")
    print(f"  Raw Credits: ${tb['total_credits']:,.2f}")
    print(f"  Raw Gap:     ${tb['raw_gap']:,.2f}")
    print(f"  6310 Rows:   {tb['account_6310']['rows']} with amounts {tb['account_6310']['amounts']} (total ${tb['account_6310']['total_debit']:,.2f})")
    print(f"  9999 Row:    {tb['account_9999']['name']} with debit ${tb['account_9999']['debit']:,.2f}")
    print(f"  1110 Cash:   USD ${tb['cash_1110'].get('USD', 0):,.2f}, EUR {tb['cash_1110'].get('EUR', 0):,.2f}, GBP {tb['cash_1110'].get('GBP', 0):,.2f}")
    print(f"  1210 PPE:    ${tb['ppe_1210_debit']:,.2f}")
    print(f"  3200 RE:     ${tb['re_3200_credit']:,.2f}")

    coa = profile["chart_of_accounts"]
    print(f"\nChart of Accounts:")
    print(f"  6310 Name:   {coa['name_6310']}")
    print(f"  6300 Name:   {coa['name_6300']}")
    print(f"  Non-Header Parents: {coa['non_header_parents']}")
    print(f"  P&L with CF Categories: {coa['pl_with_cf_category']}")
    print(f"  Equity without CF Categories: {coa['equity_without_cf_category']}")

    pp = profile["prior_period_tb"]
    print(f"\nPrior Period TB:")
    print(f"  Raw Debits:  ${pp['total_debits']:,.2f}")
    print(f"  Raw Credits: ${pp['total_credits']:,.2f}")
    print(f"  Raw Gap:     ${pp['raw_gap']:,.2f}")
    print(f"  1210 PPE:    ${pp['ppe_1210_debit']:,.2f} (jump to current: +${tb['ppe_1210_debit'] - pp['ppe_1210_debit']:,.2f})")
    print(f"  6905 Exists: {pp['has_6905']} ('{pp['name_6905']}')")

    fx = profile["fx_rates"]
    print(f"\nFX Rates:")
    print(f"  EUR Opening: {fx['eur_opening']}, Period-End: {fx['eur_period_end']}")
    print(f"  GBP Opening: {fx['gbp_opening']}, Period-End: {fx['gbp_period_end']}")


if __name__ == "__main__":
    base_data = Path(__file__).resolve().parent.parent.parent / "data"
    p = profile_all_data(base_data)
    print_profile_summary(p)
