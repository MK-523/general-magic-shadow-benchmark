from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Message:
    role: str
    text: str


@dataclass
class Variant:
    id: str
    label: str
    description: str
    injected_messages: list[Message] = field(default_factory=list)
    expected_action: str | None = None
    required_entities: list[str] = field(default_factory=list)
    blocked_claims: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    category: str
    title: str
    line_of_business: str
    policy_id: str
    customer_name: str
    initial_messages: list[Message]
    target_outcome: str
    expected_action: str
    structured_fields: dict[str, Any]
    risk_signals: list[str]
    required_entities: list[str] = field(default_factory=list)
    blocked_claims: list[str] = field(default_factory=list)
    required_structured_actions: list[str] = field(default_factory=list)
    automation_eligible: bool = False
    utility_goal: str = ""
    variants: list[Variant] = field(default_factory=list)


@dataclass
class StructuredAction:
    action_type: str
    payload: dict[str, Any]


@dataclass
class AgentDecision:
    intent: str
    action: str
    confidence: int
    rationale: str
    reply: str
    structured_actions: list[StructuredAction]
    workflow_state: str
    hallucination_risk: float
    escalation_recommended: bool
    provider: str = "mock"


@dataclass
class ConversationTurn:
    speaker: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationLog:
    scenario_id: str
    scenario_title: str
    category: str
    variant_id: str | None
    turns: list[ConversationTurn]
    outcome: dict[str, Any]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JudgeResult:
    frustration_score: float
    loop_risk: float
    hallucination_risk: float
    drop_off_risk: float
    handoff_quality: float
    unsupported_claims: list[str]
    empathy_quality: float
    frustration_evidence: list[str]
    loop_evidence: list[str]
    hallucination_evidence: list[str]
    handoff_evidence: list[str]
    findings: list[str]
    notes: str
    provider: str = "heuristic"
