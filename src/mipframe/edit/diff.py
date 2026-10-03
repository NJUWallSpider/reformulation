"""Exact direct-parent differences and freshly rechecked edit semantics."""

from fractions import Fraction
from copy import deepcopy
from mipframe.frame.schema import DType, ref_type, SchemaError
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.edit.plan import num


class ModelDiff:
    """
    MIPFRAME HELP BEGIN
    ModelDiff [api.ModelDiff]

    Parent-child comparison with actual identity maps and verified semantic directions.

    ModelDiff [type]

    When to use: Parent-child comparison with actual identity maps and verified semantic directions.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: recipe.edits
    MIPFRAME HELP END
    """
    def __init__(self, target, source, *, memory_mb=128, disk_mb=None):
        self._target = target
        self._source = source
        self._s = AlgorithmStore(
            (source, target), memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None
        )
        self._edit = None
        try:
            if (source.uid, source.version) == (target.uid, target.version):
                self._edit = source.edit(memory_mb=memory_mb, disk_mb=disk_mb)
            else:
                self._edit = target.lineage._restore(
                    source, memory_mb=memory_mb, disk_mb=disk_mb
                )
            p = self._edit._prepare()
            p.require(True)
            self._verify_target(p)
            self._semantic = deepcopy(p.semantic)
            self._frames = {}
            for axis, t in (("row", p.rows), ("var", p.vars)):
                typ = ref_type(target, axis)
                oldtyp = ref_type(source, axis)
                self._frames[axis + "s_added"] = self._s.publish(
                    {axis: typ},
                    (
                        {axis: i}
                        for i, in p.s.execute(
                            f"SELECT id FROM {t} WHERE ns=1 ORDER BY id"
                        )
                    ),
                    key=(axis,),
                )
                self._frames[axis + "s_dropped"] = self._s.publish(
                    {axis: oldtyp},
                    (
                        {axis: i}
                        for i in range(source.shape[0 if axis == "row" else 1])
                        if not p.s.execute(
                            f"SELECT 1 FROM {t} WHERE ns=0 AND old=?", (i,)
                        ).fetchone()
                    ),
                    key=(axis,),
                )
                schema = {
                    "old_" + axis: oldtyp,
                    axis: typ,
                    "old_lb": DType("number"),
                    "old_ub": DType("number"),
                    "lb": DType("number"),
                    "ub": DType("number"),
                }

                def changes():
                    for i, old, lo, hi in p.s.execute(
                        f"SELECT id,old,lb,ub FROM {t} WHERE ns=0 ORDER BY id"
                    ):
                        obj = (source.rows if axis == "row" else source.vars).by_id(old)
                        if (obj.lb, obj.ub) != (num(lo), num(hi)):
                            yield {
                                "old_" + axis: old,
                                axis: i,
                                "old_lb": obj.lb,
                                "old_ub": obj.ub,
                                "lb": num(lo),
                                "ub": num(hi),
                            }

                self._frames[axis + "_bounds_changed"] = self._s.publish(
                    schema, changes()
                )
            self._frames["domains_changed"] = self._s.publish(
                {
                    "old_var": ref_type(source, "var"),
                    "var": ref_type(target, "var"),
                    "old_kind": DType("str"),
                    "kind": DType("str"),
                },
                (
                    dict(
                        old_var=old, var=i, old_kind=source.vars.by_id(old).kind, kind=k
                    )
                    for i, old, k in p.s.execute(
                        f"SELECT id,old,kind FROM {p.vars} WHERE ns=0 ORDER BY id"
                    )
                    if source.vars.by_id(old).kind != k
                ),
            )
            self._frames["rows_changed"] = self._s.publish(
                {
                    "old_row": ref_type(source, "row"),
                    "row": ref_type(target, "row"),
                    "nnz_before": DType("int"),
                    "nnz_after": DType("int"),
                },
                (
                    dict(
                        old_row=old,
                        row=i,
                        nnz_before=source.rows.by_id(old).nnz,
                        nnz_after=n,
                    )
                    for old, i, n in p.s.execute(
                        f"SELECT r.old,t.id,t.n FROM replaces r JOIN {p.rows} t ON t.ns=1 AND t.old=r.new ORDER BY r.old,t.id"
                    )
                ),
            )
            self._stats = {}
            for axis in ("row", "var"):
                self._stats[axis] = dict(
                    before=self._degree(source, axis), after=self._degree(target, axis)
                )
        except BaseException:
            self._s.close()
            if self._edit:
                self._edit.close()
            raise

    def _verify_target(self, p):
        m = self._target
        s = self._source
        if (
            m.shape != (p.nrows, p.nvars)
            or m.nnz != p.nnz
            or m.objective.sense != p.sense
            or m.objective.constant != p.constant
        ):
            raise SchemaError("Lineage does not reproduce target facts")
        for i, name, k, lo, hi, obj in p.s.execute(
            f"SELECT id,name,kind,lb,ub,obj FROM {p.vars}"
        ):
            v = m.vars.by_id(i)
            if (v.name, v.kind, v.lb, v.ub, v.obj) != (
                name,
                k,
                num(lo),
                num(hi),
                num(obj),
            ):
                raise SchemaError("Lineage variable mismatch")
        for i, name, lo, hi in p.s.execute(f"SELECT id,name,lb,ub FROM {p.rows}"):
            r = m.rows.by_id(i)
            if (r.name, r.lb, r.ub) != (name, num(lo), num(hi)):
                raise SchemaError("Lineage row mismatch")
        position = 0
        for r, v, cid, c, old in p.entries():
            if (
                not int(m.model.row_ptr[r]) <= position < int(m.model.row_ptr[r + 1])
                or int(m.model.col_idx[position]) != v
                or m.model.num(int(m.model.coeff_id[position]))
                != (s.model.num(cid) if cid is not None else Fraction(c))
            ):
                raise SchemaError("Lineage term mismatch")
            position += 1

    def _degree(self, m, axis):
        ptr = m.model.row_ptr if axis == "row" else m.model.col_ptr
        t = self._s.raw_table("degree INTEGER")
        for i in range(len(ptr) - 1):
            self._s.execute(f"INSERT INTO {t} VALUES(?)", (int(ptr[i + 1] - ptr[i]),))
        n = len(ptr) - 1
        if not n:
            result = dict(max=0, median=None, p90=None)
        else:

            def at(i):
                return self._s.execute(
                    f"SELECT degree FROM {t} ORDER BY degree LIMIT 1 OFFSET ?", (i,)
                ).fetchone()[0]

            result = dict(
                max=at(n - 1),
                median=Fraction(at((n - 1) // 2) + at(n // 2), 2),
                p90=at((9 * n + 9) // 10 - 1),
            )
        self._s.execute(f"DROP TABLE {t}")
        return result

    def __getattr__(self, n):
        if n.startswith("_"):
            raise AttributeError(n)
        self._s.guard()
        if n in self._frames:
            return self._frames[n]
        raise AttributeError(n)

    @property
    def semantic(self):
        """
        MIPFRAME HELP BEGIN
        ModelDiff.semantic [api.ModelDiff.semantic]

        Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        ModelDiff.semantic [property]

        When to use: Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._s.guard()
        return deepcopy(self._semantic)

    @property
    def objective_changed(self):
        """
        MIPFRAME HELP BEGIN
        ModelDiff.objective_changed [api.ModelDiff.objective_changed]

        Read ModelDiff objective_changed.

        ModelDiff.objective_changed [property]

        When to use: Read ModelDiff objective_changed.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._s.guard()
        return deepcopy(self._semantic["objective"])

    @property
    def nnz_before(self):
        """
        MIPFRAME HELP BEGIN
        ModelDiff.nnz_before [api.ModelDiff.nnz_before]

        Read ModelDiff nnz_before.

        ModelDiff.nnz_before [property]

        When to use: Read ModelDiff nnz_before.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._s.guard()
        return self._source.nnz

    @property
    def nnz_after(self):
        """
        MIPFRAME HELP BEGIN
        ModelDiff.nnz_after [api.ModelDiff.nnz_after]

        Read ModelDiff nnz_after.

        ModelDiff.nnz_after [property]

        When to use: Read ModelDiff nnz_after.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._s.guard()
        return self._target.nnz

    @property
    def row_nnz_stats(self):
        """
        MIPFRAME HELP BEGIN
        ModelDiff.row_nnz_stats [api.ModelDiff.row_nnz_stats]

        Read ModelDiff row_nnz_stats.

        ModelDiff.row_nnz_stats [property]

        When to use: Read ModelDiff row_nnz_stats.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._s.guard()
        return deepcopy(self._stats["row"])

    @property
    def col_nnz_stats(self):
        """
        MIPFRAME HELP BEGIN
        ModelDiff.col_nnz_stats [api.ModelDiff.col_nnz_stats]

        Read ModelDiff col_nnz_stats.

        ModelDiff.col_nnz_stats [property]

        When to use: Read ModelDiff col_nnz_stats.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._s.guard()
        return deepcopy(self._stats["var"])

    def __repr__(self):
        return f"<ModelDiff: nnz {self.nnz_before:,} → {self.nnz_after:,}; {self.semantic}>"
