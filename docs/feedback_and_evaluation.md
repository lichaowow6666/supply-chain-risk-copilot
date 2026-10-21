# Answer feedback and evaluation

Supply Chain Risk Copilot accepts an explicit thumbs-up or thumbs-down rating beside each assistant answer. The **Evaluation** workspace separates measured answer quality, execution timing, workflow safeguards and human feedback. These provide different evidence; none is an overall business-accuracy or adoption score.

## Giving and reviewing feedback

A thumbs-down exposes eight optional reasons: **Incorrect facts**, **Missing evidence**, **Wrong company or report**, **Misunderstood my request**, **Incomplete answer**, **Unclear answer**, **Slow response** and **Other**. Multiple reasons are allowed. “Other” is a fixed code, not a free-text field.

Changing a vote replaces the existing vote; rerenders do not add votes. Clearing a rating removes its negative reasons, and changing to thumbs-up clears them too. Previously recorded task-completion feedback is retained separately. Ratings survive widget cleanup during tab changes within the current session. A failed save is visible and can be retried without interrupting the answer or financial analysis.

Explicit votes work independently of **Record anonymous usage counts**, which remains off by default. Voting does not turn on passive answer/inspection event collection. Local SQLite storage contains a random anonymous session identifier, hashed answer identifier, event type, rating, approved reason codes, timestamps and any separately submitted completion flag. It stores no answer/question text, company names, source passages or free-form comments. The database remains in ignored local artifacts.

Open **Evaluation → Feedback** to see positive/negative counts, rated-answer count and **Met expectations**: positive votes divided by submitted positive-plus-negative votes. With no votes, the rate is unavailable. The negative-reason bar chart/table counts each selected reason; its percentage denominator is all negative ratings. Because multiple reasons are allowed, percentages can total more than 100%. Missing reasons are not inferred.

**Review rated answers in this conversation → Inspect rated answer** opens the existing answer inspector. That review uses messages still available in the current session. Persistent aggregate statistics cannot reconstruct historical answers from the SQLite store. Counts represent answer votes and anonymous sessions, not unique people or verified task success. Synthetic benchmark inspections do not count as user adoption.

The improvement process is deliberate: review recurring reasons, inspect/reproduce an affected answer, identify the cause, create a regression case, then compare the next release against the same evaluation contract. Feedback does not automatically change prompts, financial formulas, retrieval configuration or model weights. A future analyst pilot can use the [Google HEART goals-to-metrics approach](https://research.google/pubs/measuring-the-user-experience-on-a-large-scale-user-centered-metrics-for-web-applications/) to measure verified task completion, time, helpfulness and return use separately. Actual adoption/retention claims need real observations and a defined consenting cohort.

## Why a RAGAS metric can be not applicable

The application checks eligibility before calling RAGAS. It does not assign zero to a missing score. All five metrics first require a successful **generated** answer, an outer reply status of `ok`, and nonempty question and response. Navigation, clarification, preparation acknowledgements and fallback messages therefore receive no fabricated generation score.

| Metric | Additional N/A conditions |
| --- | --- |
| Answer relevance | None after the common generated-answer gate |
| Faithfulness | Actual answer category is general/workflow; source/report validation failed; no full context; or full context exceeds 120,000 characters |
| Context precision | Same category/validation/size checks; requires saved source blocks specifically |
| Context recall | Missing reviewer reference; then the same checks as faithfulness |
| Factual correctness | Missing reviewer reference |

The full context comprises saved source texts plus the exact supplied structured context, including labeled calculations, assumptions and qualitative assessments. Source support does not turn those assumptions into reported facts. [RAGAS faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) measures supported answer claims; [context recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/) measures how much of the reference is supported by context. They answer different questions. Judge failures, timeouts and undefined/non-finite results are **unavailable**, not N/A. Invalid input packets can instead reject evaluation before any metric runs.

The completed 100-case run (`dfa0cc687fe643829fee7b4ba647ea13`) contains:

| Metric | Scored | N/A | Recorded N/A reasons |
| --- | ---: | ---: | --- |
| Faithfulness | 66 | 34 | 22 non-generated + 12 general/workflow |
| Answer relevance | 78 | 22 | 22 non-generated |
| Context precision | 66 | 34 | 22 non-generated + 12 general/workflow |
| Context recall | 66 | 34 | 22 non-generated + 12 general/workflow |
| Factual correctness | 78 | 22 | 22 non-generated |

The 22 non-generated replies comprise 13 navigation, seven clarification, one preparation and one fallback (`I057`). All five metrics have zero missing/unavailable results in this run. No reference/context/validation/size exclusion occurred.

**Eligibility limitation:** the gate uses the actual routed answer category. `I023`, `I032` and `I041` were expected to be company explanations but routed as general; each had seven supplied sources yet skipped evidence metrics. `I098` is general but contains cited financial figures. N/A is therefore the current software decision, not proof that evidence checking was unnecessary. Inspect routing alongside the score denominator.

## What workflow and scope checks establish

These are deterministic comparisons with authored expectations, separate from RAGAS. A false comparison fails; an absent or inapplicable comparison is N/A.

| Check | Pass criterion; N/A boundary | Pass / fail / N/A |
| --- | --- | ---: |
| Response completed | Outer reply status is `ok`; no N/A gate | 100 / 0 / 0 |
| Expected action | Actual action belongs to authored allowed values; no expectation → N/A | 94 / 6 / 0 |
| Answer category | Actual category belongs to allowed values; no expectation → N/A | 93 / 7 / 0 |
| Canvas section | Exact expected section; unspecified section → N/A | 52 / 26 / 22 |
| Analysis state | Equal selected-state fingerprints; preservation not required or comparable snapshots missing → N/A | 99 / 0 / 1 |
| Selected scope | Available target company/ticker/accession/snapshot identifiers match; no target or untargeted preparation → N/A | 90 / 0 / 10 |
| Citation identifiers | Every inline `[n]` points to an existing source; none inline → N/A | 67 / 0 / 33 |
| Source scope | Present source ticker/accession metadata matches reply scope; no scoped sources → N/A | 72 / 0 / 28 |
| Source provenance | Every supplied source has nonempty text and a locator/chunk/document/URL identifier; no sources → N/A | 72 / 0 / 28 |
| Required/forbidden terms | Literal case-insensitive presence/absence; no authored terms → N/A | Each: 0 / 0 / 100 |

State preservation covers selected analysis fields, excluding conversation history and canvas focus. Citation/provenance checks establish identifiers and metadata, not whether a source proves the claim. Missing source scope metadata is not itself rejected by the source-scope check. No semantic prompt-injection pass rate follows from the literal checks, which were not applicable throughout this dataset.

The overall contract passes only when no applicable check fails and required preservation is assessable: **70/100 passed**. Of 30 failures, 20 were section-only mismatches and ten involved other checks. These are not 30 established factually wrong answers. `I057` retained a useful deterministic fallback and passed its contract, because the outer reply was `ok`; the separate fallback counter exposes its generation failure.

Inspect **Evaluation → 100-question catalog → Open question**. **Saved per-question evaluation** shows each metric's status/reason; **This question’s workflow checks** shows each deterministic result. **Inspect this answer** opens the saved execution and evidence. Aggregate checks and the isolated synthetic permission-policy demonstration are under **Capabilities & governance**.

## Improvement priorities

1. **Improve the analyst journey first.** Investigate the six action errors, seven answer-category mismatches and 26 section mismatches. For example, `I066` sent a forecast-endpoint explanation through company/graph routing. Preserve the frozen results when comparing a later fix.
2. **Review evidence selection and answer completeness.** Mean faithfulness is 0.712, context precision 0.332, recall 0.801 and reference correctness 0.474. These are judge diagnostics on authored synthetic references, not production accuracy. Human review should distinguish answer defects from incomplete references/judge errors and address eligibility blind spots. The 28 numeric-reference cases lack a deterministic generated-number checker.
3. **Keep permission claims bounded.** The standalone synthetic policy makes real allow/deny decisions, but the live workspace has no demonstrated authenticated enterprise ACL. Scope preservation is useful evidence; it does not replace authorization enforcement.
4. **Use measured promotion criteria for fine-tuning.** The earlier held-out synthetic dense results were unchanged; English hybrid Recall@1 fell from 1.0 to 0.944. No reliable improvement was established, so base remains the default. Its 128-token audit differs from current 512-token serving and uses shared synthetic templates.
5. **Measure real usefulness and reliability.** Explicit feedback enables observations, not a fabricated adoption percentage. The existing latency sample is sequential, synthetic and partly warm-cache: answer p50 2.956s, p95 3.493s, maximum 20.391s. It does not establish concurrent-user performance, an SLA or live SEC acquisition reliability.

These priorities connect answer-quality diagnostics to concrete product improvements. The recorded [evaluation methodology](evaluation_methodology.md) preserves the benchmark protocol and results; [answer inspection](answer_inspection.md) explains the execution, evidence and timing views used to investigate an individual response.

## Validation and reproducible checks

Recorded validation passed seven backend checks in **0.022 seconds**, six UI checks in **1.864 seconds**, and two navigation/style checks in **1.026 seconds**. A focused rated-answer review check also passed in **0.814 seconds**. These are recorded test durations, not application latency claims. The existing 100-case benchmark and model weights were retained; these feedback checks did not rerun the benchmark or train models.

Backend checks cover explicit vote replacement, allowed reason codes, privacy and aggregate counts:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_answer_feedback test_benchmark_metrics.AnonymousUsageTests -v
```

UI checks cover feedback controls, opt-in event collection, failure handling, and preservation of the existing analysis:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_answer_feedback_ui test_interview_dashboard.InterviewDashboardTests.test_feedback_needs_opt_in_and_explicit_save_and_excludes_benchmark_preview test_interview_dashboard.InterviewDashboardTests.test_metadata_store_failure_cannot_interrupt_feedback_or_scenario test_interview_panel.InterviewPanelTests.test_dialog_opens_without_evaluation_or_losing_canvas_and_legacy_details -v
```

Navigation checks cover company, scenario and Evaluation-tab state:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_canvas_navigation.CanvasNavigationTests.test_plain_labels_preserve_existing_company_scenario_and_evaluation_tabs test_interview_dashboard.InterviewDashboardTests.test_optional_third_tab_without_company_keeps_scenario_model_and_results -v
```

The rated-answer review can be checked independently:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests venv/bin/python -m unittest test_answer_feedback_ui.AnswerFeedbackUITests.test_evaluation_shows_explicit_votes_and_reason_counts -v
```

Streamlit AppTest rendered the completed 100-case report, Feedback tab and eligibility/check explanations without exceptions, using saved results. Browser review verified the visible product name, plain tabs, chat avatars and rating controls. Theme validation resolved the primary color to `#2563EB`, and scoped whitespace checks passed.
