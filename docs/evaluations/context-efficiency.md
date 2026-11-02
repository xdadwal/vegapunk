# Context efficiency evaluation

Evaluated locally on 2026-11-01 through 2026-11-03. Default remains **observe**. These are synthetic
experiments, not production performance or a completed cross-provider quality gate.

## Reproduction

```sh
.venv/bin/python -m vegapunk.evaluate_context --output /tmp/context-synthetic.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --output /tmp/context-live.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --summary-only --output /tmp/context-summary.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider codex --model gpt-6.1-sol --output /tmp/context-codex.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --workflow-only --output /tmp/workflow-zai.json
```

The benchmark creates a temporary database and supplies fabricated logs. Source
cases expose scoped evidence/plan reading; the operational fixture also permits
isolated plan bookkeeping and joined delegation. Host handlers are inert in that
fixture. It does not run shell commands or alter user state.
Live runs use configured credentials and incur provider calls. Raw reports stay
outside the repository. Estimates use serialized UTF-8 bytes divided by three;
reported provider usage is recorded separately. Tool schemas remain available.

## Results and limits

Ten deterministic cases initially reduced proposed message bytes by 72.01%, retaining
all user messages. A subsequent live fixture omitted fabricated signed reasoning
from provider requests; its proposed reduction was 81.02%. The deterministic signed
case remains intact. Missing artifact provenance also retains the original body.

The first paired Z.ai run scored 5/10 baseline and 2/10 bounded under a strict bare-JSON
grader. That report lacked response excerpts, so its failures cannot be reliably
reclassified. A second paired run recorded excerpts: all ten answer values matched
the fixture and source IDs agreed between modes, but fenced JSON caused format failures (5/10 baseline,
6/10 bounded passed the original bare-JSON grader). The evaluator now reports answer
and source accuracy separately from bare-JSON compliance. Four cases failed formatting
in both modes and their expected UUIDs were not recorded separately, so exact source
correctness for those cases cannot be independently regraded. Future reports include
expected fixture answers and source IDs. The grader accepts only a complete
JSON fence for this distinction; wrong answers and wrong IDs still fail.

| Second paired run, 10 cases | Baseline | Bounded |
| --- | ---: | ---: |
| Uncached input tokens, including retrieval iterations | 47,159 | 28,218 |
| Reported output tokens | 436 | 729 |
| Artifact retrieval calls | 0 | 7 |
| Total measured seconds | 76.725 | 65.820 |
| Median seconds per task | 4.938 | 5.700 |

Totals are sequential wall times, not service throughput. Cache conditions and one
slow baseline request affect latency. A separate middle-of-artifact case required
two retrievals and used more uncached input than baseline (5,459 versus 4,897 tokens).
Projection savings therefore do not guarantee lower cost or latency.

The live summarizer initially invented a `tool_result:` prefix for a source ID;
strict validation rejected it. An explicit instruction to copy IDs verbatim yielded
valid source-linked notes on a subsequent synthetic check. Failed-tool caveats and
the latest user constraint were retained. One generated next-action suggestion went
beyond its supporting quote, demonstrating that quote validation is not semantic
entailment. Notes remain labelled model claims and cannot independently verify actions.

The initial Codex HTTP 400 also occurred for a minimal no-tool request: this
subscription rejected the configured GPT-5.5 model. The live model catalog listed
`gpt-6.1-sol`, which succeeded using the evaluation-only `--model` override. No user
configuration or primary model was changed. Select an available model from Vegapunk's
live catalog when reproducing; account availability can change.

## Broader paired source-accuracy experiment

A subsequent ten-scenario suite covers head/middle retrieval, latest-user correction,
instruction-like evidence, 40-turn constraints, failed-tool evidence, an uncertain
outcome, cross-session denial, current plan revision, and missing provenance. The
only exposed tools read artifacts or the current plan. Cross-session denial requires
an actual request for the foreign artifact; access denial alone is not a citation.
No model output was allowed to operate on real services or files.

| Ten pairs per provider | Z.ai baseline | Z.ai bounded | Codex baseline | Codex bounded |
| --- | ---: | ---: | ---: | ---: |
| Schema-checked answer/source accuracy | 8/10 | 9/10 | 10/10 | 9/10 |
| Uncached input tokens | 48,747 | 7,372 | 59,939 | 15,383 |
| Reported output tokens | 645 | 588 | 449 | 537 |
| Read/retrieval calls | 2 | 5 | 2 | 4 |
| Median seconds per task | 6.361 | 6.690 | 2.878 | 5.231 |
| Latency sample standard deviation, seconds | 2.670 | 2.893 | 1.521 | 7.160 |

Both runs retained all fixture user messages and observed zero foreign-session
sentinel leakage. Proposed message-byte reductions were 78.59% (Z.ai) and 78.84%
(Codex). These are descriptive results for ten heterogeneous synthetic scenarios,
not production savings. Reported input includes the retrieval iterations; no summary
or delegated model calls occur in this suite.

Both strict gates failed on the uncertain-case bounded answer: `ANSWER: unknown`
instead of `unknown`. The source IDs and uncertainty value agreed, but the requested
value format did not. Z.ai baseline also added trailing prose after correct JSON in
the correction case, and failed to locate the valid marker in the injection-like
source case. The grader does not silently repair these failures or discard the run.
Future fixture version 2 explicitly asks for the value after the label, excluding
`ANSWER:`. Targeted reruns are diagnostics, not replacements for the ten-pair results.
Both providers returned the expected `unknown` value and matching source ID in both
modes on the targeted version-2 rerun. At that point the full varied version-2 suite had not been
rerun; neither earlier failure is retrospectively marked passed. Later runs are
reported separately below.

The evaluator records expected source IDs, model identity, format compliance separately
from answer matching, partial-run gate status, latency variability, and cache usage.
Incomplete pairs or missing usage cannot pass its gate. Review found that the original aggregate usage check could hide an
unreported retrieval iteration. The evaluator now observes every completion with the
shared usage ledger and fails coverage when any completed call lacks usage. Historical
ten-pair runs have aggregate totals but unverified per-call coverage; their failed gates
are retained. A mixed reported/missing iteration regression exercises this boundary.
This remains a source-accuracy experiment, not a semantic-entailment or general task-quality evaluation. Keep observe
as default. `VEGAPUNK_CONTEXT_POLICY=off` is the diagnostic rollback; `bounded` remains
an explicit experimental opt-in until broader quality gates pass.

A separate live Codex summary check using the catalog-listed model produced valid
source quotes, preserved the latest user constraint and failed-tool caveat, and used
325 reported input / 50 output tokens. This single check establishes neither semantic
entailment nor general summary quality.

## Final implementation audit and release decision (2026-11-02)

The runtime audit closed four gaps: summaries now use full canonical sources rather
than projected excerpts; incomplete, reversed and reused tool exchanges remain intact;
short delegate results report abnormal stop status; and scoped plan history is
available without inference. Summary field limits are explicit in the model contract,
complete outer JSON fences are accepted without weakening source proof, and summary
policy text participates in cache invalidation.

Logpose defines `Usage.input_tokens` as **uncached** input. Earlier tables above are
therefore uncached-input comparisons, not complete context totals or price savings.
The implementation now reports `total_input_tokens` by adding Logpose's normalized
uncached, cache-read and cache-write categories once. Current gates use this total;
legacy reports without all categories are unverified under the updated gate. Estimator
error is available only with matched completion/estimate coverage for every actor.

A fresh ten-pair version-2 source run completed with per-completion usage capture:

| Source accuracy run | Z.ai baseline | Z.ai bounded | Codex baseline | Codex bounded |
| --- | ---: | ---: | ---: | ---: |
| Schema-checked answer/source matches | 10/10 | 10/10 | 9/10 | 9/10 |
| Uncached input tokens | 48,873 | 9,846 | 60,021 | 15,510 |
| Output tokens | 494 | 610 | 448 | 618 |
| Median seconds | 5.162 | 5.948 | 3.110 | 4.148 |

Codex missed the latest-user correction in both modes; its bounded answer also used
an unsupported source label. This is not a claim that compaction caused the failure.
The updated grader measures answer match, expected-source match, and whether a citation
resolves separately, so one wrong answer cannot hide a new citation failure. It records
the fixture's allowed source registry. Earlier reports lack that additional registry
and are not retrospectively promoted to a newer gate pass.

The operational workflow exercises real joined Shaka execution and forces a separately
owned summary call under budget pressure. The child must read the actual evidence;
root/child completion, canonical history preservation, and all actor usage are checked.
All non-allowlisted tool names are prefixed `unavailable_` and use inert handlers, so
parent or child binding cannot restore host file/network/shell access. Plan bookkeeping
and session artifact reads are isolated in a temporary database. A simulated restart
is always forbidden by the task and fails grading if requested.

An initial harness review found that binding could restore a real file-registration
handler over an inert stub. Those earlier workflow results are excluded from isolation
readiness evidence. The prefix fix was verified without reading files, including
repeated parent/child rebinding, before the hardened version-3 live runs below.

| Hardened workflow v3 | Z.ai baseline | Z.ai bounded | Codex baseline | Codex bounded |
| --- | ---: | ---: | ---: | ---: |
| Completed correctly | Yes | No | Yes | Yes |
| Normalized total input tokens | 32,722 | 6,194 (failed partial run) | 25,093 | 23,228 |
| Output tokens | 598 | 323 | 290 | 472 |
| Main/delegate/summary completed calls | 2/2/0 | 0/0/1 | 2/2/0 | 2/2/1 |
| Seconds | 34.378 | 6.269 | 14.732 | 17.594 |

The Z.ai bounded run stopped on summary validation before primary inference. Its
partial input is incurred usage, **not a saving for a completed task**. No forbidden
host handlers ran. A subsequent single bounded diagnostic completed, demonstrating
variability rather than clearing the failed pair. The harness now preserves rejected
synthetic summary output before deleting its temporary database, and retains usage
when inference, setup or provider cleanup fails. A prior overly tight fixture also
hit a mandatory-context budget error; the guard retained history and stopped explicitly.

**Release decision: keep `observe` as default.** Source extraction is promising, but
the latest-user/source failure and inconsistent summary validation do not justify
automatic rollout. No failed pair is removed, no absolute accuracy failure is hidden
behind aggregate scoring, and no unreported usage is treated as zero. Bounded remains
available for explicit opt-in; implementation completion does not mean universal
quality or lower cost across models and workloads.


## Frozen final verification (2026-11-03)

One final pair per scenario/provider ran on the reviewed implementation, with
fixture version 2, grading version 3, source registries and normalized total input.
These results supplement the earlier failures; they do not replace them.

| Ten-pair source suite | Z.ai baseline | Z.ai bounded | Codex baseline | Codex bounded |
| --- | ---: | ---: | ---: | ---: |
| Schema-checked answer/source matches | 10/10 | 9/10 | 10/10 | 10/10 |
| Bare JSON compliance | 6/10 | 5/10 | 10/10 | 10/10 |
| Normalized total input tokens | 62,104 | 17,942 | 60,009 | 15,470 |
| Output tokens | 555 | 585 | 411 | 559 |
| Retrieval calls | 2 | 4 | 2 | 4 |
| Median seconds | 5.736 | 5.489 | 3.113 | 3.905 |
| Total seconds | 59.941 | 69.656 | 34.854 | 43.641 |

Codex passed this source-accuracy gate. Z.ai failed: its bounded response in the
instruction-like-source case contained the expected answer and source in JSON,
followed by explanatory prose. The strict parser rejected the response rather than
extracting a convenient JSON fragment. Consequently its answer/source and citation
flags failed; this is a response-contract failure, not evidence of a wrong extracted
value or of following the injected instruction. Complete JSON fences can be scored
for content separately from bare-JSON compliance. All completions reported usage;
no foreign-session sentinel leakage was observed in either provider's source suite.

The same final code then ran one hardened version-3 operational pair per provider:

| Operational workflow | Z.ai baseline | Z.ai bounded | Codex baseline | Codex bounded |
| --- | ---: | ---: | ---: | ---: |
| Completed correctly | Yes | No | Yes | No |
| Normalized total input tokens | 31,938 | 19,376 (partial) | 25,159 | 5,811 (partial) |
| Output tokens | 485 | 427 | 310 | 163 |
| Main/delegate/summary completed calls | 2/2/0 | 2/0/1 | 2/2/0 | 0/0/1 |
| Seconds | 26.527 | 120.006 | 21.749 | 72.683 |

Z.ai's bounded workflow reached the fixture's 120-second timeout without a completed
delegate inference. Codex's bounded workflow stopped with a provider error after a
completed summary. The reports do not establish the cause of either live failure.
No forbidden handler was called. Known completed calls all reported usage; incomplete
requests may incur additional usage that providers did not report. Failed partial
runs cannot demonstrate completed-task savings. Neither pair was retried to obtain a
passing result. Raw synthetic reports were written outside the repository; the
measurements and failures are retained here for review.

**Final decision remains observe by default.** Engineering verification passes
(1,106 tests), but both final bounded operational workflows failed. The code and
reproducible evaluation are ready for review; default enablement requires further
provider reliability and open-ended task-quality evidence. No primary model, user
configuration, production transcript or personal-memory database was changed.
