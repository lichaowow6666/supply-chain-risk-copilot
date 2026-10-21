# Forecasting workbench: benchmarks and XGBoost

Implemented 2026-09-22. The subsequent [LSTM and company risk extension](lstm_company_risk.md) adds an evaluated LSTM, joint empirical company impacts and a continuous custom-price picker; its details supersede the initial scope notes below. This is an evaluated decision-support prototype, not evidence of reliable future trading performance.

## User flow

Select the company, filing and materials in Tab 1, then proceed to the combined scenario workspace. The first scenario controls now show each material's market history and an explicit price shock, initially **0%**. Choose a 5, 20, 30 or 60 trading-session horizon and click **Prepare forecasts & compare models**.

Each material has its own recommended model and user-selectable alternative. The chart combines observed benchmark prices, dotted projections, an empirical uncertainty range, and the user's selected price. Click a central/lower/upper endpoint marker or use the central-endpoint button. Enter a custom percentage or positive target price for an extreme scenario; clicking arbitrary empty chart space is not implemented. Changing a model does not silently replace the user's shock.

Model comparisons show development error, later audit error, measured interval coverage, interval width and provenance. Forecast records can be downloaded. Preparation is explicit and cached; changing spend, hedging or pass-through does not refit models.

## Numerical service and agent roles

`ForecastingAgent` validates market history, trains numerical models, evaluates them chronologically and freezes results. The orchestrator prepares those records and verifies their material, horizon, reference price and market fingerprint when a scenario executes. The Market Agent applies the selected signed shock to deterministic financial calculations. The graph service receives the same exact shock. The Fundamental Agent continues to provide filing-grounded information. No LLM estimates prices or performs financial arithmetic.

Forecast artifacts are local, ignored by Git, under `artifacts/forecasts`. Their identities include market history, request, implementation version and numerical package versions. Records contain model parameters, features, dates, errors, paths, ranges and runtime metadata. Scenarios retain the referenced records and chosen model. A stale or mismatched forecast cannot be used silently with a different market snapshot.

## Implemented models

| Model | Method | Role |
| --- | --- | --- |
| No-change | Repeat the latest observed benchmark price | Required benchmark and conservative fallback |
| Autoregressive | Ridge AR(5) on log prices, recursive projections | Classical challenger |
| Seasonal autoregressive | AR(5) plus annual sine/cosine terms | Additional Natural Gas challenger |
| XGBoost | Direct cumulative log-return forecasts conditioned on horizon | Machine-learning challenger |

XGBoost uses lagged returns, trailing mean/volatility, distance from a trailing price mean, horizon and calendar features. Parameters are fixed: 80 trees, depth 2, learning rate 0.04, minimum child weight 10, L2 penalty 10, one CPU thread, seed 42. Scaling and training use only the history available at each evaluation origin. This release does not tune hyperparameters on the audit period.

Natural Gas includes a seasonal candidate; metals use the same benchmark/AR/XGBoost comparison. Recommendations are selected from the development results for the requested material and horizon, rather than hard-coded by commodity.

## Chronological evaluation

- Training expands through each forecast origin; targets are strictly later observations.
- Up to 24 development origins select the model and calibrate ranges. At least 8 are required, each with at least 253 historical observations. Development horizons can overlap.
- Every development target matures by the start of the first later audit forecast. Four subsequent audit horizons do not overlap.
- Average absolute price error across forecast steps is the comparison score, in the benchmark's quote units. Errors for different commodities must not be compared as if their units were identical.
- The lowest development MAE is recommended only if it improves on no-change by more than 5%; otherwise no-change remains recommended. This is a conservative heuristic, not a statistical significance test.
- Later audit results neither choose the winner nor calibrate its ranges. At later audit origins, fitting can use observations that have already arrived, as in live expanding-window forecasting.
- Horizon-specific 10th and 90th percentiles of development log forecast errors define the **nominal 80% empirical range**. This replaces the old illustrative chart's 95% label in the new workbench. Audit coverage is measured and displayed; 80% coverage is not guaranteed. Error bias can put a model's central path outside its empirical range.
- Short histories yield an explicitly unvalidated no-change path without a band. Unavailable models are excluded with an explanation, never replaced under the same label.

Four audit windows provide limited evidence. There is no independent multi-year external evaluation or joint multi-material probability model in this release.

## Financial interpretation and changes to assumptions

The workbench now uses explicit user-selected shocks, including price decreases. The earlier automatic upward volatility shock remains only for legacy callers that omit explicit shocks. New explicit scenarios do not display or export the old illustrative GBM trajectories.

For each material, existing deterministic formulas remain:

- Gross cost change = entered spend × unhedged fraction × signed price shock.
- Passed-through change = gross cost change × pass-through fraction.
- Absorbed cost change = gross cost change × (1 − pass-through fraction).

The existing KPI engine applies these cost changes. Decreases use the same algebra symmetrically; this is an explicit modeling assumption and may differ from real customer contracts. Scenarios that imply negative total COGS are rejected. Saved scenario comparison now permits different price shocks against the same underlying market and filing baseline, and displays both shock assumptions.

The selected horizon describes the price forecast, **not a conversion of annual/YTD spend into that many days**. The selected endpoint is treated as a sustained price change applied to the full spending period entered below. There is no integration under the drawn scenario line, monthly purchasing schedule, hedge-maturity cash-flow model or automatic annualization. Spending and unhedged assumptions retain their existing provenance; market forecasts do not turn them into reported company facts.

## Data and scope limitations

Prices are provider futures benchmarks, not company purchase prices or a maturity-specific forward curve. Basis, currency, physical delivery, location and contract-roll effects can differ from actual procurement. Calendar dates beyond history are weekday approximations, not exchange trading calendars. Models require strictly positive prices. Weather, storage, macroeconomic inputs and explicit contract-roll features are absent.

Graph impact propagation still follows the existing supported graph relationships. Forecasting does not create missing company/material links or invent product-level spending weights.

LSTM and a joint empirical risk-case distribution are now implemented; see the extension document. GARCH, additional GBM comparisons, correlated Monte Carlo scenarios, external drivers and rolling model governance remain **in development**. No new chatbot or LangGraph integration is included.

## Runtime

`requirements-forecast.txt` pins the optional XGBoost dependency to 3.4.1. It is not a complete project environment lock. Use the existing project environment. XGBoost's macOS runtime requires OpenMP; follow its official installation prerequisites when configuring another machine.

The local environment was verified with XGBoost 3.4.1. Its OpenMP library was obtained from the official Homebrew bottle, checksum-verified and placed inside the ignored project environment; the local XGBoost library was configured to find it. No system-wide package manager installation was made. A plain dependency install on another Mac may still need OpenMP setup. If XGBoost cannot load, the numerical benchmarks remain available and the UI reports its exclusion.

## Validation and changed files

Validation completed:

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -q`: **131 tests passed**, including actual XGBoost reproducibility, chronological separation, feature leakage protection, cache reuse, signed financial/graph impacts and Streamlit controls.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. venv/bin/python /tmp/check_selected_report_ui.py`: local GLW annual/quarterly report, reset, navigation, scenario and graph checks passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. venv/bin/python /tmp/check_forecast_workbench.py`: local GLW full comparison, XGBoost selection, endpoint choice, opposite-direction material shocks, financial reconciliation and frozen forecast provenance passed. These temporary integration harnesses use local cached data and are not public data fixtures.
- Core package imports including ForecastingAgent and XGBoost passed.
- `git diff --check` passed.
- Browser check on the restarted local app passed: cached GLW preparation, forecast controls, combined chart and legend, and central-endpoint selection rendered and worked.

The local 30-session comparison retained no-change for Platinum and recommended seasonal autoregression for Natural Gas. XGBoost ran successfully but did not earn the recommendation. These findings apply to the evaluated local history and horizon, not all future samples.

Files added or changed for this implementation (earlier unrelated working changes retained):

- `pe_copilot/agents/forecasting_agent.py`: models, evaluation, immutable-by-convention forecast records and cache.
- `pe_copilot/agents/__init__.py`: ForecastingAgent export.
- `pe_copilot/agents/market_agent.py`: selected signed shocks, provenance, explicit-scenario legacy projection suppression and COGS safeguard.
- `pe_copilot/contracts.py`: price scenario and forecast references, supported horizons.
- `pe_copilot/orchestrator.py`: forecast preparation, validation, trace and scenario records.
- `pe_copilot/analytics/scenario_attribution.py`: comparison of changed price shocks.
- `pe_copilot/ui/forecasting.py`: charts, model comparison and synchronized price controls.
- `pe_copilot/ui/tabs.py`: price-first controls and request/currentness integration.
- `pe_copilot/ui/scenario_workspace.py`: combined-workspace integration.
- `pe_copilot/ui/company_setup.py`: forecast state reset when company/report changes.
- `pe_copilot/ui/presentation.py`: accurate futures labels and signed cost display.
- `tests/test_forecasting.py`: numerical and scenario regression tests.
- `tests/test_combined_workspace.py`: forecast and price-control UI regression tests.
- `tests/test_presentation.py`: chart labels and signed display tests.
- `requirements-forecast.txt`: optional dependency pin.
- `docs/forecasting_workbench.md`: behavior, assumptions, validation and limitations.

No commit or push was performed.
