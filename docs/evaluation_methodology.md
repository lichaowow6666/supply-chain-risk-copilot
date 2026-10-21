# Evaluation methodology and recorded results

This benchmark tests the analyst workflow: ask about a disclosed exposure, inspect its evidence and relationships, explain a saved price-shock scenario, and recognize a product-allocation evidence gap. The optional **Evaluation** dashboard reads saved results; opening it does not call a judge, regenerate an answer or change the company, report, forecast or scenario. Per-answer inspection remains available in the existing chat.

## Dataset and execution

`evaluations/interview_100_v1.json` contains 100 distinct authored questions and references: 90 English and 10 Chinese. It is a **synthetic development benchmark**, not an independently human-reviewed or held-out production test. References were written independently of the generated answers and are never passed to the answering model.

| Category | Cases | Business coverage |
| --- | ---: | --- |
| Disclosure | 24 | Materials, exposure, mitigation and selected-report evidence |
| Finance | 12 | Reported figures, ratios and fiscal-period distinctions |
| Graph | 10 | Source-backed relationships and unsupported links |
| Scenario | 16 | Contributions, assumptions, signs and allocation gaps |
| Forecast | 8 | Saved model, horizon, intervals and limitations |
| Education | 8 | Financial and risk concepts |
| Navigation | 8 | Canvas targets, clarification and company preparation |
| Governance | 14 | Company/report boundaries, memory, permissions and adversarial requests |

The frozen dataset SHA-256 is `a18cf6a9f4474b71010042a18b3da92987a6ebfbeb2e84f6ebdb55a3f6375d07`. The loader also verifies the underlying `report_scope_v1.json` fixture hash. Cases identify their reference basis and expected action, answer category, optional canvas section and workspace-preservation requirement. Expectations are frozen before execution; failures do not justify rewriting the reference after seeing an answer.

The serving path is the actual `WorkspaceChat` → LangGraph → existing services → configured LLM. Retrieval uses the base BGE-M3 encoder, BM25/vector fusion and the existing cross-encoder reranker. The synthetic SEC preparation adapter supplies known fictional companies and reports; indexing, retrieval, graph traversal, deterministic scenario services and forecasting still execute. This tests application integration, not live SEC availability or real-filing extraction accuracy. It is not a base-versus-fine-tuned model comparison.

The four report scopes are Alpha's earlier annual, latest annual and quarterly reports, plus Beta's quarter. Beta's disclosed revenue of USD 700 million is a **quarter** figure; the benchmark adapter excludes an incompatible year-to-date baseline. Saved scenarios use the real orchestrator, with annual spend and USD-million amounts:

| Saved scenario | Explicit assumptions | Deterministic net COGS / operating-income change |
| --- | --- | --- |
| Up | Copper spend 100; price +20%; unhedged 80%; pass-through 50% | +8 / −8 |
| Down | Same inputs; copper price −20% | −8 / +8 |
| Mixed | Up copper plus gas spend 25, price −20%, unhedged 80%, pass-through 50% | +6 / −6 |

Alpha's annual baseline is revenue 1,000, COGS 700, operating income 100, operating cash flow 80 and cash 50. Forecast cases use actual no-change and autoregressive benchmarks on 400 synthetic observations, with a five-session horizon and the saved `naive` selection. References do not assume particular forecast endpoints. XGBoost/LSTM training is outside this run. Chat explains saved operations and guides the user; the benchmark does not pretend that conversational scenario edits or enterprise access control are implemented.

## Five separate RAGAS perspectives

These are installed RAGAS 0.4.3 metrics, using the configured judge. They are diagnostics, not one overall accuracy score. Each has its own scored, not-applicable, unavailable and missing denominator. A valid zero is retained; missing scores are never converted to zero.

| Metric | What is computed | Important boundary |
| --- | --- | --- |
| [Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) | Judge decomposes the answer into claims; score = supported claims / judged claims. Claim verdicts and reasons are retained. | Context includes cited sources and the exact supplied structured financial/scenario/forecast/graph/workspace information, including labeled assumptions and qualitative assessments. Support is not independent real-world verification. |
| [Answer relevance](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/) | Generate three candidate questions from the answer; average their base BGE-M3 cosine similarity to the original question, multiplied by the noncommittal-answer penalty. | In installed 0.4.3, the multiplier is zero when **all** generated candidates are noncommittal, otherwise one. Cosine permits −1 to 1. A responsible refusal/evidence gap can receive a low score. |
| [Context precision without reference](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/) | Judge each supplied source's usefulness to the answer. Average precision = Σ(P@k × useful_k) / (Σ useful_k + ε). | Only the first eight saved source blocks are judged. Saved answer-context order can include curated or calculated sources; it is not necessarily the retriever's original rank order. |
| [Context recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/) | Supported reference statements / reference statements, using the full supplied context. | Requires an independently authored reference. Here those references are authored development targets, not independently human-reviewed truth. |
| [Factual correctness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/factual_correctness/) | Claim precision and recall against the reference, combined as F1 = 2PR / (P + R), with beta 1 and low atomicity/coverage. | Reference completeness and judge behavior influence the result. This is not a deterministic financial recalculation; individual verdicts for this metric are not captured. |

Navigation, clarification and error responses have no invented RAG scores. General generated explanations can receive relevance and reference correctness; evidence-dependent metrics are not applicable. Invalid source identifiers/report scope, absent context or excessive context size also prevent misleading context scores. Undefined/non-finite metric results are unavailable. Generator and judge may share the configured model, so correlated bias and human review remain material limitations.

## Routing, reuse and governance evidence

Route checks compare the actual action, answer category and specified canvas section with the frozen contract. A `materials` versus `exposures` target mismatch counts as a strict contract failure even if the prose is useful. This is distinct from semantic answer quality. The scripted task-outcome rate is a contract-completion proxy, not analyst acceptance or business value.

Service receipts identify operations actually completed, reused or failed. Explaining a saved scenario preserves its original calculation trace; it does not claim to have rerun the Market Agent. Recorded spans and LangGraph nodes are execution evidence, not hidden model reasoning.

Governance checks cover selected company/report scope, citation identifiers and provenance, and fingerprints of selected analysis state before/after a turn. Conversation history and canvas focus are excluded from the preservation fingerprint. Optional required/forbidden-marker checks are literal assertions, not semantic security classifiers; this dataset authors no such markers, so their checks are not applicable. Adversarial questions do not establish a semantic prompt-injection pass rate. A separate real `DemoPermissionPolicy` check permits the synthetic public filing and denies the synthetic treasury memo/unknown document. Its result is explicitly isolated: **the current WorkspaceChat has no enterprise identity/ACL enforcement demonstrated by this test**. This bounded measurement approach is consistent with the [NIST AI RMF's Govern, Map, Measure and Manage structure](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/); it is not a compliance certification.

Twenty-eight cases include labeled numeric reference metadata. These values are not independently asserted by a deterministic answer-number checker; the RAGAS reference metrics provide semantic comparison. Correct fixture arithmetic does not establish the numeric correctness of every generated answer.

## Latency and observed use

Answers run sequentially with shared model/vector caches; first use can include warm-up. Fixture preparation/scenario/forecast setup is recorded separately and excluded from server answer latency. Means and p50/p90/p95 describe this local sample, not an SLA or concurrent-load result. Provider round trips include network/SDK time; they do not reveal internal model thinking time. Nested workflow, service and model spans overlap and must not be summed. Browser rendering is outside these measurements.

Offline judging uses batches of at most four answers and eight concurrent metric tasks, with bounded metric and worker deadlines. Judge timing is separate from answering latency; per-answer evaluation duration can include scheduling waits. Partial metric failures remain visible. One answer attempt is retained per case, with no best-of selection. Resume fills missing phases rather than retrying recorded low scores or judge failures.

Anonymous pilot measurement is **off by default**. With explicit opt-in, the UI records only answer-rendered, inspection-opened and submitted helpfulness/completion feedback metadata locally. It stores no questions, answers, company identifiers or evidence. Rerenders are deduplicated; revised feedback replaces the old vote. Sessions are not unique people. Helpfulness and completion rates use their respective submitted-feedback denominators and remain unavailable with no observations. The scripted benchmark never creates adoption events.

A future analyst pilot should define goals, signals and metrics using the [Google HEART framework](https://research.google.com/pubs/archive/36299.pdf): independently checked task completion/errors, time to finish the exposure-to-scenario workflow, reported helpfulness, uptake among eligible consenting analysts, and return use over a defined period. Keep these measures separate from RAGAS and scripted contract outcomes. Real adoption and retention percentages require real observations and an appropriate consent/cohort design; they are not established here.

## Reproduction and review

Use the existing environment and configured provider credentials/local model files. The explicit runner makes external model calls with synthetic content; opening the dashboard does not. From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.chat.run_benchmark --output evaluations/results/interview_100_latest.json
```

To run generation and judging separately, preserving the same answers:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.chat.run_benchmark --phase answers --output evaluations/results/interview_100_latest.json
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.chat.run_benchmark --phase judge --resume --output evaluations/results/interview_100_latest.json
```

`--resume` requires the same dataset hash and case count. `--limit` is for a separate smoke output; do not substitute a limited run for the 100-case result. Frozen answer packets/checkpoints live in ignored local artifacts. The public result contains synthetic questions, references, answers, route/metric outcomes, configuration/version metadata and limitations, with local paths removed. The dashboard download further excludes full context packets. A mismatched catalog hash is warned about; saved references are not silently replaced.

Focused benchmark checks can be reproduced without the full suite:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_benchmark_dataset test_benchmark_runner test_benchmark_metrics test_interview_dashboard -v
```

Per-answer execution and quality inspection are described in [Answer inspection](answer_inspection.md). [Feedback and evaluation](feedback_and_evaluation.md) explains the metric eligibility rules, workflow checks and human feedback loop.

## What the recorded failures reveal

The benchmark preserves both application defects and evaluation limitations. Examples from the saved run:

| Case | Observed behavior | Interpretation and next action |
| --- | --- | --- |
| I002 | Correct spend/hedge/pass-through figures, followed by contractual and timing explanations not established in the source | Constrain company-specific extrapolation; separate general education from disclosed facts. |
| I020 | Correctly distinguishes 80% unhedged from hedged, then derives roughly 20% hedged in prose | The model introduces a calculated value outside the deterministic financial record. Calculation provenance needs a stricter guard. |
| I008 | Correctly reports a sole-source supplier evidence gap, but receives zero answer relevance | RAGAS can penalize a responsible non-answer. Inspect the actual response and claim verdicts before treating a low score as a defect. |
| I035 | Correctly cites the selected older report's USD 900M revenue, with low faithfulness for additional procedural advice | The judge treats several recommendations as factual claims unsupported by the filing. Reduce unnecessary advice and separately calibrate fact, education and workflow evaluation. |
| I014 / I054 / I068 | Navigation is returned where an explanation was expected | Route evaluation finds a user-workflow failure that a fluent prose score alone would miss. |

Several exposure answers place seven general financial records before material evidence at source eight. A context-precision score of 0.125 reflects the supplied answer-context ordering under this protocol; it is **not** an isolated score of BGE-M3 or the reranker. These are reasons to improve the next version, not reasons to remove low scores or rewrite this run's references.

## Recorded benchmark results

Completed run `dfa0cc687fe643829fee7b4ba647ea13`, 2026-09-23 18:40–18:49 UTC (24 September, 02:40–02:49 Shanghai). All **100/100** questions returned a response; **78** answers were eligible for at least one RAGAS metric. The remaining **22** navigation/clarification/fallback messages were not applicable. All planned eligible metrics completed: **354 numeric scores**, no judge failures and no missing scores.

| RAGAS metric | Mean | Scored | Not applicable | Unavailable |
| --- | ---: | ---: | ---: | ---: |
| Faithfulness | 0.712 | 66 | 34 | 0 |
| Answer relevance | 0.713 | 78 | 22 | 0 |
| Context precision | 0.332 | 66 | 34 | 0 |
| Context recall | 0.801 | 66 | 34 | 0 |
| Reference correctness F1 | 0.474 | 78 | 22 | 0 |

Strict workflow contracts passed in **70/100** cases: of the 30 failures, **20** were canvas-section-only mismatches and ten involved other checks. This is not a 70% factual-accuracy score. One fallback response (I057) was retained after answer generation failed, despite an outer successful reply status. Seven requests asked for clarification. Ninety-three completed without clarification, which is workflow automation coverage, not proof of correctness or adoption.

| Timing | Measured cases | Mean seconds | p50 seconds | p95 seconds |
| --- | ---: | ---: | ---: | ---: |
| Server answer | 100 | 2.75 | 2.96 | 3.49 |
| Recorded language calls | 94 | 2.60 | 2.87 | 3.30 |
| Provider round trips | 94 | 2.52 | 2.78 | 3.20 |
| Eligible RAGAS evaluation | 78 | 9.60 | 8.55 | 15.07 |

The first answer took 20.39 seconds including model warm-up. Six answers had no recorded language calls. Skipped-metric eligibility screening is separated from judge latency; raw screening durations remain saved. Judge duration includes bounded-batch scheduling, so it is not an isolated provider inference benchmark.

Applicable safeguards passed: selected analysis state **99/99**, reply scope **90/90**, source scope **72/72**, source provenance **72/72**, citation identifiers **67/67**. Remaining cases were not applicable. The isolated synthetic permission policy passed its three explicit decisions; this does not demonstrate enterprise ACL enforcement. Sixteen saved-result reuses were observed: **12 scenario and four forecast**. Hybrid report retrieval ran 50 times across four report scopes and five question categories. These receipts demonstrate shared service execution, not reduced engineering costs or business ROI.

The run used DeepSeek `deepseek-chat` for generation and judging, base BGE-M3 for retrieval/relevance, LangGraph 1.2.12, RAGAS 0.4.3, sentence-transformers 5.6.1 and Streamlit 1.61.1. No actual-user adoption or retention percentage is claimed. Opt-in observations remain separate from these scripted results.

## Validation coverage

The recorded implementation checks covered dataset validation (three tests), benchmark setup (two tests), and metric aggregation (six tests). Setup checks exercised three actual scenario calculations, five-session forecasts, report-history scope and the Beta baseline. The affected summary check also passed after fallback and eligibility-screening handling was updated.

Five initial dashboard/inspection checks and two hash-mismatch/storage-error checks passed without model calls. Streamlit AppTest rendered the complete 100-answer report without exceptions, including ten tables and ten metrics. Browser review confirmed the optional Evaluation tab and latency view; focused UI checks covered company/scenario-state preservation. The complete benchmark runner exited successfully, retaining all 100 attempts and 354 numeric outputs in `evaluations/results/interview_100_latest.json`. Scoped whitespace checks passed. These results describe the recorded validation, not a fresh rerun or a full-suite claim.

Not established: live SEC acquisition reliability, real-company filing extraction quality, independent human reference agreement, concurrent-load performance, production permissions, analyst adoption or financial ROI. These require different data and real pilot observations, rather than additional claims from this synthetic run.
