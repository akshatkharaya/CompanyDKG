"""Smoke tests for the DKG."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dkg.decision_engine import DecisionEngine
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
