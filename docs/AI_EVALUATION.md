# Offline agent comparison protocol

This repository currently measures deterministic fact recovery. It does not run
online models, claim statistical AI superiority, or treat a single analyst's
before/after exploration as a blinded A/B trial.

## Freeze before comparing

Freeze the source hashes, development/holdout **model families**, mipframe revision,
question set, independent answer rubric, and total budgets. Never split renamed
copies or parameter variants from one family between development and holdout.
The four public smoke models in `benchmarks/public/manifest.json` are too small a
sample to support a broad generalization claim. After inspecting a holdout failure,
record that exposure; use new model families for a later confirmatory test.

Use independent sessions with the same model/version, reasoning settings, total
wall-time budget, total billed-token budget, and tool execution resources. Run
multiple seeds/repetitions; randomize presentation order. Report failures/timeouts,
not only successful examples.

## Arms

1. Raw LP/MPS plus read-only file/compute tools; full text in context only when it
   fits. Log every extracted byte and analysis script. For huge files this is a
   raw-file/tool baseline, not a full-context baseline.
2. Numerical Model only: exact row/column inspection and sparse traversal.
3. Factual IR plus Frames, indexed views, `ops`, `alg` and explicit `check` tools; no direct source reads.
4. Deterministic MIPFrame without an LLM, reporting only recovered and verified facts.

Optional ablations remove refinement, indexed coordinates, structural
operations or discrete candidate searches. All arms see the same mathematical
model, but raw numerical IDs/names should also be randomly permuted in a separate
invariance experiment. A name-assisted hypothesis must be flagged; its verification
must inspect the complete claimed scope.

## Tasks and ground truth

- Identify the objective and principal mathematical row families, including roles,
  variable domains, all-row scope and exceptions.
- Recover incidence/index relationships and report external row/objective coupling.
- Propose useful implications or local relaxations; return verifiable witnesses.
- Reject perturbed identities, invalid cliques, incomplete index mappings, and
  misleading high-coverage generic templates.
- Retrieve counterexamples without exporting the full model back into context.

`scripts/evaluate_indexed.py` verifies indexed views and numerical round trips.
`scripts/evaluate_ops.py` checks explicit structural operations. `scripts/evaluate_public.py` checks
public MPS inputs and exact LP round trips. These are regression checks, not hidden
discoveries or evidence of AI superiority.

## Metrics

Report separately: numerical correctness, verified-claim precision, factual task
coverage, unsupported claims, query count, returned bytes, input/output tokens,
latency, peak RSS, artifact bytes, and validation cost. A missing structure is a
coverage failure; an incorrect claim marked verified is a correctness failure.

Measure downstream effects only for proved operations: unchanged LP value for
LP-implied simplifications, improved LP bounds for valid integer cuts, and solver
runtime including analysis/verification overhead. Better LP bounds alone do not
establish faster integer solving. Use solver-default baselines when eventually
comparing end-to-end reformulation; do not count improvements already obtained
cheaply by its presolver as uniquely attributable to the agent.

No numerical target such as "matches full-context AI" is accepted until the actual
paired experiment is run. Repeated-block large fixtures certify only that workload,
not arbitrary large MIP structure or asymptotic performance.

## Help-system evaluation (not an online result)

Freeze the compiled help digest, package version, intent questions, family-level
split, fixtures, tool permissions, agent model/version and seed before execution.
Compare the same task with (a) existing reference docs, (b) the short Skill, and
(c) Skill plus offline help retrieval. Do not train or fine-tune on this changing
API. The deterministic retrieval and snippet tests are a separate engineering
acceptance layer; they are not evidence of agent reasoning improvement.

Record one JSONL task record with these fields:

```json
{"protocol":1,"task_id":"heldout-task","model_family":"declared-family",
 "split":"development","package_version":"1.0.0","help_digest":"sha256",
 "agent_model":"not_run","arm":"skill_plus_help","seed":0,
 "status":"NOT_RUN","api_misuses":null,"recovered_errors":null,
 "help_calls":null,"help_returned_bytes":null,"tool_calls":null,
 "correct_verified_claims":null,"claims_requiring_verification":null,
 "unsupported_claims":null,"elapsed_seconds":null,"billed_tokens":null}
```

Attach per-event records for help query/topic IDs, actual returned byte count,
API exception class/topic, recovery attempt, and the final Report scope/status.
Define API misuse against the installed version, count repeat attempts, and score
correct verification with an independent rubric. A correct refusal at a documented
capability boundary is different from failure to find a supported method. Report
all incomplete/time-limited tasks. Never interpret local enumeration witnesses,
solver statuses or historical saved summaries as exact full-model certificates.

No results are imputed for NOT_RUN sessions. Exposed or unisolated case materials
are development data, even if their filenames were hidden. Only a frozen,
family-disjoint, unexposed set can be called held-out.
