# Offline agent comparison protocol

This repository currently measures deterministic fact recovery. It does not run
online models, claim statistical AI superiority, or treat a single analyst's
before/after exploration as a blinded A/B trial.

## Freeze before comparing

Freeze the source hashes, development/holdout **model families**, lpir revision,
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
2. Numerical IR only: row/column inspection and traversal, without semantic views.
3. v2 summary plus public query/analyze/validate/probe tools; no direct source reads.
4. Deterministic v2 without an LLM, reporting only facts the built-ins recover.

Optional ablations remove variable-length sums, objective profiles, relation
operators, or graph/decomposition proposals. All arms see the same mathematical
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

`scripts/evaluate_structure.py` rebuilds the local models under per-instance
budgets and verifies the published views. `scripts/evaluate_public.py` checks
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
