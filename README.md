# General Magic Shadow Benchmark (Upgraded)

This repository is a **production-style evaluation harness for AI insurance agents**

## What’s new

- Multi-turn simulation (agent → user → agent)
- Real Ollama model inference (no longer deterministic fallback)
- Execution engine simulating backend systems (policy admin, CRM, messaging)
- End-to-end latency tracking (agent + judge + workflow)
- Execution success scoring (not just decision correctness)

## What this actually measures


- Did it choose the correct workflow?
- Did it stay within safe operational boundaries?
- Did it execute backend actions correctly?
- Would this complete in a real insurance system?

## Architecture

```
Scenario → Simulator → Agent → Policy Guardrails → Execution → Evaluator → Dashboard
```

## Additions from before

### Execution Engine
Simulates:
- Policy admin writebacks
- SMS delivery
- CRM escalation

Outputs:
- execution_success
- execution_latency_ms
- step-by-step action results

### Multi-turn Simulation
If the agent asks for follow-up:
- User responds with structured data
- Agent gets a second pass

### Real Local Models
Ollama agent now uses:
```
POST /api/generate
```
instead of deterministic routing.

## Why this is different

Most AI benchmarks test:
> “Is the model answering correctly?”

This tests:
> “Can the system safely complete full workflows from start to finish?”

## Running

```bash
python3 -m benchmark.batch_runner
streamlit run streamlit_app.py
```

## Next steps (roadmap)

- better multi-turn branching simulation
- real API mocks instead of local execution
- adversarial scenario variants
- CI regression benchmarking
