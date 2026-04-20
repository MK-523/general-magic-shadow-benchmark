from __future__ import annotations

from typing import Any

from benchmark.models import AgentDecision, Scenario


ACTION_LATENCY_MS = {
    "writeback.update_policy": 180,
    "sms.send_confirmation": 55,
    "sms.ask_missing_details": 45,
    "crm.create_escalation": 90,
}


def _payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields", {})
    return fields if isinstance(fields, dict) else {}


def _required_entities_present(scenario: Scenario, payload: dict[str, Any]) -> tuple[bool, list[str]]:
    fields = _payload_fields(payload)
    missing = [entity for entity in scenario.required_entities if entity not in fields]
    return not missing, missing


def _system_name(action_type: str) -> str:
    if action_type.startswith("writeback."):
        return "policy_admin"
    if action_type.startswith("sms."):
        return "messaging"
    if "escalat" in action_type:
        return "crm"
    return "workflow"


def execute_structured_actions(scenario: Scenario, decision: AgentDecision) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    writeback_succeeded = False

    for index, action in enumerate(decision.structured_actions, start=1):
        action_type = action.action_type
        payload = action.payload if isinstance(action.payload, dict) else {}
        latency_ms = ACTION_LATENCY_MS.get(action_type, 70)
        status = "succeeded"
        detail = "completed"

        if action_type == "writeback.update_policy":
            required_present, missing = _required_entities_present(scenario, payload)
            if not required_present:
                status = "failed"
                detail = f"missing required fields: {', '.join(missing)}"
            else:
                writeback_succeeded = True
                detail = "policy update accepted by simulated policy admin"
        elif action_type == "sms.send_confirmation":
            if decision.action == "automate" and not writeback_succeeded:
                status = "failed"
                detail = "confirmation blocked because no successful writeback was recorded"
            else:
                detail = "confirmation queued to messaging provider"
        elif action_type == "sms.ask_missing_details":
            if not payload.get("missing_fields") and not _payload_fields(payload):
                status = "failed"
                detail = "follow-up message lacked missing-field context"
            else:
                detail = "follow-up request queued"
        elif "escalat" in action_type:
            if not payload.get("reason"):
                status = "failed"
                detail = "escalation missing routing reason"
            else:
                detail = "escalation created in simulated CRM queue"
        elif not payload:
            status = "failed"
            detail = "action payload was empty"

        steps.append(
            {
                "step": index,
                "action_type": action_type,
                "system": _system_name(action_type),
                "status": status,
                "latency_ms": latency_ms,
                "detail": detail,
            }
        )

    success_count = sum(1 for step in steps if step["status"] == "succeeded")
    failure_count = len(steps) - success_count
    total_latency_ms = sum(int(step["latency_ms"]) for step in steps)
    required_actions = list(scenario.required_structured_actions)
    action_names = [action.action_type for action in decision.structured_actions]
    required_action_coverage = (
        sum(1 for action_name in required_actions if action_name in action_names) / max(len(required_actions), 1)
        if required_actions
        else 1.0
    )

    return {
        "overall_success": failure_count == 0,
        "executed_action_count": len(steps),
        "success_count": success_count,
        "failure_count": failure_count,
        "total_latency_ms": total_latency_ms,
        "required_action_coverage": round(required_action_coverage, 2),
        "steps": steps,
    }
