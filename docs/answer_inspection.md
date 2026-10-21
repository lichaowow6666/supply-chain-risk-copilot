# Answer inspection: execution, grounding and latency

Every saved assistant reply has an **Inspect answer** button. It opens a separate large Streamlit dialog with three tabs. Opening the dialog reads the saved reply; it does not generate a new answer, switch the company/report, change scenario assumptions, or rerun a forecast/stress calculation. The existing Sources, What I did and Check grounding controls remain available.

## Routing and agents

The panel shows the selected action, answer category, canvas target and whether a language-model call or a validated local rule handled routing. The execution diagram is built from the LangGraph nodes actually recorded for that answer. It is an execution receipt, not hidden chain-of-thought.

Passive service timers record preparation through the orchestrator, selected-report discovery/retrieval through the Fundamental Agent capability, saved Market Agent scenario contributions, current market-history checks for forecast freshness, saved Forecasting Agent outputs and source-backed graph traversal. Services can be completed, reused, unavailable, failed or not invoked. A general education answer does not pretend to have called every agent.

If an answer explains a saved scenario, its original orchestrator/agent trace is preserved separately. Those historical calculation timings are not added to the current explanation latency. Older answers lack some of the new instrumentation; missing measurements remain unavailable.

## RAGAS perspectives

An explicit **Run evaluation** action evaluates the frozen answer and the same evidence supplied to its generator. It never re-retrieves another company's/current report's evidence. An optional independently authored reviewer reference enables the reference-based metrics.

| Metric | Calculation / interpretation | Required input |
| --- | --- | --- |
| Faithfulness | Supported answer claims divided by judged claims, with claim-level verdicts and reasons | Answer and supplied context |
| Answer relevance | RAGAS generates three questions from the answer and averages BGE-M3 cosine similarity to the original question, with RAGAS's noncommittal-answer handling | Question and answer |
| Context precision without reference | RAGAS judges source usefulness against the response and computes ranked precision | Question, answer and saved source order |
| Context recall | Fraction of reviewer-reference statements attributable to supplied context | Independent reference and context |
| Factual correctness | Claim-level precision/recall F1 against the reviewer reference | Independent reference and answer |

The implementation uses installed RAGAS 0.4.3 collections metrics, the configured judge provider and the existing base BGE-M3 encoder for answer relevance. Context precision is explicitly bounded to the first eight saved source blocks; these can include curated or calculated sources and are not necessarily pure retrieval ranks. Full-context faithfulness/recall includes supplied calculations, assumptions and qualitative assessments, which are not independent reported facts. Oversized contexts are not silently truncated. Scores are separate diagnostics, not one overall financial-accuracy score. Answer relevance can theoretically be negative because it uses cosine similarity.

Metrics that need absent evidence or a reference are not applicable. Navigation, clarification and error messages do not receive fabricated RAG scores. General generated explanations can receive relevance scores without fictitious SEC grounding. Provider/dependency failures leave completed metrics intact and mark failed metrics unavailable. Evaluation uses an isolated worker and bounded deadlines; repeat evaluation can reuse a matching local result. Local evaluation artifacts are ignored by Git.

## Latency

Python monotonic timers measure server-side answer handling, workflow nodes, service operations, retrieval stages, language calls and provider round trips. The latter include network delay and SDK retry time. Provider-supplied input/output/reasoning-token counts are retained when available, without storing thought content.

Workflow, service, retrieval and LLM spans are nested/inclusive views. They must not be added together. Reading a saved result measures the read, not the original agent's training/calculation. Browser rendering and client transport are outside server-side answer timing. Internal model thinking time and time to first token are unavailable with the existing non-streaming calls; no invented timing is shown. Evaluation latency is separate from answer latency.

## Implementation choices

- Streamlit dialogs and tabs keep execution and quality diagnostics separate from the business canvas; Graphviz renders the execution/architecture diagrams.
- Existing LangGraph state/checkpoints and Python orchestrator remain responsible for execution. The new layer only observes them.
- RAGAS supplies the actual metric implementations. The existing BGE-M3 model avoids adding a separate paid embedding dependency for relevance.
- Monotonic timers and immutable snapshots make measurements attributable to one answer. Isolated workers preserve the application's native-library isolation.
- Inspection observes existing execution records. It does not change agent execution, train models or alter financial formulas.

## Recorded checks and interpretation

The recorded focused validation passed 20 distinct tests covering workflow timing, saved calculation reuse, report-scoped evidence, diagnostics, metric eligibility/failure handling and dialog/session behavior. Subsequent targeted checks covered a failed-service regression, saved reviewer references and evaluation-result persistence. Opening inspection was verified not to invoke the judge or change the selected tab, forecast model, saved forecast or scenario. Whitespace validation passed.

A synthetic smoke evaluation exercised all five RAGAS metrics with the configured DeepSeek judge and actual base BGE embeddings. It completed in **30.57 seconds**:

| Metric | Recorded score |
| --- | ---: |
| Faithfulness | 1.0 |
| Answer relevance | 0.911091 |
| Context precision | Approximately 1.0 |
| Context recall | 1.0 |
| Factual correctness | 0.0 |

The zero is retained. The authored reference omitted the company name, but its contribution to the zero is only a possible explanation: this metric's individual verdicts were not captured, and the precise cause is unverified. These results require human calibration. This was one functional smoke check using synthetic evidence and a manually authored reference, not an accuracy benchmark or proof of model improvement.

A separate browser check generated a general educational answer and displayed its LangGraph route. Server-side answering took **2.61 seconds**, including **2.58 seconds** of language calls and **2.46 seconds** of provider round trips. An explicitly requested relevance evaluation took **14.5 seconds** and scored **0.92**; filing-dependent metrics remained not applicable. These are individual observations, not p50/p95 values or a load benchmark. Provider-internal thinking duration and first-token latency remain unavailable with the non-streaming API.

The larger recorded sample and its limitations are documented in [Evaluation methodology](evaluation_methodology.md). See [Feedback and evaluation](feedback_and_evaluation.md) for eligibility rules and user ratings.

## Reproduce the focused checks

Run these commands from the repository root using the existing environment. The tests use controlled fixtures rather than a live model-quality benchmark:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_workspace_langgraph test_workspace_chat.WorkspaceChatTests.test_result_explanation_reads_saved_calculations test_workspace_chat.WorkspaceChatTests.test_explanation_reuses_current_audited_chunks_and_citations test_workspace_chat.WorkspaceChatTests.test_failed_switch_preserves_current_analysis -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_chat_diagnostics -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_interview_panel -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_interview_quality -v
git diff --check
```

These checks validate the inspection workflow and failure boundaries. Model-quality claims require separately evaluating generated answers, reviewing the supplied evidence and calibrating judge results against human assessment.
