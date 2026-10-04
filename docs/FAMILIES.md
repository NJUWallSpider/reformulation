# Structural families as Frames

The default path is template structural. A family is a row of a directory,
not a separate Python class. Both default directories use entity depth 2.

```python
F = m.family("F0")
R = F.rows
neighbors = R.vars
large = m.row_families.where(mf.col("count") > 100)
large.rows
F.related("pattern")
F.related("parameters").where(basis="canonical")
F.related("histogram").where(basis="raw")
F.related("sources").where(basis="scale_normalized")
```

`related("members")` retains the family key and entity reference. `config` records
the rule, and `levels` gives the structural depth overview. Pattern role depths
are explicit: a depth-d row references depth-(d-1) variable roles. Variable
patterns reference oriented row states, which are not public row-family labels.

`count` covers the selected members; `total_count` covers the full-model family.
Subset queries preserve global labels and identities. Structural pattern ranges
are full-model observations; numerical relations aggregate only selected members.
Filters, ordering and slicing retain navigation. Projection must retain
`family_key` (direct renaming works); aggregation clears navigation. A join keeps
left navigation when its linking columns survive. Members are set-valued.

Every numerical record retains its basis and orientation. Parameter and histogram
`quantity` distinguishes value, coefficient, multiplicity and
coefficient_multiplicity. The latter includes zero occurrences in members.
Sources retain original rows, members, factors and scales, enabling exact replay.
No domain value is hidden in an object-valued cell.

## Migration from the previous interface

This is a breaking Python API change. Native v3 snapshots remain readable.

| Previous operation | Current operation |
| --- | --- |
| `m.structure.signatures().row_families(d)` | `m.families(kind="row", depth=d)` |
| `s.var_families(d)` | `m.families(kind="var", depth=d)` |
| `s.family("F0", depth=d)` | `m.family("F0", depth=d)` |
| `m.rows.family("F0@2")` | `m.family("F0@2").rows` |
| `m.vars.family("V0@1")` | `m.family("V0@1").vars` |
| `selected_rows.family(label)` | `selected_rows.families(depth=d).where(id=label).rows` |
| `family.params` | `family.related("parameters").where(basis="canonical")` |
| `family.attributes.view(basis=...)` | Filter `parameters`, `histogram` or `sources` on `basis` |
| `family.inspect()` | `family_frame.inspect()` returns bounded text |
| `row.signature("typed")` | `mf.analysis.signatures(row, method="typed")` |
| `rows.signature(...)` | `mf.analysis.signatures(rows, method=...)` |
| `rows.families()` with the old algebraic default | `mf.analysis.families(rows, method="algebraic")` |
| `m.structure.splits(...)` | `m.refinement(start, stop, kind=...)` |
| `m.structure.summary()` | `m.row_families.inspect(samples=0)` and `related("levels")` |
| `m.structure.indexed(signatures=s)` | `m.indexed(depth=..., method=..., ...)` |

Advanced method names are `algebraic`, `typed`, `template` and
`exact_structural`. Only `mf.analysis` accepts the old spelling aliases
`algebratic` and `exact_strctural`. Row-local methods reject variable input.
There are no public Structure, StructuralSignatures, Signature or Family classes.

Family views and parameter queries do not retain one SQLite connection per view.
They remain live until their model closes. Ordinary repr never computes numeric
profiles; inspect is an explicit drill-down with bounded output. Family membership
and repeated signatures do not establish symmetry, redundancy or a valid cut.
