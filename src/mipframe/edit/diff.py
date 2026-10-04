"""Exact direct-parent differences and freshly rechecked edit semantics."""

from fractions import Fraction
from copy import deepcopy
from mipframe.frame.schema import DType, ref_type, SchemaError
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.edit.plan import num


class ModelDiff:
    """
    MIPFRAME HELP BEGIN
    Parent-child comparison with actual identity maps and verified semantic directions.

    Full contract: mf.help("api.ModelDiff")
    MIPFRAME HELP END
    """
    def __init__(self, target, source, *, verify="auto", memory_mb=128, disk_mb=None):
        if verify not in ("auto", "replay"):
            raise ValueError("verify must be 'auto' or 'replay'")
        self._target, self._source = target, source
        self._s = AlgorithmStore((source, target), memory_mb=memory_mb,
                                 disk_mb=disk_mb, max_output_rows=None)
        self._edit = None
        self._frames, self._stats = {}, {}
        try:
            from mipframe.edit.verified import find, remember
            p = find(target, source) if verify == "auto" else None
            if p is None:
                if (source.uid, source.version) == (target.uid, target.version):
                    self._edit = source.edit(memory_mb=memory_mb, disk_mb=disk_mb)
                else:
                    self._edit = target.lineage._restore(
                        source, memory_mb=memory_mb, disk_mb=disk_mb)
                prepared = self._edit._prepare()
                prepared.require(True)
                self._verify_target(prepared)
                p = remember(target, source, prepared, memory_mb=memory_mb, disk_mb=disk_mb)
                self._edit.close()
                self._edit = None
            self._plan = p
            self._semantic = deepcopy(p.semantic)
        except BaseException:
            self._s.close()
            if self._edit is not None:
                self._edit.close()
            raise

    def _frame(self, name):
        p, source, target = self._plan, self._source, self._target
        if name in ("rows_added", "vars_added", "rows_dropped", "vars_dropped",
                    "row_bounds_changed", "var_bounds_changed"):
            axis = "row" if name.startswith("row") else "var"
            table = p.rows if axis == "row" else p.vars
            typ, oldtyp = ref_type(target, axis), ref_type(source, axis)
            if name.endswith("_added"):
                return self._s.publish({axis: typ},
                    ({axis: i} for i, in p.s.execute(
                        f"SELECT id FROM {table} WHERE ns=1 ORDER BY id")), key=(axis,))
            if name.endswith("_dropped"):
                def dropped():
                    retained = iter(p.s.execute(f"SELECT old FROM {table} WHERE ns=0 ORDER BY old"))
                    next_old = next(retained, (None,))[0]
                    for i in range(source.shape[0 if axis == "row" else 1]):
                        if i == next_old:
                            next_old = next(retained, (None,))[0]
                        else:
                            yield {axis: i}
                return self._s.publish({axis: oldtyp}, dropped(), key=(axis,))
            def changes():
                for i, old in p.s.execute(
                        f"SELECT id,old FROM {table} WHERE ns=0 ORDER BY id"):
                    obj = (source.rows if axis == "row" else source.vars).loc[old]
                    new = (target.rows if axis == "row" else target.vars).loc[i]
                    if (obj.lb, obj.ub) != (new.lb, new.ub):
                        yield {"old_" + axis: old, axis: i, "old_lb": obj.lb,
                               "old_ub": obj.ub, "lb": new.lb, "ub": new.ub}
            return self._s.publish({"old_" + axis: oldtyp, axis: typ,
                **{n: DType("number") for n in ("old_lb", "old_ub", "lb", "ub")}}, changes())
        if name == "domains_changed":
            return self._s.publish({"old_var": ref_type(source, "var"),
                "var": ref_type(target, "var"), "old_kind": DType("str"), "kind": DType("str")},
                (dict(old_var=old, var=i, old_kind=source.vars.loc[old].kind, kind=target.vars.loc[i].kind)
                 for i, old in p.s.execute(f"SELECT id,old FROM {p.vars} WHERE ns=0 ORDER BY id")
                 if source.vars.loc[old].kind != target.vars.loc[i].kind))
        if name == "rows_changed":
            return self._s.publish({"old_row": ref_type(source, "row"),
                "row": ref_type(target, "row"), "nnz_before": DType("int"), "nnz_after": DType("int")},
                (dict(old_row=old, row=i, nnz_before=source.rows.loc[old].nnz, nnz_after=target.rows.loc[i].nnz)
                 for old, i in p.s.execute(
                     f"SELECT r.old,t.id FROM {p.replaces} r JOIN {p.rows} t "
                     "ON t.ns=1 AND t.old=r.new ORDER BY r.old,t.id")))
        raise AttributeError(name)

    def _statistics(self, axis):
        if axis not in self._stats:
            self._stats[axis] = dict(before=self._degree(self._source, axis),
                                     after=self._degree(self._target, axis))
        return deepcopy(self._stats[axis])

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
            v = m.vars.loc[i]
            if (v.name, v.kind, v.lb, v.ub, v.obj) != (
                name,
                k,
                num(lo),
                num(hi),
                num(obj),
            ):
                raise SchemaError("Lineage variable mismatch")
        for i, name, lo, hi in p.s.execute(f"SELECT id,name,lb,ub FROM {p.rows}"):
            r = m.rows.loc[i]
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
        if n not in self._frames:
            self._frames[n] = self._frame(n)
        return self._frames[n]

    @property
    def semantic(self):
        """
        MIPFRAME HELP BEGIN
        Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        Full contract: mf.help("api.ModelDiff.semantic")
        MIPFRAME HELP END
        """
        self._s.guard()
        return deepcopy(self._semantic)

    @property
    def objective_changed(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff objective_changed.

        Full contract: mf.help("api.ModelDiff.objective_changed")
        MIPFRAME HELP END
        """
        self._s.guard()
        return deepcopy(self._semantic["objective"])

    @property
    def nnz_before(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff nnz_before.

        Full contract: mf.help("api.ModelDiff.nnz_before")
        MIPFRAME HELP END
        """
        self._s.guard()
        return self._source.nnz

    @property
    def nnz_after(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff nnz_after.

        Full contract: mf.help("api.ModelDiff.nnz_after")
        MIPFRAME HELP END
        """
        self._s.guard()
        return self._target.nnz

    @property
    def row_nnz_stats(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff row_nnz_stats.

        Full contract: mf.help("api.ModelDiff.row_nnz_stats")
        MIPFRAME HELP END
        """
        self._s.guard()
        return self._statistics("row")

    @property
    def col_nnz_stats(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff col_nnz_stats.

        Full contract: mf.help("api.ModelDiff.col_nnz_stats")
        MIPFRAME HELP END
        """
        self._s.guard()
        return self._statistics("var")

    def __repr__(self):
        return f"<ModelDiff: nnz {self.nnz_before:,} → {self.nnz_after:,}; {self.semantic}>"
