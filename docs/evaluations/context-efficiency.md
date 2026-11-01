# Context efficiency evaluation

Evaluated locally on 2026-11-01. Default remains **observe**. These are synthetic
experiments, not production performance or a completed cross-provider quality gate.

## Reproduction

```sh
.venv/bin/python -m vegapunk.evaluate_context --output /tmp/context-synthetic.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --output /tmp/context-live.json
.venv/bin/python -m vegapunk.evaluate_context --live --provider zai --summary-only --output /tmp/context-summary.json
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

A second-provider attempt using Codex returned HTTP 400 before a valid paired result.
Its cause and live compatibility remain unverified. The current ten tasks also cover
a narrow lookup workload rather than the full recovery/delegation/action-quality rubric.
Keep observe as default until broader task-quality, uncertainty, and second-provider
evaluation pass. `VEGAPUNK_CONTEXT_POLICY=off` is the diagnostic rollback;
`bounded` is an explicit experimental opt-in.
