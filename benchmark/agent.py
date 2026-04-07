from __future__ import annotations

import re

from benchmark.models import AgentDecision, Message, Scenario, StructuredAction


class MockInsuranceAgent:
    def run(self, scenario: Scenario, transcript: list[Message]) -> AgentDecision:
        corpus = " ".join(message.text.lower() for message in transcript)
        intent = scenario.category
        action = scenario.expected_action

        if any(token in corpus for token in ["doordash", "mold", "sacramento", "thinking of moving"]):
            action = "escalate"
        elif any(token in corpus for token in ["line-by-line breakdown", "not shopping yet"]):
            action = "ask_follow_up"

        confidence = {
            "automate": 90,
            "ask_follow_up": 76,
            "escalate": 64,
        }[action]

        workflow_state = {
            "quote": "rating_details_needed",
            "claim": "fnol_in_progress",
            "policy_update": "pending_writeback" if action == "automate" else "needs_review",
            "renewal": "retention_review",
        }[scenario.category]

        fields = dict(scenario.structured_fields)
        if "sacramento" in corpus:
            fields["garaging_changed"] = True
            fields["garaging_city"] = "Sacramento"
        if "doordash" in corpus:
            fields["commercial_use_disclosed"] = True
        if "mold" in corpus:
            fields["pre_existing_mold"] = True
        if "line-by-line breakdown" in corpus:
            fields["breakdown_requested"] = True

        payload = {
            "scenarioId": scenario.id,
            "policyId": scenario.policy_id,
            "intent": intent,
            "lineOfBusiness": scenario.line_of_business,
            "fields": fields,
        }

        if action == "automate":
            structured_actions = [
                StructuredAction("writeback.update_policy", payload),
                StructuredAction("sms.send_confirmation", {"policyId": scenario.policy_id}),
            ]
            reply = "I’ve captured the verified change and I’m updating the record now. I’ll confirm once the writeback completes."
            rationale = "The request stays inside a low-risk servicing boundary with enough detail for deterministic writeback."
            hallucination_risk = 0.08
        elif action == "ask_follow_up":
            structured_actions = [
                StructuredAction("sms.ask_missing_details", {"policyId": scenario.policy_id, "intent": intent})
            ]
            reply = "I can keep this moving, but I need a bit more detail before I can safely complete the workflow."
            rationale = "The workflow looks serviceable, but key details are still missing for a safe system action."
            hallucination_risk = 0.18
        else:
            structured_actions = [
                StructuredAction("crm.create_escalation", {"policyId": scenario.policy_id, "reason": scenario.risk_signals})
            ]
            reply = "I’m routing this to a licensed team member now so they can review the policy details and take the next step."
            rationale = "The transcript contains signals that make autonomous servicing unsafe or non-compliant."
            hallucination_risk = 0.34

        unsupported_claims = []
        if re.search(r"\bcovered\b|\bapproved\b|\bguarantee\b", corpus):
            unsupported_claims.append("coverage confirmed")
        if scenario.category == "quote" and "quote" in corpus and action != "escalate":
            unsupported_claims.append("quoted price")
        if scenario.category == "renewal" and "fix it" in corpus:
            unsupported_claims.append("premium fixed")
        if unsupported_claims:
            payload["unsupportedClaims"] = unsupported_claims

        return AgentDecision(
            intent=intent,
            action=action,
            confidence=confidence,
            rationale=rationale,
            reply=reply,
            structured_actions=structured_actions,
            workflow_state=workflow_state,
            hallucination_risk=hallucination_risk,
            escalation_recommended=action == "escalate",
        )
