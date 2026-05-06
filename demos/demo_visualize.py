"""Demo 1 — Build the graph and produce both interactive and static visualizations.

Run:
    python -m demos.demo_visualize

Outputs:
    outputs/dkg_interactive.html  (open in browser; drag, zoom, hover)
    outputs/dkg_static.png        (static snapshot)
"""

from __future__ import annotations

from pathlib import Path

from dkg.graph_builder import build_graph, summary
from dkg.visualize import render_interactive, render_static


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    data_path = project_root / "data" / "us_pharma_dkg.json"
    out_dir = project_root / "outputs"

    print(f"Loading graph from {data_path}")
    g = build_graph(data_path)

    print("\nGraph summary:")
    for k, v in summary(g).items():
        print(f"  {k:30s} {v}")

    interactive_path = render_interactive(g, out_dir / "dkg_interactive.html")
    print(f"\nInteractive HTML written to: {interactive_path}")
    print("  -> open this file in any modern browser; nodes are draggable, hover for details")

    static_path = render_static(g, out_dir / "dkg_static.png")
    print(f"Static PNG written to:       {static_path}")


if __name__ == "__main__":
    main()
