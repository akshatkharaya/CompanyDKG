"""Useful queries over the Decision Knowledge Graph.

Each function answers a real operational question. Together they show what
makes a knowledge graph more powerful than a spreadsheet of decisions: you
can traverse relationships and reason transitively.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import networkx as nx

from .graph_builder import edges_of_type, get_node


# ---------------------------------------------------------------------------
# 1. Decision lookup
# ---------------------------------------------------------------------------


def list_decisions_by_team(g: nx.MultiDiGraph, team_id: str) -> list[dict[str, Any]]:
    """Decisions a team owns."""
    out = []
    for u, v, d in g.out_edges(team_id, data=True):
        if d["edge_type"] == "owns" and g.nodes[v]["node_type"] == "decision":
            dec = get_node(g, v)
            out.append({"id": v, "name": dec.name, "cadence": dec.cadence.value})
    return out


def get_decision_context(g: nx.MultiDiGraph, decision_id: str) -> dict[str, Any]:
    """Full context for a decision: who owns, what it consumes, what it produces, etc."""
    dec = get_node(g, decision_id)
    ctx: dict[str, Any] = {
        "decision": {"id": decision_id, "name": dec.name, "cadence": dec.cadence.value},
        "owners": [],
        "consumes": [],
        "bounded_by": [],
        "produces": [],
        "fed_by": [],
        "overridden_by": [],
        "optimizes_for": [],
        "executed_in": [],
    }

    for u, v, d in g.in_edges(decision_id, data=True):
        et = d["edge_type"]
        node = get_node(g, u)
        item = {"id": u, "name": node.name, "type": g.nodes[u]["node_type"]}
        if et == "owns":
            ctx["owners"].append(item)
        elif et == "feeds_into":
            ctx["fed_by"].append(item)
        elif et == "overridden_by":
            ctx["overridden_by"].append(item)

    for u, v, d in g.out_edges(decision_id, data=True):
        et = d["edge_type"]
        node = get_node(g, v)
        item = {"id": v, "name": node.name, "type": g.nodes[v]["node_type"]}
        if et == "consumes":
            ctx["consumes"].append({**item, "weight": d["attributes"].get("weight", "n/a")})
        elif et == "bounded_by":
            ctx["bounded_by"].append(item)
        elif et == "produces":
            ctx["produces"].append(item)
        elif et == "optimizes_for":
            ctx["optimizes_for"].append(item)
        elif et == "executed_in":
            ctx["executed_in"].append(item)

    return ctx


# ---------------------------------------------------------------------------
# 2. Blast radius — when something goes wrong, what else is affected?
# ---------------------------------------------------------------------------


def blast_radius(g: nx.MultiDiGraph, start_id: str, max_hops: int = 4) -> list[dict[str, Any]]:
    """All decisions transitively downstream of ``start_id``.

    Follows ``produces`` and ``feeds_into`` edges. Useful for: "If our demand
    forecast is wrong, what else breaks?"
    """
    relevant_edges = {"produces", "feeds_into", "consumes"}
    visited: dict[str, int] = {start_id: 0}
    frontier = [start_id]

    for hop in range(1, max_hops + 1):
        next_frontier = []
        for nid in frontier:
            # Forward through produces and feeds_into
            for u, v, d in g.out_edges(nid, data=True):
                if d["edge_type"] in {"produces", "feeds_into"} and v not in visited:
                    visited[v] = hop
                    next_frontier.append(v)
            # Anything that consumes from this is also impacted
            for u, v, d in g.in_edges(nid, data=True):
                if d["edge_type"] == "consumes" and u not in visited:
                    visited[u] = hop
                    next_frontier.append(u)
        frontier = next_frontier
        if not frontier:
            break

    out = []
    for nid, hops in visited.items():
        if nid == start_id or g.nodes[nid]["node_type"] != "decision":
            continue
        node = get_node(g, nid)
        out.append({"id": nid, "name": node.name, "hops_away": hops, "type": "decision"})
    return sorted(out, key=lambda x: x["hops_away"])


# ---------------------------------------------------------------------------
# 3. Critical-path inputs — which inputs feed the most decisions?
# ---------------------------------------------------------------------------


def input_criticality(g: nx.MultiDiGraph) -> list[dict[str, Any]]:
    """Rank data inputs by how many decisions depend on them (weighted by criticality)."""
    scores: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"critical_count": 0, "supplementary_count": 0, "decisions": []}
    )

    for u, v, d in edges_of_type(g, "consumes"):
        if g.nodes[v]["node_type"] != "data_input":
            continue
        weight = d["attributes"].get("weight", "supplementary")
        if weight == "critical":
            scores[v]["critical_count"] += 1
        else:
            scores[v]["supplementary_count"] += 1
        scores[v]["decisions"].append(get_node(g, u).name)

    out = []
    for input_id, info in scores.items():
        node = get_node(g, input_id)
        out.append({
            "id": input_id,
            "name": node.name,
            "source_system": node.source_system,
            "critical_dependencies": info["critical_count"],
            "supplementary_dependencies": info["supplementary_count"],
            "total": info["critical_count"] + info["supplementary_count"],
            "decisions": info["decisions"],
        })
    return sorted(out, key=lambda x: (-x["critical_dependencies"], -x["total"]))


# ---------------------------------------------------------------------------
# 4. Constraint enforcement — which decisions can be overridden?
# ---------------------------------------------------------------------------


def soft_constraints(g: nx.MultiDiGraph) -> list[dict[str, Any]]:
    """List decisions whose constraints are *soft* (overridable)."""
    out = []
    for u, v, d in edges_of_type(g, "bounded_by"):
        constraint = get_node(g, v)
        if getattr(constraint, "enforcement", None) and constraint.enforcement.value == "soft":
            decision = get_node(g, u)
            out.append({
                "decision_id": u,
                "decision": decision.name,
                "constraint": constraint.name,
                "owner": constraint.owner_team_id,
            })
    return out


# ---------------------------------------------------------------------------
# 5. Cross-team dependencies — find decision chains across departments
# ---------------------------------------------------------------------------


def cross_team_chains(g: nx.MultiDiGraph) -> list[dict[str, Any]]:
    """Find ``Decision -> Artifact -> Decision`` chains spanning different teams."""
    chains = []
    for u, v, d in edges_of_type(g, "feeds_into"):
        # u is an artifact, v is a decision
        # Find the upstream decision that produced u
        upstream = [
            src for src, tgt, dd in g.in_edges(u, data=True)
            if dd["edge_type"] == "produces" and g.nodes[src]["node_type"] == "decision"
        ]
        if not upstream:
            continue
        for up_dec in upstream:
            up_owner = _owner_of(g, up_dec)
            down_owner = _owner_of(g, v)
            if up_owner and down_owner and up_owner != down_owner:
                chains.append({
                    "from_decision": get_node(g, up_dec).name,
                    "from_team": get_node(g, up_owner).name,
                    "via_artifact": get_node(g, u).name,
                    "to_decision": get_node(g, v).name,
                    "to_team": get_node(g, down_owner).name,
                })
    return chains


def _owner_of(g: nx.MultiDiGraph, decision_id: str) -> str | None:
    for u, v, d in g.in_edges(decision_id, data=True):
        if d["edge_type"] == "owns" and g.nodes[u]["node_type"] == "team":
            return u
    return None


# ---------------------------------------------------------------------------
# 6. KPI ownership — which decisions move which metric?
# ---------------------------------------------------------------------------


def kpi_to_decisions(g: nx.MultiDiGraph) -> dict[str, list[dict[str, Any]]]:
    """For each KPI, list decisions that optimize for it (and their owning teams)."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u, v, d in edges_of_type(g, "optimizes_for"):
        kpi = get_node(g, v)
        decision = get_node(g, u)
        owner = _owner_of(g, u)
        out[kpi.name].append({
            "decision": decision.name,
            "team": get_node(g, owner).name if owner else "unowned",
            "cadence": decision.cadence.value,
        })
    return dict(out)


# ---------------------------------------------------------------------------
# 7. Exception impact — what does a given exception perturb?
# ---------------------------------------------------------------------------


def exception_impact(g: nx.MultiDiGraph, exception_id: str) -> list[dict[str, Any]]:
    """Which decisions does this exception override, and what do those touch?"""
    out = []
    for u, v, d in g.out_edges(exception_id, data=True):
        if d["edge_type"] == "overridden_by":
            decision = get_node(g, v)
            owner = _owner_of(g, v)
            out.append({
                "decision": decision.name,
                "owner": get_node(g, owner).name if owner else "unowned",
                "downstream": [
                    item["name"] for item in blast_radius(g, v, max_hops=2)
                ],
            })
    return out
