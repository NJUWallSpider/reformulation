"""Exact mathematical view IR; human Markdown and machine data share one persisted document.

Numerical row references are explicit residuals, never a claimed structural explanation.
This sidecar can be compiled for an existing v2 artifact without reparsing its source.
"""

import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict
from fractions import Fraction as F
from functools import lru_cache

from .numbers import LPIRError
from .artifact_io import sha256

VERSION = "1.0"
FILE = "explanations.sqlite"
MANIFEST = "explanations.json"


def js(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def const(x):
    return {"constant": str(x)}


def par(x):
    return {"parameter": x}


def var(x):
    return {"variable": x}


def summ(x):
    return {"sum": x}


def op(name, *args):
    return {"op": name, "args": list(args)}


def cmp(name, left, right):
    return {"op": name, "left": left, "right": right}


def simplify(node, constants):
    if "parameter" in node and node["parameter"] in constants:
        return const(constants[node["parameter"]])
    n = {
        k: [simplify(x, constants) for x in v]
        if isinstance(v, list)
        else simplify(v, constants)
        if isinstance(v, dict)
        else v
        for k, v in node.items()
    }
    if n.get("op") in ("add", "sub", "mul"):
        a, b = n["args"]
        if "constant" in a and "constant" in b:
            x, y = F(a["constant"]), F(b["constant"])
            return const(
                x + y if n["op"] == "add" else x - y if n["op"] == "sub" else x * y
            )
        if n["op"] == "add":
            if a == const(0):
                return b
            if b == const(0):
                return a
        if n["op"] == "sub" and b == const(0):
            return a
        if n["op"] == "mul":
            if a == const(0) or b == const(0):
                return const(0)
            if a == const(1):
                return b
            if b == const(1):
                return a
    return n


def formula(node):
    if "constant" in node:
        return node["constant"]
    if "parameter" in node:
        return node["parameter"] + "[r]"
    if "variable" in node:
        return node["variable"] + "[r]"
    if "sum" in node:
        return "Sum(" + node["sum"] + "[r])"
    if node.get("op") == "weighted_sum":
        return "Sum(a[r,v]*x[v], v in S[r])"
    kind = node.get("op")
    if kind in ("add", "sub", "mul"):
        a, b = node["args"]
        sa, sb = formula(a), formula(b)
        if kind == "mul":
            return f"{sa}*({sb})"
        return f"({sa} {'+' if kind == 'add' else '-'} {sb})"
    if kind in ("eq", "le", "ge"):
        return (
            formula(node["left"])
            + " "
            + {"eq": "=", "le": "<=", "ge": ">="}[kind]
            + " "
            + formula(node["right"])
        )
    if kind == "range":
        return (
            formula(node["lower"])
            + " <= "
            + formula(node["expression"])
            + " <= "
            + formula(node["upper"])
        )
    if kind == "free":
        return formula(node["expression"]) + " unrestricted"
    raise LPIRError("Unknown compressed formula AST")


def relation(expr, lo, hi, params):
    if lo is not None and lo == hi:
        params["d"] = str(lo)
        return cmp("eq", expr, par("d"))
    if lo is None and hi is not None:
        params["b"] = str(hi)
        return cmp("le", expr, par("b"))
    if hi is None and lo is not None:
        params["b"] = str(lo)
        return cmp("ge", expr, par("b"))
    if lo is None and hi is None:
        return {"op": "free", "expression": expr}
    params.update(lower=str(lo), upper=str(hi))
    return {
        "op": "range",
        "lower": par("lower"),
        "expression": expr,
        "upper": par("upper"),
    }


def derive(terms, lo, hi, kind_of, temporal=None):
    """Return a complete affine identity, with a scale mapping back to the raw row."""
    params = {}
    bindings = {}
    roles = {}

    def bind(name, items, kind=None):
        bindings[name] = list(items)
        roles[name] = kind or kind_of(items[0])
        return var(name) if len(items) == 1 else summ(name)

    def done(kind, ast, scale):
        return kind, ast, F(scale), params, bindings, roles

    if temporal:
        source, target, switch, arc, q, M = temporal
        coefs = dict(terms)
        t = bind("target", [target])
        s = bind("source", [source])
        sw = bind("switch", [switch])
        e = bind("arc", [arc])
        params.update(q=q, M=M)
        rhs = op(
            "sub",
            op("mul", par("q"), op("sub", const(1), sw)),
            op("mul", par("M"), op("sub", const(1), e)),
        )
        return done(
            "switched_difference", cmp("ge", op("sub", t, s), rhs), coefs[target]
        )
    cs = [(v, c) for v, c in terms if kind_of(v) == "C"]
    discrete = [(v, c) for v, c in terms if kind_of(v) in ("B", "I")]
    # Revert local complemented literals by constructing directly from numerical terms.
    if (
        lo is not None
        and lo == hi
        and len(cs) == 2
        and cs[0][1] == -cs[1][1]
        and len(terms) in (2, 3)
    ):
        positive = next((v, c) for v, c in cs if c > 0)
        negative = next((v, c) for v, c in cs if c < 0)
        a = positive[1]
        v = bind("v", [positive[0]])
        u = bind("u", [negative[0]])
        params["d"] = str(lo / a)
        if len(terms) == 2 and lo == 0:
            return done("variable_equality", cmp("eq", v, u), a)
        rhs = par("d")
        label = "difference"
        if len(discrete) == 1:
            z, c = discrete[0]
            znode = bind("z", [z])
            params["k"] = str(-c / a)
            rhs = op("add", op("mul", par("k"), znode), par("d"))
            label = (
                "binary_controlled_difference"
                if kind_of(z) == "B"
                else "integer_controlled_difference"
            )
        return done(label, cmp("eq", op("sub", v, u), rhs), a)
    # Two-variable on/off bounds, including exact local (1-b) gates.
    if len(terms) == 2 and (lo is None) != (hi is None):
        binary = [(v, c) for v, c in terms if kind_of(v) == "B"]
        gate = (
            binary[0]
            if len(binary) == 1
            else max(binary, key=lambda t: abs(t[1]))
            if len(binary) == 2 and abs(binary[0][1]) != abs(binary[1][1])
            else None
        )
        if gate:
            x, c = next(t for t in terms if t[0] != gate[0])
            a = c
            lower = lo / a if lo is not None else None
            upper = hi / a if hi is not None else None
            if a < 0:
                lower, upper = upper, lower
            d = upper if upper is not None else lower
            k = -gate[1] / a
            xnode = bind("value", [x])
            bnode = bind("switch", [gate[0]])
            if d == -k and k < 0:
                params["M"] = str(-k)
                rhs = op("mul", par("M"), op("sub", const(1), bnode))
                label = "complemented_activation_bound"
            elif d == 0:
                params["M"] = str(k)
                rhs = op("mul", par("M"), bnode)
                label = "activation_bound"
            else:
                params.update(k=str(k), d=str(d))
                rhs = op("add", op("mul", par("k"), bnode), par("d"))
                label = "affine_variable_bound"
            return done(label, cmp("le" if upper is not None else "ge", xnode, rhs), a)
    # A continuous quantity linked to a single integer/binary control.
    if (
        len(terms) == 2
        and len(cs) == 1
        and len(discrete) == 1
        and lo is not None
        and lo == hi
    ):
        x, a = cs[0]
        z, c = discrete[0]
        params.update(k=str(-c / a), d=str(lo / a))
        return done(
            "controlled_value",
            cmp(
                "eq",
                bind("value", [x]),
                op("add", op("mul", par("k"), bind("control", [z])), par("d")),
            ),
            a,
        )
    kinds = {kind_of(v) for v, c in terms}
    magnitudes = {abs(c) for v, c in terms}
    if terms and len(kinds) == 1 and len(magnitudes) == 1:
        a = next(iter(magnitudes))
        pos = [v for v, c in terms if c > 0]
        neg = [v for v, c in terms if c < 0]
        if pos and neg:
            if lo is not None and lo == hi:
                # Put the collection on the left for one-to-many balances.
                if len(pos) == 1 and len(neg) > 1:
                    pos, neg = neg, pos
                    a = -a
                left = bind("positive", pos)
                right = bind("negative", neg)
                params["d"] = str(lo / a)
                if len(pos) == len(neg) == 1 and lo == 0:
                    label = "variable_equality"
                elif lo == 0:
                    label = "binary_balance" if kinds == {"B"} else "signed_balance"
                else:
                    label = "affine_balance"
                return done(label, cmp("eq", left, op("add", right, par("d"))), a)
            expr = op("sub", bind("positive", pos), bind("negative", neg))
            return done(
                "difference_bound" if len(terms) == 2 else "signed_sum_bound",
                relation(
                    expr,
                    lo / a if lo is not None else None,
                    hi / a if hi is not None else None,
                    params,
                ),
                a,
            )
        a = terms[0][1]
        lo1 = lo / a if lo is not None else None
        hi1 = hi / a if hi is not None else None
        if a < 0:
            lo1, hi1 = hi1, lo1
        expr = bind("members", [v for v, c in terms])
        label = "sum_equality" if lo1 is not None and lo1 == hi1 else "sum_bound"
        if kinds == {"B"} and lo1 == hi1 == 1:
            label = "exactly_one"
        elif kinds == {"B"} and lo1 is None and hi1 == 1:
            label = "at_most_one"
        elif kinds == {"B"} and hi1 is None and lo1 == 1:
            label = "at_least_one"
        return done(label, relation(expr, lo1, hi1, params), a)
    groups = defaultdict(list)
    for v, c in terms:
        groups[(kind_of(v), c)].append(v)
    if (
        len(groups) == 2
        and kinds == {"B"}
        and lo is None
        and hi is not None
        and all(c > 0 for k, c in groups)
    ):
        largest = max(groups, key=lambda t: t[1])
        smallest = min(groups, key=lambda t: t[1])
        if len(groups[largest]) == 1:
            a = smallest[1]
            params.update(a=str(largest[1] / a), b=str(hi / a))
            expr = op(
                "add",
                op("mul", par("a"), bind("center", groups[largest])),
                bind("neighbors", groups[smallest]),
            )
            return done("center_neighborhood", cmp("le", expr, par("b")), a)
    # Bounded coefficient-role anti-unification; parameter values never enter signatures.
    if len(groups) > 8:
        return None
    a = min((abs(c) for v, c in terms), default=F(1))
    parts = []
    for i, ((kind, c), members) in enumerate(sorted(groups.items())):
        name = kind.lower() + ("_negative_" if c < 0 else "_positive_") + str(i)
        params["a" + str(i)] = str(c / a)
        parts.append(op("mul", par("a" + str(i)), bind(name, members, kind)))
    expr = const(0)
    for part in parts:
        expr = op("add", expr, part)
    return done(
        "affine_roles",
        relation(
            expr,
            lo / a if lo is not None else None,
            hi / a if hi is not None else None,
            params,
        ),
        a,
    )


def expr_value(node, params, bindings):
    if "constant" in node:
        return {}, F(node["constant"])
    if "parameter" in node:
        return {}, F(params[node["parameter"]])
    if "variable" in node or "sum" in node:
        key = node.get("variable", node.get("sum"))
        ids = bindings[key]
        if "variable" in node and len(ids) != 1:
            raise LPIRError("Scalar role is not a singleton")
        return {v: F(1) for v in ids}, F(0)
    if node.get("op") not in ("add", "sub", "mul"):
        raise LPIRError("Unsupported compressed expression")
    (a, c), (b, d) = [expr_value(x, params, bindings) for x in node["args"]]
    if node["op"] == "mul":
        if a and b:
            raise LPIRError("Nonlinear compressed expression")
        return (
            ({v: x * d for v, x in a.items()}, c * d)
            if a
            else ({v: x * c for v, x in b.items()}, c * d)
        )
    sign = -1 if node["op"] == "sub" else 1
    for v, x in b.items():
        a[v] = a.get(v, F(0)) + sign * x
    return {v: x for v, x in a.items() if x}, c + sign * d


def expand_ast(ast, params, bindings, scale):
    kind = ast["op"]
    if kind in ("eq", "le", "ge"):
        a, c = expr_value(op("sub", ast["left"], ast["right"]), params, bindings)
        lo = -c if kind != "le" else None
        hi = -c if kind != "ge" else None
    else:
        a, c = expr_value(ast["expression"], params, bindings)
        lo = (
            expr_value(ast["lower"], params, bindings)[1] - c
            if kind == "range"
            else None
        )
        hi = (
            expr_value(ast["upper"], params, bindings)[1] - c
            if kind == "range"
            else None
        )
    scale = F(scale)
    lo = lo * scale if lo is not None else None
    hi = hi * scale if hi is not None else None
    if scale < 0:
        lo, hi = hi, lo
    return {v: x * scale for v, x in a.items() if x}, lo, hi


def open_db(m):
    if not (m.path / MANIFEST).exists():
        raise LPIRError("No explanation IR; run analyze('explanations')")
    meta = json.loads((m.path / MANIFEST).read_text())
    if (
        meta["version"] != VERSION
        or meta["model_version"] != m.manifest["model_version"]
    ):
        raise LPIRError("Explanation IR version mismatch")
    db = sqlite3.connect(f"file:{(m.path / FILE).resolve()}?mode=ro", uri=True)
    db.execute("PRAGMA cache_size=-8192")
    return db


def compile_ir(m, budget=None, handle=None):
    from .jobs import Budget, BudgetExceeded

    continuation = "explanations:" + m.manifest["model_version"] + ":" + VERSION
    if handle is not None and handle != continuation:
        raise LPIRError("Foreign explanation continuation")
    if (m.path / MANIFEST).exists():
        db = open_db(m)
        db.close()
        meta = json.loads((m.path / MANIFEST).read_text())
        if sha256(m.path / FILE) != meta["sha256"]:
            raise LPIRError("Explanation IR checksum mismatch")
        return {
            **meta,
            "status": "cached",
            "version": VERSION,
            "completeness": "complete",
            "continuation": None,
        }
    path = m.path / FILE
    exists = path.exists()
    start = time.perf_counter()
    db = sqlite3.connect(path)
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA temp_store=FILE")
    if not exists:
        db.executescript("""PRAGMA temp_store=FILE; PRAGMA cache_size=-8192;
        CREATE TABLE families(id TEXT PRIMARY KEY,kind TEXT,ast TEXT,formula TEXT,roles TEXT,parameters TEXT,rows INTEGER,evidence TEXT);
        CREATE TABLE instances(row_id INTEGER PRIMARY KEY,family TEXT,scale TEXT,source_family TEXT);
        CREATE INDEX instance_family ON instances(family,row_id);
        CREATE TABLE parameters(family TEXT,row_id INTEGER,name TEXT,value TEXT,PRIMARY KEY(row_id,name));
        CREATE INDEX parameter_values ON parameters(family,name,value,row_id);
        CREATE TABLE bindings(row_id INTEGER,role TEXT,variable INTEGER,PRIMARY KEY(row_id,role,variable));
        CREATE INDEX binding_variable ON bindings(variable,row_id,role);
        CREATE TABLE role_sizes(family TEXT,row_id INTEGER,role TEXT,kind TEXT,n INTEGER);
        CREATE INDEX role_family ON role_sizes(family,role);
        CREATE TABLE residuals(row_id INTEGER PRIMARY KEY,nnz INTEGER,positive INTEGER,negative INTEGER,types TEXT,min_coefficient TEXT,max_coefficient TEXT,lower_bound TEXT,upper_bound TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        """)
        state = {
            "phase": "rows",
            "model_version": m.manifest["model_version"],
            "version": VERSION,
        }
        db.execute("INSERT INTO metadata VALUES(?,?)", ("compile_state", js(state)))
        db.commit()
    else:
        entry = db.execute(
            "SELECT value FROM metadata WHERE key='compile_state'"
        ).fetchone()
        if entry is None:
            raise LPIRError("Unrecoverable incomplete sidecar; use a fresh directory")
        state = json.loads(entry[0])
        if (
            state.get("model_version") != m.manifest["model_version"]
            or state.get("version") != VERSION
        ):
            raise LPIRError("Explanation checkpoint version mismatch")
    b = Budget(**budget) if budget is not None else None

    def tick():
        if b is not None:
            b.tick()

    def checkpoint():
        db.execute(
            "INSERT OR REPLACE INTO metadata VALUES(?,?)", ("compile_state", js(state))
        )
        db.commit()

    old = sqlite3.connect(
        f"file:{(m.path / 'analysis.sqlite').resolve()}?mode=ro", uri=True
    )
    try:
        if state["phase"] == "rows":
            _compile_rows(m, db, old, state, tick, checkpoint)
            state["phase"] = "families"
            checkpoint()
        if state["phase"] == "families":
            _finalize_families(db, state, tick)
            state["phase"] = "objective"
            checkpoint()
        if state["phase"] == "objective":
            meta = _objective_profile(m, state, tick, checkpoint)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)", ("model", js(meta))
            )
            state["phase"] = "verify"
            checkpoint()
        checked = verify(m, check_manifest=False, _state=state, _tick=tick)
        state["phase"] = "complete"
        checkpoint()
    except BudgetExceeded:
        state["seconds"] = state.get("seconds", 0) + time.perf_counter() - start
        checkpoint()
        return {
            "status": "partial",
            "completeness": "partial",
            "verification": "candidate",
            "exactness": "exact",
            "model_version": m.manifest["model_version"],
            "analysis_version": VERSION,
            "scope": "full model",
            "checked_range": {
                "phase": state["phase"],
                "rows_compiled": state.get("row_cursor", 0),
                "variables_profiled": state.get("objective_cursor", 0),
                "rows_verified": state.get("verify_cursor", 0),
                "scope_total": len(m.rows),
            },
            "total_known": False,
            "total": None,
            "handle": continuation,
            "evidence_handle": continuation,
            "exceptions": {"assessed": False},
            "continuation": continuation,
        }
    finally:
        old.close()
        db.close()
    manifest = {
        "version": VERSION,
        "model_version": m.manifest["model_version"],
        "sha256": sha256(path),
        "seconds": state.get("seconds", 0) + time.perf_counter() - start,
        "verification": checked,
        "completeness": "complete",
        "continuation": None,
        "status": "complete",
        "exactness": "exact",
        "analysis_version": VERSION,
        "scope": "full model",
        "total_known": True,
        "total": len(m.rows),
        "checked_range": {
            "phase": "complete",
            "rows_verified": len(m.rows),
            "scope_total": len(m.rows),
        },
        "handle": continuation,
        "evidence_handle": continuation,
        "exceptions": {"assessed": False},
    }
    temporary = m.path / (MANIFEST + ".tmp")
    temporary.write_text(js(manifest))
    temporary.replace(m.path / MANIFEST)
    return manifest


def _compile_rows(m, db, old, state, tick, checkpoint):
    @lru_cache(maxsize=8192)
    def kind_of(v):
        return m.variables[v][2]

    for r in range(state.get("row_cursor", 0), len(m.rows)):
        tick()
        row = m.rows[r]
        lo, hi = m.num(row[2]), m.num(row[3])
        n = int(m.row_ptr[r + 1] - m.row_ptr[r])
        terms = []
        used = 0
        if n <= 50000:
            for v, c in m.iter_terms(r):
                used += (
                    c.numerator.bit_length() + c.denominator.bit_length()
                ) // 8 + 256
                if used > 16 * 1024**2:
                    terms = None
                    break
                terms.append((v, c))
        else:
            terms = None
        temporal = old.execute(
            "SELECT source,target,switch,arc,q,M FROM temporal WHERE row_id=?", (r,)
        ).fetchone()
        result = derive(terms, lo, hi, kind_of, temporal) if terms is not None else None
        if result is None:
            params = {}
            ast = relation(
                {"op": "weighted_sum", "relation": "row_terms"}, lo, hi, params
            )
            result = ("numerical_row_reference", ast, F(1), params, {}, {})
            positive = negative = 0
            types = Counter()
            low = high = None
            for v, c in m.iter_terms(r):
                positive += c > 0
                negative += c < 0
                types[kind_of(v)] += 1
                low = c if low is None else min(low, c)
                high = c if high is None else max(high, c)
            db.execute(
                "INSERT INTO residuals VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    r,
                    n,
                    positive,
                    negative,
                    js(dict(types)),
                    str(low) if low is not None else None,
                    str(high) if high is not None else None,
                    str(lo) if lo is not None else None,
                    str(hi) if hi is not None else None,
                ),
            )
        label, ast, scale, params, bindings, roles = result
        sig = js([label, ast, roles])
        fid = "e_" + hashlib.sha256(sig.encode()).hexdigest()[:20]
        db.execute(
            "INSERT OR IGNORE INTO families VALUES(?,?,?,?,?,?,?,?)",
            (fid, label, js(ast), "", js(roles), "{}", 0, ""),
        )
        source = old.execute(
            "SELECT family FROM features WHERE row_id=?", (r,)
        ).fetchone()
        db.execute(
            "INSERT INTO instances VALUES(?,?,?,?)",
            (r, fid, str(scale), source[0] if source else None),
        )
        db.executemany(
            "INSERT INTO parameters VALUES(?,?,?,?)",
            ((fid, r, k, v) for k, v in params.items()),
        )
        for role, ids in bindings.items():
            db.execute(
                "INSERT INTO role_sizes VALUES(?,?,?,?,?)",
                (fid, r, role, roles[role], len(ids)),
            )
            db.executemany(
                "INSERT INTO bindings VALUES(?,?,?)", ((r, role, v) for v in ids)
            )
        state["row_cursor"] = r + 1
        if (r + 1) % 512 == 0:
            checkpoint()


def _finalize_families(db, state, tick):
    for fid, ast_text, label in db.execute(
        "SELECT id,ast,kind FROM families WHERE evidence='' ORDER BY id"
    ):
        tick()
        count = db.execute(
            "SELECT count(*) FROM instances WHERE family=?", (fid,)
        ).fetchone()[0]
        constants = {}
        parameter_cards = []
        for (name,) in db.execute(
            "SELECT DISTINCT name FROM parameters WHERE family=? ORDER BY name", (fid,)
        ):
            mode, frequency = db.execute(
                "SELECT value,count(*) n FROM parameters WHERE family=? AND name=? GROUP BY value ORDER BY n DESC,value LIMIT 1",
                (fid, name),
            ).fetchone()
            vals = [None, None]
            distinct = 0
            for (value,) in db.execute(
                "SELECT DISTINCT value FROM parameters WHERE family=? AND name=?",
                (fid, name),
            ):
                v = F(value)
                vals = [
                    v if vals[0] is None else min(vals[0], v),
                    v if vals[1] is None else max(vals[1], v),
                ]
                distinct += 1
            card = {
                "name": name,
                "distinct": distinct,
                "min": str(vals[0]),
                "max": str(vals[1]),
                "handle": {
                    "table": "explanation_parameters",
                    "family": fid,
                    "name": name,
                },
            }
            if distinct == 1:
                constants[name] = mode
                card["constant"] = mode
            else:
                card["frequencies"] = [
                    {"value": v, "rows": n}
                    for v, n in db.execute(
                        "SELECT value,count(*) n FROM parameters WHERE family=? AND name=? GROUP BY value ORDER BY n DESC,value LIMIT 4",
                        (fid, name),
                    )
                ]
                card["frequencies_truncated"] = distinct > 4
            if distinct > 1 and frequency / count >= 0.95:
                card.update(
                    usual=mode, usual_rows=frequency, exception_rows=count - frequency
                )
                card["exceptions_handle"] = {
                    "table": "explanation_parameters",
                    "family": fid,
                    "name": name,
                    "not_equal": mode,
                }
                card["exceptions"] = [
                    {"row": r, "value": v}
                    for r, v in db.execute(
                        "SELECT row_id,value FROM parameters WHERE family=? AND name=? AND value<>? ORDER BY row_id LIMIT 5",
                        (fid, name, mode),
                    )
                ]
                card["exceptions_truncated"] = count - frequency > 5
            parameter_cards.append(card)
        ast = simplify(json.loads(ast_text), constants)
        roles = [
            {"name": role, "kind": kind, "min_count": mn, "max_count": mx}
            for role, kind, mn, mx in db.execute(
                "SELECT role,kind,min(n),max(n) FROM role_sizes WHERE family=? GROUP BY role,kind ORDER BY role",
                (fid,),
            )
        ]
        evidence = {
            "status": "verified",
            "method": "exact expansion against complete numerical rows",
            "checked_rows": count,
        }
        if label == "numerical_row_reference":
            evidence["method"] = (
                "lossless numerical row reference; no structural equivalence claim"
            )
        db.execute(
            "UPDATE families SET ast=?,formula=?,roles=?,parameters=?,rows=?,evidence=? WHERE id=?",
            (
                js(ast),
                formula(ast),
                js(roles),
                js(parameter_cards),
                count,
                js(evidence),
                fid,
            ),
        )
        state["families_done"] = state.get("families_done", 0) + 1


def _objective_profile(m, state, tick, checkpoint):
    saved = state.get("objective_state", {})
    types = Counter(saved.get("types", {}))
    signs = Counter(saved.get("signs", {}))
    isolated = Counter(saved.get("isolated", {}))
    low = F(saved["low"]) if saved.get("low") is not None else None
    high = F(saved["high"]) if saved.get("high") is not None else None
    cost = F(saved["cost"]) if saved.get("cost") is not None else None
    uniform = saved.get("uniform", True)
    nonzero = saved.get("nonzero", 0)
    for ident in range(state.get("objective_cursor", 0), len(m.variables)):
        tick()
        v = m.variables[ident]
        types[v[2]] += 1
        c = m.num(v[5])
        signs["positive" if c > 0 else "negative" if c < 0 else "zero"] += 1
        low = c if low is None else min(low, c)
        high = c if high is None else max(high, c)
        if cost is None:
            cost = c
        uniform &= c == cost
        nonzero += bool(c)
        if m.col_ptr[v[0]] == m.col_ptr[v[0] + 1]:
            isolated[v[2]] += 1
        state["objective_cursor"] = ident + 1
        state["objective_state"] = {
            "types": types,
            "signs": signs,
            "isolated": isolated,
            "low": str(low),
            "high": str(high),
            "cost": str(cost),
            "uniform": uniform,
            "nonzero": nonzero,
        }
        if (ident + 1) % 512 == 0:
            checkpoint()
    objective = {
        "sense": m.manifest["sense"],
        "constant": str(m.num(m.manifest["objective_constant"])),
        "nonzeros": nonzero,
        "signs": dict(signs),
        "min": str(low) if low is not None else None,
        "max": str(high) if high is not None else None,
        "interpretation": "linear_objective",
        "handle": {"table": "variables"},
    }
    if uniform and cost and types.get("B") == len(m.variables):
        objective["interpretation"] = (
            "maximum_cardinality"
            if (cost < 0) == (m.manifest["sense"] == "min")
            else "minimum_cardinality"
        )
        objective["mapping"] = {
            "original_sense": m.manifest["sense"],
            "uniform_cost": str(cost),
            "constant": objective["constant"],
        }
    meta = {
        "model_version": m.manifest["model_version"],
        "explanation_version": VERSION,
        "variables": len(m.variables),
        "rows": len(m.rows),
        "nonzeros": len(m.col_idx),
        "variable_types": dict(types),
        "isolated_variables_by_type": dict(isolated),
        "objective": objective,
    }
    return meta


def verify(m, check_manifest=True, _state=None, _tick=None):
    path = m.path / FILE
    if check_manifest:
        meta = json.loads((m.path / MANIFEST).read_text())
        if (
            meta["model_version"] != m.manifest["model_version"]
            or sha256(path) != meta["sha256"]
        ):
            raise LPIRError("Explanation IR checksum/version mismatch")
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    state = {} if _state is None else _state
    checked = state.get("expanded_rows", 0)
    refs = state.get("reference_rows", 0)

    @lru_cache(maxsize=128)
    def definition(fid):
        return db.execute(
            "SELECT ast,formula FROM families WHERE id=?", (fid,)
        ).fetchone()

    try:
        for r, fid, scale in db.execute(
            "SELECT row_id,family,scale FROM instances WHERE row_id>=? ORDER BY row_id",
            (state.get("verify_cursor", 0),),
        ):
            if _tick is not None:
                _tick()
            if r != checked + refs:
                raise LPIRError("Explanation row coverage/order mismatch")
            text, printed = definition(fid)
            ast = json.loads(text)
            if formula(ast) != printed:
                raise LPIRError(f"Compressed formula text disagrees with AST: {fid}")
            if (
                ast.get("expression", {}).get("op") == "weighted_sum"
                or ast.get("left", {}).get("op") == "weighted_sum"
            ):
                params = dict(
                    db.execute("SELECT name,value FROM parameters WHERE row_id=?", (r,))
                )
                expected = relation(
                    {"op": "weighted_sum", "relation": "row_terms"},
                    m.num(m.rows[r][2]),
                    m.num(m.rows[r][3]),
                    {},
                )
                if F(scale) != 1 or simplify(ast, params) != simplify(
                    expected,
                    {
                        "d": str(m.num(m.rows[r][2])),
                        "b": str(
                            m.num(m.rows[r][3])
                            if m.num(m.rows[r][3]) is not None
                            else m.num(m.rows[r][2])
                        ),
                        "lower": str(m.num(m.rows[r][2])),
                        "upper": str(m.num(m.rows[r][3])),
                    },
                ):
                    raise LPIRError(f"Numerical reference bounds mismatch at row {r}")
                positive = negative = 0
                types = Counter()
                low = high = None
                for v, c in m.iter_terms(r):
                    positive += c > 0
                    negative += c < 0
                    types[m.variables[v][2]] += 1
                    low = c if low is None else min(low, c)
                    high = c if high is None else max(high, c)
                stats = (
                    int(m.row_ptr[r + 1] - m.row_ptr[r]),
                    positive,
                    negative,
                    js(dict(types)),
                    str(low) if low is not None else None,
                    str(high) if high is not None else None,
                    str(m.num(m.rows[r][2])) if m.rows[r][2] is not None else None,
                    str(m.num(m.rows[r][3])) if m.rows[r][3] is not None else None,
                )
                stored = db.execute(
                    "SELECT nnz,positive,negative,types,min_coefficient,max_coefficient,lower_bound,upper_bound FROM residuals WHERE row_id=?",
                    (r,),
                ).fetchone()
                if stats != stored:
                    raise LPIRError(
                        f"Numerical reference statistics mismatch at row {r}"
                    )
                refs += 1
                state.update(verify_cursor=r + 1, reference_rows=refs)
                continue
            params = dict(
                db.execute("SELECT name,value FROM parameters WHERE row_id=?", (r,))
            )
            bindings = defaultdict(list)
            for role, v in db.execute(
                "SELECT role,variable FROM bindings WHERE row_id=? ORDER BY variable",
                (r,),
            ):
                bindings[role].append(v)
            a, lo, hi = expand_ast(ast, params, bindings, scale)
            original = m.rows[r]
            if a != m.row(r) or lo != m.num(original[2]) or hi != m.num(original[3]):
                raise LPIRError(f"Compressed formula mismatch at row {r}: {fid}")
            checked += 1
            state.update(verify_cursor=r + 1, expanded_rows=checked)
        count = db.execute("SELECT count(*) FROM instances").fetchone()[0]
        if count != len(m.rows):
            raise LPIRError("Explanation row coverage mismatch")
    finally:
        db.close()
    return {
        "status": "PASS",
        "expanded_rows": checked,
        "numerical_reference_rows": refs,
        "rows": count,
    }


def validate_row(m, r, ast=None):
    """Expand a stored or proposed formula with the persisted row bindings."""
    if not 0 <= r < len(m.rows):
        raise LPIRError("Invalid explanation row")
    db = open_db(m)
    try:
        row = db.execute(
            "SELECT f.ast,i.scale FROM instances i JOIN families f ON f.id=i.family WHERE row_id=?",
            (r,),
        ).fetchone()
        tree = ast if ast is not None else json.loads(row[0])
        params = dict(
            db.execute("SELECT name,value FROM parameters WHERE row_id=?", (r,))
        )
        bindings = defaultdict(list)
        for role, v in db.execute(
            "SELECT role,variable FROM bindings WHERE row_id=? ORDER BY variable", (r,)
        ):
            bindings[role].append(v)
        if (
            tree.get("expression", {}).get("op") == "weighted_sum"
            or tree.get("left", {}).get("op") == "weighted_sum"
        ):
            expected_params = {}
            expected = relation(
                {"op": "weighted_sum", "relation": "row_terms"},
                m.num(m.rows[r][2]),
                m.num(m.rows[r][3]),
                expected_params,
            )
            return F(row[1]) == 1 and simplify(tree, params) == simplify(
                expected, expected_params
            )
        a, lo, hi = expand_ast(tree, params, bindings, row[1])
        return a == m.row(r) and lo == m.num(m.rows[r][2]) and hi == m.num(m.rows[r][3])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise LPIRError(f"Invalid explanation AST or binding: {e}") from e
    finally:
        db.close()


def card(db, fid):
    r = db.execute(
        "SELECT kind,formula,roles,parameters,rows,evidence FROM families WHERE id=?",
        (fid,),
    ).fetchone()
    if r is None:
        raise LPIRError("Unknown explanation family")
    return {
        "id": fid,
        "kind": r[0],
        "rows": r[4],
        "formula": r[1],
        "roles": json.loads(r[2]),
        "parameters": json.loads(r[3]),
        "evidence": json.loads(r[5]),
        "ast_handle": {"table": "explanation_families", "id": fid},
        "instances_handle": {"table": "explanation_rows", "family": fid},
        "bindings_handle": {"table": "explanation_bindings", "family": fid},
    }


def inspect(m, key=None, offset=0, limit=20):
    db = open_db(m)
    if key:
        result = card(db, key)
        result["ast"] = json.loads(
            db.execute("SELECT ast FROM families WHERE id=?", (key,)).fetchone()[0]
        )
        items = [result]
        total = 1
    else:
        total = db.execute("SELECT count(*) FROM families").fetchone()[0]
        items = [
            card(db, fid)
            for (fid,) in db.execute(
                "SELECT id FROM families ORDER BY rows DESC,id LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]
    db.close()
    return {
        "model_version": m.manifest["model_version"],
        "analysis_version": VERSION,
        "scope": "explanations",
        "exactness": "exact",
        "verification": "verified",
        "completeness": "complete",
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(items) < total,
        "items": items,
    }


def render_document(doc):
    """Human Markdown projection; complete machine data remains in describe()."""
    from .markdown import render

    return render(doc)


def document(m, offset=0, limit=20, max_bytes=20000, raw_ids=False):
    if offset < 0 or not 1 <= limit <= 1000 or max_bytes < 2000:
        raise LPIRError("Invalid explanation document budget")
    db = open_db(m)
    meta = json.loads(
        db.execute("SELECT value FROM metadata WHERE key='model'").fetchone()[0]
    )
    count = db.execute("SELECT count(*) FROM families").fetchone()[0]
    residual_count = db.execute("SELECT count(*) FROM residuals").fetchone()[0]
    residuals = [
        {
            "row": r,
            "terms": n,
            "positive": p,
            "negative": q,
            "types": json.loads(t),
            "coefficient_min": a,
            "coefficient_max": b,
            "lower": lo,
            "upper": hi,
            "handle": {"table": "terms", "row_id": r},
        }
        for r, n, p, q, t, a, b, lo, hi in db.execute(
            "SELECT * FROM residuals ORDER BY nnz DESC,row_id LIMIT 3"
        )
    ]
    doc = {
        **meta,
        "scope": "all numerical rows; formula-family page",
        "coverage": {
            "total_families": count,
            "numerical_reference_rows": residual_count,
        },
        "large_or_unstructured_rows": residuals,
        "residuals_handle": {"table": "explanation_residuals"},
        "page": {
            "offset": offset,
            "limit": limit,
            "shown": 0,
            "next_offset": None,
            "truncated": False,
        },
        "families": [],
    }
    from .indexed import summary as indexed_summary

    from .structural import summary as structure_summary

    structured = structure_summary(m, max_bytes=min(12000, max_bytes * 3 // 4))
    if structured is not None and (structured["families"] or structured.get("objective_tiles")):
        doc["structure_views"] = structured
        fully_covered = structured["covered_rows"] == structured["scope_rows"]
        doc["presentation"] = {
            "primary": "structure",
            "show_indexed_diagnostics": structured["covered_rows"] == 0,
            "show_local_diagnostics": not (fully_covered or structured["covered_rows"]),
        }
    # Preserve the existing machine interface; complete structural coverage only
    # changes which view is primary in the human renderer.
    indexed = indexed_summary(m, max_bytes=min(6500, max_bytes // 2))
    if indexed is not None:
        doc["indexed_views"] = indexed
    from .references import translate

    if not raw_ids:
        doc = translate(m, doc)
        doc["reference_scope"] = (
            "artifact-local; short references are accepted by inspect/query/validate"
        )
    # Any selection happens in the IR document, never inside the Markdown renderer.
    for (fid,) in db.execute(
        "SELECT id FROM families ORDER BY rows DESC,id LIMIT ? OFFSET ?",
        (limit, offset),
    ):
        item = card(db, fid)
        if not raw_ids:
            item = translate(m, item)
        doc["families"].append(item)
        doc["page"].update(
            shown=len(doc["families"]),
            next_offset=offset + len(doc["families"]),
            truncated=True,
        )
        if len(render_document(doc).encode()) > max_bytes:
            doc["families"].pop()
            break
    shown = len(doc["families"])
    doc["page"].update(
        shown=shown,
        next_offset=offset + shown if offset + shown < count else None,
        truncated=offset + shown < count,
    )
    if not shown and offset < count:
        doc["page"]["reason"] = (
            "Next family exceeds document byte budget; inspect explanation or increase max_bytes"
        )
        doc["page"]["next_offset"] = offset
    db.close()
    if len(render_document(doc).encode()) > max_bytes:
        raise LPIRError("Document metadata exceeds byte budget; increase max_bytes")
    return doc
