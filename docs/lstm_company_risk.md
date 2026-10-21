# LSTM, joint company risk and clearer price controls

Implemented 2026-09-22. Extends the benchmark/XGBoost service without changing its development/audit split, 5% recommendation improvement rule, nominal 80% empirical bands or financial assumptions.

## LSTM challenger

A real PyTorch LSTM now learns from 20-session log-return sequences. It has one recurrent layer with eight hidden units and a direct output for every forecast step. Each evaluation origin refits independently using only its available history. Inputs are standardized from that prefix; targets are cumulative log returns divided by prefix return volatility times the square root of the forecast step. Training uses only complete target windows and requires at least 128 examples.

The fixed configuration uses 40 full-batch Adam epochs, learning rate 0.01, weight decay 0.001, gradient clipping at 1, CPU execution and seed 42. There is no audit-based early stopping or hyperparameter search. RNG and thread settings are restored after each fit; a lock serializes LSTM fits within the process. Reproducibility is checked in the installed runtime, not promised across all operating systems or package versions.

LSTM competes on exactly the same development and audit origins as other models. It is an experimental challenger, not automatically a superior or recommended model. Missing PyTorch, insufficient history or failed fits produce an explicit exclusion. The local installed runtime is PyTorch 2.13.0, now pinned as an optional dependency in `requirements-forecast.txt`.

Forecast artifacts are versioned `forecast-v2` and include PyTorch's version and complete dated development error vectors. Existing v1 caches are not reused for this extension. Actual model training stays behind the explicit preparation action and cache; financial and UI reruns do not retrain it.

The local GLW 30-session comparison included LSTM for both Platinum and Natural Gas. No-change remained recommended for Platinum and seasonal autoregression for Natural Gas. LSTM's Platinum later-audit MAE improved on no-change in this sample, but its development improvement did not clear the 5% gate. The later audit was not used to override the selection rule.

## Company risk distribution

For each selected material, the service takes its active forecast model's current endpoint and its dated development endpoint log errors. It joins materials by **both historical forecast origin and target-end date**, then keeps those whole error vectors together. Each historical case has equal weight:

`case price = current active-model endpoint × exp(historical endpoint log error)`

`case shock = case price / latest market reference − 1`

Each joint case then uses the same spend, unhedged exposure, pass-through and financial cascade as the explicit stress scenario. The existing cascade was extracted into `analytics/financial_impact.py` so risk cases and individual scenarios cannot diverge through duplicated formulas. Financial rounding, accounting signs, 21% tax adjustment and half-cash debt trigger are unchanged. Only available filing-backed financial metrics are included.

The service requires validated forecast references for every selected material, the same latest market date and horizon, and at least eight matched error cases. It reports unavailable when these requirements fail. It does not invent independence, interpolate missing error cases, drop adverse outcomes, or clip negative-COGS cases into plausible values. An unavailable distribution leaves the explicit scenario available.

The UI shows:

- P10, median, P90 and mean KPI levels against the filing baseline.
- A histogram for a selected KPI, with the filing baseline and manual stress scenario marked separately.
- The count/share of historical cases above or below a user-entered financial threshold.
- Mean absorbed cost in the worst 20% of company cost cases, with additive material contributions. Fractional boundary weights define exactly 20% of the equal-weight sample; cents can differ because company amounts are rounded.
- Downloadable cases, forecast references, assumptions and a risk ID tied to the scenario inputs.

There is no random resampling here: 24 dated cases remain 24 cases, not thousands of supposedly independent simulations. The GLW integration check produced 24 matched cases.

### Interpretation boundaries

This is a **conditional empirical sensitivity distribution**, not a calibrated forecast of company earnings or total company risk. Development errors also selected the model, windows can overlap, and company-level interval coverage has not been independently validated. Percentiles interpolate a small sample, making tail estimates fragile. No 95%/99% VaR claim is made.

Observed cross-material dependence is preserved within the matched sample; future dependence can change. Spending, hedge fractions and pass-through stay fixed, and only selected material risks are included. Each endpoint shock is applied to the full entered annual/YTD spending period. There is still no automatic time prorating, procurement schedule, basis-risk model, uncertain contract parameters or product-level cost allocation.

The distribution is anchored to active forecast models, while the orange line is the user's manually chosen stress. Changing a manual shock moves that comparison, not the forecast distribution. Changing the active model, spending or hedge assumptions requires a scenario rerun. Reference curves are visual only and do not invalidate a saved scenario. Stale results retain their previous-run warning.

Independent company-risk calibration, larger tail samples, external features, uncertain procurement/contract inputs, and additional GARCH/Monte Carlo methods remain **in development**.

## Answers to the chart and metric questions

1. **Active forecast model** (previously Projection model) controls the blue forecast, empirical band, preset endpoints and company risk distribution. **Reference curves (visual only)** (previously Compare dotted projections) adds thin gray curves for comparison. Neither automatically overwrites a manual price shock.
2. **Apply central forecast price** (previously Use central endpoint) copies the active model's final forecast price into the target-price and percentage-shock fields. It is not the midpoint of the band and does not run the financial scenario. Click Execute to propagate it.
3. **Development MAE** measures earlier out-of-sample absolute price error and chooses the recommendation. **Later audit MAE** measures error on separate later forecast windows as a check on generalization. Both are in material quote units, and lower is better. **Audit coverage** is the proportion of later observed prices inside the nominal 80% band; compare it with 80%, not 100%. **Interval width** is average upper minus lower bound, in quote units; narrower is useful only with adequate coverage. These help assess a model's error and uncertainty. They do not guarantee future performance or justify choosing the narrowest band.
4. Forecast endpoints are convenient presets, not admissible-price limits. The expanded **Choose any price on a scenario chart** picker accepts a continuous price anywhere vertically in its future area, including outside the band. All clicks set the endpoint at the already-selected horizon; horizontal position does not change the horizon. Raise the chart ceiling for more extreme prices, or type a precise target/percentage. Arrow keys are supported. The standard combined Plotly chart still supports its preset-marker clicks; blank-space selection lives in the custom price-picker chart. No claim is made that empty space in the main Plotly chart is clickable.

The custom picker uses a local SVG Streamlit v2 component. It does not load an external chart CDN. Values cross the component boundary through Streamlit's supported trigger/callback API and are validated before changing scenario inputs.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -q`: 143 tests passed. New coverage includes LSTM targets/reproducibility/RNG isolation/fallback, matched material dependence, exact financial-engine parity across every supported pool, tail attribution, identity checks, stale/missing inputs, impossible tails and custom out-of-band price selection.
- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -p test_company_risk.py -v`: all eight targeted risk tests passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. venv/bin/python /tmp/check_forecast_workbench.py`: GLW LSTM selection and endpoint application, mixed signed shocks, frozen provenance, 24 joint risk cases, visual-only comparison independence and custom +200% shock/staleness passed. This temporary harness uses local cached data, not a public data fixture.
- Full local 30-session model evaluation ran successfully for Platinum and Natural Gas, including actual PyTorch and XGBoost fits.
- `git diff --check` passed.
- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -p test_combined_workspace.py -q`: six UI regressions passed after the final chart sizing adjustment.
- Core agent/analytics/risk imports and the installed PyTorch runtime passed.
- Browser verification after a clean restart passed: expanded the custom chart ceiling, clicked outside the empirical band, confirmed a +249.6% shock and synchronized target price, executed the scenario and inspected the rendered 24-case company KPI distribution. The layout was checked visually.

### Files changed in this extension

- `pe_copilot/agents/lstm_forecast.py` (new): LSTM training and inference.
- `pe_copilot/agents/forecasting_agent.py`: LSTM eligibility/evaluation, finite-path validation, dated errors and cache version/runtime metadata.
- `pe_copilot/agents/market_agent.py`: reuse the extracted financial cascade without formula changes.
- `pe_copilot/analytics/financial_impact.py` (new): shared deterministic cascade.
- `pe_copilot/analytics/company_risk.py` (new): paired case distributions, validation, percentiles and tail attribution.
- `pe_copilot/orchestrator.py`: compute and preserve risk outputs with execution trace.
- `pe_copilot/ui/forecasting.py`: clearer model controls, metric explanations and picker integration.
- `pe_copilot/ui/price_picker.py` and `pe_copilot/ui/assets/price_picker.js` (new): continuous price selection.
- `pe_copilot/ui/company_risk.py` (new): distribution, threshold and attribution views.
- `pe_copilot/ui/tabs.py`: company risk section.
- `tests/test_forecasting.py`, `tests/test_combined_workspace.py`, `tests/test_company_risk.py` (new): relevant regression tests.
- `requirements-forecast.txt`: optional PyTorch pin.
- `docs/forecasting_workbench.md`, `docs/lstm_company_risk.md` (new): current behavior and validation record.

Existing unrelated working changes were retained. No commit or push was performed.

Implementation references: [PyTorch LSTM API](https://docs.pytorch.org/docs/2.14/generated/torch.nn.LSTM.html), [PyTorch reproducibility](https://docs.pytorch.org/docs/2.14/notes/randomness.html), [Streamlit component examples](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/examples).
