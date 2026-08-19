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

from .confidence import compute_confidence
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
    confidence_model: Optional[dict[str, float]] = None

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
        if self.confidence_model:
            lines.append("\nConfidence breakdown:")
            for k, v in self.confidence_model.items():
                if k == "final":
                    continue
                prefix = "  " if k == "base" else " "
                lines.append(f"{prefix} - {k}: {v:+.3f}" if k != "base" else f"  - {k}: {v:.3f}")
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
            # Previously unimplemented — now complete
            "dec_demand_forecast": self._decide_demand_forecast,
            "dec_warehouse_schedule": self._decide_warehouse_schedule,
            "dec_price_change": self._decide_price_change,
            "dec_supplier_negotiation": self._decide_supplier_negotiation,
            "dec_cash_forecast": self._decide_cash_forecast,
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
        confidence = 0.85 # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision
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
        confidence = 0.8 # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision
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
            confidence=0.9, # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision
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
        confidence = 0.9 # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision

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
        confidence = 0.85 # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision

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
                confidence=0.95, # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision
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
            confidence=0.92, # This is a made up number for demonstration — in a real system, you'd have a more rigorous way to estimate confidence based on measured performance of this decision
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

    def _decide_demand_forecast(
        self, decision_id: str, sku: str | None = None
    ) -> Recommendation:
        """Evaluate forecast accuracy per SKU and recommend adjustments.

        Logic:
          - Compute MAPE against last 4 weeks of historical actuals
          - SKUs with MAPE > 15% get forecast revised toward 4-week average
          - Confidence penalised by exception count, freshness, and avg MAPE
        """
        ctx = self._graph_context(decision_id)
        skus_to_check = (
            {sku: self.world["skus"][sku]} if sku else self.world["skus"]
        )
        mape_threshold = 0.15

        rationale: list[str] = []
        high_error_skus: list[tuple[str, float, int, float]] = []

        for sku_id, sku_state in skus_to_check.items():
            actuals = sku_state.get("historical_actuals", [])
            forecast = sku_state["weekly_forecast"]
            if actuals and forecast > 0:
                mape = sum(abs(a - forecast) / forecast for a in actuals) / len(actuals)
                avg_actual = sum(actuals) / len(actuals)
                rationale.append(
                    f"{sku_id}: forecast={forecast:,}, avg_actual={avg_actual:,.0f}, MAPE={mape:.1%}"
                )
                if mape > mape_threshold:
                    high_error_skus.append((sku_id, mape, forecast, avg_actual))
            else:
                rationale.append(f"{sku_id}: no historical actuals — publishing forecast as-is")

        active = self._active_exceptions(decision_id)
        confidence, confidence_model = compute_confidence(0.88, active, self.world, decision_id)

        if high_error_skus:
            adjustments: list[str] = []
            for sku_id, mape, old_fc, avg_actual in high_error_skus:
                new_fc = int(0.5 * old_fc + 0.5 * avg_actual)
                adjustments.append(f"{sku_id}: {old_fc:,} -> {new_fc:,} (MAPE={mape:.1%})")
                rationale.append(f"High MAPE on {sku_id} — blending forecast with 4-week average")
            action = "Revise forecast: " + "; ".join(adjustments)
        else:
            action = "Forecast within accuracy bounds; publish as-is"
            rationale.append(f"All SKUs within MAPE threshold ({mape_threshold:.0%})")

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=confidence,
            confidence_model=confidence_model,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            active_exceptions=active,
            expected_kpi_impact={
                "Forecast accuracy (MAPE)": "decrease" if high_error_skus else "maintain"
            },
        )

    def _decide_warehouse_schedule(self, decision_id: str) -> Recommendation:
        """Recommend daily warehouse labor and throughput plan.

        Logic:
          - Compute daily order volume from SKU weekly forecasts
          - If weather exception: activate contingency (critical orders only)
          - If utilization > 85%: schedule overtime
          - If utilization < 60%: reduce shifts, use for cross-training
        """
        ctx = self._graph_context(decision_id)
        skus = self.world["skus"]
        daily_volume = sum(s["weekly_forecast"] / 7 for s in skus.values())
        dc_capacity = self.world["dc_capacity_available"]
        labor_hours = self.world.get("dc_labor_hours_available", 2400)
        weather_active = "exc_weather_disruption" in self.world.get("active_exceptions", set())

        utilization = daily_volume / (dc_capacity / 7)

        rationale = [
            f"Projected daily order volume: {daily_volume:,.0f} units",
            f"DC daily capacity share: {dc_capacity / 7:,.0f} units ({utilization:.0%} utilization)",
            f"Labor hours budgeted: {labor_hours:,}h",
        ]

        triggered: list[str] = []
        active = self._active_exceptions(decision_id)
        confidence, confidence_model = compute_confidence(0.88, active, self.world, decision_id)

        if weather_active:
            action = (
                "Activate contingency plan: halt non-urgent picks, "
                "prioritize controlled substances & hospital orders"
            )
            rationale.append("Weather disruption active — prioritize critical-care fulfillment")
            confidence = max(0.0, confidence - 0.15)
        elif utilization > 0.85:
            extra_hours = int((utilization - 0.85) * labor_hours * 0.5)
            action = f"Schedule standard shifts + {extra_hours}h overtime; prioritize picking queue"
            rationale.append(f"Utilization {utilization:.0%} — overtime authorization required")
            triggered.append("DC storage capacity")
            confidence = max(0.0, confidence - 0.05)
        elif utilization > 0.60:
            action = "Run standard shift schedule; no overtime required"
            rationale.append(f"Utilization {utilization:.0%} — standard operations")
        else:
            action = "Run reduced-shift schedule; use slack time for cross-training"
            rationale.append(f"Low utilization ({utilization:.0%}) — training opportunity")

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=confidence,
            confidence_model=confidence_model,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=triggered,
            active_exceptions=active,
            expected_kpi_impact={
                "DC throughput efficiency": "at risk" if weather_active else "maintain"
            },
        )

    def _decide_price_change(
        self, decision_id: str, sku: str | None = None
    ) -> Recommendation:
        """Recommend monthly pricing changes per SKU.

        Logic:
          - If gross margin < floor: raise price to restore margin + 2% buffer
          - If price > competitor by > 5%: lower toward competitive parity
          - Otherwise: hold
        """
        ctx = self._graph_context(decision_id)
        skus_to_check = (
            {sku: self.world["skus"][sku]} if sku else self.world["skus"]
        )
        min_margin_pct = self.world.get("min_gross_margin_pct", 0.30)

        rationale = [f"Minimum gross margin floor: {min_margin_pct:.0%}"]
        recommended_changes: list[str] = []
        triggered: list[str] = []
        active = self._active_exceptions(decision_id)
        confidence, confidence_model = compute_confidence(0.80, active, self.world, decision_id)

        for sku_id, sku_state in skus_to_check.items():
            price = sku_state.get("current_price", 0.0)
            cost = sku_state.get("unit_cost", 0.0)
            comp_price = sku_state.get("competitor_price", price)

            if price <= 0 or cost <= 0:
                rationale.append(f"{sku_id}: price/cost data unavailable — skipped")
                continue

            margin = (price - cost) / price
            rationale.append(
                f"{sku_id}: price=${price:.2f}, cost=${cost:.2f}, "
                f"margin={margin:.1%}, competitor=${comp_price:.2f}"
            )

            if margin < min_margin_pct:
                floor_price = cost / (1 - min_margin_pct)
                new_price = round(floor_price * 1.02, 2)
                recommended_changes.append(
                    f"Raise {sku_id} ${price:.2f} -> ${new_price:.2f} (below margin floor)"
                )
                triggered.append("Cost-plus pricing floor")
                confidence = max(0.0, confidence - 0.10)
            elif price > comp_price * 1.05:
                new_price = round(comp_price * 1.02, 2)
                recommended_changes.append(
                    f"Lower {sku_id} ${price:.2f} -> ${new_price:.2f} (>5% above competitor)"
                )
            else:
                recommended_changes.append(
                    f"Hold {sku_id} at ${price:.2f} (margin OK, within competitive range)"
                )

        action = (
            "No pricing changes required this cycle"
            if not recommended_changes
            else "; ".join(recommended_changes)
        )
        any_raise = any("Raise" in r for r in recommended_changes)

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=confidence,
            confidence_model=confidence_model,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=list(dict.fromkeys(triggered)),  # deduplicate
            active_exceptions=active,
            expected_kpi_impact={"Gross margin": "improve" if any_raise else "maintain"},
        )

    def _decide_supplier_negotiation(
        self, decision_id: str, supplier_id: str | None = None
    ) -> Recommendation:
        """Recommend supplier contract renewal and rebate actions.

        Logic:
          - Contracts expiring in < 30 days: urgent renewal flag
          - Contracts expiring in 30-90 days: begin negotiations
          - Annual spend >= rebate threshold: claim rebate
          - Annual spend >= 85% of threshold: consider volume uplift to qualify
        """
        ctx = self._graph_context(decision_id)
        contracts = self.world.get("supplier_contracts", {})
        contracts_to_check = (
            {supplier_id: contracts[supplier_id]} if supplier_id else contracts
        )

        rationale: list[str] = []
        actions: list[str] = []
        active = self._active_exceptions(decision_id)
        confidence, confidence_model = compute_confidence(0.75, active, self.world, decision_id)

        for sup_id, contract in contracts_to_check.items():
            name = contract.get("name", sup_id)
            days_to_expiry = contract.get("days_to_expiry", 999)
            spend = contract.get("annual_spend", 0)
            rebate_threshold = contract.get("rebate_threshold", 0)
            rebate_pct = contract.get("rebate_pct", 0.0)

            rationale.append(
                f"{name}: expires in {days_to_expiry}d, spend=${spend:,.0f}, "
                f"rebate threshold=${rebate_threshold:,.0f} @ {rebate_pct:.1%}"
            )

            if days_to_expiry <= 30:
                actions.append(f"URGENT: Initiate renewal with {name} (expiring in {days_to_expiry}d)")
                confidence = max(0.0, confidence - 0.10)
            elif days_to_expiry <= 90:
                actions.append(f"Begin renewal negotiation with {name} ({days_to_expiry}d to expiry)")

            if spend >= rebate_threshold:
                rebate_value = spend * rebate_pct
                actions.append(
                    f"Claim ${rebate_value:,.0f} rebate from {name} "
                    f"(spend ${spend:,.0f} >= threshold ${rebate_threshold:,.0f})"
                )
                rationale.append(f"{name}: eligible for ${rebate_value:,.0f} annual rebate")
            elif spend >= rebate_threshold * 0.85:
                shortfall = rebate_threshold - spend
                actions.append(
                    f"Increase {name} volume by ${shortfall:,.0f} to unlock "
                    f"{rebate_pct:.1%} rebate (${spend * rebate_pct:,.0f})"
                )
                rationale.append(f"{name}: ${shortfall:,.0f} below rebate threshold")

        action = (
            "No supplier contracts require immediate action; monitor quarterly"
            if not actions
            else "; ".join(actions)
        )

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=confidence,
            confidence_model=confidence_model,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            active_exceptions=active,
            expected_kpi_impact={"Gross margin": "improve" if actions else "maintain"},
        )

    def _decide_cash_forecast(self, decision_id: str) -> Recommendation:
        """Project 30-day cash position from AR collections and PO outflows.

        Logic:
          - Estimate AR collections using payment patterns and collection probability
          - Project outflows: 4 weeks of operational spend + pending POs
          - If projected 30d cash < min buffer: pre-arrange credit drawdown
          - If large customer default exception: reduce collection confidence, escalate
        """
        ctx = self._graph_context(decision_id)
        aging = self.world.get("ar_aging", {})
        patterns = self.world.get("customer_payment_patterns", {})

        collections_7d = 0.0
        collections_30d = 0.0
        for cust_id, info in aging.items():
            balance = info.get("balance", 0)
            days_overdue = info.get("days_overdue", 0)
            pattern = patterns.get(cust_id, {})
            prob = pattern.get("collection_probability", 0.90)
            avg_days = pattern.get("avg_days_to_pay", 30)
            days_remaining = max(0, avg_days - days_overdue)
            if days_remaining <= 7:
                collections_7d += balance * prob
            elif days_remaining <= 30:
                collections_30d += balance * prob

        weekly_outflow = self.world.get("projected_weekly_outflow", 0)
        pending_po = self.world.get("pending_po_outflows", 0)
        outflow_30d = weekly_outflow * 4 + pending_po

        cash = self.world.get("cash_on_hand", 0)
        projected_cash_30d = cash + collections_7d + collections_30d - outflow_30d
        min_buffer = self.world.get("min_cash_buffer", 0)
        net_30d = collections_7d + collections_30d - outflow_30d

        rationale = [
            f"AR collections expected (7d): ${collections_7d:,.0f}",
            f"AR collections expected (30d): ${collections_30d:,.0f}",
            f"Projected 30d outflows (ops + POs): ${outflow_30d:,.0f}",
            f"Projected cash position (30d): ${projected_cash_30d:,.0f} "
            f"(min buffer: ${min_buffer:,.0f})",
        ]

        active = self._active_exceptions(decision_id)
        confidence, confidence_model = compute_confidence(0.82, active, self.world, decision_id)
        triggered: list[str] = []
        escalate = None

        if "exc_large_customer_default" in self.world.get("active_exceptions", set()):
            rationale.append("Large customer default active — AR collections may be overstated")
            confidence = max(0.0, confidence - 0.15)
            escalate = self._escalation_team(decision_id)

        if projected_cash_30d < min_buffer:
            shortfall = min_buffer - projected_cash_30d
            action = (
                f"ALERT: 30d projected cash ${projected_cash_30d:,.0f} below min buffer — "
                f"pre-arrange ${shortfall:,.0f} credit drawdown"
            )
            triggered.append("Minimum cash buffer policy")
            confidence = max(0.0, confidence - 0.10)
        elif net_30d > 0:
            action = f"30d outlook positive (net +${net_30d:,.0f}); continue standard PO schedule"
        else:
            action = (
                f"30d outlook shows net outflow of ${abs(net_30d):,.0f}; "
                f"monitor AR collections closely"
            )

        return Recommendation(
            decision_id=decision_id,
            decision_name=get_node(self.g, decision_id).name,
            recommended_action=action,
            confidence=confidence,
            confidence_model=confidence_model,
            rationale=rationale,
            consulted_inputs=ctx["inputs"],
            triggered_constraints=triggered,
            active_exceptions=active,
            escalate_to=escalate,
            expected_kpi_impact={
                "Cash coverage ratio": "maintain" if projected_cash_30d >= min_buffer else "at risk",
                "Forecast accuracy (MAPE)": "maintain",
            },
        )
