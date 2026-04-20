from __future__ import annotations

import os
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from benchmark.actions import normalize_action
from benchmark.models import AgentDecision, JudgeResult, Message, Scenario, StructuredAction
from benchmark.workflow import deterministic_decision


class GeminiAdapterUnavailable(RuntimeError):
    pass


class ClaudeAdapterUnavailable(RuntimeError):
    pass


class OllamaAdapterUnavailable(RuntimeError):
    pass


_LAST_GEMINI_CALL_AT = 0.0


def gemini_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def build_gemini_client() -> Any:
    if not gemini_enabled():
        raise GeminiAdapterUnavailable("GEMINI_API_KEY is not set.")

    try:
        from google import genai
    except ImportError as exc:
        raise GeminiAdapterUnavailable("Install google-genai to enable Gemini adapters.") from exc

    return genai.Client()


def claude_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def build_claude_client() -> Any:
    if not claude_enabled():
        raise ClaudeAdapterUnavailable("ANTHROPIC_API_KEY is not set.")

    try:
        import anthropic
    except ImportError as exc:
        raise ClaudeAdapterUnavailable("Install anthropic to enable Claude adapters.") from exc

    return anthropic.Anthropic()


def ollama_enabled() -> bool:
    return bool(os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_agent_model_name() -> str:
    return os.getenv("OLLAMA_AGENT_MODEL", "qwen2.5:14b-instruct")


def ollama_judge_model_name() -> str:
    return os.getenv("OLLAMA_JUDGE_MODEL", "qwen2.5:14b-instruct")


def _post_json(url: str, payload: dict[str, Any], label: str) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise OllamaAdapterUnavailable(
            f"Ollama request failed for {label}. Is Ollama running at {ollama_base_url()}?"
        ) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaAdapterUnavailable(f"Ollama returned non-JSON for {label}.") from exc


def _ollama_generate(*, model: str, system: str, prompt: str, label: str) -> str:
    response = _post_json(
        f"{ollama_base_url()}/api/generate",
        {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        },
        label,
    )
    text = response.get("response", "")
    if not text:
        raise OllamaAdapterUnavailable(f"Ollama returned empty text for {label}.")
    return text


class OllamaInsuranceAgent:
    def __init__(self) -> None:
        self.model = ollama_agent_model_name()

    def run(self, scenario: Scenario, transcript: list[Message]) -> AgentDecision:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        system = (
            "You are an insurance SMS shadow agent. Return only valid JSON. "
            "Choose action from: automate, ask_follow_up, escalate."
        )
        prompt = (
            f"Scenario: {scenario.title}\n"
            f"Required entities: {scenario.required_entities}\n"
            f"Required actions: {scenario.required_structured_actions}\n"
            f"Transcript:\n{transcript_block}\n\n"
            "Return JSON with keys: intent, action, confidence, rationale, reply, workflow_state, hallucination_risk, escalation_recommended, structured_actions"
        )

        try:
            text = _ollama_generate(model=self.model, system=system, prompt=prompt, label=f"ollama agent {scenario.id}")
            parsed = json.loads(text)
            structured_actions = [
                StructuredAction(
                    action_type=item.get("action_type", ""),
                    payload=item.get("payload", {}),
                )
                for item in parsed.get("structured_actions", [])
            ]
            return AgentDecision(
                intent=parsed.get("intent", scenario.category),
                action=normalize_action(
                    parsed.get("action"),
                    structured_action_names=[a.action_type for a in structured_actions],
                    escalation_recommended=parsed.get("escalation_recommended"),
                ),
                confidence=int(parsed.get("confidence", 80)),
                rationale=parsed.get("rationale", ""),
                reply=parsed.get("reply", ""),
                structured_actions=structured_actions,
                workflow_state=parsed.get("workflow_state", ""),
                hallucination_risk=float(parsed.get("hallucination_risk", 0.2)),
                escalation_recommended=bool(parsed.get("escalation_recommended", False)),
                provider=f"ollama:{self.model}",
            )
        except Exception:
            return deterministic_decision(scenario, transcript, provider=f"ollama:{self.model}:fallback")


class OllamaConversationJudge:
    def __init__(self) -> None:
        self.model = ollama_judge_model_name()

    def score(self, scenario: Scenario, transcript: list[Message], decision: AgentDecision) -> JudgeResult:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        shared_context = (
            f"Scenario: {scenario.title}\n"
            f"Transcript:\n{transcript_block}\n"
        )
        findings = json.loads(
            _ollama_generate(
                model=self.model,
                system="Return JSON findings",
                prompt=shared_context,
                label="ollama judge",
            )
        )
        return JudgeResult(
            frustration_score=0.5,
            loop_risk=0.5,
            hallucination_risk=0.5,
            drop_off_risk=0.5,
            handoff_quality=0.5,
            unsupported_claims=[],
            empathy_quality=0.5,
            frustration_evidence=[],
            loop_evidence=[],
            hallucination_evidence=[],
            handoff_evidence=[],
            findings=findings.get("findings", []),
            notes="ollama basic judge",
            provider=f"ollama:{self.model}",
        )
