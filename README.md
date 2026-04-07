# General Magic Shadow Benchmark

Insurance-focused shadow-mode benchmark harness for quote, claim, policy update, and renewal conversations.

## What's there in here

- A scenario library for quote / claim / policy update / renewal flows
- A mock insurance SMS agent with workflow state and structured actions
- one synthetic user simulator
- An evaluator that separates deterministic workflow checks from agentic judgment of the situation
- A batch runner that logs every conversation to `runs/latest.jsonl`
- A Streamlit dashboard to look at metrics and transcripts
- Optional Google AI Studio / Gemini-backed agent and judge adapters so you can benchmark real model behavior instead of only rules
- Optional Anthropic Claude-backed agent and judge adapters
- Optional Ollama-backed local open models so you can run without excessive API usage
- Provider comparison views and brokerage-ops KPIs such as deflection, manual review rate, and structured completion

## Project layout

- `benchmark/scenarios.py`: seeded scenario library
- `benchmark/agent.py`: mock insurance SMS agent
- `benchmark/simulator.py`: synthetic user turns
- `benchmark/evaluator.py`: benchmark metrics
- `benchmark/batch_runner.py`: batch execution and JSONL logging
- `benchmark/adapters.py`: Google Gemini adapters for agent decisions and judge scoring
- `streamlit_app.py`: inspection dashboard

## Fastest way to run it locally

```bash
cd /Users/maheshk/Desktop/general-magic-shadow-benchmark
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m benchmark.batch_runner
streamlit run streamlit_app.py
```

Then open the local Streamlit URL it prints, usually `http://localhost:8501`.

## Scoped runs for free-tier usage

Run one scenario or a small subset instead of the full suite:

```bash
python3 -m benchmark.batch_runner --scenario policy-update-address --no-variants
python3 -m benchmark.batch_runner --scenario policy-update-address --variant policy-update-address-garaging-change
python3 -m benchmark.batch_runner --limit 1
```

Useful mode flags:

```bash
python3 -m benchmark.batch_runner --scenario quote-auto-multi-driver --agent-only

#judge on top of mock agent
python3 -m benchmark.batch_runner --scenario quote-auto-multi-driver --judge-only
```

## Run with Anthropic Claude-backed agent and judge

```bash
cd /Users/maheshk/Desktop/general-magic-shadow-benchmark
source .venv/bin/activate
export ANTHROPIC_API_KEY=your_key_here
export USE_CLAUDE_AGENT=1
export USE_CLAUDE_JUDGE=1
python3 -m benchmark.batch_runner
streamlit run streamlit_app.py
```

Optional model overrides:

```bash
export CLAUDE_AGENT_MODEL=claude-sonnet-4-20250514
export CLAUDE_JUDGE_MODEL=claude-sonnet-4-20250514
```

## Run with local open models using Ollama

Start Ollama and pull a model first:

```bash
ollama pull qwen2.5:14b-instruct
```

Then run:

```bash
cd /Users/maheshk/Desktop/general-magic-shadow-benchmark
source .venv/bin/activate
export USE_OLLAMA_AGENT=1
export USE_OLLAMA_JUDGE=1
export OLLAMA_AGENT_MODEL=qwen2.5:14b-instruct
export OLLAMA_JUDGE_MODEL=qwen2.5:14b-instruct
python3 -m benchmark.batch_runner --scenario policy-update-address --no-variants
streamlit run streamlit_app.py
```

## run with google ai studio agent

The Gemini integrations use the official Google GenAI SDK with structured JSON outputs. By default they are off.

```bash
cd /Users/maheshk/Desktop/general-magic-shadow-benchmark
source .venv/bin/activate
export GEMINI_API_KEY=your_key_here
export USE_GEMINI_AGENT=1
export USE_GEMINI_JUDGE=1
export GEMINI_REQUEST_DELAY_SECONDS=12.5
python3 -m benchmark.batch_runner
streamlit run streamlit_app.py
```

Optional model overrides:

```bash
export GEMINI_AGENT_MODEL=gemini-2.5-flash
export GEMINI_JUDGE_MODEL=gemini-2.5-flash
```

## Notes

- Ollama uses a local HTTP API by default at `http://127.0.0.1:11434` and does not require an external API key.
- The Gemini adapters use structured JSON outputs so the agent decision and judge output come back as typed data instead of free-form text.
- The dashboard metrics are designed around insurance operations questions General Magic would care about:
  safe deflection, structured workflow completion, correct escalation boundaries, and whether the agent creates usable system actions instead of just nice-looking replies.
- Hard checks are deterministic:
  entity coverage, writeback presence, automation eligibility, containment, and escalation-policy correctness.
- Soft checks are agentic:
  frustration, loop risk, hallucination risk, empathy, drop-off risk, and handoff quality come from the ai judge.
- The judge now runs in two passes:
  first it extracts concrete findings from the transcript and agent output, then it scores from those findings
