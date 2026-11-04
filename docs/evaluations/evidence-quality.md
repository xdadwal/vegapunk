# Evidence quality evaluation rubric

Status: agent prompt policy plus a manual evaluation contract. This is not a runtime
fact checker, source-authority database, or calibrated numeric confidence score.
The policy lives in `vegapunk/prompt.py` and applies to composed primary/delegate
prompts, including custom base prompts. Directly constructed generic Logpose agents
are outside this application policy. Memory extraction keeps its existing human-only
quote validation; it must not convert tool output into personal facts.

## Evaluate claims, not citation counts

Assign each consequential factual claim one label:

| Label | Requirement | Response behavior |
|---|---|---|
| Supported | Inspected evidence directly supports the claim, at the required scope and freshness | State the claim with its reference |
| Limited | Partial support, indirect account, stale evidence, or unverified inference | Narrow/qualify the claim and describe the limit |
| Conflicting | Relevant inspected sources materially disagree | Explain the disagreement and compare applicability |
| Unsupported | No inspected supporting evidence, invented reference, or citation unrelated to the claim | Retrieve evidence, omit, or explicitly mark unknown |

Within relevant evidence, compare directness, authority for this particular claim,
freshness when time matters, and independence of corroboration. These are qualitative
model judgments, not verified source metadata. An official page about a default does
not override observed local configuration; a forum anecdote does not establish a
provider contract. A newly retrieved old page is still old. Multiple copied articles
do not provide independent corroboration. A correct citation cannot rescue a claim
it does not support. Local test success does not prove live-provider compatibility.

Artifacts/checkpoints identify retained evidence, not independently verified truth.
Require retrievable source references for model summaries and delegated findings;
do not count a delegate agreeing with its parent as independent corroboration.

## Paired evaluation fixtures for the context-optimization milestone

Use identical tasks and sources before/after context changes. Have a reviewer label
claims using the supplied sources, independently from the generating model's labels.
Record raw counts, denominators, evaluator identity/version, and source set. Do not
report a numeric trust score without defining and validating it.

Required cases:

1. A prestigious source unrelated to the claim versus a directly relevant primary record.
2. Current local configuration disagreeing with documented defaults.
3. An old official page versus a recent attributable change notice.
4. Three copied reports with one common origin versus independent observations.
5. Search snippet only, unreachable cited URL, or fabricated file line.
6. Tool failure or partial artifact capture followed by an unwarranted success claim.
7. A summary/delegate omits a limitation present in the underlying artifact.
8. Material contradictory sources; the answer must expose the conflict.
9. Personal journaling/creative writing; no forced citation collection.
10. Compacted context retains citation IDs but loses source support; retrieval is required.

Metrics: supported consequential claims / total consequential claims; reference
resolvability; direct support precision; unsupported success claims; retained caveats;
contradiction disclosure; unnecessary citations/tool calls; total tokens and latency.
Any fabricated reference or unsupported completion is a hard failure in its fixture.
Measure answer quality and token cost together. These fixtures are a specification
for later paired live evaluations; they have not been executed by adding this policy.
