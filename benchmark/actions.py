from __future__ import annotations

from typing import Iterable


CANONICAL_ACTIONS = ("automate", "ask_follow_up", "escalate")


def normalize_action(
    action: str | None,
    *,
    structured_action_names: Iterable[str] | None = None,
    escalation_recommended: bool | None = None,
) -> str:
    raw = (action or "").strip()
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    normalized = "_".join(part for part in normalized.split("_") if part)

    if normalized in CANONICAL_ACTIONS:
        return normalized

    action_names = [name.lower() for name in (structured_action_names or [])]
    label = normalized

    if escalation_recommended or any("escalat" in name for name in action_names):
        return "escalate"

    if "escalat" in label or "specialist" in label or "human_intervention" in label or "underwriting_review" in label:
        return "escalate"

    if any(name == "sms.ask_missing_details" for name in action_names):
        return "ask_follow_up"

    if any(name.startswith("writeback.") for name in action_names):
        return "automate"

    if any(
        token in label
        for token in (
            "ask",
            "missing",
            "clarify",
            "confirm_safety",
            "confirm_usage",
            "collect",
            "gather",
            "inquire",
            "awaiting",
            "await",
            "details",
            "review_and_breakdown",
        )
    ):
        return "ask_follow_up"

    if any(
        token in label
        for token in (
            "update",
            "send",
            "provide",
            "issue",
            "deliver",
            "confirmation",
            "complete",
            "writeback",
        )
    ):
        return "automate"

    return "ask_follow_up"


def has_escalation_action(structured_action_names: Iterable[str] | None) -> bool:
    return any("escalat" in (name or "").lower() for name in (structured_action_names or []))


def has_writeback_action(structured_action_names: Iterable[str] | None) -> bool:
    return any((name or "").lower().startswith("writeback.") for name in (structured_action_names or []))


def has_follow_up_action(structured_action_names: Iterable[str] | None) -> bool:
    normalized_names = [(name or "").lower() for name in (structured_action_names or [])]
    return any(
        name == "sms.ask_missing_details" or name.endswith(".ask_missing_details")
        for name in normalized_names
    )
