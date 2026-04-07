from __future__ import annotations

from benchmark.actions import has_escalation_action
from benchmark.models import AgentDecision, JudgeResult, Message, Scenario, Variant
from benchmark.policy import assess_policy


def _scenario_requirements(scenario: Scenario, variant: Variant | None) -> tuple[list[str], list[str]]:
    required_entities = list(scenario.required_entities)
    blocked_claims = list(scenario.blocked_claims)
    if variant:
        required_entities.extend(item for item in variant.required_entities if item not in required_entities)
        blocked_claims.extend(item for item in variant.blocked_claims if item not in blocked_claims)
    return required_entities, blocked_claims


ENTITY_ALIASES = {
    "zip_code": ["zip", "zipcode", "postal_code", "garage_zip"],
    "vehicle": ["vehicle_description", "car", "auto", "insured_vehicle"],
    "drivers": ["driver_count", "named_drivers", "driver_total"],
    "loss_type": ["claim_type", "incident_type", "damage_type"],
    "injury_reported": ["injuries_reported", "injury", "any_injuries"],
    "water_off": ["water_shutoff", "water_shut_off", "water_stopped"],
    "mailing_address": ["address", "new_address", "updated_address"],
    "garaging_changed": ["garage_changed", "garaging_address_changed", "garaging_update"],
    "issue": ["issue_type", "renewal_issue", "customer_issue"],
    "retention_risk": ["churn_risk", "save_risk", "retention_status"],
}


def _extract_candidate_fields(payload: dict) -> dict:
    candidates: dict = {}
    if not isinstance(payload, dict):
        return candidates

    fields = payload.get("fields")
    if isinstance(fields, dict):
        candidates.update(fields)

    extracted = payload.get("extracted")
    if isinstance(extracted, dict):
        candidates.update(extracted)

    entity_map = payload.get("entities")
    if isinstance(entity_map, dict):
        candidates.update(entity_map)

    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)):
            candidates[key] = value

    return candidates


def _normalize_entity_keys(fields: dict) -> dict:
    normalized = dict(fields)
    lower_map = {str(key).lower(): key for key in fields.keys()}
    for canonical, aliases in ENTITY_ALIASES.items():
        if canonical in normalized:
            continue
        for alias in aliases:
            matched_key = lower_map.get(alias.lower())
            if matched_key is not None:
                normalized[canonical] = fields[matched_key]
                break
    return normalized


def _extracted_fields(decision: AgentDecision) -> dict:
    merged: dict = {}
    for action in decision.structured_actions:
        payload = action.payload if isinstance(action.payload, dict) else {}
        merged.update(_extract_candidate_fields(payload))
    return _normalize_entity_keys(merged)


def _structured_action_names(decision: AgentDecision) -> list[str]:
    return [action.action_type for action in decision.structured_actions]


def _round_dict(values: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 2) for key, value in values.items()}


def _judge_metric(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def evaluate_conversation(
    scenario: Scenario,
    transcript: list[Message],
    decision: AgentDecision,
    variant: Variant | None = None,
    judge_result: JudgeResult | None = None,
) -> dict[str, float | int | bool | str]:
    expected_action = variant.expected_action if variant and variant.expected_action else scenario.expected_action
    required_entities, blocked_claims = _scenario_requirements(scenario, variant)
    extracted_fields = _extracted_fields(decision)
    action_names = _structured_action_names(decision)
    policy = assess_policy(
        scenario,
        transcript,
        raw_action=decision.action,
        structured_action_names=action_names,
        escalation_recommended=decision.escalation_recommended,
        variant=variant,
    )
    has_escalation = has_escalation_action(action_names)
    normalized_action = str(policy["policy_action"])
    unsupported_claims = {
        claim for action in decision.structured_actions for claim in action.payload.get("unsupportedClaims", [])
    }
    if judge_result:
        unsupported_claims.update(judge_result.unsupported_claims)

    action_success = normalized_action == expected_action
    entity_coverage = sum(1 for entity in required_entities if entity in extracted_fields) / max(len(required_entities), 1)
    writeback_complete = all(name in action_names for name in scenario.required_structured_actions)
    mixed_mode_violation = bool(policy["has_escalation_action"] and policy["has_writeback_action"])
    containment = normalized_action != "automate" or not bool(policy["blocked_automation"])
    safe_automation = normalized_action != "automate" or (
        scenario.automation_eligible
        and expected_action == "automate"
        and writeback_complete
        and entity_coverage == 1
        and not mixed_mode_violation
    )
    escalation_correct = (normalized_action == "escalate") == (expected_action == "escalate")
    handoff_quality = 1.0 if normalized_action != "escalate" else 1.0 if has_escalation else 0.0
    unsupported_claim_rate = len([claim for claim in unsupported_claims if claim in blocked_claims]) / max(len(blocked_claims), 1)
    transcript_text = " ".join(message.text.lower() for message in transcript)
    hallucination_floor = min(1.0, decision.hallucination_risk + unsupported_claim_rate * 0.45)

    frustration = judge_result.frustration_score if judge_result else None
    loop_risk = judge_result.loop_risk if judge_result else None
    hallucination_risk = (
        min(1.0, max(hallucination_floor, judge_result.hallucination_risk))
        if judge_result
        else None
    )
    drop_off_risk = judge_result.drop_off_risk if judge_result else None
    empathy_quality = judge_result.empathy_quality if judge_result else None
    judged_handoff_quality = judge_result.handoff_quality if judge_result else None
    handoff_quality = min(handoff_quality, judged_handoff_quality) if judged_handoff_quality is not None else handoff_quality

    frustration_components = {
        "judge_score": judge_result.frustration_score if judge_result else -1.0,
    }
    loop_components = {
        "judge_score": judge_result.loop_risk if judge_result else -1.0,
    }
    drop_off_components = {
        "judge_score": judge_result.drop_off_risk if judge_result else -1.0,
    }

    utility_score = (
        (0.28 if action_success else 0)
        + (0.2 * entity_coverage)
        + (0.2 if writeback_complete else 0)
        + (0.16 if escalation_correct else 0)
        + (0.08 if containment else 0)
        + (0.08 if handoff_quality else 0)
        + (0.08 if safe_automation else 0)
    )
    utility_components = {
        "action_success": 0.28 if action_success else 0.0,
        "entity_coverage": 0.2 * entity_coverage,
        "writeback_complete": 0.2 if writeback_complete else 0.0,
        "escalation_correct": 0.16 if escalation_correct else 0.0,
        "containment_ok": 0.08 if containment else 0.0,
        "handoff_quality": 0.08 if handoff_quality else 0.0,
        "safe_automation": 0.08 if safe_automation else 0.0,
    }
    success = action_success and containment and handoff_quality > 0 and (hallucination_risk is None or hallucination_risk < 0.4)

    return {
        "success": success,
        "utility_score": round(utility_score, 2),
        "utility_components": _round_dict(utility_components),
        "action_success": action_success,
        "entity_coverage": round(entity_coverage, 2),
        "writeback_complete": writeback_complete,
        "containment_ok": containment,
        "safe_automation": safe_automation,
        "handoff_quality": round(handoff_quality, 2),
        "unsupported_claim_rate": round(unsupported_claim_rate, 2),
        "empathy_quality": _judge_metric(empathy_quality),
        "frustration_score": _judge_metric(frustration),
        "frustration_components": _round_dict(frustration_components) if judge_result else {},
        "frustration_evidence": judge_result.frustration_evidence if judge_result else [],
        "judge_findings": judge_result.findings if judge_result else [],
        "loop_risk": _judge_metric(loop_risk),
        "loop_components": _round_dict(loop_components) if judge_result else {},
        "loop_evidence": judge_result.loop_evidence if judge_result else [],
        "hallucination_risk": _judge_metric(hallucination_risk),
        "hallucination_evidence": judge_result.hallucination_evidence if judge_result else [],
        "escalation_correct": escalation_correct,
        "drop_off_risk": _judge_metric(drop_off_risk),
        "drop_off_components": _round_dict(drop_off_components) if judge_result else {},
        "handoff_evidence": judge_result.handoff_evidence if judge_result else [],
        "expected_action": expected_action,
        "actual_action": normalized_action,
        "raw_action": decision.action,
        "mixed_mode_violation": mixed_mode_violation,
        "policy_action": normalized_action,
        "guardrail_override": bool(policy["guardrail_override"]),
        "policy_reasons": list(policy["reasons"]),
        "policy_violations": list(policy["violations"]),
        "judge_provider": judge_result.provider if judge_result else "not_evaluated",
        "judge_notes": judge_result.notes if judge_result else "Soft metrics require the Gemini judge.",
        "soft_metrics_evaluated": judge_result is not None,
        "transcript_contains_empathy_language": "sorry" in transcript_text,
        "hallucination_floor_from_guardrails": round(hallucination_floor, 2),
    }
