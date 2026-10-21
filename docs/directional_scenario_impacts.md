# Directional impacts and grounded explanations

The scenario graph no longer uses colored entity-type dots. Entity types remain in the inspector and column labels. The legend now distinguishes red (adverse company cost effect), green (favorable), amber (uncertain/mixed connectivity), gray (no modeled residual cost) and teal/dashed hover selection. Arrows retain their disclosed relationship direction.

## Financial scope and sign rules

- The company node shows the saved waterfall's absorbed cost once, plus baseline → stressed KPI figures in USD millions.
- Selected material nodes show approximate company-level material contributions. These use saved gross cost results and the user's pass-through assumption. Individual rounded material contributions may differ slightly from the saved company total.
- Disclosed material-to-product, material-to-operation and product-to-segment connections can carry conditional cost pressure/relief. Endpoint types are checked as well as relation names. These annotations have **no allocated dollar amount**.
- Sales-market relationships, ownership and ambiguous financial mechanisms do not automatically inherit a cost sign. They remain amber. Opposing/multiple path effects are retained separately rather than assigned arbitrary weights.
- Signs describe the company's cost perspective, not commodity-investor returns. The current stress engine still generates its existing upward stress; favorable annotation support is tested using synthetic negative-cost results and does not introduce a new shock control.
- No changes were made to financial formulas, stress horizons, hedge/pass-through assumptions, tax assumptions or revenue treatment. Revenue remains unchanged in the existing cost-offset model.
- Downstream dollar allocation remains **in development** and requires defensible spend weights/contract assumptions.

## Grounded explanations

Explanations are deterministic templates over the saved calculation, user inputs and filing excerpts. They identify spend basis, units, shock horizon, report context, formula, cost absorbed/passed through and limitations. Node/edge inspectors carry source citations and disclosed paths. A new explanation-service trace step records their generation. The same explanations are saved with the scenario for future chatbot use.

No extra LLM call is used for these explanations or arithmetic. Existing filing extraction can use an LLM for narrative entities, relations and features, labelled inferred with validated source quotations. Financial observations are parsed deterministically; curated graph records retain their own provenance. Not every node/edge has an independently LLM-written description.

The combined UI reuses annotations only for the same company, snapshot and run. It removes stale explanations when inputs change. Evidence filters remove signed paths containing hidden edges; report-level explanation excerpts are explicitly shown separately from the graph's relationship filter.

## Changed files

- `pe_copilot/supply_chain/scenario_impacts.py`: scoped annotations, conservative cost-path rules, saved-result explanations and filtered-view replay (new).
- `pe_copilot/orchestrator.py`: attach/persist annotations and expose the explanation trace step.
- `pe_copilot/ui/graph_explorer.py`: show annotations, legend guidance, scenario explanations and readable filing excerpts.
- `pe_copilot/ui/assets/graph_explorer.html`: remove type dots, add directional styles/legend, node labels and inspector annotations.
- `tests/test_scenario_impacts.py`: signs, neutral/mixed scenarios, scope guards, evidence filters, financial scope, persistence and safe rendering (new).
- `tests/test_combined_workspace.py`: explanation visibility and stale-input clearing.
- `docs/directional_scenario_impacts.md`: this implementation record (new).

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -q`: **112 tests passed**.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. venv/bin/python /tmp/check_selected_report_ui.py`: GLW annual/quarterly report selection, scenario runs and graph scope passed. The first invocation without `PYTHONPATH=.` failed to import the package; the corrected invocation passed.
- `node --check /tmp/graph-impact-check.js`: extracted graph JavaScript passed syntax validation.
- `git diff --check`: passed.
- Core package and new annotation imports passed.
- Browser check on a fresh local preview confirmed a completed GLW scenario, the color legend, company cost/KPI annotations and grounded explanations. The existing GLW snapshot lacks exact Platinum/Natural Gas nodes; these explanations explicitly disclose missing graph matches instead of inventing connections. Conditional signed edges are covered by synthetic graph tests.

Restart a long-running Streamlit process after this update: development hot reload can retain older cached orchestrator/request classes. The verified fresh preview uses port 8503.

No commit or push performed. Other pre-existing work was preserved.
