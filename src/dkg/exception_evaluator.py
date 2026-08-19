"""Automatic exception evaluation driven by world state.

Each Exception_ node in the graph declares a ``trigger_condition`` (plain
English text). This module evaluates those conditions against live world-state
values and returns the set of exception IDs that are currently active.

Usage
-----
    from dkg.exception_evaluator import evaluate_exceptions

    active = evaluate_exceptions(graph, world_state)
    world_state["active_exceptions"] = active   # replace manual list

Exceptions that cannot be auto-detected from structured data
(``exc_supplier_recall``, ``exc_weather_disruption``) are left out of
automatic evaluation — they require an external signal and should remain
manually toggled or fed from an event stream.
"""

from __future__ import annotations

from typing import Any

import networkx as nx


def evaluate_exceptions(
    graph: nx.MultiDiGraph, world_state: dict[str, Any]
) -> set[str]:
    """Evaluate all auto-detectable exceptions and return a set of active IDs.

    Parameters
    ----------
    graph:
        The loaded DKG (from ``graph_builder.build_graph``).
    world_state:
        Live world-state dict. Must include the standard keys from
        ``data/world_state.json``.
    """
    active: set[str] = set()

    # exc_dsi_directive: DSI exceeds target by 15%
    # DSI (Days Sales Inventory) = total_on_hand_days_equivalent / daily_demand
    skus = world_state.get("skus", {})
    total_on_hand = sum(s.get("on_hand", 0) + s.get("in_transit", 0) for s in skus.values())
    total_weekly_demand = sum(s.get("weekly_forecast", 1) for s in skus.values())
    if total_weekly_demand > 0:
        dsi = total_on_hand / (total_weekly_demand / 7)
        dsi_target = world_state.get("dsi_target_days", 14)
        if dsi > dsi_target * 1.15:
            active.add("exc_dsi_directive")

    # exc_stockout_risk: Days of supply < 5 for any SKU
    for sku_id, sku in skus.items():
        daily_demand = sku.get("weekly_forecast", 0) / 7
        if daily_demand > 0:
            dos = (sku.get("on_hand", 0) + sku.get("in_transit", 0)) / daily_demand
            if dos < 5:
                active.add("exc_stockout_risk")
                break

    # exc_large_customer_default: Customer >30 days overdue with balance > $5M
    for cust_id, aging in world_state.get("ar_aging", {}).items():
        if aging.get("days_overdue", 0) > 30 and aging.get("balance", 0) > 5_000_000:
            active.add("exc_large_customer_default")
            break

    # exc_credit_drawdown: Cash < min buffer + 7 days projected outflow
    cash = world_state.get("cash_on_hand", 0)
    min_buffer = world_state.get("min_cash_buffer", 0)
    daily_outflow = world_state.get("projected_weekly_outflow", 0) / 7
    if cash < min_buffer + 7 * daily_outflow:
        active.add("exc_credit_drawdown")

    # exc_dea_flag: Suspicious order pattern present in world state
    if world_state.get("dea_flagged_orders"):
        active.add("exc_dea_flag")

    # exc_supplier_recall and exc_weather_disruption are not auto-detectable
    # from structured data — preserve them from the manual list if present
    manual = world_state.get("active_exceptions", set())
    for manual_exc in ("exc_supplier_recall", "exc_weather_disruption"):
        if manual_exc in manual:
            active.add(manual_exc)

    return active
