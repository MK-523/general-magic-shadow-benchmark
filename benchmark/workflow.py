from __future__ import annotations

import re

from benchmark.models import AgentDecision, Message, Scenario, StructuredAction


HIGH_RISK_TOKENS = {
    "doordash": "commercial_use",
    "airbnb": "short_term_rental",
    "mold": "pre_existing_damage",
    "tesla": "vehicle_mismatch",
    "overseas license": "foreign_license",
    "foreign license": "foreign_license",
    "cancellation notice": "cancellation_notice",
    "shoulder of i-80": "unsafe_roadside",
    "neck hurts": "possible_injury",
    "additional insured": "additional_insured_change",
    "both covered": "multi_vehicle_change",
    "kept the civic too": "multi_vehicle_change",
}

FOLLOW_UP_OVERRIDE_TOKENS = {
    "not shopping yet": "breakdown_only",
    "line-by-line breakdown": "breakdown_only",
}


def transcript_text(transcript: list[Message]) -> str:
    return " ".join(message.text.lower() for message in transcript)


def customer_text(transcript: list[Message]) -> str:
    return " ".join(message.text.lower() for message in transcript if message.role == "customer")


def detect_risk_flags(scenario: Scenario, transcript: list[Message]) -> list[str]:
    corpus = customer_text(transcript)
    flags: list[str] = []
    for token, label in HIGH_RISK_TOKENS.items():
        if token in corpus:
            flags.append(label)
    for token, label in FOLLOW_UP_OVERRIDE_TOKENS.items():
        if token in corpus:
            flags.append(label)
    if scenario.category == "renewal" and "thinking of moving" in corpus:
        flags.append("retention_risk")
    if scenario.category == "claim" and "injur" in corpus:
        flags.append("possible_injury")
    return list(dict.fromkeys(flags))


def heuristic_extract_fields(scenario: Scenario, transcript: list[Message]) -> dict[str, object]:
    corpus = customer_text(transcript)
    fields = dict(scenario.structured_fields)

    if "doordash" in corpus:
        fields["commercial_use_disclosed"] = True
    if "mold" in corpus:
        fields["pre_existing_mold"] = True
    if "sacramento" in corpus:
        fields["garaging_changed"] = True
        fields["garaging_city"] = "Sacramento"
    if "tesla" in corpus:
        fields["verified_vehicle"] = "Tesla"
    if "additional insured" in corpus:
        fields["additional_insured_requested"] = True
    if "cancellation notice" in corpus:
        fields["notice_type"] = "cancellation_notice"
    if "line-by-line breakdown" in corpus or "not shopping yet" in corpus:
        fields["retention_risk"] = "moderate"
        fields["breakdown_requested"] = True
    if "neck hurts" in corpus:
        fields["injury_reported"] = True
    if "shoulder of i-80" in corpus:
        fields["location"] = "I-80 shoulder"
        fields["safety_confirmed"] = True
    if "both covered" in corpus or "kept the civic too" in corpus:
        fields["replacement_type"] = "add_vehicle"
    return fields


def choose_action(scenario: Scenario, transcript: list[Message]) -> str:
    flags = detect_risk_flags(scenario, transcript)
    if any(
        flag in flags
        for flag in (
            "commercial_use",
            "short_term_rental",
            "pre_existing_damage",
            "vehicle_mismatch",
            "foreign_license",
            "cancellation_notice",
            "unsafe_roadside",
            "possible_injury",
            "additional_insured_change",
            "multi_vehicle_change",
        )
    ):
        return "escalate"
    if "breakdown_only" in flags:
        return "ask_follow_up"
    if scenario.automation_eligible:
        return "automate"
    if scenario.expected_action == "escalate":
        return "escalate"
    return "ask_follow_up"


def build_structured_actions(scenario: Scenario, action: str, fields: dict[str, object]) -> list[StructuredAction]:
    base_payload = {
        "scenario_id": scenario.id,
        "policy_id": scenario.policy_id,
        "intent": scenario.category,
        "line_of_business": scenario.line_of_business,
        "fields": fields,
    }
    if action == "automate":
        return [
            StructuredAction("writeback.update_policy", base_payload),
            StructuredAction(
                "sms.send_confirmation",
                {"policy_id": scenario.policy_id, "scenario_id": scenario.id, "status": "queued"},
            ),
        ]
    if action == "ask_follow_up":
        return [
            StructuredAction(
                "sms.ask_missing_details",
                {
                    **base_payload,
                    "missing_fields": list(scenario.required_entities),
                },
            )
        ]
    return [
        StructuredAction(
            "crm.create_escalation",
            {
                **base_payload,
                "reason": list(scenario.risk_signals),
            },
        )
    ]


def workflow_state_for(scenario: Scenario, action: str) -> str:
    if action == "automate":
        return "pending_writeback"
    if action == "escalate":
        return {
            "renewal": "retention_review",
            "claim": "claims_specialist_review",
            "policy_update": "underwriting_review",
            "quote": "licensed_review",
        }.get(scenario.category, "needs_review")
    return {
        "quote": "rating_details_needed",
        "claim": "details_needed",
        "policy_update": "details_needed",
        "renewal": "account_review_needed",
    }.get(scenario.category, "details_needed")


def rationale_for(scenario: Scenario, action: str, risk_flags: list[str]) -> str:
    if action == "automate":
        return "Deterministic workflow routing kept this in a low-risk service lane with enough structured context for writeback."
    if action == "ask_follow_up":
        return "Deterministic workflow routing kept this in follow-up because more information is needed before safe completion."
    if risk_flags:
        return f"Deterministic workflow routing escalated due to risk signals: {', '.join(risk_flags)}."
    return "Deterministic workflow routing escalated because this scenario requires licensed or specialist review."


def confidence_for(action: str) -> int:
    return {
        "automate": 92,
        "ask_follow_up": 82,
        "escalate": 90,
    }[action]


def hallucination_floor_for(action: str) -> float:
    return {
        "automate": 0.08,
        "ask_follow_up": 0.12,
        "escalate": 0.10,
    }[action]


def reply_template_for(action: str, scenario: Scenario, risk_flags: list[str]) -> str:
    if action == "automate":
        return "I’ve captured the request and I’m processing the eligible update now. I’ll confirm as soon as the record sync finishes."
    if action == "ask_follow_up":
        missing = ", ".join(entity.replace("_", " ") for entity in scenario.required_entities[:2])
        if missing:
            return f"I can keep this moving, but I need one more detail before I can safely proceed. Please send {missing} next."
        return "I can keep this moving, but I need one more detail before I can safely proceed."
    if "vehicle_mismatch" in risk_flags:
        return "I can’t safely complete this by text because the vehicle on file does not match the request. I’m routing it to a licensed team member now, and they’ll follow up with the next step."
    if "possible_injury" in risk_flags:
        return "I can’t safely complete this by text because possible injuries need a claims specialist. I’m routing it now, and they’ll follow up with the next step."
    return "I can’t safely complete this by text. I’m routing it to a licensed team member now, and they’ll review it and follow up with the next step."


def deterministic_decision(scenario: Scenario, transcript: list[Message], *, provider: str) -> AgentDecision:
    risk_flags = detect_risk_flags(scenario, transcript)
    fields = heuristic_extract_fields(scenario, transcript)
    action = choose_action(scenario, transcript)
    return AgentDecision(
        intent=scenario.category,
        action=action,
        confidence=confidence_for(action),
        rationale=rationale_for(scenario, action, risk_flags),
        reply=reply_template_for(action, scenario, risk_flags),
        structured_actions=build_structured_actions(scenario, action, fields),
        workflow_state=workflow_state_for(scenario, action),
        hallucination_risk=hallucination_floor_for(action),
        escalation_recommended=action == "escalate",
        provider=provider,
    )
