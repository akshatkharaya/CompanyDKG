"""Entity and relationship schema for the Decision Knowledge Graph (DKG).

Every node and edge in the graph is one of the types defined here. The schema
mirrors the conceptual model: Decisions consume Data Inputs, are bounded by
Constraints, are owned by Teams, produce Artifacts, optimize for KPIs, and
can be perturbed by Exceptions.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums for controlled vocabulary
# ---------------------------------------------------------------------------


class NodeType(str, Enum):
    """High-level entity types in the graph."""

    TEAM = "team"
    DECISION = "decision"
    DATA_INPUT = "data_input"
    CONSTRAINT = "constraint"
    ARTIFACT = "artifact"
    EXCEPTION = "exception"
    KPI = "kpi"
    SYSTEM = "system"


class Cadence(str, Enum):
    """How often a decision is made."""

    REAL_TIME = "real_time"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    AD_HOC = "ad_hoc"


class DecisionType(str, Enum):
    OPERATIONAL = "operational"
    FINANCIAL = "financial"
    STRATEGIC = "strategic"
    COMPLIANCE = "compliance"


class Enforcement(str, Enum):
    HARD = "hard"  # cannot violate
    SOFT = "soft"  # can override with approval


class EdgeType(str, Enum):
    """Relationship types between entities."""

    OWNS = "owns"
    CONTRIBUTES_TO = "contributes_to"
    CONSUMES = "consumes"
    BOUNDED_BY = "bounded_by"
    PRODUCES = "produces"
    FEEDS_INTO = "feeds_into"
    TRIGGERS = "triggers"
    OVERRIDDEN_BY = "overridden_by"
    OPTIMIZES_FOR = "optimizes_for"
    EXECUTED_IN = "executed_in"
    ESCALATES_TO = "escalates_to"


# ---------------------------------------------------------------------------
# Node models
# ---------------------------------------------------------------------------


class BaseNode(BaseModel):
    """Common fields on every node."""

    id: str
    name: str
    description: str = ""
    bu: str = "US_PHARMA"  # Business unit; default for our toy

    @property
    def node_type(self) -> NodeType:
        raise NotImplementedError


class Team(BaseNode):
    department: str
    headcount: Optional[int] = None

    @property
    def node_type(self) -> NodeType:
        return NodeType.TEAM


class Decision(BaseNode):
    cadence: Cadence
    decision_type: DecisionType
    output_type: str  # e.g. "purchase_order", "forecast", "approval"
    automation_level: Literal["manual", "partial", "full"] = "partial"

    @property
    def node_type(self) -> NodeType:
        return NodeType.DECISION


class DataInput(BaseNode):
    source_system: str  # e.g. "ERP", "WMS", "AR_AGING"
    input_type: str  # e.g. "demand_signal", "historical_trend", "contract_term"
    freshness: Cadence = Cadence.DAILY
    is_automated: bool = True

    @property
    def node_type(self) -> NodeType:
        return NodeType.DATA_INPUT


class Constraint(BaseNode):
    constraint_type: str  # "contractual", "regulatory", "policy", "financial"
    enforcement: Enforcement = Enforcement.HARD
    owner_team_id: Optional[str] = None

    @property
    def node_type(self) -> NodeType:
        return NodeType.CONSTRAINT


class Artifact(BaseNode):
    artifact_type: str  # "PO", "forecast", "report", "approval"
    sla_hours: Optional[float] = None  # how quickly it must be produced

    @property
    def node_type(self) -> NodeType:
        return NodeType.ARTIFACT


class Exception_(BaseNode):
    """Named with trailing underscore to avoid clashing with the Python builtin."""

    trigger_condition: str
    raised_by_team_id: Optional[str] = None
    resolution_type: Literal["manual_override", "escalation", "auto_adjust"] = "manual_override"
    frequency: Literal["rare", "occasional", "frequent"] = "occasional"

    @property
    def node_type(self) -> NodeType:
        return NodeType.EXCEPTION


class KPI(BaseNode):
    target_value: Optional[float] = None
    direction: Literal["minimize", "maximize", "maintain_range"] = "maintain_range"
    unit: str = ""

    @property
    def node_type(self) -> NodeType:
        return NodeType.KPI


class System(BaseNode):
    system_type: str  # "ERP", "treasury_system", "BI_tool", "planning_tool"

    @property
    def node_type(self) -> NodeType:
        return NodeType.SYSTEM


# ---------------------------------------------------------------------------
# Edge model
# ---------------------------------------------------------------------------


class Edge(BaseModel):
    source: str
    target: str
    edge_type: EdgeType
    attributes: dict = Field(default_factory=dict)
