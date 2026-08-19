"""Calibrated confidence scoring for decision recommendations.

Instead of magic literal numbers, confidence is decomposed into three
additive penalties applied to a handler-supplied base:

  final = base
        - exception_count * 0.05          (each active exception adds uncertainty)
        - freshness_penalty (0..0.15)      (stale world-state data)
        - mape_penalty (0..0.20)           (forecast error for forecast-type decisions)

The breakdown dict returned alongside the final score lets callers show
how confidence was computed, making recommendations auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def compute_confidence(
    base: float,
    active_exceptions: list[str],
    world_state: dict[str, Any],
    decision_id: str | None = None,
) -> tuple[float, dict[str, float]]:
    """Return (final_confidence, breakdown_dict).

    Parameters
    ----------
    base:
        Starting confidence supplied by the handler (reflects rule certainty).
    active_exceptions:
        List of exception *names* currently active for this decision, as
        returned by ``DecisionEngine._active_exceptions()``.
    world_state:
        Live world-state dict. Reads ``last_updated`` (ISO-8601 str) and
        ``forecast_mape`` (dict[sku_id, float]).
    decision_id:
        If provided and contains "forecast", applies the MAPE penalty.
    """
    breakdown: dict[str, float] = {"base": base}

    # 1. Exception count penalty — each active exception adds uncertainty
    exc_penalty = len(active_exceptions) * 0.05
    breakdown["exception_penalty"] = -exc_penalty

    # 2. Data freshness penalty — stale world state reduces reliability
    freshness_penalty = 0.0
    last_updated_str = world_state.get("last_updated")
    if last_updated_str:
        try:
            last_updated = datetime.fromisoformat(last_updated_str)
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            hours_stale = (datetime.now(timezone.utc) - last_updated).total_seconds() / 3600
            if hours_stale > 24:
                freshness_penalty = min(0.15, (hours_stale / 24) * 0.05)
        except (ValueError, TypeError):
            pass
    breakdown["freshness_penalty"] = -freshness_penalty

    # 3. Forecast MAPE penalty — high forecast error means less reliable recommendations
    mape_penalty = 0.0
    if decision_id and "forecast" in decision_id:
        mape_values = world_state.get("forecast_mape", {})
        if mape_values:
            avg_mape = sum(mape_values.values()) / len(mape_values)
            mape_penalty = min(0.20, avg_mape)
    breakdown["forecast_mape_penalty"] = -mape_penalty

    final = max(0.0, min(1.0, base - exc_penalty - freshness_penalty - mape_penalty))
    breakdown["final"] = final
    return final, breakdown
