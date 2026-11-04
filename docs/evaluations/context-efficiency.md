# Context efficiency evaluation

Evaluated locally on 2026-11-01. Default remains **observe**. These are synthetic
experiments, not production performance or a completed cross-provider quality gate.

## Reproduction

```sh
.venv/bin/python -m vegapunk.evaluate_context --output /tmp/context-synthetic.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --output /tmp/context-live.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --summary-only --output /tmp/context-summary.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider codex --model gpt-6.1-sol --output /tmp/context-codex.json
```

The benchmark creates a temporary database, supplies fabricated logs, and exposes
only session artifact reading. It does not run shell commands or alter user state.
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
| Reported input tokens, including retrieval iterations | 47,159 | 28,218 |
| Reported output tokens | 436 | 729 |
| Artifact retrieval calls | 0 | 7 |
| Total measured seconds | 76.725 | 65.820 |
| Median seconds per task | 4.938 | 5.700 |

Totals are sequential wall times, not service throughput. Cache conditions and one
slow baseline request affect latency. A separate middle-of-artifact case required
two retrievals and used more total input than baseline (5,459 versus 4,897 tokens).
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
| Reported input tokens | 48,747 | 7,372 | 59,939 | 15,383 |
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
modes on the targeted version-2 rerun. The full varied version-2 suite has not been
rerun; neither earlier failure is retrospectively marked passed.

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
