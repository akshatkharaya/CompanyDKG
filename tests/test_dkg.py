"""Smoke tests for the DKG."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dkg.decision_engine import DecisionEngine
from dkg.exception_evaluator import evaluate_exceptions
from dkg.graph_builder import build_graph, summary
from dkg.queries import (
    blast_radius,
    cross_team_chains,
    get_decision_context,
    input_criticality,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "us_pharma_dkg.json"
WORLD_PATH = PROJECT_ROOT / "data" / "world_state.json"


@pytest.fixture(scope="module")
def graph():
    return build_graph(DATA_PATH)


@pytest.fixture(scope="module")
def world():
    with open(WORLD_PATH) as f:
        w = json.load(f)
    w["active_exceptions"] = set(w["active_exceptions"])
    return w


def test_graph_loads(graph):
    s = summary(graph)
    assert s["total_nodes"] > 50
    assert s["total_edges"] > 60
    assert s["node:decision"] == 12


def test_every_decision_has_owner(graph):
    decisions = [n for n, d in graph.nodes(data=True) if d["node_type"] == "decision"]
    for did in decisions:
        ctx = get_decision_context(graph, did)
        assert ctx["owners"], f"Decision {did} has no owner"


def test_blast_radius_reaches_treasury(graph):
    """Inventory PO decision should propagate to the treasury cash decision."""
    affected = blast_radius(graph, "dec_place_po", max_hops=4)
    names = [a["name"] for a in affected]
    assert any("Cash coverage" in n or "cash" in n.lower() for n in names)


def test_cross_team_chains_nonempty(graph):
    chains = cross_team_chains(graph)
    assert len(chains) > 0
    # The classic Inventory -> FP&A linkage should be present
    assert any(
        "Inventory" in c["from_team"] and "FP&A" in c["to_team"]
        for c in chains
    )


def test_input_criticality_top_input_is_well_connected(graph):
    ranking = input_criticality(graph)
    assert ranking, "No data inputs found"
    top = ranking[0]
    assert top["critical_dependencies"] >= 1


def test_engine_po_under_dsi_directive_reduces_qty(graph, world):
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_place_po", sku="SKU_001_amox_500")
    assert "DSI" in " ".join(rec.rationale)
    assert rec.confidence > 0


def test_engine_capex_rejects_below_hurdle(graph, world):
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_capex_approval", project_id="proj_legacy_replatform")
    assert "Reject" in rec.recommended_action


def test_engine_dea_flags_orders(graph, world):
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_dea_reporting")
    assert "Halt" in rec.recommended_action
    assert rec.escalate_to is not None


# ---------------------------------------------------------------------------
# Phase 1 — new handlers
# ---------------------------------------------------------------------------


def test_engine_demand_forecast_high_mape_revises(graph, world):
    """Oxycodone has MAPE ~22% in the fixture — forecast should be revised."""
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_demand_forecast", sku="SKU_003_oxy_10")
    assert "Revise" in rec.recommended_action
    assert rec.confidence > 0
    assert rec.confidence_model is not None
    assert "base" in rec.confidence_model


def test_engine_demand_forecast_low_mape_holds(graph, world):
    """Amoxicillin has MAPE ~8% — forecast should be published as-is."""
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_demand_forecast", sku="SKU_001_amox_500")
    assert "as-is" in rec.recommended_action.lower()


def test_engine_warehouse_schedule_returns_action(graph, world):
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_warehouse_schedule")
    assert rec.recommended_action
    assert rec.confidence > 0
    assert rec.confidence_model is not None


def test_engine_price_change_flags_competitor_gap(graph, world):
    """Amoxicillin is priced $4.50 vs competitor $4.20 — >5% above, so lower recommended."""
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_price_change", sku="SKU_001_amox_500")
    assert "Lower" in rec.recommended_action or "Hold" in rec.recommended_action


def test_engine_supplier_negotiation_urgent_renewal(graph, world):
    """Pfizer expires in 45 days — negotiation should be flagged."""
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_supplier_negotiation", supplier_id="sup_pfizer")
    assert "renewal" in rec.recommended_action.lower() or "Initiate" in rec.recommended_action


def test_engine_cash_forecast_returns_projection(graph, world):
    engine = DecisionEngine(graph, world)
    rec = engine.run("dec_cash_forecast")
    assert rec.recommended_action
    assert rec.confidence > 0
    assert "30d" in rec.recommended_action or "outlook" in rec.recommended_action.lower()


def test_all_decisions_now_have_handlers(graph, world):
    """Every decision node in the graph must have a registered handler."""
    engine = DecisionEngine(graph, world)
    decisions = [n for n, d in graph.nodes(data=True) if d["node_type"] == "decision"]
    assert len(decisions) == 12
    missing = [d for d in decisions if d not in engine.supported_decisions()]
    assert missing == [], f"Decisions without handlers: {missing}"


# ---------------------------------------------------------------------------
# Phase 1 — exception evaluator
# ---------------------------------------------------------------------------


def test_exception_evaluator_detects_stockout(graph, world):
    """Oxycodone has ~3.7 days of supply in the fixture — should trigger stockout."""
    active = evaluate_exceptions(graph, world)
    assert "exc_stockout_risk" in active


def test_exception_evaluator_detects_dea_flag(graph, world):
    active = evaluate_exceptions(graph, world)
    assert "exc_dea_flag" in active


def test_exception_evaluator_preserves_manual_exceptions(graph, world):
    """Manual-only exceptions (weather, recall) present in world state are preserved."""
    world_with_weather = {**world, "active_exceptions": {"exc_weather_disruption"}}
    active = evaluate_exceptions(graph, world_with_weather)
    assert "exc_weather_disruption" in active
