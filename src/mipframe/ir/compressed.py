"""Factual, traceable model maps over exact numerical snapshots.

Exact numeric histograms spill to SQLite. Graph work is linear in vertices and
sparse entries, never in the matrix area. The machine schema is not a rendering.
"""

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from decimal import localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

from mipframe.frame.entity import _sense
from mipframe.frame.entity import _text
from mipframe.ops._kernels.union_find import DSU
from mipframe.options import options
from mipframe.frame.row import Formula
from mipframe.ir.display import canonical_pattern_index
from mipframe.ir.typed import bound_class
from mipframe.store.numbers import compare_numeric_text

SCHEMA_VERSION = "0.3"
NEAR_ZERO = Fraction(1, 10**9)


def _fraction(n, total):
    return Fraction(n, total) if total else Fraction(0)


def _profile(pairs):
    """Exact weighted linear quantiles; pairs are sorted (value, frequency)."""
    pairs = list(pairs)
    n = sum(count for _, count in pairs)
    if not n:
        return dict(
            kind="empty", count=0, min=None, median=None, p90=None, max=None, mode=None
        )

    def at(rank):
        seen = 0
        for value, count in pairs:
            seen += count
            if seen > rank:
                return value

    def quantile(q):
        position = (n - 1) * q
        lo = position.numerator // position.denominator
        a = at(lo)
        return a + (position - lo) * (at(min(lo + 1, n - 1)) - a)

    result = dict(
        kind="constant" if len(pairs) == 1 else "varying",
        count=n,
        min=pairs[0][0],
        median=quantile(Fraction(1, 2)),
        p90=quantile(Fraction(9, 10)),
        max=pairs[-1][0],
        mode=max(pairs, key=lambda pair: pair[1])[0],
    )
    if len(pairs) == 1:
        result["value"] = pairs[0][0]
    return result


class _NumericProfiles:
    """Exact external histograms, including high-cardinality coefficients."""

    def __init__(self):
        self.db = sqlite3.connect("")
        self.db.execute("PRAGMA cache_size=-1024")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.create_collation(
            "rational",
            compare_numeric_text,
        )
        self.db.execute(
            # Values are canonical Fraction strings: byte equality is already
            # exact equality. Numeric ordering is needed only for quantiles.
            "CREATE TABLE hist(kind TEXT,value TEXT,n INTEGER,PRIMARY KEY(kind,value))"
        )

    def add(self, kind, value, count=1):
        self.db.execute(
            "INSERT INTO hist VALUES(?,?,?) ON CONFLICT(kind,value) DO UPDATE SET n=n+excluded.n",
            (kind, str(value), count),
        )

    def profile(self, kind):
        n, distinct = self.db.execute(
            "SELECT coalesce(sum(n),0),count(*) FROM hist WHERE kind=?", (kind,)
        ).fetchone()
        if not n:
            return _profile([])
        ranks = {0, n - 1}
        positions = [(n - 1) * q for q in (Fraction(1, 2), Fraction(9, 10))]
        for p in positions:
            lo = p.numerator // p.denominator
            ranks.update((lo, min(lo + 1, n - 1)))
        found, seen, mode, frequency = {}, 0, None, -1
        for text, count in self.db.execute(
            "SELECT value,n FROM hist WHERE kind=? ORDER BY value COLLATE rational", (kind,)
        ):
            value = Fraction(text)
            for rank in ranks:
                if seen <= rank < seen + count:
                    found[rank] = value
            if count > frequency:
                mode, frequency = value, count
            seen += count
        quantiles = []
        for p in positions:
            lo = p.numerator // p.denominator
            quantiles.append(
                found[lo] + (p - lo) * (found[min(lo + 1, n - 1)] - found[lo])
            )
        result = dict(
            kind="constant" if distinct == 1 else "varying",
            count=n,
            min=found[0],
            median=quantiles[0],
            p90=quantiles[1],
            max=found[n - 1],
            mode=mode,
        )
        if distinct == 1:
            result["value"] = found[0]
        return result


def _degree_profile(ptr, members):
    counts = Counter(int(ptr[i + 1]) - int(ptr[i]) for i in members)
    return _profile(sorted(counts.items()))


def _tokens(signature):
    if not signature.depth:
        return [], []

    def pattern_record(pattern):
        result = []
        for coefficient, color, count in pattern:
            multiplicity = (
                dict(kind="constant", value=count.min)
                if count.min == count.max
                else dict(
                    kind="varying", min=count.min, max=count.max, mode_id=count.mode
                )
            )
            result.append(
                dict(
                    sign="+" if coefficient > 0 else "-",
                    var_role=f"V{color}",
                    var_role_depth=signature.depth - 1,
                    multiplicity=multiplicity,
                )
            )
        return result

    orientations = [pattern_record(p) for p in signature.patterns]
    return orientations[canonical_pattern_index(signature)], orientations


def _signature_id(signature):
    # Model/config scoped identity, stable across save/load. Colors alone are
    # intentionally not advertised as globally comparable structural hashes.
    digest = hashlib.sha256(signature.key().encode()).hexdigest()
    return f"{'rf' if signature.kind == 'row' else 'vr'}:{signature.depth}:{digest}"


def _varying_roles(family, primary):
    """Observe variation without building per-member coefficient histograms."""
    fs = family._store
    store, m, config = fs.result._store, fs.owner.model, fs.result._config

    def histogram():
        params = family._parameters
        return [params._key(r) for r in params.profile.roles if r.coefficients.varying]

    if (family.signature.mode != 'template' or not fs.depth
            or len(primary) > min(32, store.memory // 1024)):
        return histogram()
    pos, neg = (store.array(config, kind, fs.depth) for kind in ('pos', 'neg'))
    # Numerically tied dual orientations use the histogram's exact tie-break.
    for row in family.rows.ids:
        if neg[row] >= 0 and pos[row] == neg[row]:
            return histogram()
    colors = store.array(config, 'var', fs.depth - 1)
    edge = store.array(config, 'edge', -1)
    orientation = canonical_pattern_index(family.signature)
    roles = {(1 if r['sign'] == '+' else -1, int(r['var_role'][1:])): None
             for r in primary}
    varying = set()
    for row in family.rows.ids:
        reverse = -1 if neg[row] >= 0 and pos[row] > neg[row] else 1
        if orientation:
            reverse = -reverse
        for start in range(int(m.row_ptr[row]), int(m.row_ptr[row + 1]), store.block):
            stop = min(start + store.block, int(m.row_ptr[row + 1]))
            neighbors = colors[m.col_idx[start:stop]]
            signs = edge[start:stop] * reverse
            for role in roles.keys() - varying:
                sign, neighbor = role
                mask = (neighbors == neighbor) & ((signs > 0) if sign > 0 else (signs < 0))
                for ident in np.unique(m.coeff_id[start:stop][mask]):
                    value = abs(m.num(int(ident)))
                    if roles[role] is None:
                        roles[role] = value
                    elif roles[role] != value:
                        varying.add(role)
                        break
        if len(varying) == len(roles):
            break
    return [f"{'+' if sign > 0 else '-'}V{neighbor}" for sign, neighbor in sorted(varying)]


class CompressedIR(Mapping):
    """
    Eager schema 0.3 summary with model-local IDs and bounded display.

            ``to_dict()`` returns an independent exact snapshot. ``to_json()`` represents
            Fractions as rational strings. Only these detached exports outlive the model.

    MIPFRAME HELP BEGIN
    CompressedIR [api.CompressedIR]

    Bounded factual model map prepared by read/load.

    CompressedIR [type]

    When to use: Bounded factual model map prepared by read/load.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: home
    MIPFRAME HELP END
    """

    def __init__(self, owner, *, depth=2, memory_mb=128):
        if type(depth) is not int or depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        self._owner = owner
        self._result = owner.structure.signatures(
            depth=depth, mode="template", memory_mb=memory_mb
        )
        self._depth = depth
        self._family_frames = {}
        self._family_tables = {}
        self._data = self._build()

    def _guard(self):
        self._owner.model

    def _release(self):
        """Release the eager map's memory on owner close; exports are detached."""
        self._data.clear()
        self._family_frames.clear()
        self._result = None

    def __getitem__(self, key):
        self._guard()
        if key == "indexed":
            from mipframe.ir.indexing.views import ir_summary

            return ir_summary(self._owner)
        return deepcopy(self._data[key])

    def __iter__(self):
        self._guard()
        for key in tuple(self._data):
            self._guard()
            yield key

    def __len__(self):
        self._guard()
        return len(self._data)

    def __getattr__(self, key):
        if key in {
            "schema_version",
            "model",
            "size",
            "matrix",
            "domains",
            "numerics",
            "objective",
            "topology",
            "structure",
            "attention",
            "indexed",
        }:
            return self[key]
        raise AttributeError(key)

    def to_dict(self):
        """
        MIPFRAME HELP BEGIN
        CompressedIR.to_dict [api.CompressedIR.to_dict]

        Export the complete factual IR summary with exact numbers.

        CompressedIR.to_dict()

        When to use: Export the complete factual IR summary with exact numbers.

        Returns: Independent dict; rational values remain Fractions.

        Establishes: The exported data outlives the model and is not truncated by display limits.

        Does not establish: The summary does not embed the full numerical model, member lists or parameter histograms. Structural groups do not prove mathematical equivalence.

        IR schema 0.3 retains IDs, depths, scopes and configurations. Access detailed objects through m.rows, m.vars, m.matrix and m.structure. For a family use m.structure.signatures().family(id, depth=depth); for indexed tensors and operators use m.structure.indexed().tensor(id) or .operator(id).

        See also: api.Model.ir, api.StructuralSignatures.family, api.Structure.indexed
        MIPFRAME HELP END
        """
        self._guard()
        return {**deepcopy(self._data), "indexed": self["indexed"]}

    def to_json(self, *, indent=2):
        """
        MIPFRAME HELP BEGIN
        CompressedIR.to_json [api.CompressedIR.to_json]

        Export the complete factual IR summary as JSON.

        CompressedIR.to_json(*, indent=2)

        When to use: Export the complete factual IR summary as JSON.

        Returns: JSON string; rational values are encoded as strings.

        Establishes: The exported data outlives the model and is not truncated by display limits.

        Does not establish: The summary does not embed the full numerical model, member lists or parameter histograms. Structural groups do not prove mathematical equivalence.

        IR schema 0.3 retains IDs, depths, scopes and configurations. Access detailed objects through m.rows, m.vars, m.matrix and m.structure. For a family use m.structure.signatures().family(id, depth=depth); for indexed tensors and operators use m.structure.indexed().tensor(id) or .operator(id).

        See also: api.Model.ir, api.StructuralSignatures.family, api.Structure.indexed
        MIPFRAME HELP END
        """
        self._guard()
        return json.dumps(
            self.to_dict(),
            default=lambda x: str(x) if isinstance(x, Fraction) else x,
            ensure_ascii=False,
            indent=indent,
            allow_nan=False,
        )

    def _build(self):
        m, result, depth = self._owner.model, self._result, self._depth
        nr, nv = self._owner.shape
        nz = self._owner.nnz
        role_depth = max(0, depth - 1)
        colors = result._store.array(result._config, "var", role_depth)
        types = Counter({"B": 0, "I": 0, "C": 0})
        senses = Counter({"<=": 0, "=": 0, ">=": 0})
        bounds, support = Counter(), Counter()
        positive = negative = near_zero = 0
        numeric = _NumericProfiles()
        try:
            for row in m.rows:
                lo, hi = m.num(row[2]), m.num(row[3])
                sense = _sense(lo, hi)
                senses[sense] += 1
                # Equality once, ranges both endpoints, free rows no endpoints.
                values = (
                    (lo, hi) if sense == "range" else (lo if sense == ">=" else hi,)
                )
                for value in values:
                    if value is not None and value != 0:
                        numeric.add("rhs", value)
            for v in m.variables:
                types[m.variable_type(v)] += 1
                bounds[bound_class(m.num(v[3]), m.num(v[4]))] += 1
                cost = m.num(v[5])
                if cost:
                    numeric.add("objective", cost)
                    support[int(colors[v[0]])] += 1
                    positive += cost > 0
                    negative += cost < 0
            # Number IDs are interned; aggregate occurrences externally before
            # decoding, without keeping a Python object for every nonzero.
            numeric.db.execute(
                "CREATE TABLE coefficient_ids(id INTEGER PRIMARY KEY,n INTEGER)"
            )
            numeric.db.executemany(
                "INSERT INTO coefficient_ids VALUES(?,1) ON CONFLICT(id) DO UPDATE SET n=n+1",
                ((int(i),) for i in m.coeff_id),
            )
            for ident, count in numeric.db.execute("SELECT id,n FROM coefficient_ids"):
                value = abs(m.num(ident))
                numeric.add("matrix", value, count)
                near_zero += count if 0 < value <= NEAR_ZERO else 0
            coefficients, rhs, costs = (
                numeric.profile(kind) for kind in ("matrix", "rhs", "objective")
            )
        finally:
            numeric.db.close()
        objective_nnz = positive + negative
        objective = dict(
            sense=m.manifest["sense"],
            constant=m.num(m.manifest["objective_constant"]),
            nonzeros=objective_nnz,
            variables=nv,
            sign_counts={
                "positive": positive,
                "negative": negative,
                "zero": nv - objective_nnz,
            },
            kind="constant"
            if not objective_nnz
            else "uniform"
            if costs["kind"] == "constant"
            else "varying",
            coefficient_profile=costs,
            role_depth=role_depth,
            role_support=[
                dict(
                    id=f"V{color}",
                    depth=role_depth,
                    count=count,
                    fraction=_fraction(count, objective_nnz),
                )
                for color, count in sorted(
                    support.items(), key=lambda item: (-item[1], item[0])
                )
            ],
        )
        topology = self._topology()
        overview = []
        for d in range(depth + 1):
            row_roles, var_roles = (
                len(self._family_frame("row", d)),
                len(self._family_frame("var", d)),
            )
            overview.append(
                dict(
                    depth=d,
                    row_roles=row_roles,
                    var_roles=var_roles,
                    delta_rows=None if not d else row_roles - overview[-1]["row_roles"],
                    delta_vars=None if not d else var_roles - overview[-1]["var_roles"],
                )
            )
        variables = self._families("var", role_depth)
        families = self._families("row", depth)
        refinements = [
            self._refinement(kind, d)
            for d in range(1, depth + 1)
            for kind in ("row", "var")
        ]
        attention = []
        if near_zero:
            attention.append(
                dict(
                    kind="fact",
                    code="near_zero_coefficients",
                    count=near_zero,
                )
            )
        for kind in ("rows", "variables"):
            if topology[f"isolated_{kind}"]:
                attention.append(
                    dict(
                        kind="fact",
                        code=f"isolated_{kind}",
                        count=topology[f"isolated_{kind}"],
                    )
                )
        for delta in refinements:
            if delta["split_count"]:
                attention.append(
                    dict(
                        kind="structural_abstraction",
                        code="refinement_splits",
                        axis=delta["kind"],
                        from_depth=delta["from_depth"],
                        to_depth=delta["to_depth"],
                        count=delta["split_count"],
                    )
                )
        return dict(
            schema_version=SCHEMA_VERSION,
            indexed={"status": "not_run", "runs": []},
            model=dict(
                name=Path(m.manifest.get("source") or m.path).stem,
                model_version=m.manifest["model_version"],
                sense=m.manifest["sense"],
            ),
            size=dict(variables=nv, rows=nr, nonzeros=nz),
            matrix=dict(
                density=_fraction(nz, nr * nv),
                row_nnz=_degree_profile(m.row_ptr, range(nr)),
                column_nnz=_degree_profile(m.col_ptr, range(nv)),
            ),
            domains=dict(
                variable_types=dict(types),
                row_senses=dict(senses),
                bounds=dict(bounds),
            ),
            numerics=dict(
                absolute_coefficients=coefficients,
                nonzero_rhs=rhs,
                bounds=dict(bounds),
                rhs_convention="raw finite nonzero row bounds; equality once, ranged rows both endpoints",
                near_zero_coefficients=dict(
                    count=near_zero,
                    threshold=NEAR_ZERO,
                    comparison="0 < abs(coef) <= threshold",
                ),
                coefficient_dynamic_range=coefficients["max"] / coefficients["min"]
                if nz
                else None,
            ),
            objective=objective,
            topology=topology,
            structure=dict(
                mode="template",
                depth=depth,
                variable_role_depth=role_depth,
                config=result.config,
                overview=overview,
                variable_roles=variables,
                row_families=families,
                refinements=refinements,
            ),
            attention=attention,
        )

    def _family_frame(self, kind, depth):
        self._guard()
        key = kind, depth
        if key not in self._family_frames:
            method = (
                self._result._row_families
                if kind == "row"
                else self._result._var_families
            )
            self._family_frames[key] = method(depth)
        return self._family_frames[key]

    def _family_directory(self, kind, depth):
        """Reuse the prepared family table for the Model's typed accessors."""
        self._guard()
        from mipframe.frame.adapters import family_directory

        key = kind, depth
        if key not in self._family_tables:
            self._family_tables[key] = family_directory(self._family_frame(kind, depth))
        return self._family_tables[key]

    def _families(self, kind, depth):
        frame = self._family_frame(kind, depth)
        m = self._owner.model
        total = len(m.rows) if kind == "row" else len(m.variables)
        summaries = []
        # Positions are disk-backed coverage order for both axes.
        for position in range(len(frame)):
            f = frame.loc[f"F{position}"]
            sig = f.signature
            members = f.rows if kind == "row" else f.vars
            record = dict(
                id=f.id,
                signature_id=_signature_id(sig),
                count=f.count,
                fraction=_fraction(f.count, total),
                depth=depth,
                parent=f.parent,
                parent_depth=f.parent_depth,
            )
            degree = _degree_profile(
                m.row_ptr if kind == "row" else m.col_ptr, members.ids
            )
            if kind == "var":
                typ, bounds, objective = sig.base
                record.update(
                    base=dict(type=typ, bounds=bounds, objective=objective),
                    degree=degree,
                )
            else:
                primary, orientations = _tokens(sig)
                # Exact parameter histograms remain query objects, not IR payload.
                varying = _varying_roles(f, primary)
                f.attributes._close()
                record.update(
                    relation=sig.base[0],
                    rhs_class=sig.base[1],
                    nnz=degree,
                    structure=primary,
                    structure_available=bool(depth),
                    orientations=orientations,
                    sign_symmetric=f.sign_symmetric,
                    parameters=dict(
                        varying_coefficient_roles=varying,
                    ),
                )
            summaries.append(record)
        return summaries

    def _refinement(self, kind, depth):
        delta = self._result.splits(depth=depth, kind=kind)
        splits = []
        for split in delta:
            children = []
            for child in split["children"]:
                entry = dict(
                    id=child["id"],
                    depth=depth,
                    count=child["count"],
                )
                if kind == "row":
                    entry["structure"] = _tokens(
                        delta.after.loc[child["id"]].signature
                    )[0]
                children.append(entry)
            splits.append(
                dict(
                    parent=split["parent"],
                    parent_depth=depth - 1,
                    count=split["count"],
                    children=children,
                )
            )
        return dict(
            kind=kind,
            from_depth=depth - 1,
            to_depth=depth,
            before_count=delta.before_count,
            after_count=delta.after_count,
            split_count=len(delta),
            splits=splits,
            unchanged=delta.unchanged,
        )

    def _topology(self):
        m = self._owner.model
        nr, nv = self._owner.shape
        dsu = DSU(nr + nv)
        for r in range(nr):
            for p in range(int(m.row_ptr[r]), int(m.row_ptr[r + 1])):
                dsu.union(r, nr + int(m.col_idx[p]))
        counts = {}
        for i in range(nr + nv):
            root = dsu.find(i)
            stats = counts.setdefault(root, [0, 0, 0, i])
            if i < nr:
                stats[0] += 1
                stats[2] += int(m.row_ptr[i + 1]) - int(m.row_ptr[i])
            else:
                stats[1] += 1
        ordered = sorted(
            counts.items(),
            key=lambda item: (-item[1][2], -sum(item[1][:2]), item[1][3]),
        )
        components = [
            dict(
                id=f"C{i}",
                rows=a,
                variables=b,
                nonzeros=z,
                row_fraction=_fraction(a, nr),
                var_fraction=_fraction(b, nv),
                nonzero_fraction=_fraction(z, self._owner.nnz),
            )
            for i, (_, (a, b, z, _)) in enumerate(ordered)
        ]
        return dict(
            connected_components=len(components),
            isolated_rows=sum(
                int(m.row_ptr[i]) == int(m.row_ptr[i + 1]) for i in range(nr)
            ),
            isolated_variables=sum(
                int(m.col_ptr[i]) == int(m.col_ptr[i + 1]) for i in range(nv)
            ),
            largest_component=components[0] if components else None,
            components=components,
            convention="bipartite constraint-variable graph; isolated vertices count as components; ranked by nonzeros, then vertices",
        )

    def show(self):
        """
        MIPFRAME HELP BEGIN
        CompressedIR.show [api.CompressedIR.show]

        Render a bounded factual model overview.

        CompressedIR.show()

        When to use: Render a bounded factual model overview.

        Returns: Notebook-friendly Formula text; does not print.

        Establishes: Rendering uses completed summaries and does not run discovery.

        Does not establish: The summary does not embed the full numerical model, member lists or parameter histograms. Structural groups do not prove mathematical equivalence.

        IR schema 0.3 retains IDs, depths, scopes and configurations. Access detailed objects through m.rows, m.vars, m.matrix and m.structure. For a family use m.structure.signatures().family(id, depth=depth); for indexed tensors and operators use m.structure.indexed().tensor(id) or .operator(id).

        See also: api.Model.ir, api.StructuralSignatures.family, api.Structure.indexed
        MIPFRAME HELP END
        """
        self._guard()
        text = _render(self._data)
        from mipframe.ir.indexing.views import ir_summary

        indexed = ir_summary(self._owner, limit=5)
        text += "\n\nIndexed: " + indexed["status"]
        for run in indexed["runs"][:5]:
            if indexed["total_runs"] > 1:
                scope = run["scope"]
                label = (
                    "Full model"
                    if scope["full_model"]
                    else f"Selection: {scope['rows']:,} rows × {scope['variables']:,} variables"
                )
                config = run["signatures"]
                text += (
                    f"\n  {label} · {config['config']['mode']} depth {config['depth']}"
                )
            nt, no = run["total_tensors"], run["total_operators"]
            text += f"\n  {nt} coordinate view{'s' if nt != 1 else ''} · {no} exact operator{'s' if no != 1 else ''}"
            for t in run["tensors"][:5]:
                text += f"\n    {t['shape'][0]:,} × {t['shape'][1]:,} · {t['variables']:,} variables · missing {t['missing_cells']:,}"
            if run["total_tensors"] > 5:
                text += "\n    ..."
        if indexed["total_runs"] > 5:
            text += "\n  ..."
        return Formula(text)

    def __repr__(self):
        return str(self.show())

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


def _number(value):
    if value is None:
        return "—"
    text = _text(value)
    if len(text) > 32:
        return _approx(value)
    return text


def _approx(value):
    """Bounded scientific notation without float overflow or underflow."""
    value = Fraction(value)
    with localcontext() as context:
        context.prec = 3
        number = Decimal(value.numerator) / Decimal(value.denominator)
        return format(number, ".2e").replace("E", "e")


def _degree(value):
    if value["kind"] == "empty":
        return "empty"
    if value["kind"] == "constant":
        return _number(value["value"])
    return f"{_number(value['min'])}..{_number(value['max'])} (mode {_number(value['mode'])})"


def _pattern(tokens):
    parts = []
    for token in tokens[:12]:
        multiplicity = token["multiplicity"]
        suffix = (
            f"×{multiplicity['value']}"
            if multiplicity["kind"] == "constant"
            else f"×[{multiplicity['min']}..{multiplicity['max']}]"
        )
        parts.append(f"{token['sign']}{token['var_role']}{suffix}")
    if len(tokens) > 12:
        parts.append(f"... ({len(tokens) - 12} signed groups omitted)")
    return " | ".join(parts) or "(no terms)"


def _percent(value):
    return f"{float(value) * 100:.1f}%"


def _render(data):
    size, matrix, domains, numeric, objective, topology, structure = (
        data[k]
        for k in (
            "size",
            "matrix",
            "domains",
            "numerics",
            "objective",
            "topology",
            "structure",
        )
    )
    lines = [
        "MIPFrame compressed IR",
        _text(data["model"]["name"]),
        "",
        "Model",
        f"  {size['variables']:,} variables · {size['rows']:,} rows · {size['nonzeros']:,} nonzeros",
        f"  sense: {objective['sense']} · density: {_approx(matrix['density']) if matrix['density'] else '0'}",
        "  variables: "
        + " · ".join(f"{n:,} {k}" for k, n in domains["variable_types"].items()),
        "  rows: " + " · ".join(f"{n:,} {k}" for k, n in domains["row_senses"].items()),
    ]
    for label, key in (("row nnz", "row_nnz"), ("col nnz", "column_nnz")):
        p = matrix[key]
        lines.append(
            f"  {label}: min {_number(p['min'])} · median {_number(p['median'])} · p90 {_number(p['p90'])} · max {_number(p['max'])}"
        )
    a, rhs = numeric["absolute_coefficients"], numeric["nonzero_rhs"]
    lines += [
        "",
        "Numerics",
        f"  |A|: min {_number(a['min'])} · median {_number(a['median'])} · max {_number(a['max'])}",
        f"  nonzero rhs: min {_number(rhs['min'])} · max {_number(rhs['max'])}",
        "  bounds: "
        + (" · ".join(f"{n:,} {k}" for k, n in numeric["bounds"].items()) or "empty"),
        f"  coefficient dynamic range: {_number(numeric['coefficient_dynamic_range'])}",
        f"  near-zero coefficients (0 < |A| <= 1e-9): {numeric['near_zero_coefficients']['count']:,}",
        "",
        "Objective",
        f"  sense: {objective['sense']} · constant: {_number(objective['constant'])}",
        f"  nonzeros: {objective['nonzeros']:,} / {size['variables']:,}",
        f"  coefficient signs: +{objective['sign_counts']['positive']:,} / -{objective['sign_counts']['negative']:,}",
    ]
    profile = objective["coefficient_profile"]
    text = (
        f"constant({_number(profile['value'])})"
        if profile["kind"] == "constant"
        else _degree(profile)
    )
    lines += [f"  coefficients: {text}", "  support:"]
    limit = options.ir.max_var_roles
    for role in objective["role_support"][:limit]:
        lines.append(
            f"    {role['id']}  {role['count']:,} variables ({_percent(role['fraction'])})"
        )
    if len(objective["role_support"]) > limit:
        covered = sum(r["count"] for r in objective["role_support"][:limit])
        lines.append(
            f"    ... {len(objective['role_support']) - limit:,} roles omitted; shown cover {_percent(_fraction(covered, objective['nonzeros']))} of objective support → m.vars.where(mf.col('obj') != 0)"
        )
    lines += [
        "",
        "Topology",
        f"  connected components: {topology['connected_components']:,}",
        f"  isolated vars/rows: {topology['isolated_variables']:,} / {topology['isolated_rows']:,}",
    ]
    largest = topology["largest_component"]
    if largest:
        lines.append(
            f"  largest ({largest['id']}): {_percent(largest['row_fraction'])} rows / {_percent(largest['var_fraction'])} vars / {_percent(largest['nonzero_fraction'])} nonzeros"
        )
        if topology["connected_components"] > 1:
            lines.append(
                "  next (nonzeros): "
                + ", ".join(
                    _percent(c["nonzero_fraction"]) for c in topology["components"][1:4]
                )
                + (" ..." if topology["connected_components"] > 4 else "")
            )
    lines += [
        "",
        f"Structure (template, depth={structure['depth']})",
        "  Depth   Row roles   Var roles",
    ]
    for level in structure["overview"]:
        delta = lambda key: "" if level[key] is None else f" ({level[key]:+d})"
        lines.append(
            f"  {level['depth']:<7} {level['row_roles']}{delta('delta_rows')}       {level['var_roles']}{delta('delta_vars')}"
        )
    for kind, key, limit, depth, total in (
        (
            "Variable roles",
            "variable_roles",
            options.ir.max_var_roles,
            structure["variable_role_depth"],
            size["variables"],
        ),
        (
            "Row families",
            "row_families",
            options.ir.max_row_families,
            structure["depth"],
            size["rows"],
        ),
    ):
        records = structure[key]
        lines += [
            "",
            f"{kind} @ depth {depth}",
            f"  showing {min(limit, len(records)):,} of {len(records):,}, sorted by coverage",
            "  id  count  parent  type  bounds  obj  degree"
            if key == "variable_roles"
            else "  id  count  parent  relation  rhs  nnz  structure",
        ]
        for r in records[:limit]:
            prefix = f"  {r['id']}  {r['count']:,}  {r['parent'] or '—'}"
            if key == "variable_roles":
                base = r["base"]
                lines.append(
                    f"{prefix}  {base['type']}  {base['bounds']}  {base['objective']}  {_degree(r['degree'])}"
                )
            else:
                pattern = (
                    _pattern(r["structure"])
                    if r["structure_available"]
                    else "(base role only)"
                )
                lines.append(
                    f"{prefix}  {r['relation']}  {_text(r['rhs_class'])}  {_degree(r['nnz'])}  {pattern}"
                )
        covered = sum(r["count"] for r in records[:limit])
        lines.append(
            f"  shown families cover {_percent(_fraction(covered, total))}; singleton families: {sum(r['count'] == 1 for r in records):,}"
        )
        if len(records) > limit:
            method = "var_families" if key == "variable_roles" else "row_families"
            default_depth = 1 if key == "variable_roles" else 2
            target = (
                f"m.{method}"
                if depth == default_depth
                else f"m.structure.signatures(depth={depth}).{method}()"
            )
            lines.append(f"  {len(records) - limit:,} families omitted → {target}")
    # Latest row delta; variable delta explains the actual displayed legend.
    for delta in structure["refinements"]:
        wanted = (
            structure["depth"]
            if delta["kind"] == "row"
            else structure["variable_role_depth"]
        )
        if delta["to_depth"] != wanted:
            continue
        lines += [
            "",
            f"{'Row' if delta['kind'] == 'row' else 'Variable'} refinement {delta['from_depth']} → {delta['to_depth']}",
            f"  {delta['before_count']} → {delta['after_count']} families · {delta['split_count']} splits",
        ]
        target = (
            f"m.structure.splits(kind={delta['kind']!r}, "
            f"from_depth={delta['from_depth']}, depth={delta['to_depth']})"
        )
        for split in delta["splits"][: options.ir.max_splits]:
            lines.append(f"  {split['parent']} ({split['count']:,})")
            for child in split["children"][:12]:
                detail = (
                    "  " + _pattern(child["structure"]) if "structure" in child else ""
                )
                lines.append(f"    → {child['id']}  {child['count']:,}{detail}")
            if len(split["children"]) > 12:
                lines.append(
                    f"    ... {len(split['children']) - 12:,} children omitted → {target}"
                )
        if delta["split_count"] > options.ir.max_splits:
            lines.append(
                f"  ... {delta['split_count'] - options.ir.max_splits:,} splits omitted → {target}"
            )
        lines.append(
            f"  {delta['unchanged']['families']} families unchanged ({delta['unchanged']['members']:,} members)"
        )
    if data["attention"]:
        lines += ["", "Attention (facts / structural abstractions)"]
        for item in data["attention"][: options.ir.max_attention]:
            lines.append(f"  {item['code']}: {item['count']:,}")
        if len(data["attention"]) > options.ir.max_attention:
            lines.append(
                f"  ... {len(data['attention']) - options.ir.max_attention} items omitted → m.ir.attention"
            )
    lines.append(
        "\nInspect details through m.rows, m.vars, m.matrix and m.structure. Signed groups are multisets, not formulas."
    )
    return "\n".join(lines)
