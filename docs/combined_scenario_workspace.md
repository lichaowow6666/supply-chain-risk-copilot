# Combined scenario workspace

Tab 2 and Tab 3 are now one **Scenario Analysis** workspace. Tab 1 retains company preparation, report selection, financial overview, exposure discovery and material selection.

The combined page follows this order:

1. Scenario assumptions and monitored KPI selection.
2. Interactive supply-chain graph, available before a scenario is run.
3. Financial impact and monitored KPIs after a successful run.
4. Market history and illustrative forward paths.
5. Expandable execution record showing agents, assumptions and evidence.

The graph uses the company and filing selected in Tab 1. It never silently substitutes another filing if that graph is unavailable. A completed scenario supplies the graph overlay automatically. Changing assumptions removes the stale overlay and labels previous financial results until the scenario is rerun. Old saved Tab 2/Tab 3 navigation values migrate to the combined page.

## Visualization implementation

| Layer | Technology and role |
| --- | --- |
| Application | Streamlit controls, session state, layout and iframe embedding |
| Graph rendering | Custom HTML, CSS, native SVG and plain JavaScript in `pe_copilot/ui/assets/graph_explorer.html` |
| Graph structure | Python and NetworkX `MultiDiGraph`, allowing directed relationships and multiple edges between entities |
| Graph data | Dated JSON evidence snapshots; Python assembles filtered nodes, edges, provenance, observations and permitted chains |
| Market charts | Plotly |

The graph uses a custom column layout grouped by entity type, with curved directed edges. JavaScript provides hover chain highlighting, click-to-pin details, dragging, pan, zoom, fit, search and expanded viewing. This is not a D3, Cytoscape or React Flow implementation. The existing visual asset is unchanged by this interface merge.

Scenario highlights currently indicate potential exposure along evidence relationships. Red/green financial attribution and dollar allocation to individual graph nodes/edges remain **in development**. The current graph must not be interpreted as a quantified causal model. Financial calculations remain deterministic Python and were not changed by this merge. No LangGraph dependency was added.

## Files changed in this implementation

- `app.py`: two-tab routing and migration of old navigation state.
- `pe_copilot/ui/navigation.py`: shared labels and migration helper (new).
- `pe_copilot/ui/scenario_workspace.py`: report-scoped combined renderer (new).
- `pe_copilot/ui/tabs.py`: assumptions → graph → financial impacts → charts; expandable execution record; safe display of saved multi-material results after selection changes.
- `pe_copilot/ui/graph_explorer.py`: pinned report context, automatic scenario overlay, and guards against stale or mismatched overlays.
- `tests/test_report_selection.py`: updated navigation expectation.
- `tests/test_combined_workspace.py`: combined layout, run/overlay synchronization, stale inputs, graph browsing without selected materials, missing snapshot and navigation coverage (new).
- `docs/combined_scenario_workspace.md`: implementation and validation record (new).

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v`: **106 tests passed**.
- `/tmp/check_selected_report_ui.py` via the local Python environment: GLW annual and latest quarterly report flows passed, including report resets, navigation, period-specific scenarios and matching graph/run scope. Uses existing local filing and exposure caches.
- Core agent/analytics imports and the new workspace renderer imported successfully.
- `git diff --check`: passed.
- Browser inspection of a fresh preview on port 8502 confirmed the two-tab navigation, GLW report context, scenario controls and embedded interactive graph. The browser session ended before post-run visual inspection; completed-run behavior was verified by the automated UI tests above.
- The older preview on port 8501 retained a stale imported module. Automatic approval review blocked inspection of that process because of a usage limit, so it was not restarted. Use the fresh preview or restart the older Streamlit process before reviewing these changes.

No commit or push was performed. Existing unrelated repository changes were preserved.
