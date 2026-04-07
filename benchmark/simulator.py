from __future__ import annotations

from benchmark.models import Message, Scenario, Variant


class SyntheticUserSimulator:
    def next_turn(self, scenario: Scenario, variant: Variant | None = None) -> list[Message]:
        if variant is None:
            return []
        return variant.injected_messages
