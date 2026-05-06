"""Demo 3 — Use the graph + world state to make autonomous decisions.

Run:
    python -m demos.demo_decide
"""

from __future__ import annotations

import json
from pathlib import Path

from dkg.decision_engine import DecisionEngine
from dkg.graph_builder import build_graph


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]

    g = build_graph(project_root / "data" / "us_pharma_dkg.json")

    with open(project_root / "data" / "world_state.json") as f:
        world = json.load(f)
    world["active_exceptions"] = set(world["active_exceptions"])

    engine = DecisionEngine(g, world)

    print("Running autonomous decisions across the BU...")
    print(f"Active exceptions in world state: {sorted(world['active_exceptions'])}")

    # 1. Inventory: try multiple SKUs to show how DSI directive plays out
    for sku in ["SKU_001_amox_500", "SKU_002_lipitor_20", "SKU_003_oxy_10"]:
        rec = engine.run("dec_place_po", sku=sku)
        print(rec.pretty())

    # Re-run one SKU with stockout exception flipped on, to show how the same
    # decision branches under a different world state.
    print("\n\n>>> Now simulating a stockout-risk exception on the same SKU <<<")
    world_alt = {**world, "active_exceptions": world["active_exceptions"] | {"exc_stockout_risk"}}
    engine_alt = DecisionEngine(g, world_alt)
    print(engine_alt.run("dec_place_po", sku="SKU_002_lipitor_20").pretty())

    # 2. Credit decisions for three customer profiles
    for cid in ["cust_pharmacy_alpha", "cust_hospital_beta", "cust_indie_gamma"]:
        print(engine.run("dec_credit_limit", customer_id=cid).pretty())

    # 3. Collections — sweep the AR aging
    print(engine.run("dec_collections_action").pretty())

    # 4. Cash coverage
    print(engine.run("dec_cash_coverage").pretty())

    # 5. Capex — three projects with different profiles
    for pid in ["proj_dc_expansion", "proj_robotics_pilot", "proj_legacy_replatform"]:
        print(engine.run("dec_capex_approval", project_id=pid).pretty())

    # 6. DEA suspicious-order reporting
    print(engine.run("dec_dea_reporting").pretty())

    # 7. Route plan
    print(engine.run("dec_route_plan").pretty())


if __name__ == "__main__":
    main()
