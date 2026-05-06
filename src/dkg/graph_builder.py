"""Build a NetworkX graph from the seed JSON data.

We use a MultiDiGraph because two nodes can be related by more than one edge
type (e.g. a Decision can both `produce` an Artifact and `feed_into` itself
through downstream paths).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

from .schema import (
    Artifact,
    BaseNode,
    Constraint,
    DataInput,
    Decision,
    Edge,
    EdgeType,
    Exception_,
    KPI,
    NodeType,
    System,
    Team,
)

_NODE_BUILDERS: dict[str, type[BaseNode]] = {
    "teams": Team,
    "decisions": Decision,
    "data_inputs": DataInput,
    "constraints": Constraint,
    "artifacts": Artifact,
    "exceptions": Exception_,
    "kpis": KPI,
    "systems": System,
}


def load_raw(path: str | Path) -> dict[str, Any]:
    """Load the raw JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_graph(path: str | Path) -> nx.MultiDiGraph:
    """Build a typed MultiDiGraph from the JSON file.

    Each node carries the full Pydantic model under the ``model`` key and a
    convenience ``node_type`` string for filtering. Each edge carries an
    ``edge_type`` and optional ``attributes`` dict.
    """
    raw = load_raw(path)
    g = nx.MultiDiGraph()

    # ---- nodes -------------------------------------------------------------
    for collection_name, model_cls in _NODE_BUILDERS.items():
        for record in raw.get(collection_name, []):
            node = model_cls(**record)
            g.add_node(
                node.id,
                model=node,
                node_type=node.node_type.value,
                name=node.name,
            )

    # ---- edges -------------------------------------------------------------
    for record in raw.get("edges", []):
        edge = Edge(**record)
        if edge.source not in g.nodes:
            raise ValueError(f"Edge references unknown source: {edge.source}")
        if edge.target not in g.nodes:
            raise ValueError(f"Edge references unknown target: {edge.target}")
        g.add_edge(
            edge.source,
            edge.target,
            key=edge.edge_type.value,
            edge_type=edge.edge_type.value,
            attributes=edge.attributes,
        )

    return g


def summary(g: nx.MultiDiGraph) -> dict[str, int]:
    """Return counts by node type and edge type for a quick sanity check."""
    counts: dict[str, int] = {}
    for _, data in g.nodes(data=True):
        nt = data["node_type"]
        counts[f"node:{nt}"] = counts.get(f"node:{nt}", 0) + 1
    for _, _, data in g.edges(data=True):
        et = data["edge_type"]
        counts[f"edge:{et}"] = counts.get(f"edge:{et}", 0) + 1
    counts["total_nodes"] = g.number_of_nodes()
    counts["total_edges"] = g.number_of_edges()
    return counts


def nodes_by_type(g: nx.MultiDiGraph, node_type: NodeType | str) -> list[BaseNode]:
    """Return all node *models* of a given type."""
    nt = node_type.value if isinstance(node_type, NodeType) else node_type
    return [d["model"] for _, d in g.nodes(data=True) if d["node_type"] == nt]


def get_node(g: nx.MultiDiGraph, node_id: str) -> BaseNode:
    return g.nodes[node_id]["model"]


def edges_of_type(
    g: nx.MultiDiGraph, edge_type: EdgeType | str
) -> list[tuple[str, str, dict]]:
    """Return all edges of a given type as (source, target, data) triples."""
    et = edge_type.value if isinstance(edge_type, EdgeType) else edge_type
    return [(u, v, d) for u, v, d in g.edges(data=True) if d["edge_type"] == et]
