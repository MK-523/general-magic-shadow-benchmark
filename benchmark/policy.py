from __future__ import annotations

from dataclasses import replace

from benchmark.actions import (
    has_escalation_action,
    has_follow_up_action,
    has_writeback_action,
    normalize_action,
)
from benchmark.models import AgentDecision, Message, Scenario, StructuredAction, Variant

LEGACY_PLACEHOLDER_ACTIONS = {"automate", "ask_follow_up", "escalate"}


SAFE_ESCALATION_REPLY = (
    "I’m routing this to a licensed team member now so they can review the policy details and handle the next step."
)
SAFE_FOLLOW_UP_REPLY = (
    "I can keep this moving, but I need one more detail before I can safely complete the workflow."
)
SAFE_AUTOMATE_REPLY = (
    "I’ve captured the request and I’m processing the eligible update now. I’ll confirm as soon as the record sync finishes."
)


def variant_for_id(scenario: Scenario, variant_id: str | None) -> Variant | None:
    if not variant_id:
        return None
    return next((item for item in scenario.variants if item.id == variant_id), None)


def _contract_action(scenario: Scenario, variant: Variant | None) -> str:
    if variant and variant.expected_action:
        return variant.expected_action
    return scenario.expected_action


def _contract_reasons(scenario: Scenario, variant: Variant | None) -> list[str]:
    reasons = list(scenario.risk_signals)
    if variant:
        reasons.append(f"Variant: {variant.label}")
        if variant.description:
            reasons.append(variant.description)
    return reasons


def _required_entities(scenario: Scenario, variant: Variant | None) -> list[str]:
    entities = list(scenario.required_entities)
    if variant:
        for entity in variant.required_entities:
            if entity not in entities:
                entities.append(entity)
    return entities


def assess_policy(
    scenario: Scenario,
    transcript: list[Message],
    *,
    raw_action: str | None,
    structured_action_names: list[str],
    escalation_recommended: bool | None = None,
    variant: Variant | None = None,
) -> dict[str, object]:
    expected_action = _contract_action(scenario, variant)
    normalized_action = normalize_action(
        raw_action,
        structured_action_names=structured_action_names,
        escalation_recommended=escalation_recommended,
    )
    transcript_text = " ".join(message.text.lower() for message in transcript)
    has_escalation = has_escalation_action(structured_action_names)
    has_writeback = has_writeback_action(structured_action_names)
    has_follow_up = has_follow_up_action(structured_action_names)
    must_escalate = expected_action == "escalate"
    should_automate = expected_action == "automate" and scenario.automation_eligible
    blocked_automation = normalized_action == "automate" and (
        not scenario.automation_eligible or expected_action != "automate"
    )
    transcript_risk_override = False

    policy_action = normalized_action
    reasons: list[str] = []
    violations: list[str] = []
    missing_fields = _required_entities(scenario, variant)

    if must_escalate:
        policy_action = "escalate"
        reasons.extend(_contract_reasons(scenario, variant))
        if normalized_action != "escalate":
            violations.append("Escalation-required scenario was not escalated.")

    if blocked_automation:
        if must_escalate:
            policy_action = "escalate"
        else:
            policy_action = "ask_follow_up"
        violations.append("Automation attempted outside the allowed servicing boundary.")
        if not scenario.automation_eligible:
            reasons.append("Scenario contract disallows direct automation.")

    if has_writeback and policy_action != "automate":
        violations.append("Writeback emitted for a non-automation outcome.")

    if has_escalation and has_writeback:
        violations.append("Mixed-mode behavior: escalation and writeback were emitted together.")

    if expected_action == "ask_follow_up" and normalized_action == "escalate":
        reasons.append("Model escalated a scenario contract that only required follow-up.")

    if expected_action == "automate" and normalized_action != "automate":
        reasons.append("Model failed to fully deflect a contract-eligible servicing request.")

    if "airbnb" in transcript_text:
        reasons.append("Short-term rental disclosure changes occupancy risk.")
        transcript_risk_override = True
    if "doordash" in transcript_text:
        reasons.append("Commercial use disclosure affects rating and underwriting.")
        transcript_risk_override = True
    if "overseas license" in transcript_text or "foreign license" in transcript_text:
        reasons.append("Non-U.S. licensing needs human review.")
        transcript_risk_override = True
    if "cancellation notice" in transcript_text:
        reasons.append("Active cancellation notice creates lapse sensitivity.")
        transcript_risk_override = True
    if "tesla" in transcript_text and "honda" in transcript_text:
        reasons.append("Requested document no longer matches the verified vehicle on file.")
        transcript_risk_override = True
    if "shoulder of i-80" in transcript_text:
        reasons.append("Unsafe roadside location should route to urgent human handling.")
        transcript_risk_override = True
    if "neck hurts" in transcript_text or "injur" in transcript_text:
        reasons.append("Possible injury creates claims escalation risk.")
        transcript_risk_override = True
    if "additional insured" in transcript_text:
        reasons.append("Additional insured changes should not be handled as routine COI automation.")
        transcript_risk_override = True
    if "mold" in transcript_text:
        transcript_risk_override = True
    if "kept the civic too" in transcript_text or "both covered" in transcript_text:
        reasons.append("Multiple active vehicles need endorsement review.")
        transcript_risk_override = True

    if transcript_risk_override:
        policy_action = "escalate"
        if normalized_action != "escalate":
            violations.append("Transcript-level risk trigger requires escalation.")

    if should_automate and not transcript_risk_override and normalized_action != "automate":
        policy_action = "automate"
        reasons.append("Scenario contract allows direct automation with deterministic writeback.")
        violations.append("Model failed to fully deflect a contract-eligible servicing request.")

    return {
        "expected_action": expected_action,
        "normalized_action": normalized_action,
        "policy_action": policy_action,
        "guardrail_override": policy_action != normalized_action,
        "must_escalate": must_escalate,
        "should_automate": should_automate,
        "blocked_automation": blocked_automation,
        "has_escalation_action": has_escalation,
        "has_writeback_action": has_writeback,
        "has_follow_up_action": has_follow_up,
        "missing_fields": missing_fields,
        "reasons": list(dict.fromkeys(reasons)),
        "violations": list(dict.fromkeys(violations)),
    }


def apply_policy_guardrails(
    scenario: Scenario,
    transcript: list[Message],
    decision: AgentDecision,
    *,
    variant: Variant | None = None,
) -> tuple[AgentDecision, dict[str, object]]:
    cleaned_actions = [
        action for action in decision.structured_actions if action.action_type not in LEGACY_PLACEHOLDER_ACTIONS
    ]
    structured_action_names = [action.action_type for action in cleaned_actions]
    assessment = assess_policy(
        scenario,
        transcript,
        raw_action=decision.action,
        structured_action_names=structured_action_names,
        escalation_recommended=decision.escalation_recommended,
        variant=variant,
    )
    policy_action = str(assessment["policy_action"])
    assessment["reply_violations"] = list(_reply_violations(decision.reply, assessment))
    structured_actions = _backfill_structured_actions(
        scenario,
        cleaned_actions,
        policy_action=policy_action,
        assessment=assessment,
        variant=variant,
    )

    if policy_action == decision.action:
        updated = replace(
            decision,
            structured_actions=structured_actions,
            escalation_recommended=policy_action == "escalate",
        )
        if assessment["reply_violations"] or structured_actions != decision.structured_actions:
            return _rewrite_reply(updated, policy_action, assessment), assessment
        return updated, assessment

    if policy_action == "escalate":
        reply = SAFE_ESCALATION_REPLY
        workflow_state = "policy_guardrail_escalated"
        rationale = f"{decision.rationale} Policy guardrail forced escalation."
    elif policy_action == "ask_follow_up":
        reply = SAFE_FOLLOW_UP_REPLY
        workflow_state = "policy_guardrail_follow_up"
        rationale = f"{decision.rationale} Policy guardrail blocked automation."
    else:
        reply = decision.reply
        workflow_state = decision.workflow_state
        rationale = decision.rationale

    updated = replace(
        decision,
        action=policy_action,
        structured_actions=structured_actions,
        reply=reply,
        workflow_state=workflow_state,
        rationale=rationale,
        escalation_recommended=policy_action == "escalate",
    )
    return _rewrite_reply(updated, policy_action, assessment), assessment


def _base_fields_payload(scenario: Scenario, variant: Variant | None) -> dict[str, object]:
    fields = dict(scenario.structured_fields)
    if variant:
        for entity in variant.required_entities:
            if entity in scenario.structured_fields:
                fields[entity] = scenario.structured_fields[entity]
    return {
        "scenario_id": scenario.id,
        "policy_id": scenario.policy_id,
        "intent": scenario.category,
        "line_of_business": scenario.line_of_business,
        "fields": fields,
    }


def _merge_payload(action: StructuredAction, extra: dict[str, object]) -> StructuredAction:
    payload = dict(action.payload) if isinstance(action.payload, dict) else {}
    for key, value in extra.items():
        if key == "fields" and isinstance(value, dict):
            existing_fields = payload.get("fields", {})
            if not isinstance(existing_fields, dict):
                existing_fields = {}
            payload["fields"] = {**value, **existing_fields}
        elif key not in payload:
            payload[key] = value
    return StructuredAction(action.action_type, payload)


def _backfill_structured_actions(
    scenario: Scenario,
    structured_actions: list[StructuredAction],
    *,
    policy_action: str,
    assessment: dict[str, object],
    variant: Variant | None = None,
) -> list[StructuredAction]:
    actions = list(structured_actions)
    names = [action.action_type for action in actions]
    base_payload = _base_fields_payload(scenario, variant)

    if policy_action != "automate":
        actions = [
            action
            for action in actions
            if not action.action_type.startswith("writeback.") and action.action_type != "sms.send_confirmation"
        ]
        names = [action.action_type for action in actions]

    if policy_action == "automate":
        enriched: list[StructuredAction] = []
        for action in actions:
            if action.action_type in {"writeback.update_policy", "sms.send_confirmation"}:
                enriched.append(_merge_payload(action, base_payload))
            else:
                enriched.append(action)
        actions = enriched
        names = [action.action_type for action in actions]
        if "writeback.update_policy" not in names:
            actions.append(StructuredAction("writeback.update_policy", base_payload))
        if "sms.send_confirmation" not in names:
            actions.append(
                StructuredAction(
                    "sms.send_confirmation",
                    {
                        "policy_id": scenario.policy_id,
                        "scenario_id": scenario.id,
                        "status": "queued",
                    },
                )
            )
        return actions

    if policy_action == "ask_follow_up":
        enriched = []
        follow_up_payload = {
            "policy_id": scenario.policy_id,
            "scenario_id": scenario.id,
            "reason": assessment["reasons"],
            "missing_fields": list(scenario.required_entities),
            **base_payload,
        }
        for action in actions:
            if has_follow_up_action([action.action_type]):
                enriched.append(_merge_payload(action, follow_up_payload))
            else:
                enriched.append(action)
        actions = enriched
        if not has_follow_up_action([action.action_type for action in actions]):
            actions.append(StructuredAction("sms.ask_missing_details", follow_up_payload))
        return actions

    enriched = []
    escalation_payload = {
        "policy_id": scenario.policy_id,
        "scenario_id": scenario.id,
        "reason": assessment["reasons"] or scenario.risk_signals,
        **base_payload,
    }
    for action in actions:
        if "escalat" in action.action_type.lower():
            enriched.append(_merge_payload(action, escalation_payload))
        else:
            enriched.append(action)
    actions = enriched
    if not has_escalation_action([action.action_type for action in actions]):
        actions.append(StructuredAction("crm.create_escalation", escalation_payload))
    return actions


def _reply_violations(reply: str, assessment: dict[str, object]) -> list[str]:
    lowered = (reply or "").lower()
    violations: list[str] = []

    unsupported_phrases = [
        "you're covered",
        "you are covered",
        "claim is approved",
        "approved",
        "guaranteed",
        "we updated your policy",
        "i updated your policy",
        "i'll send it right away",
        "we have completed this update",
    ]
    if any(phrase in lowered for phrase in unsupported_phrases):
        violations.append("Reply uses unsupported certainty or completion language.")

    if assessment["policy_action"] == "escalate" and not any(
        phrase in lowered for phrase in ("routing", "review", "specialist", "team member", "follow up")
    ):
        violations.append("Escalation reply does not clearly explain next-step handoff.")

    if assessment["policy_action"] == "ask_follow_up" and "?" not in reply:
        violations.append("Follow-up reply does not clearly request the next required detail.")

    return violations


def _rewrite_reply(decision: AgentDecision, policy_action: str, assessment: dict[str, object]) -> AgentDecision:
    if policy_action == "escalate":
        reply = _escalation_reply(assessment)
    elif policy_action == "ask_follow_up":
        reply = _follow_up_reply(assessment)
    else:
        reply = _automate_reply(assessment)

    return replace(decision, reply=reply)


def _customer_facing_reason(assessment: dict[str, object]) -> str:
    reasons = " ".join(str(item).lower() for item in assessment.get("reasons", []))

    mapping = [
        ("vehicle on file", "the vehicle on file does not match the request"),
        ("tesla", "the vehicle on file does not match the request"),
        ("short-term rental", "the occupancy details need underwriting review"),
        ("airbnb", "the occupancy details need underwriting review"),
        ("doordash", "commercial use changes how this needs to be reviewed"),
        ("foreign license", "the driver licensing details need review"),
        ("non-u.s. licensing", "the driver licensing details need review"),
        ("cancellation notice", "the cancellation notice needs urgent account review"),
        ("unsafe roadside", "your situation may need urgent human handling"),
        ("possible injury", "possible injuries need a claims specialist"),
        ("additional insured", "adding an additional insured cannot be completed as a routine certificate request"),
        ("mold", "pre-existing damage details need claims review"),
    ]
    for token, message in mapping:
        if token in reasons:
            return message

    if assessment.get("must_escalate"):
        return "this request needs licensed review before it can move forward"
    if assessment.get("should_automate"):
        return "the request is eligible for a standard servicing update"
    return "I need one more detail to move this forward safely"


def _escalation_reply(assessment: dict[str, object]) -> str:
    reason = _customer_facing_reason(assessment)
    return (
        f"I can’t safely complete this by text because {reason}. "
        "I’m routing it to a licensed team member now, and they’ll review it and follow up with the next step."
    )


def _automate_reply(assessment: dict[str, object]) -> str:
    reason = _customer_facing_reason(assessment)
    return (
        f"I’ve captured the request because {reason}. "
        "I’m processing the eligible update now and I’ll confirm as soon as the record sync finishes."
    )


def _follow_up_reply(assessment: dict[str, object]) -> str:
    reason = _customer_facing_reason(assessment)
    missing_fields = [str(field).replace("_", " ") for field in assessment.get("missing_fields", [])]
    next_ask = ""
    if missing_fields:
        requested = ", ".join(missing_fields[:2])
        next_ask = f" Please send {requested} next."
    return (
        "I can keep this moving, but I need one more detail before I can safely proceed. "
        f"This helps because {reason}.{next_ask}"
    )
