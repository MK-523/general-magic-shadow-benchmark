from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from benchmark.agent import MockInsuranceAgent
from benchmark.adapters import (
    ClaudeAdapterUnavailable,
    ClaudeConversationJudge,
    ClaudeInsuranceAgent,
    claude_enabled,
    GeminiAdapterUnavailable,
    GeminiConversationJudge,
    GeminiInsuranceAgent,
    gemini_enabled,
    OllamaAdapterUnavailable,
    OllamaConversationJudge,
    OllamaInsuranceAgent,
    ollama_enabled,
)
from benchmark.evaluator import evaluate_conversation
from benchmark.models import ConversationLog, ConversationTurn, JudgeResult, Scenario, Variant
from benchmark.policy import apply_policy_guardrails
from benchmark.scenarios import scenario_library, scenario_map
from benchmark.simulator import SyntheticUserSimulator


def build_conversation_log(
    scenario: Scenario,
    agent: MockInsuranceAgent,
    simulator: SyntheticUserSimulator,
    variant: Variant | None = None,
    judge: GeminiConversationJudge | ClaudeConversationJudge | OllamaConversationJudge | None = None,
) -> ConversationLog:
    transcript = list(scenario.initial_messages)
    transcript.extend(simulator.next_turn(scenario, variant))
    raw_decision = agent.run(scenario, transcript)
    decision, policy_assessment = apply_policy_guardrails(scenario, transcript, raw_decision, variant=variant)
    judge_result: JudgeResult | None = judge.score(scenario, transcript, decision) if judge else None
    metrics = evaluate_conversation(scenario, transcript, decision, variant, judge_result)

    turns = [ConversationTurn(speaker=message.role, text=message.text) for message in transcript]
    turns.append(
        ConversationTurn(
            speaker="agent_shadow",
            text=decision.reply,
            metadata={
                "action": decision.action,
                "raw_action": raw_decision.action,
                "guardrail_override": policy_assessment["guardrail_override"],
            },
        )
    )

    outcome = {
        "provider": decision.provider,
        "intent": decision.intent,
        "action": decision.action,
        "raw_action": raw_decision.action,
        "confidence": decision.confidence,
        "workflow_state": decision.workflow_state,
        "rationale": decision.rationale,
        "policy_assessment": policy_assessment,
        "structured_actions": [asdict(action) for action in decision.structured_actions],
    }

    return ConversationLog(
        scenario_id=scenario.id,
        scenario_title=scenario.title,
        category=scenario.category,
        variant_id=variant.id if variant else None,
        turns=turns,
        outcome=outcome,
        metrics=metrics,
    )


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _verbose_enabled() -> bool:
    return _env_enabled("BENCHMARK_VERBOSE")


def _resolve_execution_plan(
    *,
    scenario_id: str | None,
    variant_id: str | None,
    include_variants: bool,
    limit: int | None,
) -> list[tuple[Scenario, Variant | None]]:
    scenarios = scenario_library()
    if scenario_id:
        scenarios = [scenario_map()[scenario_id]]

    plan: list[tuple[Scenario, Variant | None]] = []
    for scenario in scenarios:
        if variant_id:
            matched = next((variant for variant in scenario.variants if variant.id == variant_id), None)
            if matched:
                plan.append((scenario, matched))
            continue

        plan.append((scenario, None))
        if include_variants:
            for variant in scenario.variants:
                plan.append((scenario, variant))

    return plan[:limit] if limit is not None else plan


def run_batch(
    output_dir: str = "runs",
    *,
    scenario_id: str | None = None,
    variant_id: str | None = None,
    include_variants: bool = True,
    limit: int | None = None,
    agent_only: bool = False,
    judge_only: bool = False,
) -> Path:
    use_gemini_agent = _env_enabled("USE_GEMINI_AGENT")
    use_gemini_judge = _env_enabled("USE_GEMINI_JUDGE")
    use_claude_agent = _env_enabled("USE_CLAUDE_AGENT")
    use_claude_judge = _env_enabled("USE_CLAUDE_JUDGE")
    use_ollama_agent = _env_enabled("USE_OLLAMA_AGENT")
    use_ollama_judge = _env_enabled("USE_OLLAMA_JUDGE")
    agent: MockInsuranceAgent | GeminiInsuranceAgent | ClaudeInsuranceAgent | OllamaInsuranceAgent = MockInsuranceAgent()
    judge: GeminiConversationJudge | ClaudeConversationJudge | OllamaConversationJudge | None = None

    if sum([
        use_gemini_agent or use_gemini_judge,
        use_claude_agent or use_claude_judge,
        use_ollama_agent or use_ollama_judge,
    ]) > 1:
        raise RuntimeError("Choose only one LLM provider at a time: Gemini, Claude, or Ollama.")

    if agent_only and judge_only:
        raise RuntimeError("Choose at most one of --agent-only or --judge-only.")

    if judge_only and not (use_gemini_judge or use_claude_judge or use_ollama_judge):
        raise RuntimeError("--judge-only requires an LLM judge to be enabled.")

    if use_gemini_agent:
        if not gemini_enabled():
            raise GeminiAdapterUnavailable("USE_GEMINI_AGENT is set but GEMINI_API_KEY is missing.")
        agent = GeminiInsuranceAgent()
    if use_gemini_judge:
        if not gemini_enabled():
            raise GeminiAdapterUnavailable("USE_GEMINI_JUDGE is set but GEMINI_API_KEY is missing.")
        judge = GeminiConversationJudge()
    if use_claude_agent:
        if not claude_enabled():
            raise ClaudeAdapterUnavailable("USE_CLAUDE_AGENT is set but ANTHROPIC_API_KEY is missing.")
        agent = ClaudeInsuranceAgent()
    if use_claude_judge:
        if not claude_enabled():
            raise ClaudeAdapterUnavailable("USE_CLAUDE_JUDGE is set but ANTHROPIC_API_KEY is missing.")
        judge = ClaudeConversationJudge()
    if use_ollama_agent:
        if not ollama_enabled():
            raise OllamaAdapterUnavailable("USE_OLLAMA_AGENT is set but Ollama is not available.")
        agent = OllamaInsuranceAgent()
    if use_ollama_judge:
        if not ollama_enabled():
            raise OllamaAdapterUnavailable("USE_OLLAMA_JUDGE is set but Ollama is not available.")
        judge = OllamaConversationJudge()

    if agent_only:
        judge = None
    if judge_only:
        agent = MockInsuranceAgent()

    simulator = SyntheticUserSimulator()
    logs: list[ConversationLog] = []
    plan = _resolve_execution_plan(
        scenario_id=scenario_id,
        variant_id=variant_id,
        include_variants=include_variants,
        limit=limit,
    )

    for scenario, variant in plan:
        label = variant.id if variant else "base"
        if _verbose_enabled():
            print(f"[benchmark] running {scenario.id} ({label})", flush=True)
        logs.append(build_conversation_log(scenario, agent, simulator, variant, judge))

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    file_path = output_path / f"batch-{timestamp}.jsonl"

    with file_path.open("w", encoding="utf-8") as handle:
        for log in logs:
            handle.write(json.dumps(log.to_dict()) + "\n")

    latest_path = output_path / "latest.jsonl"
    latest_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
    return file_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the General Magic shadow benchmark.")
    parser.add_argument("--output-dir", default="runs")
    parser.add_argument("--scenario", help="Run only one scenario id.")
    parser.add_argument("--variant", help="Run only one variant id.")
    parser.add_argument("--limit", type=int, help="Run only the first N planned conversations.")
    parser.add_argument("--no-variants", action="store_true", help="Exclude variants and run only base scenarios.")
    parser.add_argument("--agent-only", action="store_true", help="Run the selected agent without judge scoring.")
    parser.add_argument("--judge-only", action="store_true", help="Run the judge on top of the mock agent only.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    written = run_batch(
        output_dir=args.output_dir,
        scenario_id=args.scenario,
        variant_id=args.variant,
        include_variants=not args.no_variants,
        limit=args.limit,
        agent_only=args.agent_only,
        judge_only=args.judge_only,
    )
    print(f"Wrote benchmark conversations to {written}")
