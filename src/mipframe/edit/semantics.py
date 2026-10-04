"""Two-direction inclusion checks with grounded, exact proof dependencies."""

from fractions import Fraction
from math import inf
import hashlib
from mipframe.linear.claim import source_claim
from mipframe.edit.plan import num


def semantic(p):
    s = p.s
    e = p.edit
    m = e._owner
    claims = p.table("id INTEGER PRIMARY KEY,digest TEXT,rhs TEXT")
    terms = p.table("claim INTEGER,v INTEGER,c TEXT,PRIMARY KEY(claim,v)")
    nodes = p.table(
        "id INTEGER PRIMARY KEY,domain TEXT,lp INTEGER DEFAULT 0,mip INTEGER DEFAULT 0"
    )
    outputs = p.table("proof INTEGER,claim INTEGER")
    deps = p.table("proof INTEGER,fact INTEGER,parent INTEGER")
    facts = p.table(
        "id INTEGER PRIMARY KEY,kind TEXT,entity INTEGER,side TEXT,lp INTEGER DEFAULT 0,mip INTEGER DEFAULT 0,UNIQUE(kind,entity,side)"
    )
    factclaims = p.table("fact INTEGER,claim INTEGER")
    roots = p.table("binding INTEGER,proof INTEGER")
    goals = p.table(
        "id INTEGER PRIMARY KEY,direction TEXT,claim INTEGER,op INTEGER,axis TEXT,entity INTEGER"
    )
    s.execute(f"CREATE INDEX {claims}_digest ON {claims}(digest)")
    for table, columns in ((outputs, 'claim,proof'), (deps, 'proof'),
                           (factclaims, 'fact,claim'), (roots, 'proof,binding')):
        s.execute(f'CREATE INDEX {table}_lookup ON {table}({columns})')

    from functools import partial
    normalized = partial(_normalized_claim, s, claims, terms, m.shape[1])

    def claim_ids(claim):
        signs = (
            (1, -1)
            if claim.relation == "=="
            else (-1,) if claim.relation == ">=" else (1,)
        )
        return [
            normalized(claim.expr._items(s), -claim.expr.constant, sign)
            for sign in signs
        ]

    def stronger(a, b):
        da, ra = s.execute(
            f"SELECT digest,rhs FROM {claims} WHERE id=?", (a,)
        ).fetchone()
        db, rb = s.execute(
            f"SELECT digest,rhs FROM {claims} WHERE id=?", (b,)
        ).fetchone()
        if da != db or num(ra) > num(rb):
            return False
        return not any(
            s.execute(
                f"SELECT v,c FROM {terms} WHERE claim=? EXCEPT SELECT v,c FROM {terms} WHERE claim=? LIMIT 1",
                (x, y),
            ).fetchone()
            for x, y in ((a, b), (b, a))
        )

    def providers(cl):
        digest = s.execute(f"SELECT digest FROM {claims} WHERE id=?", (cl,)).fetchone()[
            0
        ]
        for node, c in s.execute(
            f"SELECT o.proof,o.claim FROM {outputs} o JOIN {claims} c ON c.id=o.claim WHERE c.digest=?",
            (digest,),
        ):
            if stronger(c, cl):
                yield node

    memo = p.table("object INTEGER PRIMARY KEY,proof INTEGER")

    def add_report(report, cache=True):
        key = id(report)
        cached = (
            s.execute(f"SELECT proof FROM {memo} WHERE object=?", (key,)).fetchone()
            if cache
            else None
        )
        if cached is not None:
            return cached[0]
        e._check_proof(report)
        ident = s.execute(
            f"INSERT INTO {nodes}(domain) VALUES(?)", (report.scope["domain"],)
        ).lastrowid
        if cache:
            s.execute(f"INSERT INTO {memo} VALUES(?,?)", (key, ident))
        for cl in claim_ids(report.conclusion):
            s.execute(f"INSERT INTO {outputs} VALUES(?,?)", (ident, cl))
        for d in report.dependencies._iterate():
            kind = d["kind"]
            if kind == "report":
                parent = add_report(report._parents[d["parent"]])
                s.execute(f"INSERT INTO {deps} VALUES(?,NULL,?)", (ident, parent))
                continue
            if kind not in ("row", "var", "integer"):
                raise ValueError("Unsupported proof dependency")
            entity = d["row"] if kind == "row" else d["var"]
            # _iterate exposes stored integer identities, not named entities.
            side = d["side"]
            s.execute(
                f"INSERT OR IGNORE INTO {facts}(kind,entity,side) VALUES(?,?,?)",
                (kind, entity, side),
            )
            fid = s.execute(
                f"SELECT id FROM {facts} WHERE kind=? AND entity=? AND side=?",
                (kind, entity, side),
            ).fetchone()[0]
            s.execute(f"INSERT INTO {deps} VALUES(?,?,NULL)", (ident, fid))
        return ident

    # Keep references to visited Report objects to avoid Python identity reuse.
    for binding, report in enumerate(e._proofs):
        if getattr(report, "_batch", None):
            for key in report._batch.keys():
                child = report.for_target(key)
                s.execute(
                    f"INSERT INTO {roots} VALUES(?,?)",
                    (binding, add_report(child, cache=False)),
                )
        else:
            s.execute(f"INSERT INTO {roots} VALUES(?,?)", (binding, add_report(report)))

    _ground_facts(p, facts, factclaims, claim_ids)
    _close_dependencies(s, nodes, deps, facts, factclaims, providers)

    def emit(direction, axis, entity, side, value, op):
        if axis == "new":
            items = (
                ((ns, v), Fraction(c))
                for ns, v, c in s.execute(
                    "SELECT ns,v,c FROM new_terms WHERE r=? ORDER BY ns,v", (entity,)
                )
            )
        elif axis == "row":
            items = m.rows.loc[entity].expr._items(s)
        else:
            items = iter(((entity, Fraction(1)),))
        cl = normalized(items, value, -1 if side == "lb" else 1)
        s.execute(
            f"INSERT INTO {goals}(direction,claim,op,axis,entity) VALUES(?,?,?,?,?)",
            (direction, cl, op, axis, entity),
        )

    # Each explicit operation retains its own evidence coverage obligation.
    for op, kind in s.execute("SELECT id,kind FROM operations ORDER BY id"):
        if kind == "add_rows":
            for i, lo, hi in s.execute(
                "SELECT id,lb,ub FROM new_rows WHERE batch=?", (op,)
            ):
                for side, val in (("lb", num(lo)), ("ub", num(hi))):
                    if val not in (-inf, inf):
                        emit("forward", "new", i, side, val, op)
        elif kind in ("drop_rows", "set_row", "set_var"):
            for axis, i in s.execute("SELECT axis,id FROM subjects WHERE op=?", (op,)):
                obj = m.rows.loc[i] if axis == "row" else m.vars.loc[i]
                for side in ("lb", "ub"):
                    if (
                        kind != "drop_rows"
                        and not s.execute(
                            "SELECT 1 FROM fields WHERE op=? AND field=?", (op, side)
                        ).fetchone()
                    ):
                        continue
                    old = getattr(obj, side)
                    if kind == "drop_rows":
                        new = -inf if side == "lb" else inf
                    else:
                        rec = s.execute(
                            "SELECT value FROM changes WHERE axis=? AND id=? AND field=?",
                            (axis, i, side),
                        ).fetchone()
                        new = num(rec[0]) if rec else old
                    if new == old:
                        continue
                    tighter = new > old if side == "lb" else new < old
                    value = new if tighter else old
                    if value not in (-inf, inf):
                        emit(
                            "forward" if tighter else "reverse",
                            axis,
                            i,
                            side,
                            value,
                            op,
                        )
    direction_ok = {d: {k: True for k in ("forward", "reverse")} for d in ("lp", "mip")}
    eligible = p.table("id INTEGER PRIMARY KEY")
    for gid, direction, cl, op, axis, i in s.execute(
        f"SELECT * FROM {goals} ORDER BY id"
    ):
        attached = bool(s.execute("SELECT 1 FROM proofs WHERE op=?", (op,)).fetchone())
        s.execute(f"DELETE FROM {eligible}")
        for node in providers(cl):
            if (
                not attached
                or s.execute(
                    f"SELECT 1 FROM {roots} r JOIN proofs b ON b.p=r.binding WHERE b.op=? AND r.proof=?",
                    (op, node),
                ).fetchone()
            ):
                s.execute(f"INSERT OR IGNORE INTO {eligible} VALUES(?)", (node,))
        for domain in ("lp", "mip"):
            query = f"SELECT 1 FROM {nodes} n JOIN {eligible} x ON x.id=n.id WHERE 1=1"
            if domain == "lp":
                query += " AND n.domain='relaxation'"
            if direction == "reverse":
                query += f" AND n.{domain}=1"
            valid = s.execute(query + " LIMIT 1").fetchone() is not None
            # Constant tautologies need no external evidence.
            if (
                not s.execute(f"SELECT 1 FROM {terms} WHERE claim=?", (cl,)).fetchone()
                and num(
                    s.execute(f"SELECT rhs FROM {claims} WHERE id=?", (cl,)).fetchone()[
                        0
                    ]
                )
                >= 0
            ):
                valid = True
            direction_ok[domain][direction] &= valid
            if attached and domain == "mip" and not valid:
                p.issue(
                    "proof",
                    axis,
                    i,
                    f"Operation {op}: uncovered or ungrounded {direction} claim",
                )
                for fid, k, entity, side in s.execute(
                    f"SELECT id,kind,entity,side FROM {facts} WHERE mip=0"
                ):
                    p.issue("dependency", k, entity, f"Unclosed fact: {side}")
    for i, kind in s.execute(
        "SELECT id,value FROM changes WHERE axis='var' AND field='kind'"
    ):
        old = m.vars.loc[i].kind
        if old != kind:
            direction_ok["mip"]["forward" if kind == "I" else "reverse"] = False
    added = s.execute("SELECT count(*) FROM new_vars").fetchone()[0]
    dropped = s.execute("SELECT count(*) FROM dropped WHERE axis='var'").fetchone()[0]
    coordinate = (
        "CHANGED"
        if added and dropped
        else "EXTENDED" if added else "REDUCED" if dropped else "SAME"
    )

    def relation(d):
        if coordinate != "SAME":
            return "NOT_COMPARABLE"
        a, b = direction_ok[d]["forward"], direction_ok[d]["reverse"]
        return "SAME" if a and b else "SUPERSET" if a else "SUBSET" if b else "UNKNOWN"

    objective = _objective_relation(p, coordinate)
    result = dict(
        coordinates=coordinate,
        lp=relation("lp"),
        mip=relation("mip"),
        objective=objective,
        optimal_value="UNKNOWN",
    )
    if result["mip"] == "SAME" and objective["kind"] != "CHANGED":
        result["optimal_value"] = (
            "PRESERVED" if objective["kind"] == "SAME" else "TRANSFORMED"
        )
    return result


def _normalized_claim(s, claims, terms, variable_count, items, rhs, sign=1):
    it = iter(items)
    first = next(it, None)
    scale = abs(first[1]) if first else Fraction(1)
    ident = s.execute(
        f"INSERT INTO {claims}(digest,rhs) VALUES(NULL,?)",
        (str(rhs * sign / scale),),
    ).lastrowid
    h = hashlib.sha256()
    from itertools import chain

    for v, c in chain(() if first is None else (first,), it):
        if isinstance(v, tuple):
            if v[0]:
                v = variable_count + v[1]
            else:
                v = v[1]
        c = c * sign / scale
        h.update(f"{v}:{c};".encode())
        s.execute(f"INSERT INTO {terms} VALUES(?,?,?)", (ident, v, str(c)))
    s.execute(f"UPDATE {claims} SET digest=? WHERE id=?", (h.hexdigest(), ident))
    return ident



def _ground_facts(p, facts, factclaims, claim_ids):
    s, m = p.s, p.edit._owner
    def final_fact(kind, entity, side):
        t = p.rows if kind == "row" else p.vars
        r = s.execute(
            f"SELECT lb,ub"
            + (",kind" if kind != "row" else "")
            + f" FROM {t} WHERE ns=0 AND old=?",
            (entity,),
        ).fetchone()
        if r is None:
            return False
        if kind == "integer":
            return r[2] == "I"
        obj = m.rows.loc[entity] if kind == "row" else m.vars.loc[entity]
        return (
            side in ("lb", "eq")
            and num(r[0]) >= obj.lb
            and (side != "eq" or num(r[1]) <= obj.ub)
        ) or (side == "ub" and num(r[1]) <= obj.ub)

    for fid, kind, entity, side in s.execute(
        f"SELECT id,kind,entity,side FROM {facts}"
    ):
        valid = final_fact(kind, entity, side)
        s.execute(
            f"UPDATE {facts} SET lp=?,mip=? WHERE id=?",
            (int(valid and kind != "integer"), int(valid), fid),
        )
        if kind != "integer":
            obj = m.rows.loc[entity] if kind == "row" else m.vars.loc[entity]
            for cl in claim_ids(source_claim(obj, side)):
                s.execute(f"INSERT INTO {factclaims} VALUES(?,?)", (fid, cl))


def _close_dependencies(s, nodes, deps, facts, factclaims, providers):
    for domain in ("lp", "mip"):
        while True:
            changed = False
            for (node,) in s.execute(
                f"SELECT id FROM {nodes} WHERE {domain}=0"
                + (" AND domain='relaxation'" if domain == "lp" else "")
            ):
                blocked = s.execute(
                    f"SELECT 1 FROM {deps} d LEFT JOIN {facts} f ON f.id=d.fact LEFT JOIN {nodes} n ON n.id=d.parent WHERE d.proof=? AND (coalesce(f.{domain},n.{domain},0)=0) LIMIT 1",
                    (node,),
                ).fetchone()
                if not blocked:
                    s.execute(f"UPDATE {nodes} SET {domain}=1 WHERE id=?", (node,))
                    changed = True
            for (fid,) in s.execute(
                f"SELECT id FROM {facts} WHERE {domain}=0 AND kind!='integer'"
            ):
                cls = (
                    cl
                    for cl, in s.execute(
                        f"SELECT claim FROM {factclaims} WHERE fact=?", (fid,)
                    )
                )
                if all(
                    any(
                        s.execute(
                            f"SELECT {domain} FROM {nodes} WHERE id=?", (node,)
                        ).fetchone()[0]
                        for node in providers(cl)
                    )
                    for cl in cls
                ):
                    s.execute(f"UPDATE {facts} SET {domain}=1 WHERE id=?", (fid,))
                    changed = True
            if not changed:
                break



def _objective_relation(p, coordinate):
    s, m = p.s, p.edit._owner
    objective = {"kind": "CHANGED"}
    if coordinate == "SAME" and p.sense == m.objective.sense:
        factor = None
        affine = True
        for old, c in s.execute(f"SELECT old,obj FROM {p.vars} ORDER BY id"):
            before = m.vars.loc[old].obj
            after = num(c)
            if before:
                a = after / before
                if factor is None:
                    factor = a
                if a != factor:
                    affine = False
            elif after:
                affine = False
        factor = Fraction(1) if factor is None else factor
        if affine and factor > 0:
            shift = p.constant - factor * m.objective.constant
            objective = dict(
                kind=(
                    "SAME"
                    if factor == 1 and shift == 0
                    else "SHIFT" if factor == 1 else "POSITIVE_AFFINE"
                ),
                scale=factor,
                offset=shift,
            )
    return objective
