# Agent-trace retrieval regression

The 2026-10-03 run `2026-10-03T12-31-56-840Z_01a101bf-e9a8-7259-909d-6028b1c2e2a4.jsonl`
and its accompanying analysis supplied these task intents. The raw transcript is
external development evidence; it is not required by tests or bundled in help.

The observed failures included `set cover` returning decomposition, `graph matching`
returning isomorphism, `claim construction` returning Model, and nonexistent
`api.Report.close` returning other owners' close methods. Some attempted help calls
in the trace were preceded by an exception and therefore never executed.

Baseline below is a replay against this checkout before the retrieval changes,
not a claim that every query appeared verbatim in the trace.

| Query | Previous first result | Required first result |
| --- | --- | --- |
| batch linear proof | api.Checker.linear_many | api.Checker.linear_batch |
| many linear certificates | api.Checker.linear_many | api.Checker.linear_batch |
| tighten big M | concept.conditions | concept.big-m |
| claim construction | api.Model | api.Claim |
| fractional point feasibility | limitation.optimization | limitation.fractional-point |
| resource cover | recipe.closure | concept.resource-cover |
| report resource lifecycle | recipe.closure | concept.report-resources |
| conditional case split | api.StructuralSignatures.splits | concept.conditions |
| set cover | recipe.decomposition | concept.resource-cover |
| graph matching | api.alg.graph.isomorphism_classes | limitation.graph-matching |
| schema | api.Frame.schema | api.Frame.schema |

The original retrieval-only change improved this 11-query set from 3/11 to 11/11.
After adding the scalar-style batch builder, general batch intents route to
`Checker.linear_batch`; explicit `Frame native batch certificates` still routes to
`Checker.linear_many`. Tests also
require 11 longer paraphrases to succeed through search, without exact aliases.
Existing Recall@3 and mathematical-boundary tests remain in place. This set is
curated from one run and does not estimate general retrieval quality.

Changes index alias vocabulary, add API intent keywords, correct the Frame
keyword sequence, and keep checker-specific instructions on their own topics.
Focused capability topics explain unsupported operations instead of routing
nearby words to unrelated recipes. Unknown dotted API paths return no match;
spelling suggestions remain suggestions rather than advertised contracts.

These retrieval changes implement no new solver, assumption-discharge rule,
report close method, or fractional solution checker, and add no onboarding
instructions.

Run `python scripts/build_help.py --check` and `python -m pytest -q tests/help`.
