# Graph orchestration and interactive explorer — steps 7–8

Current status: step 9 company onboarding and annual/quarterly history are implemented. See [company_onboarding.md](company_onboarding.md) for the current workflow, validation and remaining limits; historical planning statements below describe the earlier implementation batch.

Implemented: a stateless graph request route and an interactive Tab 3 over the reviewed MTRN evidence snapshot. Step 9, three-year 10-K/10-Q ingestion and historical comparison, remains **in development**. Chatbot natural-language routing and financial loss propagation through graph edges are also **in development**.

## Using Tab 3

Run `venv/bin/streamlit run app.py` and select **Supply Chain Evidence Graph**. Choose a filing snapshot, relationship view, financial observation period and focus entity. The default Copper view shows its evidenced material/product/segment/market chain. Four views separate material exposure, physical supply, financial mechanisms and all disclosed relationships. Turn off the focused-chain filter to inspect the whole selected view.

Hover an entity to highlight its upstream and downstream chains and display available features, financial observations, assumptions and source passages. Click to pin details; Escape unpins. Search finds and centers entities. Drag nodes to arrange, drag the background to pan, zoom with the wheel or buttons, and use Fit/Reset. Expand requests browser full screen where permitted. Clicking a relationship opens its evidence. The page also provides relationship evidence, observations, coverage gaps and an actual execution trace outside the visualization.

Cyan indicates the current chain; amber indicates scenario exposure. When both apply, amber remains visible. Directed traversal does not flood unrelated sibling branches through a common parent. Highlighting is bounded to the selected lens, confirmation filter and focused-chain view. It does not establish causality or calculate monetary losses. All-relations mode includes descriptive relationships such as hierarchy and acquisitions.

Execute a Tab 2 stress test, then select **Latest Tab 2 run** in Tab 3. The run's material names and percentage shocks are copied into an immutable context, with a run ID and horizon. Percent values are converted to fractions for the interface; financial formulas are unchanged. Changes to company, selected materials, spend, hedging, pass-through or baseline inputs invalidate the saved overlay until the stress test is rerun. Unmatched material names produce a warning rather than an invented edge. A zero shock adds no material exposure highlight.

Tab 2 does not currently identify its baseline filing period. The UI therefore explicitly describes its overlay as a material-exposure mapping onto the selected filing, without reconciling or allocating its dollar impacts. Disclosed annual sensitivities retain their original horizon, as-of date, assumptions and company scope. They are not quarterly costs, segment losses or realized annual expenses.

## Shared interface for future chat

`CopilotOrchestrator.invoke(GraphRequest)` returns a `ResultEnvelope`: company/snapshot/observation context, graph data, reported observations, deterministic derived observations, assumptions, citation references, warnings and execution steps. Selections and chat history remain caller-owned; the cached orchestrator stores neither. Existing discovery and stress methods retain their existing contracts and calculations. This is a deterministic graph route; it does not add an LLM or a natural-language planner.

```python
from pe_copilot.contracts import GraphRequest, HighlightContext, WorkspaceContext

context = WorkspaceContext('company:0001104657', 'mtrn-2025-evidence-v1')
focus = HighlightContext(
    source='chatbot', run_id='question-123', ticker='MTRN',
    company_id=context.company_id, snapshot_id=context.snapshot_id,
    entity_ids=('concept:material:copper',),
)
result = orchestrator.invoke(GraphRequest(context, source='chatbot', highlight=focus))
```

A future chatbot can set the caller's `graph_external_highlight` session value to this typed context and select **Chatbot request**. Until that routing is built, the UI explicitly labels it in development. Wrong-company or wrong-snapshot highlights are rejected. The graph itself is already generic: independently curated company bundles can be discovered by ticker without copying MTRN facts into another company.

## Temporal design for step 9

Filing snapshot selection and financial observation period selection are separate. The current 2025 filing includes 2023–2025 comparative annual revenue figures. Selecting 2024 filters those observations; it does **not** assert that 2025 relationships existed in 2024. Dated hypothetical sensitivities remain separately labelled with their actual as-of date.

The existing document and snapshot identities preserve company CIK, form, period end, filing date, accession and content hash. Observations retain start/end periods, currency, unit, related entity and provenance. Step 9 should add independent reviewed snapshots for each filing, explicit cross-filing entity continuity, amendment/restatement handling and comparable metric definitions. Quarterly versus year-to-date durations must be distinguished before trend calculations. Never derive quarterly figures by simply dividing annual observations. Historical relationship changes require filing evidence rather than inferred continuity.

## Changed files in this batch

| File | Change |
|---|---|
| `app.py` | Renamed Tab 3 and passed orchestrator/company context |
| `pe_copilot/contracts.py` | Added graph requests, result envelopes and immutable scenario contexts |
| `pe_copilot/orchestrator.py` | Added snapshot discovery and stateless graph execution with provenance and trace |
| `pe_copilot/supply_chain/graph_repository.py` | Added local company-bundle discovery |
| `pe_copilot/supply_chain/explorer.py` | Added scoped lenses, chains, observation formatting and exposure overlays |
| `pe_copilot/ui/tabs.py` | Connected saved Tab 2 runs and stale-input detection; removed the Tab 3 placeholder |
| `pe_copilot/ui/graph_explorer.py` | Added filing/period controls, evidence inspection, warnings and interactive rendering |
| `pe_copilot/ui/graph_visualization.py` | Added safe graph-data embedding |
| `pe_copilot/ui/assets/graph_explorer.html` | Added offline SVG visualization and interaction controls |
| `tests/test_graph_explorer.py` | Added ten focused offline behavior/UI regression tests |
| `docs/knowledge_graph_foundation.md` | Updated completion status |
| `docs/graph_pipeline.md` | Linked the new integration and clarified remaining scope |
| `docs/graph_explorer.md` | Added usage, architecture, temporal boundaries and validation record |

No dependency was added. Existing uncommitted changes were preserved. No commit, push or publication was performed.

## Validation

Commands run:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -p test_graph_explorer.py -v
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -c "from pe_copilot.agents import MarketAgent, FundamentalAgent; from pe_copilot.analytics import run_stress_test; from pe_copilot.contracts import GraphRequest; from pe_copilot.ui.graph_explorer import render_supply_chain_tab"
node --check /tmp/margin-graph-explorer-check.js
git diff --check
```

The temporary JavaScript file contains the executable script extracted from `graph_explorer.html`. Additional offline checks exercised all four lenses on the saved MTRN bundle, confirmed 2024 observation filtering while retaining 2025-dated sensitivities, and ran the complete app with Streamlit AppTest in Hugging Face offline mode. Browser checks on the local app covered rendering, node selection, search, source details and browser console errors. Streamlit fixture tests cover overlay activation, stale-run suppression, view switching and the incomplete chatbot message. The source-link test found and fixed handling of absent source URLs. The scenario colors were corrected so a selected chain cannot hide amber exposure.

Results: **57 tests passed**, including ten new explorer tests and all local MTRN integration tests. Core imports, JavaScript syntax, complete-app smoke and whitespace checks passed. The browser reported no console errors.

Live LLM/embedding/market connectivity scripts were intentionally not run. New-company ingestion, historical filings and a complete question-to-chatbot-to-graph flow are deferred features, not validated functionality.
