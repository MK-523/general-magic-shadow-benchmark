from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from benchmark.actions import normalize_action
from benchmark.batch_runner import run_batch
from benchmark.models import Message
from benchmark.policy import assess_policy, variant_for_id
from benchmark.scenarios import scenario_library, scenario_map


RUNS_DIR = Path("runs")


def list_run_files() -> list[Path]:
    return sorted(RUNS_DIR.glob("batch-*.jsonl"), reverse=True)


def first_provider(path: Path) -> str:
    try:
        first_line = next(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        first_record = json.loads(first_line)
        return first_record.get("outcome", {}).get("provider", "mock")
    except (StopIteration, json.JSONDecodeError, OSError):
        return "unknown"


def default_run_label(run_files: list[Path]) -> str:
    for path in run_files:
        provider = first_provider(path)
        if provider != "mock":
            return path.name
    return "latest"


def load_logs(path: Path | None = None) -> list[dict]:
    target = path or (RUNS_DIR / "latest.jsonl")
    if not target.exists():
        run_batch(str(RUNS_DIR))
        target = RUNS_DIR / "latest.jsonl"
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalized_action(log: dict) -> str:
    structured_action_names = [
        action.get("action_type", "")
        for action in log.get("outcome", {}).get("structured_actions", [])
        if isinstance(action, dict)
    ]
    return normalize_action(
        log.get("outcome", {}).get("raw_action") or log.get("outcome", {}).get("action"),
        structured_action_names=structured_action_names,
        escalation_recommended=log.get("outcome", {}).get("escalation_recommended"),
    )


def policy_summary(log: dict) -> dict[str, object]:
    scenario = scenario_map()[log["scenario_id"]]
    variant = variant_for_id(scenario, log.get("variant_id"))
    transcript = [
        Message(role=turn["speaker"], text=turn["text"])
        for turn in log.get("turns", [])
        if turn.get("speaker") != "agent_shadow"
    ]
    structured_action_names = [
        action.get("action_type", "")
        for action in log.get("outcome", {}).get("structured_actions", [])
        if isinstance(action, dict)
    ]
    return assess_policy(
        scenario,
        transcript,
        raw_action=log.get("outcome", {}).get("raw_action") or log.get("outcome", {}).get("action"),
        structured_action_names=structured_action_names,
        escalation_recommended=log.get("outcome", {}).get("escalation_recommended"),
        variant=variant,
    )


def derived_metrics(log: dict) -> dict[str, float | bool | str]:
    scenario = scenario_map()[log["scenario_id"]]
    policy = policy_summary(log)
    expected_action = str(policy["expected_action"])
    actual_action = str(policy["normalized_action"])
    recommended_action = str(policy["policy_action"])
    entity_coverage = log["metrics"]["entity_coverage"]
    writeback_complete = log["metrics"]["writeback_complete"]
    hallucination_risk = log["metrics"].get("hallucination_risk")
    handoff_quality = log["metrics"]["handoff_quality"]
    action_success = actual_action == expected_action
    mixed_mode_violation = bool(policy["has_escalation_action"] and policy["has_writeback_action"])
    containment_ok = actual_action != "automate" or not bool(policy["blocked_automation"])
    safe_automation = actual_action != "automate" or (
        scenario.automation_eligible
        and expected_action == "automate"
        and writeback_complete
        and entity_coverage == 1
        and not mixed_mode_violation
    )
    escalation_correct = (actual_action == "escalate") == (expected_action == "escalate")
    utility_score = (
        (0.28 if action_success else 0.0)
        + (0.2 * entity_coverage)
        + (0.2 if writeback_complete else 0.0)
        + (0.16 if escalation_correct else 0.0)
        + (0.08 if containment_ok else 0.0)
        + (0.08 if handoff_quality else 0.0)
        + (0.08 if safe_automation else 0.0)
    )
    success = action_success and containment_ok and handoff_quality > 0 and (
        hallucination_risk is None or hallucination_risk < 0.4
    )
    return {
        "actual_action": actual_action,
        "action_success": action_success,
        "containment_ok": containment_ok,
        "safe_automation": safe_automation,
        "escalation_correct": escalation_correct,
        "utility_score": round(utility_score, 2),
        "success": success,
        "mixed_mode_violation": mixed_mode_violation,
        "raw_action": log.get("outcome", {}).get("raw_action") or log.get("outcome", {}).get("action"),
        "normalized_action": str(policy["normalized_action"]),
        "policy_action": recommended_action,
        "actual_action": actual_action,
        "expected_action": expected_action,
        "guardrail_override": bool(policy["guardrail_override"]),
        "policy_reasons": list(policy["reasons"]),
        "policy_violations": list(policy["violations"]),
    }


def aggregate(logs: list[dict]) -> dict[str, float]:
    total = len(logs) or 1
    success = sum(1 for log in logs if derived_metrics(log)["success"])
    escalation = sum(1 for log in logs if derived_metrics(log)["escalation_correct"])
    containment = sum(1 for log in logs if derived_metrics(log)["containment_ok"])
    safe_automation = sum(1 for log in logs if derived_metrics(log)["safe_automation"])
    writeback = sum(1 for log in logs if log["metrics"]["writeback_complete"])
    avg_utility = sum(float(derived_metrics(log)["utility_score"]) for log in logs) / total
    avg_entity_coverage = sum(log["metrics"]["entity_coverage"] for log in logs) / total
    judged = [log for log in logs if log["metrics"].get("soft_metrics_evaluated")]
    judged_total = len(judged) or 1
    avg_empathy = sum((log["metrics"]["empathy_quality"] or 0) for log in judged) / judged_total if judged else None
    avg_frustration = sum((log["metrics"]["frustration_score"] or 0) for log in judged) / judged_total if judged else None
    avg_loop = sum((log["metrics"]["loop_risk"] or 0) for log in judged) / judged_total if judged else None
    avg_hallucination = sum((log["metrics"]["hallucination_risk"] or 0) for log in judged) / judged_total if judged else None
    avg_drop_off = sum((log["metrics"]["drop_off_risk"] or 0) for log in judged) / judged_total if judged else None
    return {
        "total_runs": total,
        "success_rate": success / total,
        "escalation_precision": escalation / total,
        "containment_rate": containment / total,
        "safe_automation_rate": safe_automation / total,
        "writeback_rate": writeback / total,
        "avg_utility_score": avg_utility,
        "avg_utility_percent": min(1.0, avg_utility / 1.08),
        "avg_entity_coverage": avg_entity_coverage,
        "soft_metrics_coverage": len(judged) / total,
        "avg_empathy_quality": avg_empathy,
        "avg_frustration": avg_frustration,
        "avg_loop_risk": avg_loop,
        "avg_hallucination_risk": avg_hallucination,
        "avg_drop_off_risk": avg_drop_off,
    }


def delta_summary(current: dict[str, float], baseline: dict[str, float]) -> list[dict[str, object]]:
    metrics = [
        ("Utility", current["avg_utility_percent"], baseline["avg_utility_percent"]),
        ("Success", current["success_rate"], baseline["success_rate"]),
        ("Containment", current["containment_rate"], baseline["containment_rate"]),
        ("Safe automation", current["safe_automation_rate"], baseline["safe_automation_rate"]),
        ("Writeback", current["writeback_rate"], baseline["writeback_rate"]),
        ("Entity coverage", current["avg_entity_coverage"], baseline["avg_entity_coverage"]),
        ("Escalation", current["escalation_precision"], baseline["escalation_precision"]),
    ]
    rows: list[dict[str, object]] = []
    for label, current_value, baseline_value in metrics:
        rows.append(
            {
                "metric": label,
                "current": f"{current_value:.0%}",
                "baseline": f"{baseline_value:.0%}",
                "delta": f"{(current_value - baseline_value):+.0%}",
            }
        )
    return rows


def active_judge_label(logs: list[dict]) -> str:
    judge_providers = sorted(
        {
            log["metrics"].get("judge_provider", "not_evaluated")
            for log in logs
            if log["metrics"].get("soft_metrics_evaluated")
        }
    )
    if not judge_providers:
        return "not evaluated"
    return ", ".join(judge_providers)


def run_context_warnings(logs: list[dict]) -> list[str]:
    warnings: list[str] = []
    if len(logs) <= 2:
        warnings.append("Scoped run: this batch contains very few conversations, so aggregate percentages may be misleading.")
    providers = sorted({log["outcome"].get("provider", "mock") for log in logs})
    if providers == ["mock"]:
        warnings.append("Mock provider: these results reflect the seeded baseline agent, not a real model.")
    if any(
        metric is not None and not 0.0 <= metric <= 1.0
        for log in logs
        for metric in [
            log["metrics"].get("frustration_score"),
            log["metrics"].get("loop_risk"),
            log["metrics"].get("hallucination_risk"),
            log["metrics"].get("drop_off_risk"),
            log["metrics"].get("empathy_quality"),
            log["metrics"].get("handoff_quality"),
        ]
    ):
        warnings.append("Judge outputs appear out of bounds; the local model may not be following the scoring rubric cleanly.")
    if any(log["metrics"].get("entity_coverage", 0) == 0 for log in logs):
        warnings.append("Entity coverage is low for at least one run; inspect the structured action payloads for schema mismatch.")
    return warnings


def run_composition(logs: list[dict]) -> dict[str, object]:
    base_runs = sum(1 for log in logs if not log["variant_id"])
    variant_runs = len(logs) - base_runs
    categories = sorted({log["category"] for log in logs})
    providers = sorted({log["outcome"].get("provider", "mock") for log in logs})
    return {
        "total_runs": len(logs),
        "base_runs": base_runs,
        "variant_runs": variant_runs,
        "categories": categories,
        "providers": providers,
    }


def action_distribution(logs: list[dict]) -> list[dict]:
    total = len(logs) or 1
    actions = ["automate", "ask_follow_up", "escalate"]
    rows: list[dict] = []
    for action in actions:
        count = sum(1 for log in logs if derived_metrics(log)["actual_action"] == action)
        rows.append({"action": action, "count": count, "share": f"{count / total:.0%}"})
    return rows


def expectation_alignment(logs: list[dict]) -> list[dict]:
    total = len(logs) or 1
    actions = ["automate", "ask_follow_up", "escalate"]
    rows: list[dict] = []
    for expected in actions:
        subset = [log for log in logs if log["metrics"]["expected_action"] == expected]
        row = {"expected": expected, "n": len(subset), "share": f"{len(subset) / total:.0%}"}
        for actual in actions:
            row[actual] = sum(1 for log in subset if derived_metrics(log)["actual_action"] == actual)
        rows.append(row)
    return rows


def metric_distribution(logs: list[dict]) -> list[dict]:
    def summary(values: list[float | None], label: str) -> dict[str, object]:
        filtered = sorted(value for value in values if value is not None)
        if not filtered:
            return {"metric": label, "mean": "N/A", "min": "N/A", "median": "N/A", "max": "N/A"}
        mid = len(filtered) // 2
        median = filtered[mid] if len(filtered) % 2 == 1 else (filtered[mid - 1] + filtered[mid]) / 2
        mean = sum(filtered) / len(filtered)
        return {
            "metric": label,
            "mean": round(mean, 2),
            "min": round(filtered[0], 2),
            "median": round(median, 2),
            "max": round(filtered[-1], 2),
        }

    return [
        summary([log["metrics"]["utility_score"] / 1.08 for log in logs], "utility"),
        summary([log["metrics"]["entity_coverage"] for log in logs], "entity_coverage"),
        summary([log["metrics"]["frustration_score"] for log in logs], "frustration"),
        summary([log["metrics"]["loop_risk"] for log in logs], "loop_risk"),
        summary([log["metrics"]["hallucination_risk"] for log in logs], "hallucination"),
        summary([log["metrics"]["drop_off_risk"] for log in logs], "drop_off"),
        summary([log["metrics"]["empathy_quality"] for log in logs], "empathy"),
    ]


def aggregate_ops_kpis(logs: list[dict]) -> dict[str, float]:
    total = len(logs) or 1
    automated = [log for log in logs if derived_metrics(log)["actual_action"] == "automate"]
    escalated = [log for log in logs if derived_metrics(log)["actual_action"] == "escalate"]
    follow_ups = [log for log in logs if derived_metrics(log)["actual_action"] == "ask_follow_up"]
    return {
        "deflection_rate": len(automated) / total,
        "review_rate": len(escalated) / total,
        "follow_up_rate": len(follow_ups) / total,
        "structured_completion_rate": sum(1 for log in logs if log["metrics"]["writeback_complete"]) / total,
        "high_risk_fail_rate": sum(
            1
            for log in logs
            if log["metrics"]["hallucination_risk"] is not None and log["metrics"]["hallucination_risk"] >= 0.4
        )
        / total,
    }


def provider_summary(logs: list[dict]) -> list[dict]:
    providers = sorted({log["outcome"].get("provider", "mock") for log in logs})
    summary_rows: list[dict] = []
    for provider in providers:
        subset = [log for log in logs if log["outcome"].get("provider", "mock") == provider]
        totals = aggregate(subset)
        ops = aggregate_ops_kpis(subset)
        summary_rows.append(
            {
                "provider": provider,
                "utility": round(totals["avg_utility_score"], 2),
                "success": f"{totals['success_rate']:.0%}",
                "containment": f"{totals['containment_rate']:.0%}",
                "safe_automation": f"{totals['safe_automation_rate']:.0%}",
                "deflection": f"{ops['deflection_rate']:.0%}",
                "review_rate": f"{ops['review_rate']:.0%}",
                "high_risk_fail": f"{ops['high_risk_fail_rate']:.0%}",
            }
        )
    return summary_rows


def category_summary(logs: list[dict]) -> list[dict]:
    categories = sorted({log["category"] for log in logs})
    rows: list[dict] = []
    for category in categories:
        subset = [log for log in logs if log["category"] == category]
        totals = aggregate(subset)
        ops = aggregate_ops_kpis(subset)
        rows.append(
            {
                "category": category,
                "utility": round(totals["avg_utility_score"], 2),
                "success": f"{totals['success_rate']:.0%}",
                "containment": f"{totals['containment_rate']:.0%}",
                "writeback": f"{totals['writeback_rate']:.0%}",
                "entity_coverage": f"{totals['avg_entity_coverage']:.0%}",
                "deflection": f"{ops['deflection_rate']:.0%}",
                "review_rate": f"{ops['review_rate']:.0%}",
            }
        )
    return rows


def top_failures(logs: list[dict], limit: int = 5) -> list[dict]:
    ranked: list[tuple[float, dict]] = []
    for log in logs:
        metrics = {**log["metrics"], **derived_metrics(log)}
        severity = 0.0
        severity += (1.0 - metrics["utility_score"] / 1.08) * 4
        severity += 2.5 if not metrics["success"] else 0.0
        severity += 1.5 if not metrics["action_success"] else 0.0
        severity += 1.0 if not metrics["writeback_complete"] else 0.0
        severity += 1.0 if not metrics["containment_ok"] else 0.0
        severity += 1.0 if not metrics["escalation_correct"] else 0.0
        severity += 1.5 * (1.0 - metrics["entity_coverage"])
        severity += (metrics["hallucination_risk"] or 0.0)
        severity += (metrics["drop_off_risk"] or 0.0)
        ranked.append(
            (
                severity,
                {
                    "scenario": log["scenario_title"],
                    "variant": log["variant_id"] or "base",
                    "provider": log["outcome"].get("provider", "mock"),
                    "action": metrics["actual_action"],
                    "expected": metrics["expected_action"],
                    "utility": round(metrics["utility_score"] / 1.08, 2),
                    "success": metrics["success"],
                    "entity_coverage": metrics["entity_coverage"],
                    "writeback": metrics["writeback_complete"],
                    "containment": metrics["containment_ok"],
                    "hallucination": metrics["hallucination_risk"],
                    "drop_off": metrics["drop_off_risk"],
                    "severity": round(severity, 2),
                    "raw_action": metrics["raw_action"],
                    "violations": "; ".join(metrics["policy_violations"]),
                },
            )
        )
    return [row for _, row in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]]


st.set_page_config(page_title="General Magic Shadow Benchmark", layout="wide")
st.title("General Magic Shadow Benchmark")
st.caption("Insurance SMS benchmark focused on safe containment, automation eligibility, handoff quality, and writeback integrity.")

col_a, col_b = st.columns([1, 5])
with col_a:
    if st.button("Run batch"):
        run_batch(str(RUNS_DIR))
run_files = list_run_files()
run_labels = ["latest"] + [path.name for path in run_files]
default_label = default_run_label(run_files)
default_index = run_labels.index(default_label) if default_label in run_labels else 0
selected_run = st.selectbox("Run file", run_labels, index=default_index)
selected_path = None if selected_run == "latest" else RUNS_DIR / selected_run
logs = load_logs(selected_path)
with col_b:
    st.write(f"{len(scenario_library())} base scenarios loaded")
    providers = sorted({log["outcome"].get("provider", "mock") for log in logs})
    if providers:
        st.write(f"Providers in current run: {', '.join(providers)}")
    st.write(f"Loaded run: {selected_run}")
summary = aggregate(logs)
ops = aggregate_ops_kpis(logs)
composition = run_composition(logs)

comparison_labels = ["none"] + [label for label in run_labels if label != selected_run]
comparison_run = st.selectbox("Compare against", comparison_labels, index=0)
baseline_logs = []
baseline_summary = None
if comparison_run != "none":
    baseline_path = None if comparison_run == "latest" else RUNS_DIR / comparison_run
    baseline_logs = load_logs(baseline_path)
    baseline_summary = aggregate(baseline_logs)

metric_cols = st.columns(7)
metric_cols[0].metric("Utility", f"{summary['avg_utility_percent']:.0%}")
metric_cols[1].metric("Success", f"{summary['success_rate']:.0%}")
metric_cols[2].metric("Containment", f"{summary['containment_rate']:.0%}")
metric_cols[3].metric("Safe automation", f"{summary['safe_automation_rate']:.0%}")
metric_cols[4].metric("Writeback", f"{summary['writeback_rate']:.0%}")
metric_cols[5].metric("Entity coverage", f"{summary['avg_entity_coverage']:.0%}")
metric_cols[6].metric("Escalation", f"{summary['escalation_precision']:.0%}")

for warning in run_context_warnings(logs):
    st.warning(warning)

if baseline_summary is not None:
    st.subheader("Run Delta")
    st.caption(f"Current: {selected_run} vs baseline: {comparison_run}")
    st.dataframe(delta_summary(summary, baseline_summary), use_container_width=True)

st.subheader("Run Composition")
composition_cols = st.columns(4)
composition_cols[0].metric("Conversations", str(composition["total_runs"]))
composition_cols[1].metric("Base runs", str(composition["base_runs"]))
composition_cols[2].metric("Variant runs", str(composition["variant_runs"]))
composition_cols[3].metric("Providers", str(len(composition["providers"])))
st.caption(f"Categories: {', '.join(composition['categories'])}")
st.caption(f"Providers: {', '.join(composition['providers'])}")

with st.expander("What the metrics mean", expanded=False):
    st.markdown(
        """
        - `Utility`: normalized weighted composite for General Magic-style value delivery: correct action, entity capture, writeback integrity, containment, handoff quality, and safe automation.
        - `Success`: the run chose the expected action, stayed within the allowed containment boundary, and avoided high hallucination risk.
        - `Containment`: the agent kept the conversation in the right lane instead of automating a flow that should stay with a human.
        - `Safe automation`: the agent only automated when the scenario was eligible and the required writeback + field coverage were present.
        - `Writeback`: required system actions were emitted for the workflow.
        - `Entity coverage`: share of required workflow fields captured in structured output.
        - `Escalation`: whether the agent got the escalate / do-not-escalate boundary right.
        - `Frustration`, `Loop risk`, `Hallucination`, `Drop-off`: agentic judge metrics. They are only shown when a configured LLM judge is enabled.
        """
    )

risk_cols = st.columns(4)
risk_cols[0].metric("Frustration", f"{summary['avg_frustration']:.2f}" if summary["avg_frustration"] is not None else "N/A")
risk_cols[1].metric("Loop risk", f"{summary['avg_loop_risk']:.2f}" if summary["avg_loop_risk"] is not None else "N/A")
risk_cols[2].metric("Hallucination", f"{summary['avg_hallucination_risk']:.2f}" if summary["avg_hallucination_risk"] is not None else "N/A")
risk_cols[3].metric("Drop-off", f"{summary['avg_drop_off_risk']:.2f}" if summary["avg_drop_off_risk"] is not None else "N/A")
st.metric("Empathy", f"{summary['avg_empathy_quality']:.2f}" if summary["avg_empathy_quality"] is not None else "N/A")
st.caption(f"Soft-metric coverage: {summary['soft_metrics_coverage']:.0%} of runs judged by {active_judge_label(logs)}")

st.subheader("Brokerage Ops KPIs")
kpi_cols = st.columns(5)
kpi_cols[0].metric("Deflection", f"{ops['deflection_rate']:.0%}", help=f"{sum(1 for log in logs if derived_metrics(log)['actual_action'] == 'automate')}/{len(logs)}")
kpi_cols[1].metric("Manual review", f"{ops['review_rate']:.0%}", help=f"{sum(1 for log in logs if derived_metrics(log)['actual_action'] == 'escalate')}/{len(logs)}")
kpi_cols[2].metric("Follow-up", f"{ops['follow_up_rate']:.0%}", help=f"{sum(1 for log in logs if derived_metrics(log)['actual_action'] == 'ask_follow_up')}/{len(logs)}")
kpi_cols[3].metric("Structured completion", f"{ops['structured_completion_rate']:.0%}", help=f"{sum(1 for log in logs if log['metrics']['writeback_complete'])}/{len(logs)}")
kpi_cols[4].metric("High-risk failures", f"{ops['high_risk_fail_rate']:.0%}", help=f"{sum(1 for log in logs if log['metrics']['hallucination_risk'] is not None and log['metrics']['hallucination_risk'] >= 0.4)}/{len(logs)}")

st.subheader("Action Mix")
st.dataframe(action_distribution(logs), use_container_width=True)

st.subheader("Expected vs Actual")
st.dataframe(expectation_alignment(logs), use_container_width=True)

st.subheader("Metric Distribution")
st.dataframe(metric_distribution(logs), use_container_width=True)

st.subheader("Provider Comparison")
st.dataframe(provider_summary(logs), use_container_width=True)

st.subheader("Category Scorecard")
st.dataframe(category_summary(logs), use_container_width=True)

st.subheader("Top Failures")
st.dataframe(top_failures(logs), use_container_width=True)

st.subheader("Decision Audit")
st.dataframe(
    [
        {
            "scenario": log["scenario_title"],
            "variant": log["variant_id"] or "base",
            "expected": derived_metrics(log)["expected_action"],
            "raw_action": derived_metrics(log)["raw_action"],
            "actual_action": derived_metrics(log)["actual_action"],
            "recommended_action": derived_metrics(log)["policy_action"],
            "override": derived_metrics(log)["guardrail_override"],
            "violations": "; ".join(derived_metrics(log)["policy_violations"]),
        }
        for log in logs
    ],
    use_container_width=True,
)

categories = sorted({log["category"] for log in logs})
selected_category = st.selectbox("Category", ["all", *categories], index=0)
filtered = logs if selected_category == "all" else [log for log in logs if log["category"] == selected_category]

st.subheader("Conversation runs")
st.dataframe(
    [
        {
            "scenario": log["scenario_title"],
            "variant": log["variant_id"] or "base",
            "action": derived_metrics(log)["actual_action"],
            "provider": log["outcome"].get("provider", "mock"),
            "expected": log["metrics"]["expected_action"],
            "utility": derived_metrics(log)["utility_score"],
            "entity_coverage": log["metrics"]["entity_coverage"],
            "writeback": log["metrics"]["writeback_complete"],
            "containment": derived_metrics(log)["containment_ok"],
            "success": derived_metrics(log)["success"],
            "safe_automation": derived_metrics(log)["safe_automation"],
            "handoff_quality": log["metrics"]["handoff_quality"],
            "empathy": log["metrics"]["empathy_quality"],
            "unsupported_claims": log["metrics"]["unsupported_claim_rate"],
            "soft_eval": log["metrics"]["soft_metrics_evaluated"],
        }
        for log in filtered
    ],
    use_container_width=True,
)

labels = [f"{log['scenario_title']} :: {log['variant_id'] or 'base'}" for log in filtered]
selected_label = st.selectbox("Transcript", labels, index=0)
selected = filtered[labels.index(selected_label)]

left, right = st.columns([1.2, 1])
with left:
    st.subheader("Transcript")
    for turn in selected["turns"]:
        speaker = turn["speaker"]
        st.markdown(f"**{speaker}**")
        st.write(turn["text"])

with right:
    st.subheader("Outcome")
    st.json(selected["outcome"])
    st.subheader("Metrics")
    st.json(selected["metrics"])
    st.subheader("Policy Audit")
    st.json(
        {
            "raw_action": derived_metrics(selected)["raw_action"],
            "actual_action": derived_metrics(selected)["actual_action"],
            "recommended_action": derived_metrics(selected)["policy_action"],
            "expected_action": derived_metrics(selected)["expected_action"],
            "guardrail_override": derived_metrics(selected)["guardrail_override"],
            "violations": derived_metrics(selected)["policy_violations"],
            "reasons": derived_metrics(selected)["policy_reasons"],
        }
    )
    st.subheader("Metric Breakdown")
    st.json(
        {
            "utility_components": selected["metrics"].get("utility_components", {}),
            "judge_findings": selected["metrics"].get("judge_findings", []),
            "frustration_components": selected["metrics"].get("frustration_components", {}),
            "frustration_evidence": selected["metrics"].get("frustration_evidence", []),
            "loop_components": selected["metrics"].get("loop_components", {}),
            "loop_evidence": selected["metrics"].get("loop_evidence", []),
            "hallucination_evidence": selected["metrics"].get("hallucination_evidence", []),
            "drop_off_components": selected["metrics"].get("drop_off_components", {}),
            "handoff_evidence": selected["metrics"].get("handoff_evidence", []),
        }
    )
    st.subheader("Why this scenario matters")
    scenario = next(item for item in scenario_library() if item.id == selected["scenario_id"])
    st.write(scenario.utility_goal)
