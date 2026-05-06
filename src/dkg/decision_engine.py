"""Autonomous decision engine driven by the knowledge graph.

This module demonstrates how a knowledge graph turns from documentation into
an *active* system. For each decision in the graph, we implement a small
rule-based reasoner that:

1. Discovers — via the graph — what inputs, constraints, and exceptions
   apply to the decision.
2. Reads the current state of the world from a live "world state" dict
   (in a real system, these would be live data pulls).
3. Produces a recommendation with a full trace of *why*.

The point is transparency: every recommendation is explained by which graph
nodes it consulted. You can audit, override, or extend the logic without
touching a black-box model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import networkx as nx

from .graph_builder import get_node
from .queries import _owner_of


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class Recommendation:
    decision_id: str
    decision_name: str
    recommended_action: str
    confidence: float  # 0..1
    rationale: list[str] = field(default_factory=list)
    consulted_inputs: list[str] = field(default_factory=list)
    triggered_constraints: list[str] = field(default_factory=list)
    active_exceptions: list[str] = field(default_factory=list)
    expected_kpi_impact: dict[str, str] = field(default_factory=dict)
    escalate_to: Optional[str] = None

    def pretty(self) -> str:
        lines = [
            f"\n{'='*72}",
            f"DECISION: {self.decision_name}  ({self.decision_id})",
            f"{'='*72}",
            f"Recommended action : {self.recommended_action}",
            f"Confidence         : {self.confidence:.0%}",
        ]
        if self.escalate_to:
            lines.append(f"Escalate to        : {self.escalate_to}")
        if self.rationale:
            lines.append("\nRationale:")
            lines.extend(f"  - {r}" for r in self.rationale)
        if self.consulted_inputs:
            lines.append("\nInputs consulted:")
            lines.extend(f"  - {i}" for i in self.consulted_inputs)
        if self.triggered_constraints:
            lines.append("\nConstraints triggered:")
            lines.extend(f"  - {c}" for c in self.triggered_constraints)
        if self.active_exceptions:
            lines.append("\nActive exceptions:")
            lines.extend(f"  - {e}" for e in self.active_exceptions)
        if self.expected_kpi_impact:
            lines.append("\nExpected KPI impact:")
            for k, v in self.expected_kpi_impact.items():
                lines.append(f"  - {k}: {v}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DecisionEngine:
    """Looks up decision context in the graph, then applies rule-based logic."""

    def __init__(self, graph: nx.MultiDiGraph, world_state: dict[str, Any]):
        self.g = graph
        self.world = world_state
        self._handlers: dict[str, Callable[..., Recommendation]] = {
            "dec_place_po": self._decide_po,
            "dec_credit_limit": self._decide_credit,
            "dec_collections_action": self._decide_collections,
            "dec_cash_coverage": self._decide_cash_coverage,
            "dec_capex_approval": self._decide_capex,
            "dec_dea_reporting": self._decide_dea,
            "dec_route_plan": self._decide_route,
        }

    def supported_decisions(self) -> list[str]:
        return list(self._handlers.keys())

    def run(self, decision_id: str, **inputs) -> Recommendation:
        if decision_id not in self._handlers:
            raise ValueError(
                f"No handler for decision '{decision_id}'. Supported: {self.supported_decisions()}"
            )
        return self._handlers[decision_id](decision_id, **inputs)

    # -- Helpers ---------------------------------------------------------

    def _graph_context(self, decision_id: str) -> dict[str, list[str]]:
        """Pull declared inputs/constraints/exceptions from the graph."""
        ctx = {"inputs": [], "constraints": [], "exceptions": []}
        for u, v, d in self.g.out_edges(decision_id, data=True):
            if d["edge_type"] == "consumes":
                ctx["inputs"].append(get_node(self.g, v).name)
            elif d["edge_type"] == "bounded_by":
                ctx["constraints"].append(get_node(self.g, v).name)
        for u, v, d in self.g.in_edges(decision_id, data=True):
            if d["edge_type"] == "overridden_by":
                ctx["exceptions"].append(u)  # keep id — we'll match against world state
        return ctx

    def _active_exceptions(self, decision_id: str) -> list[str]:
        """Filter exception list down to the ones currently flagged active in world state."""
        ctx = self._graph_context(decision_id)
        active_flags = self.world.get("active_exceptions", set())
        return [
            get_node(self.g, e).name for e in ctx["exceptions"] if e in active_flags
        ]

    def _escalation_team(self, decision_id: str) -> Optional[str]:
        owner = _owner_of(self.g, decision_id)
        return get_node(self.g, owner).name if owner else None

    # -- Handlers --------------------------------------------------------

    def _decide_po(self, decision_id: str, sku: str | None = None) -> Recommendation:
        """Place weekly POs.

        Logic:
          target_qty = forecast - on_hand - in_transit + safety_min
          - If active DSI directive: cut target by 20%
          - If active stockout risk: bump target by 30% and escalate
          - Cap by DC capacity
        """
        ctx = self._graph_context(decision_id)
        sku = sku or next(iter(self.world["skus"]))
        sku_state = self.world["skus"][sku]

        forecast = sku_state["weekly_forecast"]
        on_hand = sku_state["on_hand"]
        in_transit = sku_state["in_transit"]
        safety_min = sku_state["safety_min"]
        dc_capacity_avail = self.world["dc_capacity_available"]

        target = max(0, forecast - on_hand - in_transit + safety_min)
        rationale = [
            f"Base target = forecast({forecast}) - on_hand({on_hand}) - "
            f"in_transit({in_transit}) + safety_min({safety_min}) = {target}"
        ]

        active = self._active_exceptions(decision_id)
        triggered_constraints: list[str] = []
        confidence = 0.85
        escalate = None

        if "exc_dsi_directive" in self.world.get("active_exceptions", set()):
            old = target
            target = int(target * 0.8)
            rationale.append(f"DSI reduction directive active: cut target {old} -> {target}")
            confidence -= 0.1

        if "exc_stockout_risk" in self.world.get("active_exceptions", set()):
            old = target
            target = int(target * 1.3)
            rationale.append(f"Stockout risk active: increase target {old} -> {target}")
            escalate = self._escalation_team(decision_id)

        if target > dc_capacity_avail:
            rationale.append(
                f"DC capacity constraint hit: target {target} > available {dc_capacity_avail}, capped"
            )
            target = dc_capacity_avail
            triggered_constraints.append("DC storage capacity")
            confidence -= 0.05

        if target < safety_min:
            rationale.append(
                f"Safety minimum violated ({target} < {safety_min}); escalating"
            )
            triggered_constraints.append("Contractual safety stock minimums")
            escalate = self._escalation_team(decision_id)
            confidence -= 0.2

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=f"Place PO for SKU={sku} qty={target}",
            confidence=max(0.0, confidence),
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=triggered_constraints,
            active_exceptions=active,
            expected_kpi_impact={
                "DSI": "decrease" if target < forecast else "increase",
                "Fill rate": "maintain" if target >= safety_min else "at risk",
            },
            escalate_to=escalate,
        )

    def _decide_credit(self, decision_id: str, customer_id: str | None = None) -> Recommendation:
        ctx = self._graph_context(decision_id)
        customer_id = customer_id or next(iter(self.world["customers"]))
        cust = self.world["customers"][customer_id]
        score = cust["credit_score"]
        requested_limit = cust["requested_limit"]
        days_late_avg = cust["days_late_avg"]
        tier_default = self.world["credit_policy_defaults"][cust["tier"]]

        rationale = [
            f"Customer tier={cust['tier']} -> default limit ${tier_default:,}",
            f"Credit score = {score}, avg days-late = {days_late_avg}",
        ]
        confidence = 0.8
        escalate = None
        triggered = []

        if score >= 700 and days_late_avg <= 5:
            recommended = min(requested_limit, int(tier_default * 1.5))
            rationale.append(f"Strong credit profile; approve up to ${recommended:,}")
        elif score >= 600:
            recommended = min(requested_limit, tier_default)
            rationale.append(f"Moderate profile; approve at default ${recommended:,}")
        else:
            recommended = int(tier_default * 0.5)
            rationale.append(f"Weak profile; approve only ${recommended:,}")
            confidence -= 0.1

        if requested_limit > recommended:
            rationale.append(
                f"Customer requested ${requested_limit:,} > recommended ${recommended:,}"
            )

        if "exc_large_customer_default" in self.world.get("active_exceptions", set()):
            rationale.append("Large customer default exception is active — escalate ALL credit decisions")
            escalate = self._escalation_team(decision_id)
            triggered.append("Customer credit policy limits")
            confidence -= 0.2

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=f"Approve credit limit ${recommended:,} for {customer_id}",
            confidence=max(0.0, confidence),
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=triggered,
            active_exceptions=self._active_exceptions(decision_id),
            expected_kpi_impact={
                "Bad debt ratio": "low risk" if score >= 700 else "moderate risk",
                "DSO": "neutral",
            },
            escalate_to=escalate,
        )

    def _decide_collections(self, decision_id: str) -> Recommendation:
        ctx = self._graph_context(decision_id)
        aging = self.world["ar_aging"]

        rationale = []
        actions: list[str] = []
        for cust_id, info in aging.items():
            days = info["days_overdue"]
            balance = info["balance"]
            if days >= 90:
                actions.append(f"Legal action on {cust_id} (${balance:,}, {days}d)")
                rationale.append(f"{cust_id}: {days}d / ${balance:,} -> legal")
            elif days >= 60:
                actions.append(f"Place {cust_id} on credit hold (${balance:,})")
                rationale.append(f"{cust_id}: {days}d / ${balance:,} -> hold shipments")
            elif days >= 30:
                actions.append(f"Call {cust_id}")
                rationale.append(f"{cust_id}: {days}d / ${balance:,} -> phone call")

        if not actions:
            rationale.append("No customers materially overdue this week.")
            actions = ["Monitor"]

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action="; ".join(actions),
            confidence=0.9,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            active_exceptions=self._active_exceptions(decision_id),
            expected_kpi_impact={"DSO": "decrease", "Bad debt ratio": "decrease"},
        )

    def _decide_cash_coverage(self, decision_id: str) -> Recommendation:
        ctx = self._graph_context(decision_id)
        cash = self.world["cash_on_hand"]
        weekly_outflow = self.world["projected_weekly_outflow"]
        min_buffer = self.world["min_cash_buffer"]
        credit_avail = self.world["credit_facility_available"]

        weeks_coverage = cash / weekly_outflow if weekly_outflow else float("inf")
        rationale = [
            f"Cash on hand: ${cash:,.0f}",
            f"Projected weekly outflow: ${weekly_outflow:,.0f}",
            f"Coverage: {weeks_coverage:.1f} weeks",
            f"Min buffer policy: ${min_buffer:,.0f}",
        ]

        triggered = []
        confidence = 0.9

        if cash < min_buffer:
            shortfall = min_buffer - cash + weekly_outflow
            action = f"Draw ${shortfall:,.0f} on credit facility (available ${credit_avail:,.0f})"
            rationale.append(f"Below min buffer -> draw ${shortfall:,.0f}")
            triggered.append("Minimum cash buffer policy")
            if shortfall > credit_avail:
                action = "Escalate: shortfall exceeds available credit facility"
                confidence -= 0.4
        elif weeks_coverage > 6:
            action = "Sweep excess cash to short-term investments"
            rationale.append("Over-funded — recommend sweep")
        else:
            action = "Hold; coverage in target range"

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=max(0.0, confidence),
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=triggered,
            active_exceptions=self._active_exceptions(decision_id),
            expected_kpi_impact={"Cash coverage ratio": "maintain"},
        )

    def _decide_capex(self, decision_id: str, project_id: str | None = None) -> Recommendation:
        ctx = self._graph_context(decision_id)
        project_id = project_id or next(iter(self.world["capex_proposals"]))
        proj = self.world["capex_proposals"][project_id]
        amount = proj["amount"]
        irr = proj["projected_irr"]
        hurdle = self.world["capex_hurdle_rate"]
        cash_health = self.world["cash_coverage_weeks"]

        rationale = [
            f"Project amount: ${amount:,.0f}",
            f"Projected IRR: {irr:.1%} (hurdle: {hurdle:.1%})",
            f"Current cash coverage: {cash_health:.1f} weeks",
        ]

        triggered = []
        escalate = None
        confidence = 0.85

        if irr < hurdle:
            decision = "Reject — IRR below hurdle rate"
            rationale.append("IRR < hurdle -> reject")
        elif cash_health < 2.5 and amount > 1_000_000:
            decision = "Defer — insufficient cash coverage for this size"
            rationale.append("Cash coverage too low for >$1M outlay")
            triggered.append("Minimum cash buffer policy")
            confidence -= 0.1
        elif amount > 10_000_000:
            decision = "Recommend approval; route to Board (>$10M threshold)"
            triggered.append("Capex approval thresholds")
            escalate = "Board of Directors"
        elif amount > 1_000_000:
            decision = "Recommend approval; route to CFO (>$1M threshold)"
            triggered.append("Capex approval thresholds")
            escalate = "CFO"
        else:
            decision = "Approve — within team authority"

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=decision,
            confidence=max(0.0, confidence),
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=triggered,
            active_exceptions=self._active_exceptions(decision_id),
            escalate_to=escalate,
        )

    def _decide_dea(self, decision_id: str) -> Recommendation:
        ctx = self._graph_context(decision_id)
        flagged = self.world["dea_flagged_orders"]

        rationale = [f"Found {len(flagged)} orders >3-sigma above customer baseline."]
        if not flagged:
            return Recommendation(
                decision_id=decision_id,
                decision_name=get_node(self.g, decision_id).name,
                recommended_action="No suspicious orders today; submit nil report.",
                confidence=0.95,
                rationale=rationale,
                consulted_inputs=ctx["inputs"],
                expected_kpi_impact={"DEA reporting compliance": "maintain"},
            )

        actions = []
        for order in flagged:
            actions.append(
                f"Halt order {order['order_id']} ({order['drug']}, "
                f"{order['quantity']} units, customer {order['customer']})"
            )
            rationale.append(
                f"Order {order['order_id']}: {order['quantity']} units of "
                f"{order['drug']} ({order['z_score']:.1f}σ above baseline)"
            )

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action="; ".join(actions) + "; file SOR with DEA within 24h",
            confidence=0.92,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=["DEA controlled substance quotas"],
            active_exceptions=self._active_exceptions(decision_id),
            expected_kpi_impact={"DEA reporting compliance": "maintain"},
            escalate_to="Regulatory Compliance + Legal",
        )

    def _decide_route(self, decision_id: str) -> Recommendation:
        ctx = self._graph_context(decision_id)
        weather_active = "exc_weather_disruption" in self.world.get("active_exceptions", set())

        if weather_active:
            action = "Activate contingency routes; prioritize critical-care customers; defer non-urgent deliveries"
            rationale = ["Weather disruption exception active — switch to contingency plan"]
            confidence = 0.7
        else:
            action = "Run standard cost-optimized routing"
            rationale = ["No active disruptions; standard optimization applies"]
            confidence = 0.95

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=confidence,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            active_exceptions=self._active_exceptions(decision_id),
            expected_kpi_impact={
                "On-time delivery rate": "at risk" if weather_active else "maintain"
            },
        )
