# Agentic Margin Copilot

## Purpose and structure

This repository is a decision-support prototype combining report-scoped SEC 10-K/10-Q retrieval, LangGraph chat, commodity futures in DuckDB, deterministic financial stress scenarios, evidence-backed NetworkX supply-chain graphs, forecasting, answer evaluation, and a Streamlit UI.

Important paths:

- `app.py`: primary Streamlit entry point.
- `pe_copilot/agents/`: fundamental/RAG and market-analysis agents.
- `pe_copilot/analytics/`: deterministic graph stress analytics.
- `scripts/ingest_10k.py`: SEC filing and Chroma ingestion.
- `scripts/ingest_futures.py`: Yahoo Finance and DuckDB ingestion.
- `scripts/build_graph.py`: synthetic supply-chain graph generation.
- `pe_copilot/orchestrator.py`: shared business-service coordinator used by the application.
- `pe_copilot/chat/`: LangGraph, retrieval, explanations, evaluation, and local feedback.
- `evaluations/`: versioned synthetic datasets and saved evaluation results.
- `sec_filings/` and `examples/supply_chain_graph.json`: filing and synthetic graph data.

## Verified commands

Activate the existing local environment:

```bash
source venv/bin/activate
```

Run the application:

```bash
streamlit run app.py
```

Or use the existing environment directly:

```bash
venv/bin/streamlit run app.py
```

Run the automated unit tests:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v
```

Run the existing connectivity scripts only when their required credentials, network access, and proxy configuration are intentionally available:

```bash
venv/bin/python test_llm.py
venv/bin/python test_network.py
```

These connectivity scripts are not part of the automated test suite: they contain no assertions and make external calls. The repository has optional `requirements-chat.txt` and `requirements-forecast.txt` overlays for the verified local environment, not a complete application lock or portable clean-install procedure. No linting or formatting command is configured. Do not invent or claim a verified clean-install or lint command.

Validate core package imports without writing bytecode:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -c "from pe_copilot.agents import MarketAgent, FundamentalAgent; from pe_copilot.analytics import run_stress_test"
```

## Working rules

- Inspect relevant source, data contracts, repository status, and existing behavior before editing.
- Make small, scoped, independently reviewable changes. Avoid unrelated cleanup.
- Run the most relevant available validation after each change. Add focused automated tests when changing testable behavior; do not substitute external connectivity scripts for unit tests.
- Run only essential, targeted checks for the affected user workflow. Do not routinely run the full test suite; broaden validation only when the change or a specific failure justifies it.
- Report every changed file, the validation commands run, their results, and any validation that could not be run.
- Do not commit or push unless the user explicitly requests it.
- Never expose or publish secrets, credentials, `.env` contents, proprietary data, raw company data, local databases, vector stores, or identifying local configuration. Keep public demonstrations synthetic and clearly label synthetic data.
- Keep deterministic financial calculations in ordinary Python code, outside LLM prompts and generated prose. LLMs may extract or explain inputs, but must not be the calculation engine.
- Preserve and validate units, currencies, fiscal/time periods, scenario horizons, assumptions, and accounting signs throughout ingestion, calculation, display, and serialization.
- Do not silently change financial formulas, fallback figures, confidence levels, stress horizons, pass-through logic, hedging assumptions, tax assumptions, or other scenario assumptions. Document proposed changes and update focused tests.
- Mark placeholders, simulated behavior, reserved UI areas, and incomplete integrations explicitly as **in development**. Do not present them as implemented production functionality.
- Preserve source provenance and distinguish reported facts, LLM-extracted values, deterministic derivations, assumptions, fallbacks, and synthetic values.
