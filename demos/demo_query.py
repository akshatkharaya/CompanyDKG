"""Demo 2 — Run a battery of useful queries against the graph.

Run:
    python -m demos.demo_query
"""

from __future__ import annotations

import json
from pathlib import Path

from dkg.graph_builder import build_graph
from dkg.queries import (
    blast_radius,
    cross_team_chains,
    exception_impact,
    get_decision_context,
    input_criticality,
    kpi_to_decisions,
    list_decisions_by_team,
    soft_constraints,
)


def section(title: str) -> None:
    print(f"\n{'='*72}\n{title}\n{'='*72}")


def jp(obj) -> None:
    """Pretty-print a JSON-serializable object."""
    print(json.dumps(obj, indent=2, default=str))


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    g = build_graph(project_root / "data" / "us_pharma_dkg.json")

    section("Q1. What decisions does the FP&A team own?")
    jp(list_decisions_by_team(g, "team_fpa"))

    section("Q2. Full context for the weekly PO decision (dec_place_po)")
    jp(get_decision_context(g, "dec_place_po"))

    section("Q3. Blast radius of the demand-forecast decision (3 hops)")
    print("If the demand forecast is wrong, which decisions are affected?")
    jp(blast_radius(g, "dec_demand_forecast", max_hops=3))

    section("Q4. Most critical data inputs (by # of decisions consuming them)")
    for row in input_criticality(g)[:8]:
        print(
            f"  {row['name']:35s} crit={row['critical_dependencies']:2d}  "
            f"sup={row['supplementary_dependencies']:2d}  src={row['source_system']}"
        )

    section("Q5. Cross-team decision chains (dependency hand-offs)")
    for chain in cross_team_chains(g):
        print(
            f"  {chain['from_team']:25s} ({chain['from_decision']})\n"
            f"    -> via {chain['via_artifact']}\n"
            f"    -> {chain['to_team']:25s} ({chain['to_decision']})\n"
        )

    section("Q6. Decisions with overridable (soft) constraints")
    jp(soft_constraints(g))

    section("Q7. KPI -> which decisions move it?")
    for kpi, decs in kpi_to_decisions(g).items():
        print(f"\n  {kpi}")
        for d in decs:
            print(f"    - {d['decision']:42s} owner={d['team']:25s} cadence={d['cadence']}")

    section("Q8. Exception impact: 'CFO DSI reduction directive'")
    jp(exception_impact(g, "exc_dsi_directive"))


if __name__ == "__main__":
    main()
