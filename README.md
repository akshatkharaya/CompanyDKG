# McKesson Decision Knowledge Graph (US Pharma)

A toy implementation of a decision-centric knowledge graph for the McKesson US Pharmaceutical Distribution business unit. It demonstrates three things:

1. **Modeling** — how operational, financial, and compliance decisions can be represented as a graph of teams, decisions, inputs, constraints, artifacts, exceptions, KPIs, and systems.
2. **Querying** — how graph traversal answers questions that are awkward in spreadsheets ("if forecast X is wrong, what else breaks?", "which inputs are most critical?").
3. **Reasoning** — how the same graph can drive *autonomous* recommendations with full traceability.

---

## Project structure

```
mckesson_dkg/
├── data/
│   ├── us_pharma_dkg.json        # Graph data: nodes + edges
│   └── world_state.json          # Live state the engine reasons over
├── src/dkg/
│   ├── schema.py                 # Pydantic models for every node + edge type
│   ├── graph_builder.py          # JSON -> NetworkX MultiDiGraph
│   ├── visualize.py              # Interactive (pyvis) + static (matplotlib)
│   ├── queries.py                # Useful traversals / lookups
│   ├── decision_engine.py        # Rule-based autonomous decision maker (all 12 decisions)
│   ├── confidence.py             # Calibrated confidence scoring (replaces magic literals)
│   └── exception_evaluator.py   # Auto-detects active exceptions from world state
├── demos/
│   ├── demo_visualize.py         # Demo 1: build & render the graph
│   ├── demo_query.py             # Demo 2: run example queries
│   └── demo_decide.py            # Demo 3: make autonomous decisions across all 12 decisions
├── tests/
│   └── test_dkg.py               # Smoke tests (18 tests)
├── outputs/                      # Generated HTML and PNG live here
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Setup (with VS Code)

### Prerequisites

- Python 3.10 or newer
- VS Code with the Python extension installed

### 1. Open the project

Unzip the project and open the `mckesson_dkg` folder in VS Code:

```
File -> Open Folder -> mckesson_dkg
```

VS Code will detect `.vscode/settings.json` and `.vscode/launch.json` automatically.

### 2. Create a virtual environment

In the integrated terminal (`` Ctrl+` ``):

```bash
python -m venv .venv
```

Activate it:

- **macOS / Linux**: `source .venv/bin/activate`
- **Windows (PowerShell)**: `.venv\Scripts\Activate.ps1`
- **Windows (cmd)**: `.venv\Scripts\activate.bat`

VS Code should prompt to select this interpreter; if not, run **Python: Select Interpreter** from the command palette and choose `.venv`.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Or, with the editable install:

```bash
pip install -e ".[dev]"
```

### 4. Verify the install

```bash
pytest
```

You should see all tests passing.

---

## Running the demos

There are three ways to run any demo:

**(a) From the terminal:**

```bash
python -m demos.demo_visualize
python -m demos.demo_query
python -m demos.demo_decide
```

**(b) From VS Code:** open the Run & Debug panel (`Ctrl+Shift+D`), pick one of the three configurations, hit F5.

**(c) From the integrated tasks** — same launch entries are wired up in `.vscode/launch.json`.

### Demo 1 — Visualize

Builds the graph and writes:

- `outputs/dkg_interactive.html` — open in any browser. Nodes are draggable, color-coded by type, hover for full attributes.
- `outputs/dkg_static.png` — for slides and reports.

Color legend (in both views):

| Color | Type |
|---|---|
| Teal | Team |
| Purple | Decision |
| Blue | Data input |
| Coral | Constraint |
| Amber | Artifact |
| Pink | Exception |
| Green | KPI |
| Gray | System |

### Demo 2 — Query

Runs a battery of queries that show what the graph is good for:

- Decisions owned by a team
- Full context of any decision (inputs, constraints, what produces it, what consumes it)
- **Blast radius**: if X is wrong, what else is impacted?
- **Input criticality ranking**: which inputs are linchpins?
- **Cross-team dependency chains** (Inventory → FP&A → Treasury, etc.)
- Decisions with overridable constraints
- KPI → which decisions move it
- Exception impact analysis

### Demo 3 — Decide

This is the punchline. The decision engine:

1. Calls `evaluate_exceptions()` to automatically derive which exceptions are active from the current world state (e.g. stockout risk fires when days-of-supply drops below 5 — no manual toggling needed).
2. Looks up each decision's structural context from the graph (inputs, constraints, exceptions).
3. Reads the current world state from `data/world_state.json`.
4. Applies decision-specific rules to produce a recommendation with rationale.

All 12 decisions in the graph now have handlers. The demo runs through every one so you can see how a single exception ripples across multiple teams.

Each recommendation prints:

```
DECISION: Weekly SKU-level demand forecast  (dec_demand_forecast)
========================================================================
Recommended action : Revise forecast: SKU_003_oxy_10: 1,500 -> 1,462 (MAPE=18.3%)
Confidence         : 74%
Rationale:
  - SKU_001_amox_500: forecast=12,000, avg_actual=11,925, MAPE=8.5%
  - SKU_002_lipitor_20: forecast=8,000, avg_actual=7,950, MAPE=11.9%
  - SKU_003_oxy_10: forecast=1,500, avg_actual=1,425, MAPE=18.3%
  - High MAPE on SKU_003_oxy_10 — blending forecast with 4-week average
Inputs consulted:
  - Historical sales transactions
  - Daily order volume
Active exceptions:
  - Imminent stockout alert
Expected KPI impact:
  - Forecast accuracy (MAPE): decrease

Confidence breakdown:
  - base: 0.880
  - exception_penalty: -0.050
  - freshness_penalty: -0.000
  - forecast_mape_penalty: -0.140
```

Every recommendation is auditable: you can see which graph nodes were consulted, which rules fired, and exactly how the confidence score was computed.

---

## How to extend

### Add a new decision

1. Add a record to `data/us_pharma_dkg.json` under `decisions`, `data_inputs`, `constraints`, etc., as needed.
2. Add `edges` connecting your new decision to teams, inputs, constraints, KPIs, and produced artifacts.
3. Optionally add a handler in `src/dkg/decision_engine.py` for autonomous reasoning.
4. Re-run `demo_visualize.py` to see it in the graph.

### Add a new query

Open `src/dkg/queries.py` and write a function. The graph is a NetworkX `MultiDiGraph`, so anything in [the NetworkX docs](https://networkx.org/) works directly — shortest paths, centrality, community detection, etc.

### Swap rule-based logic for an LLM

Each handler in `decision_engine.py` is independent. You can replace any of them with an LLM call — the graph still provides the structured context via `_graph_context()`, so the model gets a curated input bundle rather than raw text. The `Recommendation` dataclass is the shared output contract between rule-based and LLM handlers.

### Extend the confidence model

`src/dkg/confidence.py` contains `compute_confidence()`, which decomposes confidence into three penalties applied to a handler-supplied base:

| Penalty | Source | Max penalty |
|---|---|---|
| Exception count | `len(active_exceptions) × 0.05` | unbounded |
| Data freshness | Hours since `world_state["last_updated"]` | −0.15 |
| Forecast MAPE | `world_state["forecast_mape"]` average | −0.20 |

To add a new signal (e.g. supplier reliability score, weather severity), extend `compute_confidence()` with a new penalty term and add the corresponding field to `world_state.json`.

### Auto-detect new exceptions

`src/dkg/exception_evaluator.py` maps each exception's `trigger_condition` to a world-state check. To make a new exception auto-detectable, add an `if` block in `evaluate_exceptions()` that reads the relevant world-state fields and adds the exception ID to `active`. Exceptions that require an external signal (FDA recall notice, weather warning) stay in the manual fallback path.

---

## Why a graph and not a relational DB?

The questions you actually want to answer in this domain are *traversal* questions: "what's downstream of this?", "what feeds this?", "who owns this?". Those are O(1) hops in a graph and ugly recursive joins in a relational schema.

For a real production system, the same data model maps cleanly onto a property graph database (Neo4j, ArangoDB, Memgraph) — you'd swap `graph_builder.py` for a Cypher loader and keep everything else.

---

## License

Toy / educational use. 
