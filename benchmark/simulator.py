from __future__ import annotations

from benchmark.models import Message, Scenario, Variant, AgentDecision


class SyntheticUserSimulator:
    def next_turn(self, scenario: Scenario, variant: Variant | None = None) -> list[Message]:
        if variant is None:
            return []
        return variant.injected_messages

    def continue_after_decision(
        self,
        scenario: Scenario,
        transcript: list[Message],
        decision: AgentDecision,
        variant: Variant | None = None,
    ) -> list[Message]:
        if decision.action != "ask_follow_up":
            return []

        # simple completion: provide remaining structured fields
        fields = scenario.structured_fields
        parts = []
        for key, value in fields.items():
            parts.append(f"{key.replace('_', ' ')} is {value}")

        if not parts:
            return []

        reply = "Here are the remaining details: " + ", ".join(parts) + "."
        return [Message("customer", reply)]
