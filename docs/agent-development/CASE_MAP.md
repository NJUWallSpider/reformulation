# Development corpus mapping

This file is development provenance, not installed help or an agent answer key.
Paths below are relative to an explicitly supplied external `--data-root`.
Narratives and scripts inform the generic patterns; solver logs and historical
statuses do not become mipframe Reports. No historical script is executed by the
help regression harness.

| Material | Transferable steps | Generic help topics | Important scope boundary |
| --- | --- | --- | --- |
| `logic_chain/test9.md`, `playground/test9/` | Declare coupling rows, compare components, sum rows | decomposition, linear | Excluded rows can still couple blocks |
| `logic_chain/test10.md`, `playground/test10/` | Recover coordinates, validate full products, associate raw coefficients | coordinates, roles | Same role need not mean equal coefficients |
| `logic_chain/test11.md`, `playground/test11/` | Establish local coordinates before comparing payloads | templates | Local templates are not full-model symmetry |
| `logic_chain/test12.md`, `playground/test12/` | Containment, exact differences, nonnegative residual certificates | support | Containment without signs/bounds proves no fixing |
| `logic_chain/test13.md`, `playground/test13/` | Recover paths and inspect local discrete claims | network, enumeration | Failed numerical candidate is not a proof of impossibility |
| `logic_chain/test14.md`, `playground/test4/` | On/off reasoning and weighted binary conflict implications | conditions, round, conflicts | Continuous disjunctions require more than pattern recognition |
| `logic_chain/test15.md`, `playground/test15/` | Witnessed conflicts and clique candidates | conflicts, enumeration | LP scores guide search; they do not validate cuts |
| `logic_chain/test16.md`, `playground/test16/` | Small integer structure and local verification | enumeration, round | Local counterexamples need not extend to a model solution |
| `logic_chain/test17.md`, `playground/test17/` | Conflict witnesses and odd-cycle half-sums | conflicts, odd-cycle | Establish every edge and integer domain before rounding |
| `logic_chain/test18.md`, `playground/test18/` | Packing supports and candidate cliques | conflicts, enumeration | Re-parsed modified files do not prove equivalence |
| `logic_chain/test30.md`, `playground/test30/` | Conditional contradictions and objective-related hypotheses | conditions | Keep assumptions; do not silently discharge them |
| `logic_chain/test31.md`, `playground/test31/` | Signed incidence, graph paths, retained-source certificates | network, transitive | A graph reduction alone cannot delete constraints |
| `logic_chain/test32.md`, `playground/test32/` | Compensate residuals, exact rational bounds, integer rounding | linear, round | Bind certificates to the actual transformed snapshot |
| `logic_chain/test33.md`, `playground/test33/` | Precedence closure and resource union aggregation | closure, conflicts | No double counting, and capacity arithmetic alone is not a conflict proof |
| `logic_chain/test34.md`, `playground/test34/` | One-hot/epigraph observations and explicit unsupported steps | epigraph, limitation.disjunction | No general continuous disjunctive-hull checker or elimination |

Cases 19–29 have model files but no supplied analysis narratives/scripts. They are
not labeled as observed agent trajectories. These materials are exposed development
data, not held-out evaluation data.

## Executed scope versus historical claims

`scripts/evaluate_help_cases.py` has a fixed snapshot manifest: raw inputs for
9–12, historical presolved inputs for 16–17 and 30–34. Its result records specify
actual input hashes, model identities when loading succeeds, explicit subsets,
and exclusions. The manifest does not run preprocessing or silently fall back to
a different model when a parser rejects a file.

The default independent recipe fixtures cover every generic recipe, including
boundary, coefficient and domain perturbations. External checks supplement them:
full discovered coordinates for case 10, explicitly selected containment witnesses
for case 12, and declared bounded scopes for several larger analyses. Neither a
small passing scope nor a proof of an original side is advertised as a complete
historical rediscovery or a new bound improvement.
