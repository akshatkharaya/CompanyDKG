"""Visualization helpers for the DKG.

Two output modes:
- :func:`render_interactive` produces a self-contained HTML file using pyvis
  (force-directed, draggable, hover tooltips).
- :func:`render_static` produces a static PNG via matplotlib (good for reports
  and slide decks).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
from pyvis.network import Network


# Color scheme aligned with the conceptual diagrams we built earlier.
NODE_COLORS = {
    "team": "#1D9E75",        # teal
    "decision": "#7F77DD",    # purple
    "data_input": "#378ADD",  # blue
    "constraint": "#D85A30",  # coral
    "artifact": "#EF9F27",    # amber
    "exception": "#D4537E",   # pink
    "kpi": "#639922",         # green
    "system": "#888780",      # gray
}

EDGE_COLORS = {
    "owns": "#0F6E56",
    "consumes": "#185FA5",
    "bounded_by": "#993C1D",
    "produces": "#854F0B",
    "feeds_into": "#BA7517",
    "overridden_by": "#993556",
    "optimizes_for": "#3B6D11",
    "executed_in": "#5F5E5A",
    "contributes_to": "#1D9E75",
    "triggers": "#7F77DD",
    "escalates_to": "#888780",
}


def _format_tooltip(node_id: str, data: dict) -> str:
    """Build an HTML tooltip showing all of a node's interesting fields."""
    model = data["model"]
    fields = model.model_dump()
    rows = [f"<b>{model.name}</b>", f"<i>{data['node_type']}</i><br>"]
    for k, v in fields.items():
        if k in ("id", "name") or v in (None, "", []):
            continue
        rows.append(f"<b>{k}</b>: {v}")
    return "<br>".join(rows)


def render_interactive(
    g: nx.MultiDiGraph,
    output_path: str | Path,
    height: str = "850px",
    width: str = "100%",
    notebook: bool = False,
) -> Path:
    """Render the graph as an interactive HTML file via pyvis."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    net = Network(
        height=height,
        width=width,
        directed=True,
        notebook=notebook,
        cdn_resources="in_line",
        bgcolor="#ffffff",
        font_color="#222222",
    )

    for nid, data in g.nodes(data=True):
        color = NODE_COLORS.get(data["node_type"], "#888888")
        net.add_node(
            nid,
            label=data["name"],
            title=_format_tooltip(nid, data),
            color=color,
            shape="dot",
            size=20 if data["node_type"] == "decision" else 14,
        )

    for u, v, edata in g.edges(data=True):
        et = edata["edge_type"]
        net.add_edge(
            u,
            v,
            label=et,
            title=et,
            color=EDGE_COLORS.get(et, "#999999"),
            arrows="to",
            font={"size": 9, "align": "middle"},
        )

    net.set_options(
        """
        {
          "physics": {
            "forceAtlas2Based": {
              "gravitationalConstant": -60,
              "centralGravity": 0.005,
              "springLength": 130,
              "springConstant": 0.06
            },
            "minVelocity": 0.5,
            "solver": "forceAtlas2Based",
            "stabilization": { "iterations": 250 }
          },
          "interaction": { "hover": true, "tooltipDelay": 100 }
        }
        """
    )

    # Pyvis's write_html signature changed across versions; handle both.
    try:
        net.write_html(str(output_path), open_browser=False, notebook=notebook)
    except TypeError:
        net.write_html(str(output_path))

    return output_path


def render_static(
    g: nx.MultiDiGraph,
    output_path: str | Path,
    figsize: tuple[float, float] = (16, 12),
    seed: int = 42,
) -> Path:
    """Render the graph as a static PNG via matplotlib."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pos = nx.spring_layout(g, seed=seed, k=1.4, iterations=80)

    fig, ax = plt.subplots(figsize=figsize)

    for ntype, color in NODE_COLORS.items():
        nodes = [n for n, d in g.nodes(data=True) if d["node_type"] == ntype]
        if not nodes:
            continue
        nx.draw_networkx_nodes(
            g, pos, nodelist=nodes, node_color=color,
            node_size=700, alpha=0.9, ax=ax, label=ntype,
        )

    nx.draw_networkx_edges(
        g, pos, alpha=0.35, arrows=True, arrowsize=10,
        edge_color="#666666", width=0.7, ax=ax,
    )

    labels = {n: d["name"] for n, d in g.nodes(data=True)}
    nx.draw_networkx_labels(g, pos, labels=labels, font_size=7, ax=ax)

    ax.legend(loc="upper left", fontsize=9, frameon=True)
    ax.set_title("McKesson US Pharma — Decision Knowledge Graph", fontsize=13)
    ax.axis("off")

    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output_path
