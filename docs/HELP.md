# Offline help and agent onboarding

The installed package is the authority for syntax. `mf.help` reads bundled English
contracts and examples without opening a model, running IR, or calling a service.
Python 3.11 or newer is required. Confirm the actual environment first:

```python
import sys
import mipframe as mf
print(sys.executable, mf.__version__)
mf.help()
mf.help("recover coordinates from row families")
mf.help("recipe.coordinates", detail="full")
mf.help(mf.Frame.join)
```

Text is a notebook-friendly `Formula` with no printing side effect. Use
`format="json"` for detached structured results. Caught exceptions are supported:

```python
try:
    result = left.join(right, on="var")
except Exception as error:
    guidance = mf.help(error)
    # Inspect the diagnostic and contract before choosing the correct cardinality.
```

Unknown uniqueness is different from observed duplicate keys. Help never retries a
query, replaces a certificate, or modifies the model.

## Command line

```sh
python -m mipframe help
python -m mipframe help 'support containment'
python -m mipframe help VarFrame.project --detail full
python -m mipframe help 'linear certificate' --format json
python -m mipframe help skill --detail full
```

Exit codes are 0 for results, 1 for no match, and 2 for invalid arguments or help
resource errors. No browser, pager, interactive terminal, repository checkout,
network connection or writable installation directory is required.

## What to read

- [Copyable Skill](agent/SKILL.md): the short workflow and mathematical boundaries.
  It is not automatically installed or added to global agent configuration.
- [API contracts](agent/API.md): explicit registered paths and real signatures.
- [Concepts and limitations](agent/CONCEPTS.md): identity, exactness, scope and errors.
- [Coordinates](agent/recipes/coordinates.md), [containment](agent/recipes/support.md),
  [directed incidence](agent/recipes/network.md), [conflicts](agent/recipes/conflicts.md),
  [odd cycles](agent/recipes/odd-cycle.md), [closure unions](agent/recipes/closure.md),
  [verified edits](agent/recipes/edits.md).

All 16 recipes state their required inputs. Their exact printed code is executed
against independent small fixtures in `tests/help/conftest.py`. These fixtures are
development tests, not implicit model assumptions. Help only contains generic
patterns; no external case narratives, historical scripts or model files ship in
the package.

## Retrieval and trust

Lookup tries topic IDs and public API names, then registered aliases, then a
weighted BM25 inverted index with controlled English normalization and synonyms.
Alias words are also indexed, so longer questions can reuse the same vocabulary.
Ties use stable topic IDs. Natural-language questions favor recipes; explicit
API paths favor contracts. Unknown paths (including `api.Report.close` and
`Report.close`) return no result with bounded spelling
suggestions. Input is never evaluated as Python, SQL, or a module import path.

Task queries such as `batch linear proof`, `claim construction` and `schema`
lead to the relevant API contract. `tighten big M`, `resource cover`,
`report resource lifecycle` and `conditional case split` explain the supported
workflow and its limits. `set cover` and `graph matching` explicitly distinguish
available verification primitives from optimization algorithms not provided by
the package. `fractional point feasibility` explains exact activity/bound checks
and why `check.solution` is an integer-model checker.

General batch-proof queries lead to `Checker.batch`, whose builder accepts scalar
Claims, sources and per-target assumptions. `Frame native batch certificates`
leads to `Checker.linear_many` for inputs already represented as three Frames.

JSON includes package/documentation versions, query/match mode, signatures,
preconditions, guarantees, non-guarantees, examples, related topics and a content
completeness flag. Brief results preserve their important restrictions; full
results include the complete topic. Output limits count topics, not arbitrary
characters in the middle of examples.

Static lookup of registered live objects remains available after their model is
closed. It does not evaluate properties, `repr`, rows, coefficients or a query
executor. Help resolves only registered bindings to inspect signatures. Source
signature drift produces a warning; a release containing stale resources is
rejected at build time.

## Maintaining one content source

Edit `_help/content.py`, `_help/recipes.py` and `_help/skill.md`. Add a binding to
the explicit registry when adding a public API. Do not infer the API surface from
all non-private names. Update the frozen intent evaluation when deliberately
changing a user-facing capability, and review the mathematical limitations.

```sh
python scripts/build_help.py
python scripts/build_help.py --check
python -m pytest -q tests/help
python -m build
```

The compiler generates `catalog.json`, the agent reference pages and marked
`MIPFRAME HELP BEGIN/END` docstring blocks. It preserves prose outside those
blocks. Runtime error hints only store a topic ID; they do not load the index.
Build hooks validate version, source/content fingerprints and the catalog digest
without importing NumPy. `importlib.resources` reads the bundled catalog and
Skill. The first query builds no files; subsequent queries reuse process memory.

Add runnable recipe fixtures and failure perturbations alongside any example.
Never fix a drift warning by copying an old signature back into the registry.
Signatures, including injected resource parameters, come from the live callable.

## Deterministic acceptance and external data

The help tests cover exact API resolution, CLI parity, version drift, hostile or
unknown query text, retrieval Recall@3, high-risk conclusion boundaries, error
recovery and all recipe snippets. Distribution isolation is reproducible with
`scripts/verify_help_distribution.py`; it builds a wheel, installs outside this
checkout, makes that installation read-only, disables network access, and queries
Python, CLI and the Skill without loading models.

The agent-trace regression set additionally requires the first result to match
each of 11 task queries and 11 non-alias paraphrases. It checks that brief output
retains capability boundaries and unknown API paths do not return unrelated
contracts. This is a fixed offline retrieval regression, not a new online agent
evaluation; see [retrieval notes](agent-development/help-retrieval.md).

External data is explicitly opt-in:

```sh
python scripts/evaluate_help_cases.py --data-root /path/to/reform_test \
  --cases 9 10 12 --output /tmp/help-cases.json
```

The manifest in that script fixes the snapshot path and checked scope for each
case. There is no silent substitution between raw and presolved files. Records
include the actual input hash, snapshot identity, scope and limits. Missing files
are NOT_RUN, runtime/check failures are FAIL, and neither counts as PASS. A PASS
only covers the listed scope, not every historical narrative or solver result.
The harness never imports historical scripts or runs optimization/model rewrites.

Development provenance: cases 9–18 and 30–34 supplied analysis narratives and code;
19–29 had model files only. The generic fixtures cover the transferable primitives.
The external harness uses selected checks for 9–12, 16–17 and 30–34; its bounded
checks explicitly do not claim complete historical analysis. In particular the
full coordinate check covers 190,000 variables and the 950-by-200 product, whereas
template, conditional and disjunctive examples retain their local scope.

No online agent evaluation runs here. See [AI_EVALUATION.md](AI_EVALUATION.md) for
reproducible arms, metrics and the model-family holdout rule.
