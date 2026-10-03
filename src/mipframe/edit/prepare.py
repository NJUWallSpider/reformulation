"""Normalize final facts without creating a model or triggering structural analysis."""

from fractions import Fraction
from math import ceil, floor, inf
from mipframe.edit.plan import num, EditValidationError, NonIsolatedVariableError
from mipframe.frame.schema import DType
from mipframe.check.report import Report


class Prepared:
    def __init__(self, edit):
        self.edit = edit
        self.s = edit._store
        self.tables = []
        self.nnz = 0
        self.vars = self.table(
            "id INTEGER PRIMARY KEY,ns INTEGER,old INTEGER,name TEXT,kind TEXT,lb TEXT,ub TEXT,obj TEXT,n INTEGER DEFAULT 0,UNIQUE(ns,old)"
        )
        self.rows = self.table(
            "id INTEGER PRIMARY KEY,ns INTEGER,old INTEGER,name TEXT,lb TEXT,ub TEXT,n INTEGER DEFAULT 0,UNIQUE(ns,old)"
        )
        self.bad = self.table(
            "id INTEGER PRIMARY KEY,category TEXT,axis TEXT,entity INTEGER,reason TEXT"
        )
        try:
            self._metadata()
            self._structure()
            from mipframe.edit.semantics import semantic

            self.semantic = semantic(self)
            self.errors = self.s.execute(
                f"SELECT count(*) FROM {self.bad} WHERE category!='infeasible'"
            ).fetchone()[0]
            self.infeasible = self.s.execute(
                f"SELECT count(*) FROM {self.bad} WHERE category='infeasible'"
            ).fetchone()[0]
        except BaseException:
            self.close()
            raise

    def table(self, definition):
        t = self.s.raw_table(definition)
        self.tables.append(t)
        return t

    def close(self):
        for t in reversed(self.tables):
            self.s.execute(f"DROP TABLE IF EXISTS {t}")
        self.tables = []

    def issue(self, category, axis, ident, reason):
        self.s.execute(
            f"INSERT INTO {self.bad}(category,axis,entity,reason) VALUES(?,?,?,?)",
            (category, axis, ident, reason),
        )

    def _change(self, axis, i, field, old):
        r = self.s.execute(
            "SELECT value FROM changes WHERE axis=? AND id=? AND field=?",
            (axis, i, field),
        ).fetchone()
        return r[0] if r else old

    def _metadata(self):
        m = self.edit._owner.model
        s = self.s
        setting = s.execute("SELECT value FROM settings WHERE key='sense'").fetchone()
        self.sense = setting[0] if setting else m.manifest["sense"]
        self.constant = (
            num(
                s.execute("SELECT value FROM settings WHERE key='constant'").fetchone()[
                    0
                ]
            )
            if setting
            else m.num(m.manifest["objective_constant"])
        )
        self.nvars = 0

        def n(v, side):
            return str((-inf if side == "lb" else inf) if v is None else m.num(v))

        for v in m.variables:
            i, name, kind, lo, hi, obj, line = v
            cost = (
                s.execute("SELECT c FROM objective WHERE ns=0 AND v=?", (i,)).fetchone()
                if setting
                else None
            )
            cost = cost[0] if cost else "0" if setting else str(m.num(obj))
            if s.execute(
                "SELECT 1 FROM dropped WHERE axis='var' AND id=?", (i,)
            ).fetchone():
                if num(cost):
                    self.issue(
                        "isolated",
                        "var",
                        i,
                        "Deleted variable has a nonzero final objective coefficient",
                    )
                continue
            vals = (
                self._change("var", i, "kind", kind),
                self._change("var", i, "lb", n(lo, "lb")),
                self._change("var", i, "ub", n(hi, "ub")),
            )
            s.execute(
                f"INSERT INTO {self.vars}(id,ns,old,name,kind,lb,ub,obj) VALUES(?,0,?,?,?,?,?,?)",
                (self.nvars, i, name, *vals, cost),
            )
            self.nvars += 1
        for i, batch, label, name, kind, lo, hi, cost in s.execute(
            "SELECT * FROM new_vars ORDER BY id"
        ):
            if setting:
                val = s.execute(
                    "SELECT c FROM objective WHERE ns=1 AND v=?", (i,)
                ).fetchone()
                cost = val[0] if val else "0"
            s.execute(
                f"INSERT INTO {self.vars}(id,ns,old,name,kind,lb,ub,obj) VALUES(?,1,?,?,?,?,?,?)",
                (self.nvars, i, name, kind, lo, hi, cost),
            )
            self.nvars += 1
        self.nrows = 0
        for i, name, lo, hi, line in m.rows:
            if s.execute(
                "SELECT 1 FROM dropped WHERE axis='row' AND id=?", (i,)
            ).fetchone():
                continue
            vals = (
                self._change("row", i, "lb", n(lo, "lb")),
                self._change("row", i, "ub", n(hi, "ub")),
            )
            s.execute(
                f"INSERT INTO {self.rows}(id,ns,old,name,lb,ub) VALUES(?,0,?,?,?,?)",
                (self.nrows, i, name, *vals),
            )
            self.nrows += 1
        for i, batch, label, name, lo, hi in s.execute(
            "SELECT * FROM new_rows ORDER BY id"
        ):
            s.execute(
                f"INSERT INTO {self.rows}(id,ns,old,name,lb,ub) VALUES(?,1,?,?,?,?)",
                (self.nrows, i, name, lo, hi),
            )
            self.nrows += 1
        for axis, t in (("row", self.rows), ("var", self.vars)):
            s.execute(f"CREATE INDEX {t}_name ON {t}(name)")
            for i, name in s.execute(
                f"SELECT id,name FROM {t} WHERE name IN (SELECT name FROM {t} GROUP BY name HAVING count(*)>1) ORDER BY id"
            ):
                self.issue("name", axis, i, f"Duplicate final name {name!r}")
            for i, lo, hi in s.execute(f"SELECT id,lb,ub FROM {t}"):
                if num(lo) > num(hi):
                    self.issue("infeasible", axis, i, "Lower bound exceeds upper bound")
        for i, lo, hi in s.execute(f"SELECT id,lb,ub FROM {self.vars} WHERE kind='I'"):
            if lo != "-inf" and hi != "inf" and ceil(num(lo)) > floor(num(hi)):
                self.issue("infeasible", "var", i, "Integer domain contains no integer")

    def entries(self):
        """Yield final row/column and either original number ID or new exact text."""
        m = self.edit._owner.model
        s = self.s
        for r, ns, old in s.execute(f"SELECT id,ns,old FROM {self.rows} ORDER BY id"):
            if ns:
                terms = (
                    (vns, v, None, c)
                    for vns, v, c in s.execute(
                        "SELECT ns,v,c FROM new_terms WHERE r=? ORDER BY ns,v", (old,)
                    )
                )
            else:
                terms = (
                    (0, int(m.col_idx[p]), int(m.coeff_id[p]), None)
                    for p in range(int(m.row_ptr[old]), int(m.row_ptr[old + 1]))
                )
            for vns, v, cid, c in terms:
                dest = s.execute(
                    f"SELECT id FROM {self.vars} WHERE ns=? AND old=?", (vns, v)
                ).fetchone()
                yield r, None if dest is None else dest[0], cid, c, v

    def _structure(self):
        for r, v, cid, c, original in self.entries():
            if v is None:
                self.issue(
                    "isolated",
                    "var",
                    original,
                    f"Deleted variable is used by surviving row {r}",
                )
                continue
            self.nnz += 1
            self.s.execute(f"UPDATE {self.rows} SET n=n+1 WHERE id=?", (r,))
            self.s.execute(f"UPDATE {self.vars} SET n=n+1 WHERE id=?", (v,))
        for r, lo, hi in self.s.execute(f"SELECT id,lb,ub FROM {self.rows} WHERE n=0"):
            if not num(lo) <= 0 <= num(hi):
                self.issue("infeasible", "row", r, "Empty row excludes zero")

    def report(self, allow_infeasible=False):
        where = "WHERE category!='infeasible'" if allow_infeasible else ""
        bad = self.s.publish(
            {
                "category": DType("str"),
                "axis": DType("str"),
                "id": DType("int"),
                "reason": DType("str"),
            },
            (
                dict(category=c, axis=a, id=i, reason=r)
                for c, a, i, r in self.s.execute(
                    f"SELECT category,axis,entity,reason FROM {self.bad} {where} ORDER BY id"
                )
            ),
        )
        return Report(
            self.s,
            len(bad) == 0,
            dict(
                operation="edit build contract",
                errors=len(bad),
                feasibility="not generally checked",
            ),
            bad,
        )

    def require(self, allow_infeasible):
        if self.errors or self.infeasible and not allow_infeasible:
            rep = self.report(allow_infeasible)
            cls = (
                NonIsolatedVariableError
                if self.s.execute(
                    f"SELECT 1 FROM {self.bad} WHERE category='isolated' LIMIT 1"
                ).fetchone()
                else EditValidationError
            )
            raise cls(rep)
