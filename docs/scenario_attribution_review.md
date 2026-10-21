# Scenario attribution, baseline and color review

This review supersedes the earlier teal hover-color description in `directional_scenario_impacts.md`.

## Findings and changes

The model computes company financial stress directly from assumed material spending, unhedged exposure, pass-through and market volatility. The evidence graph does not drive the dollar calculation. Its product/segment connections therefore cannot establish a dollar contribution by product.

The unchanged financial convention is:

`material absorbed cost = period spend × shock fraction × unhedged fraction × (1 − pass-through)`

`scenario COGS = reported COGS + sum(absorbed costs)`

`scenario operating income = reported operating income − sum(absorbed costs)`

The shock is the existing model's `round(1.96 × standard deviation(daily log returns) × sqrt(30), 4)`, based on stored futures prices. It is applied to annual or year-to-date spending as selected. This is a stress sensitivity, not a forecast, and the cost-offset convention leaves revenue unchanged. No financial formulas, horizons, tax rates or default parameter values were changed.

1. **Material attribution:** the financial section now shows each material's gross cost, pass-through offset, absorbed COGS and corresponding operating-income contribution. New runs retain full-precision components; a separate display-rounding row reconciles rounded material values to the existing company total. Older saved runs explicitly identify approximate attribution from rounded values.
2. **Input provenance:** the interface now prominently identifies $25M spend/material, 80% unhedged and 75% pass-through as illustrative prototype defaults, not reported company figures or calibrated estimates. The filing supplies the financial baseline. These parameter inputs are assumptions; source evidence identifying a material does not establish its spending or hedge percentage.
3. **Reference comparison:** users can save a completed current run as a session reference, change inputs, and compare assumptions, material contributions and KPIs. Both runs must use the same company/report, period, currency/units, material set, model, financial baseline, market fingerprint and shock. A report/company switch clears the reference. Market changes block an assumption-only comparison with an explicit message. Multiple changed parameters show their combined effect by material, not a unique causal decomposition among parameters.
4. **Graph colors:** hover/selection now uses blue dashed outlines, explicitly labelled “not impact.” Green is reserved for favorable calculated/conditional impacts. Unassigned nodes say “Impact not assessed — connectivity only.” Graph colors remain relative to the reported financial baseline, not to a saved reference scenario.
5. **KPI display:** increased COGS and decreased profit now show adverse colors. Previously those income-statement colors were reversed. Displayed deltas now reconcile to stressed minus baseline values, including legacy net-debt rows whose stored delta could be an intermediate value. Calculated endpoint values are unchanged.

## Product attribution limitation

A defensible product breakdown needs material spend or consumption weights by product, consistent period/currency, relevant hedge/pass-through terms, accounting treatment and reconciliation to company totals. Current graph connectivity and sales shares do not provide those inputs. Product allocation remains **in development**. The UI explicitly reports this gap rather than creating a Ceramic Substrates/Gorilla Glass allocation. An eventual user-entered allocation would need to be labelled an assumption and checked for completeness and double counting.

For equipment precious metals, carrying value, leased metal balances and repeatedly recycled inventory are not equivalent to annual expensed input spend. The existing input guidance remains applicable.

## Changed files

- `pe_copilot/agents/market_agent.py`: retain precise material cost components alongside existing outputs.
- `pe_copilot/analytics/scenario_attribution.py`: contribution reconciliation and guarded reference comparison (new).
- `pe_copilot/supply_chain/scenario_impacts.py`: use saved precise components when available.
- `pe_copilot/ui/tabs.py`: input provenance, attribution table, explicit product gap and reference-comparison controls.
- `pe_copilot/ui/presentation.py`: correct KPI colors/delta display and replace factual-sounding scenario prose with assumption labels.
- `pe_copilot/ui/assets/graph_explorer.html`: separate blue selection from green impact; label unassessed nodes.
- `tests/test_presentation.py`: adverse KPI colors and endpoint reconciliation.
- `tests/test_scenario_impacts.py`: legacy precision compatibility and color semantics.
- `tests/test_combined_workspace.py`: saving a reference, modifying exposure, rerunning and comparison display.
- `tests/test_scenario_attribution.py`: hedge-change comparison, rounding reconciliation, exact components and mismatch rejection (new).
- `docs/scenario_attribution_review.md`: this review (new).

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -q`: 119 tests passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. venv/bin/python /tmp/check_selected_report_ui.py`: GLW annual and quarterly scenarios, report reset, navigation and graph scope passed.
- Core package imports and attribution imports passed.
- `node --check /tmp/graph-impact-check.js`: graph JavaScript syntax passed.
- `git diff --check`: passed.

No live SEC/LLM call was needed for these checks. No commit or push was performed.
