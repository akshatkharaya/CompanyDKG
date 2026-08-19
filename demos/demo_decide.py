"""Demo 3 — Use the graph + world state to make autonomous decisions.

All 12 decisions in the graph now have handlers. Exceptions are derived
automatically from world state via evaluate_exceptions() rather than being
toggled manually.

Run:
    python -m demos.demo_decide
"""

from __future__ import annotations

import json
from pathlib import Path

from dkg.decision_engine import DecisionEngine
from dkg.exception_evaluator import evaluate_exceptions
from dkg.graph_builder import build_graph


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]

    g = build_graph(project_root / "data" / "us_pharma_dkg.json")

    with open(project_root / "data" / "world_state.json") as f:
        world = json.load(f)

    # Derive active exceptions automatically from world state instead of
    # relying on the manually-maintained list in world_state.json.
    world["active_exceptions"] = evaluate_exceptions(g, world)
    print("Auto-detected active exceptions:")
    for exc_id in sorted(world["active_exceptions"]):
        print(f"  - {exc_id}")

    engine = DecisionEngine(g, world)

    # -------------------------------------------------------------------------
    # 1. Demand forecast — shows MAPE-based confidence decomposition
    # -------------------------------------------------------------------------
    print("\n\n>>> DEMAND PLANNING <<<")
    print(engine.run("dec_demand_forecast").pretty())

    # -------------------------------------------------------------------------
    # 2. Inventory: POs for all SKUs, then show exception branching
    # -------------------------------------------------------------------------
    print("\n\n>>> INVENTORY PURCHASING <<<")
    for sku in ["SKU_001_amox_500", "SKU_002_lipitor_20", "SKU_003_oxy_10"]:
        print(engine.run("dec_place_po", sku=sku).pretty())

    # Show how the same decision branches when the DSI directive is lifted.
    print("\n\n>>> Simulating world WITHOUT DSI directive — same SKU <<<")
    world_no_dsi = {
        **world,
        "active_exceptions": world["active_exceptions"] - {"exc_dsi_directive"},
    }
    print(DecisionEngine(g, world_no_dsi).run("dec_place_po", sku="SKU_002_lipitor_20").pretty())

    # -------------------------------------------------------------------------
    # 3. Warehouse scheduling
    # -------------------------------------------------------------------------
    print("\n\n>>> WAREHOUSE OPERATIONS <<<")
    print(engine.run("dec_warehouse_schedule").pretty())

    # -------------------------------------------------------------------------
    # 4. Credit & collections
    # -------------------------------------------------------------------------
    print("\n\n>>> CREDIT & COLLECTIONS <<<")
    for cid in ["cust_pharmacy_alpha", "cust_hospital_beta", "cust_indie_gamma"]:
        print(engine.run("dec_credit_limit", customer_id=cid).pretty())

    print(engine.run("dec_collections_action").pretty())

    # -------------------------------------------------------------------------
    # 5. Pricing
    # -------------------------------------------------------------------------
    print("\n\n>>> PRICING & CONTRACTS <<<")
    print(engine.run("dec_price_change").pretty())

    # -------------------------------------------------------------------------
    # 6. Supplier negotiation
    # -------------------------------------------------------------------------
    print("\n\n>>> SUPPLIER MANAGEMENT <<<")
    for sup_id in ["sup_pfizer", "sup_teva", "sup_mckesson_mfg"]:
        print(engine.run("dec_supplier_negotiation", supplier_id=sup_id).pretty())

    # -------------------------------------------------------------------------
    # 7. Treasury: cash coverage, 30-day cash forecast, capex
    # -------------------------------------------------------------------------
    print("\n\n>>> TREASURY & FP&A <<<")
    print(engine.run("dec_cash_coverage").pretty())
    print(engine.run("dec_cash_forecast").pretty())

    for pid in ["proj_dc_expansion", "proj_robotics_pilot", "proj_legacy_replatform"]:
        print(engine.run("dec_capex_approval", project_id=pid).pretty())

    # -------------------------------------------------------------------------
    # 8. Compliance & logistics
    # -------------------------------------------------------------------------
    print("\n\n>>> COMPLIANCE & LOGISTICS <<<")
    print(engine.run("dec_dea_reporting").pretty())
    print(engine.run("dec_route_plan").pretty())


if __name__ == "__main__":
    main()
