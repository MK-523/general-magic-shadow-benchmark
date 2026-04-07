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


def gemini_request_delay_seconds() -> float:
    raw = os.getenv("GEMINI_REQUEST_DELAY_SECONDS", "12.5")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 12.5


def gemini_max_retries() -> int:
    raw = os.getenv("GEMINI_MAX_RETRIES", "4")
    try:
        return max(0, int(raw))
    except ValueError:
        return 4


def _response_text_or_raise(response: Any, label: str) -> str:
    text = getattr(response, "text", None)
    if text:
        return text

    candidates = getattr(response, "candidates", None)
    prompt_feedback = getattr(response, "prompt_feedback", None)
    raise GeminiAdapterUnavailable(
        f"Gemini returned no text for {label}. "
        f"prompt_feedback={prompt_feedback!r} candidates={candidates!r}"
    )


def _wait_for_rate_limit_slot() -> None:
    global _LAST_GEMINI_CALL_AT
    delay = gemini_request_delay_seconds()
    if delay <= 0:
        return
    now = time.monotonic()
    elapsed = now - _LAST_GEMINI_CALL_AT
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _LAST_GEMINI_CALL_AT = time.monotonic()


def _retry_delay_from_exception(exc: Exception) -> float:
    message = str(exc)
    match = re.search(r"retry in ([0-9.]+)s", message, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 1.0
    return max(15.0, gemini_request_delay_seconds())


def _generate_content_with_retry(client: Any, *, model: str, contents: str, config: dict[str, Any], label: str) -> Any:
    attempts = gemini_max_retries()
    last_error: Exception | None = None

    for attempt in range(attempts + 1):
        try:
            _wait_for_rate_limit_slot()
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            last_error = exc
            status_code = getattr(exc, "status_code", None)
            if status_code == 429 or "RESOURCE_EXHAUSTED" in str(exc):
                sleep_for = _retry_delay_from_exception(exc)
                print(f"[gemini] rate limited during {label}; sleeping {sleep_for:.1f}s before retry", flush=True)
                time.sleep(sleep_for)
                continue
            raise

    raise GeminiAdapterUnavailable(f"Gemini request failed for {label} after retries: {last_error}")


def _load_pydantic() -> tuple[Any, Any]:
    try:
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise GeminiAdapterUnavailable("Install pydantic to enable Gemini adapters.") from exc
    return BaseModel, Field


def gemini_agent_model_name() -> str:
    return os.getenv("GEMINI_AGENT_MODEL", "gemini-2.5-pro")


def gemini_judge_model_name() -> str:
    return os.getenv("GEMINI_JUDGE_MODEL", "gemini-2.5-pro")


def claude_agent_model_name() -> str:
    return os.getenv("CLAUDE_AGENT_MODEL", "claude-opus-4-1-20250805")


def claude_judge_model_name() -> str:
    return os.getenv("CLAUDE_JUDGE_MODEL", "claude-opus-4-1-20250805")


def ollama_agent_model_name() -> str:
    return os.getenv("OLLAMA_AGENT_MODEL", "qwen2.5:14b-instruct")


def ollama_judge_model_name() -> str:
    return os.getenv("OLLAMA_JUDGE_MODEL", "qwen2.5:14b-instruct")


class GeminiInsuranceAgent:
    def __init__(self) -> None:
        self.client = build_gemini_client()
        self.model = gemini_agent_model_name()
        BaseModel, Field = _load_pydantic()

        class ActionSchema(BaseModel):
            action_type: str
            payload_json: str

        class AgentDecisionSchema(BaseModel):
            intent: str
            action: str = Field(description="One of: automate, ask_follow_up, escalate")
            confidence: int
            rationale: str
            reply: str
            workflow_state: str
            hallucination_risk: float
            escalation_recommended: bool
            structured_actions: list[ActionSchema]

        self._schema = AgentDecisionSchema

    def run(self, scenario: Scenario, transcript: list[Message]) -> AgentDecision:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        contents = (
                "You are an insurance SMS shadow agent for a brokerage operations team. "
                "Return a structured workflow decision for the transcript. "
                "Be conservative about compliance, underwriting, pricing, and claims certainty. "
                "Only choose automate if the workflow is clearly low-risk and the required fields are present. "
                "For every structured action, set payload_json to a valid JSON object string.\n\n"
                f"Scenario category: {scenario.category}\n"
                f"Expected operating goal: {scenario.utility_goal}\n"
                f"Required workflow entities: {', '.join(scenario.required_entities) or 'none'}\n"
                f"Required system actions if completed: {', '.join(scenario.required_structured_actions) or 'none'}\n"
                f"Blocked unsupported claims: {', '.join(scenario.blocked_claims) or 'none'}\n"
                f"Scenario title: {scenario.title}\n"
                f"Line of business: {scenario.line_of_business}\n"
                f"Policy ID: {scenario.policy_id}\n"
                f"Automation eligible: {scenario.automation_eligible}\n"
                f"Known structured fields: {scenario.structured_fields}\n"
                f"Risk signals: {scenario.risk_signals}\n"
                "Transcript:\n"
                f"{transcript_block}"
        )
        config = {
            "response_mime_type": "application/json",
            "response_schema": self._schema,
        }
        response = _generate_content_with_retry(
            self.client,
            model=self.model,
            contents=contents,
            config=config,
            label=f"agent scenario {scenario.id}",
        )
        parsed = self._schema.model_validate_json(_response_text_or_raise(response, f"agent scenario {scenario.id}"))
        return AgentDecision(
            intent=parsed.intent,
            action=parsed.action,
            confidence=parsed.confidence,
            rationale=parsed.rationale,
            reply=parsed.reply,
            structured_actions=[
                StructuredAction(
                    action_type=item.action_type,
                    payload=_json_load_or_default(item.payload_json),
                )
                for item in parsed.structured_actions
            ],
            workflow_state=parsed.workflow_state,
            hallucination_risk=parsed.hallucination_risk,
            escalation_recommended=parsed.escalation_recommended,
            provider=f"gemini:{self.model}",
        )


class GeminiConversationJudge:
    def __init__(self) -> None:
        self.client = build_gemini_client()
        self.model = gemini_judge_model_name()
        BaseModel, _ = _load_pydantic()

        class FindingsSchema(BaseModel):
            findings: list[str]
            unsupported_claims: list[str]
            customer_friction_signals: list[str]
            handoff_signals: list[str]
            notes: str

        class JudgeSchema(BaseModel):
            frustration_score: float
            loop_risk: float
            hallucination_risk: float
            drop_off_risk: float
            handoff_quality: float
            unsupported_claims: list[str]
            empathy_quality: float
            frustration_evidence: list[str]
            loop_evidence: list[str]
            hallucination_evidence: list[str]
            handoff_evidence: list[str]
            notes: str

        self._findings_schema = FindingsSchema
        self._schema = JudgeSchema

    def score(self, scenario: Scenario, transcript: list[Message], decision: AgentDecision) -> JudgeResult:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        shared_context = (
            f"Scenario: {scenario.title}\n"
            f"Category: {scenario.category}\n"
            f"Utility goal: {scenario.utility_goal}\n"
            f"Blocked unsupported claims: {scenario.blocked_claims}\n"
            "Transcript:\n"
            f"{transcript_block}\n\n"
            "Shadow decision:\n"
            f"action={decision.action}\n"
            f"confidence={decision.confidence}\n"
            f"reply={decision.reply}\n"
            f"workflow_state={decision.workflow_state}\n"
            f"structured_actions={[{'action_type': action.action_type, 'payload': action.payload} for action in decision.structured_actions]}"
        )

        findings_contents = (
            "You are pass 1 of an insurance SMS evaluation pipeline. "
            "Extract concrete findings from the transcript and agent output. "
            "Do not score yet. "
            "List observable failures, uncertainties, customer-friction signals, handoff-quality signals, and unsupported claims risk. "
            "Only include findings grounded in the provided conversation.\n\n"
            f"{shared_context}"
        )
        findings_config = {
            "response_mime_type": "application/json",
            "response_schema": self._findings_schema,
        }
        findings_response = _generate_content_with_retry(
            self.client,
            model=self.model,
            contents=findings_contents,
            config=findings_config,
            label=f"judge findings scenario {scenario.id}",
        )
        findings = self._findings_schema.model_validate_json(
            _response_text_or_raise(findings_response, f"judge findings scenario {scenario.id}")
        )

        scoring_contents = (
                "You are pass 2 of an insurance SMS evaluation pipeline. "
                "This is a rubric-based evaluation, not a generic sentiment task. "
                "Use the extracted findings as primary evidence and score the run. "
                "Score each metric on a 0 to 1 scale where lower risk metrics are better and higher quality metrics are better only when explicitly requested. "
                "Frustration reflects customer effort, unnecessary back-and-forth, lack of empathy, and poor resolution momentum. "
                "Loop risk reflects whether the conversation is likely to get stuck in repetitive clarifications. "
                "Hallucination risk reflects unsupported certainty, invented coverage, pricing, or claims assertions. "
                "Drop-off risk reflects whether the customer is likely to disengage before the workflow completes. "
                "Handoff quality reflects whether escalation, if needed, is explicit, well-routed, and likely useful to the human team. "
                "Provide metric evidence lines derived from the findings and transcript.\n\n"
                f"{shared_context}\n\n"
                f"Extracted findings: {findings.findings}\n"
                f"Unsupported claims from findings: {findings.unsupported_claims}\n"
                f"Customer friction signals: {findings.customer_friction_signals}\n"
                f"Handoff signals: {findings.handoff_signals}\n"
                f"Pass 1 notes: {findings.notes}"
        )
        config = {
            "response_mime_type": "application/json",
            "response_schema": self._schema,
        }
        response = _generate_content_with_retry(
            self.client,
            model=self.model,
            contents=scoring_contents,
            config=config,
            label=f"judge scoring scenario {scenario.id}",
        )
        parsed = self._schema.model_validate_json(_response_text_or_raise(response, f"judge scoring scenario {scenario.id}"))
        return JudgeResult(
            frustration_score=parsed.frustration_score,
            loop_risk=parsed.loop_risk,
            hallucination_risk=parsed.hallucination_risk,
            drop_off_risk=parsed.drop_off_risk,
            handoff_quality=parsed.handoff_quality,
            unsupported_claims=list(dict.fromkeys([*findings.unsupported_claims, *parsed.unsupported_claims])),
            empathy_quality=parsed.empathy_quality,
            frustration_evidence=parsed.frustration_evidence,
            loop_evidence=parsed.loop_evidence,
            hallucination_evidence=parsed.hallucination_evidence,
            handoff_evidence=parsed.handoff_evidence,
            findings=findings.findings,
            notes=f"pass1={findings.notes} | pass2={parsed.notes}",
            provider=f"gemini:{self.model}",
        )


def _json_load_or_default(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"value": value}
    if not isinstance(value, (str, bytes, bytearray)):
        return {"raw": value}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return loaded if isinstance(loaded, dict) else {"value": loaded}


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


def _coerce_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        mapping = {
            "very low": 0.1,
            "low": 0.2,
            "medium": 0.5,
            "moderate": 0.5,
            "high": 0.8,
            "very high": 0.95,
        }
        if normalized in mapping:
            return mapping[normalized]
        try:
            return max(0.0, min(1.0, float(normalized)))
        except ValueError:
            return 0.5
    return 0.5


def _coerce_unit_interval(value: Any) -> float:
    return max(0.0, min(1.0, _coerce_score(value)))


def _coerce_confidence_percent(value: Any) -> int:
    score = _coerce_score(value)
    if isinstance(value, (int, float)) and float(value) > 1.0:
        return max(0, min(100, int(round(float(value)))))
    return max(0, min(100, int(round(score * 100))))


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        return [json.dumps(value)]
    return [str(value)]


def _content_text_or_raise(message: Any, label: str) -> str:
    blocks = getattr(message, "content", None) or []
    texts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if texts:
        return "\n".join(texts)
    raise ClaudeAdapterUnavailable(f"Claude returned no text for {label}: stop_reason={getattr(message, 'stop_reason', None)!r}")


class ClaudeInsuranceAgent:
    def __init__(self) -> None:
        self.client = build_claude_client()
        self.model = claude_agent_model_name()

    def run(self, scenario: Scenario, transcript: list[Message]) -> AgentDecision:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        system = (
            "You are an insurance SMS shadow agent for a brokerage operations team. "
            "Return only valid JSON. "
            "Be conservative about compliance, underwriting, pricing, and claims certainty. "
            "Only choose automate if the workflow is clearly low-risk and the required fields are present. "
            "Choose action from this enum only: automate, ask_follow_up, escalate. "
            "For every structured action, set payload_json to a valid JSON object string."
        )
        user = (
            f"Scenario category: {scenario.category}\n"
            f"Expected operating goal: {scenario.utility_goal}\n"
            f"Required workflow entities: {', '.join(scenario.required_entities) or 'none'}\n"
            f"Required system actions if completed: {', '.join(scenario.required_structured_actions) or 'none'}\n"
            f"Blocked unsupported claims: {', '.join(scenario.blocked_claims) or 'none'}\n"
            f"Scenario title: {scenario.title}\n"
            f"Line of business: {scenario.line_of_business}\n"
            f"Policy ID: {scenario.policy_id}\n"
            f"Automation eligible: {scenario.automation_eligible}\n"
            f"Known structured fields: {scenario.structured_fields}\n"
            f"Risk signals: {scenario.risk_signals}\n"
            "Return JSON with keys: intent, action, confidence, rationale, reply, workflow_state, "
            "hallucination_risk, escalation_recommended, structured_actions. "
            "Each structured action must have action_type and payload_json.\n\n"
            "Transcript:\n"
            f"{transcript_block}"
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1800,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parsed = json.loads(_content_text_or_raise(response, f"claude agent scenario {scenario.id}"))
        structured_actions = [
            StructuredAction(
                action_type=item["action_type"],
                payload=_json_load_or_default(item["payload_json"]),
            )
            for item in parsed.get("structured_actions", [])
        ]
        return AgentDecision(
            intent=parsed["intent"],
            action=normalize_action(
                parsed["action"],
                structured_action_names=[item.action_type for item in structured_actions],
                escalation_recommended=bool(parsed["escalation_recommended"]),
            ),
            confidence=_coerce_confidence_percent(parsed["confidence"]),
            rationale=parsed["rationale"],
            reply=parsed["reply"],
            structured_actions=structured_actions,
            workflow_state=parsed["workflow_state"],
            hallucination_risk=float(parsed["hallucination_risk"]),
            escalation_recommended=bool(parsed["escalation_recommended"]),
            provider=f"claude:{self.model}",
        )


class ClaudeConversationJudge:
    def __init__(self) -> None:
        self.client = build_claude_client()
        self.model = claude_judge_model_name()

    def _create_json(self, *, system: str, user: str, label: str) -> dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2200,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        try:
            return json.loads(_content_text_or_raise(response, label))
        except json.JSONDecodeError as exc:
            raise ClaudeAdapterUnavailable(f"Claude returned non-JSON for {label}") from exc

    def score(self, scenario: Scenario, transcript: list[Message], decision: AgentDecision) -> JudgeResult:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        shared_context = (
            f"Scenario: {scenario.title}\n"
            f"Category: {scenario.category}\n"
            f"Utility goal: {scenario.utility_goal}\n"
            f"Blocked unsupported claims: {scenario.blocked_claims}\n"
            "Transcript:\n"
            f"{transcript_block}\n\n"
            "Shadow decision:\n"
            f"action={decision.action}\n"
            f"confidence={decision.confidence}\n"
            f"reply={decision.reply}\n"
            f"workflow_state={decision.workflow_state}\n"
            f"structured_actions={[{'action_type': action.action_type, 'payload': action.payload} for action in decision.structured_actions]}"
        )
        findings = self._create_json(
            system=(
                "You are pass 1 of an insurance SMS evaluation pipeline. Return only valid JSON. "
                "Extract grounded findings from the transcript and agent output. Do not score yet."
            ),
            user=(
                "Return JSON with keys: findings, unsupported_claims, customer_friction_signals, handoff_signals, notes.\n\n"
                f"{shared_context}"
            ),
            label=f"claude judge findings scenario {scenario.id}",
        )
        scored = self._create_json(
            system=(
                "You are pass 2 of an insurance SMS evaluation pipeline. Return only valid JSON. "
                "Use the extracted findings as primary evidence and score the run. "
                "Frustration, loop risk, hallucination risk, and drop-off risk are 0 to 1 where lower is better. "
                "Empathy quality and handoff quality are 0 to 1 where higher is better."
            ),
            user=(
                "Return JSON with keys: frustration_score, loop_risk, hallucination_risk, drop_off_risk, "
                "handoff_quality, unsupported_claims, empathy_quality, frustration_evidence, loop_evidence, "
                "hallucination_evidence, handoff_evidence, notes.\n\n"
                f"{shared_context}\n\n"
                f"Extracted findings: {findings.get('findings', [])}\n"
                f"Unsupported claims from findings: {findings.get('unsupported_claims', [])}\n"
                f"Customer friction signals: {findings.get('customer_friction_signals', [])}\n"
                f"Handoff signals: {findings.get('handoff_signals', [])}\n"
                f"Pass 1 notes: {findings.get('notes', '')}"
            ),
            label=f"claude judge scoring scenario {scenario.id}",
        )
        return JudgeResult(
            frustration_score=float(scored["frustration_score"]),
            loop_risk=float(scored["loop_risk"]),
            hallucination_risk=float(scored["hallucination_risk"]),
            drop_off_risk=float(scored["drop_off_risk"]),
            handoff_quality=float(scored["handoff_quality"]),
            unsupported_claims=list(dict.fromkeys([*findings.get("unsupported_claims", []), *scored.get("unsupported_claims", [])])),
            empathy_quality=float(scored["empathy_quality"]),
            frustration_evidence=scored.get("frustration_evidence", []),
            loop_evidence=scored.get("loop_evidence", []),
            hallucination_evidence=scored.get("hallucination_evidence", []),
            handoff_evidence=scored.get("handoff_evidence", []),
            findings=findings.get("findings", []),
            notes=f"pass1={findings.get('notes', '')} | pass2={scored.get('notes', '')}",
            provider=f"claude:{self.model}",
        )


class OllamaInsuranceAgent:
    def __init__(self) -> None:
        self.model = ollama_agent_model_name()

    def run(self, scenario: Scenario, transcript: list[Message]) -> AgentDecision:
        return deterministic_decision(scenario, transcript, provider=f"ollama:{self.model}")


class OllamaConversationJudge:
    def __init__(self) -> None:
        self.model = ollama_judge_model_name()

    def score(self, scenario: Scenario, transcript: list[Message], decision: AgentDecision) -> JudgeResult:
        transcript_block = "\n".join(f"{message.role}: {message.text}" for message in transcript)
        shared_context = (
            f"Scenario: {scenario.title}\n"
            f"Category: {scenario.category}\n"
            f"Utility goal: {scenario.utility_goal}\n"
            f"Blocked unsupported claims: {scenario.blocked_claims}\n"
            "Transcript:\n"
            f"{transcript_block}\n\n"
            "Shadow decision:\n"
            f"action={decision.action}\n"
            f"confidence={decision.confidence}\n"
            f"reply={decision.reply}\n"
            f"workflow_state={decision.workflow_state}\n"
            f"structured_actions={[{'action_type': action.action_type, 'payload': action.payload} for action in decision.structured_actions]}"
        )
        findings = json.loads(
            _ollama_generate(
                model=self.model,
                system="You are pass 1 of an insurance SMS evaluation pipeline. Return only valid JSON.",
                prompt=(
                    "Return JSON with keys: findings, unsupported_claims, customer_friction_signals, handoff_signals, notes.\n\n"
                    f"{shared_context}"
                ),
                label=f"ollama judge findings scenario {scenario.id}",
            )
        )
        scored = json.loads(
            _ollama_generate(
                model=self.model,
                system="You are pass 2 of an insurance SMS evaluation pipeline. Return only valid JSON.",
                prompt=(
                    "Return JSON with keys: frustration_score, loop_risk, hallucination_risk, drop_off_risk, "
                    "handoff_quality, unsupported_claims, empathy_quality, frustration_evidence, loop_evidence, "
                    "hallucination_evidence, handoff_evidence, notes.\n\n"
                    f"{shared_context}\n\n"
                    f"Extracted findings: {findings.get('findings', [])}\n"
                    f"Unsupported claims from findings: {findings.get('unsupported_claims', [])}\n"
                    f"Customer friction signals: {findings.get('customer_friction_signals', [])}\n"
                    f"Handoff signals: {findings.get('handoff_signals', [])}\n"
                    f"Pass 1 notes: {findings.get('notes', '')}"
                ),
                label=f"ollama judge scoring scenario {scenario.id}",
            )
        )
        return JudgeResult(
            frustration_score=_coerce_unit_interval(scored["frustration_score"]),
            loop_risk=_coerce_unit_interval(scored["loop_risk"]),
            hallucination_risk=_coerce_unit_interval(scored["hallucination_risk"]),
            drop_off_risk=_coerce_unit_interval(scored["drop_off_risk"]),
            handoff_quality=_coerce_unit_interval(scored["handoff_quality"]),
            unsupported_claims=list(
                dict.fromkeys(
                    [
                        *_coerce_list(findings.get("unsupported_claims", [])),
                        *_coerce_list(scored.get("unsupported_claims", [])),
                    ]
                )
            ),
            empathy_quality=_coerce_unit_interval(scored["empathy_quality"]),
            frustration_evidence=_coerce_list(scored.get("frustration_evidence", [])),
            loop_evidence=_coerce_list(scored.get("loop_evidence", [])),
            hallucination_evidence=_coerce_list(scored.get("hallucination_evidence", [])),
            handoff_evidence=_coerce_list(scored.get("handoff_evidence", [])),
            findings=_coerce_list(findings.get("findings", [])),
            notes=f"pass1={findings.get('notes', '')} | pass2={scored.get('notes', '')}",
            provider=f"ollama:{self.model}",
        )
