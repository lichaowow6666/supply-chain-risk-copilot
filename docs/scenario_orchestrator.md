# Shared scenario orchestration and execution records

Implemented September 22, 2026. This extends the application coordinator without merging the tabs, installing LangGraph, adding a chatbot, or changing financial formulas.

## Responsibilities and communication

The orchestrator is an ordinary Python coordinator, not an LLM supervisor. Agents do not negotiate with each other or communicate through a message broker. The coordinator invokes agent methods, validates returned data, and passes structured inputs to the next operation.

| Component | Responsibility | Returned information |
| --- | --- | --- |
| Fundamental Agent | Resolve the requested prepared report using the filing service | Company identity, financial baseline with XBRL/derivation evidence, source-backed material exposures |
| Market Agent | Capture local futures history and execute existing financial calculations | Historical series, shocks, cost waterfall, KPI results and illustrative forward paths |
| Graph service | Load the pinned graph and map permitted exposure paths | Graph entities/edges, citations, potential-exposure overlay and coverage warnings |
| Orchestrator | Validate scope, sequence calls, assemble results and record execution | One run ID, request, financial inputs/results, market fingerprint, graph payload, assumptions, citations, warnings and trace |

Fundamental disclosure extraction is LLM-assisted and cached; numerical financial inputs come from inline XBRL and documented Python derivations. A normal stress run can reuse cached material extraction without another model call. Market Agent is deterministic Python analytics, despite its agent class name. The execution record reports actual service calls and their outputs, not private model reasoning or a fictional agent conversation.

Company discovery now calls `FundamentalAgent.analyze_report(ticker, accession, market_materials)` rather than bypassing the agent. Legacy discovery/stress methods remain compatible; the application stress button uses the new governed scenario route.

## Shared request and result

`ScenarioMaterial` uses period spend and an unhedged fraction. `ScenarioRequest` identifies the company, ticker, accession, graph snapshot, optional financial-period boundaries, spending basis, currency, unit, selected materials and pass-through fraction. Supported scope remains USD millions and a 30-day price shock. Baselines are resolved by the agent from the report, not accepted as arbitrary UI/chat numerical claims.

`CopilotOrchestrator.invoke()` accepts both graph requests and scenario requests. A scenario proceeds through:

1. Fundamental Agent resolves the selected report and its material evidence. Validate company, accession, graph snapshot, annual/YTD duration, units and each financial input's matching source or derivation. Each selected material must have evidence in that report.
2. Market Agent reads all selected series in one DuckDB transaction. Validate dates, positive finite prices, contract identity and material identity. Record series dates, observation counts and content fingerprints.
3. Market Agent executes the existing formulas on those captured frames. No second database read can change prices halfway through that calculation. Financial formulas, tax assumptions, debt trigger, shock horizon and rounding behavior are unchanged.
4. Graph service maps the scenario's material shocks onto the pinned company's filing graph. The graph overlay and financial result share one run ID. Dollar losses are **not** allocated to individual graph branches; signed node/edge effects remain **in development**.
5. Assemble a `ScenarioResult`, validate JSON serialization and save a local execution record. Tabs retain their current layout and receive the same result/highlight context.

`ScenarioResult` contains the request, baseline, selected exposure evidence, market metadata/fingerprint, simulation output including histories, graph payload, highlight context, assumptions, citations, warnings, execution trace and a combined input fingerprint. `to_dict()` converts pandas histories to dated JSON records and rejects nonfinite/unsupported values.

Results are saved under ignored `artifacts/scenario_runs/<run-id>.json`. This is a local completed-run/failure record, not LangGraph checkpointing: it does not automatically resume an interrupted process. Record-write failures preserve the validated result and explain that the user should use the download button.

## User-visible trace and failure behavior

Tab 2 streams running/completed step updates and displays **How this result was produced**. Users can inspect assumptions, financial and material citations, market series identifiers/dates and download the full execution record.

A company/report/unit mismatch, absent selected-material evidence or invalid market input fails the scenario and withholds its financial results. A graph load/mapping failure produces a **partial** result: valid financial calculations remain available with an explicit graph limitation. Failure stages are recorded. Raw provider exception messages are withheld because they may contain connection credentials. Application-authored validation messages remain visible.

Existing stale-input/report-reset controls remain active. A request captures historical price inputs for audit; it does not itself download market prices. The application's separate daily-refresh workflow remains responsible for database freshness. Old history retains the existing calculation warning behavior.

## LangGraph recommendation

Implement the orchestrator contract and execution visibility first. Complete the combined scenario workspace next. Consider introducing LangGraph as part of the chatbot workflow when there is a concrete need for multi-step routing, clarification pauses, durable state or recovery across interruptions. A simple chatbot that only calls one scenario function does not require it.

LangGraph is an orchestration runtime for stateful workflows; it is different from this project's supply-chain knowledge graph. Its nodes could wrap report analysis, scenario validation, calculation, graph retrieval and grounded explanation while retaining these domain contracts. Its checkpointing must be configured with a durable store to support recovery; adopting the framework alone does not make business calculations or explanations correct.

Official references reviewed: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview), [streaming](https://docs.langchain.com/oss/python/langgraph/streaming), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence). No LangGraph dependency or hosted tracing service was added.

## Files changed for this request

| File | Change |
| --- | --- |
| `pe_copilot/contracts.py` | Scenario request/material/result contracts and actor/basis/input/output trace fields. |
| `pe_copilot/scenario_support.py` | JSON serialization, fingerprints, market-input checks and report-scope validation. |
| `pe_copilot/orchestrator.py` | Agent coordination, scenario execution, shared run context, failure trace and local audit storage. |
| `pe_copilot/agents/fundamental_agent.py` | Explicit report-analysis interface backed by the filing service. |
| `pe_copilot/agents/market_agent.py` | Transactional history capture and optional captured-history calculation input; formulas unchanged. |
| `pe_copilot/filings/service.py` | Include source filing hash in financial-baseline metadata. |
| `app.py` | Connect the stress UI to the orchestrator scenario route. |
| `pe_copilot/ui/tabs.py` | Scenario request construction, progress trace, failure handling and execution-record inspection/download. |
| `tests/test_scenario_orchestration.py` | Ten synthetic offline tests covering contracts, agent integration, identical calculations, market pinning, scope isolation and failures. |
| `docs/scenario_orchestrator.md` | Architecture, scope, migration recommendation and validation record. |

Generated GLW scenario records stay local and ignored. No commit or push was made.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v`: **102 tests passed**, including ten new tests.
- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -c "from pe_copilot.agents import MarketAgent, FundamentalAgent; from pe_copilot.analytics import run_stress_test; from pe_copilot.contracts import ScenarioRequest, ScenarioResult"`: passed.
- `git diff --check`: passed.
- Local Streamlit AppTest integration with actual prepared GLW annual and quarterly reports and local futures history: overview, report reset, Tab 2 navigation, scenario calculation and graph scope passed. Verified the execution panel is present, both agents appear in the recorded trace, and scenario result/highlight/graph overlay run IDs agree.
- Automated tests verify preserved legacy waterfall/KPI values, no history refetch during a scenario, immutable prior output after database changes, JSON records, invalid/mismatched report rejection, nonfinite-value handling, safe provider errors, partial graph failure, quarterly spending basis and chatbot-origin request compatibility.
- No browser pixel-level review or actual conversational chatbot/LangGraph execution was performed; those integrations are not part of this change.
