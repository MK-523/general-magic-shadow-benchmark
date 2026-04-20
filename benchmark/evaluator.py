from __future__ import annotations

from benchmark.actions import has_escalation_action
from benchmark.models import AgentDecision, JudgeResult, Message, Scenario, Variant
from benchmark.policy import assess_policy


def evaluate_conversation(
    scenario: Scenario,
    transcript: list[Message],
    decision: AgentDecision,
    variant: Variant | None = None,
    judge_result: JudgeResult | None = None,
) -> dict:
    expected_action = variant.expected_action if variant and variant.expected_action else scenario.expected_action

    policy = assess_policy(
        scenario,
        transcript,
        raw_action=decision.action,
        structured_action_names=[a.action_type for a in decision.structured_actions],
        escalation_recommended=decision.escalation_recommended,
        variant=variant,
    )

    normalized_action = policy["policy_action"]

    action_success = normalized_action == expected_action
    containment = normalized_action != "automate" or not policy["blocked_automation"]

    execution_success = True  # will be overridden by batch runner

    success = action_success and containment and execution_success

    return {
        "success": success,
        "action_success": action_success,
        "containment_ok": containment,
        "expected_action": expected_action,
        "actual_action": normalized_action,
        "policy_violations": list(policy["violations"]),
    }
