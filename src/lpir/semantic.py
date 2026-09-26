"""Exact, overlapping semantic views. Formula memberships never gate reconstruction."""

from collections import defaultdict
from fractions import Fraction as F
import hashlib, json, sqlite3, math, sys
from .numbers import LPIRError

VERSION = "2.0"


def text(x):
    return None if x is None else str(x)


def connect(m, readonly=False):
    path = m.path / "analysis.sqlite"
    db = (
        sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        if readonly
        else sqlite3.connect(path)
    )
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA temp_store=FILE")
    return db


def classification(terms, lo, hi):
    n = len(terms)
    types = {t[2] for t in terms}
    cs = [t[1] for t in terms]
    tags = ["general_linear"]
    eq = lo is not None and lo == hi
    side = hi if hi is not None else lo
    if not n:
        tags += ["empty"]
    elif lo is None and hi is None:
        tags += ["free"]
    elif n == 1:
        tags += ["singleton"]
    else:
        if types == {"B"}:
            tags += ["binary_linear"]
            if all(c == 1 for c in cs):
                if eq and lo == 1:
                    tags += ["set_partitioning"]
                elif lo is None and hi == 1:
                    tags += ["set_packing"]
                elif hi is None and lo == 1:
                    tags += ["set_covering"]
                elif eq and lo.denominator == 1:
                    tags += ["cardinality"]
                elif lo is None and hi is not None and hi.denominator == 1:
                    tags += ["invariant_knapsack"]
            if (
                all(c > 0 and c.denominator == 1 for c in cs)
                and side is not None
                and side.denominator == 1
            ):
                tags += ["equation_knapsack" if eq else "knapsack"]
        elif types <= {"B", "I"}:
            tags += ["integer_linear"]
        elif "B" in types:
            tags += ["mixed_binary"]
        if n == 2:
            if eq:
                tags += ["aggregation"]
            if cs[0] == -cs[1]:
                tags += ["difference"]
            if "B" in types and not eq:
                tags += ["variable_bound"]
        if (
            types <= {"B", "I"}
            and all(c > 0 and c.denominator == 1 for c in cs)
            and lo is None
            and hi is not None
            and hi.denominator == 1
        ):
            tags += ["integer_knapsack"]
    priority = [
        "empty",
        "free",
        "singleton",
        "set_partitioning",
        "set_packing",
        "set_covering",
        "cardinality",
        "invariant_knapsack",
        "aggregation",
        "difference",
        "variable_bound",
        "equation_knapsack",
        "knapsack",
        "integer_knapsack",
        "mixed_binary",
        "binary_linear",
        "integer_linear",
        "general_linear",
    ]
    return next(t for t in priority if t in tags), tags


def discover(m, memory_mb=128):
    db = connect(m)
    db.executescript("""
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE features(row_id INTEGER PRIMARY KEY,class TEXT,tags TEXT,nnz INTEGER,sense TEXT,lower TEXT,upper TEXT,scale TEXT,shift TEXT,family TEXT);
    CREATE TABLE families(id TEXT PRIMARY KEY,kind TEXT,formula TEXT,ast TEXT);
    CREATE TABLE roles(row_id INTEGER,role TEXT,coefficient TEXT,kind TEXT,count INTEGER,PRIMARY KEY(row_id,role));
    CREATE TABLE bindings(row_id INTEGER,role TEXT,variable INTEGER,complement INTEGER,PRIMARY KEY(row_id,variable));
    CREATE INDEX binding_variable ON bindings(variable,row_id);
    CREATE TABLE facts(id INTEGER PRIMARY KEY,kind TEXT,row_id INTEGER,variable INTEGER,lower TEXT,upper TEXT,source TEXT);
    CREATE TABLE runs(id TEXT PRIMARY KEY,kind TEXT,spec TEXT,status TEXT,cursor INTEGER,processed INTEGER,summary TEXT);
    CREATE TABLE results(run TEXT,ordinal INTEGER,record TEXT,PRIMARY KEY(run,ordinal));
    CREATE TABLE temporal(row_id INTEGER PRIMARY KEY,source INTEGER,target INTEGER,switch INTEGER,arc INTEGER,q TEXT,M TEXT,assignment TEXT);
    CREATE TABLE time_bounds(variable INTEGER PRIMARY KEY,lower TEXT,upper TEXT);
    CREATE TABLE assignment_members(block TEXT,side INTEGER,idx INTEGER,row_id INTEGER,PRIMARY KEY(block,side,idx));
    CREATE TABLE assignment_edges(block TEXT,variable INTEGER,left_idx INTEGER,right_idx INTEGER,PRIMARY KEY(block,variable));
    CREATE TABLE graphs(row_id INTEGER PRIMARY KEY,center INTEGER,coefficient TEXT,neighbor_coefficient TEXT,upper TEXT);
    CREATE TABLE edges(source INTEGER,target INTEGER,PRIMARY KEY(source,target));
    CREATE TABLE omitted(row_id INTEGER PRIMARY KEY,reason TEXT);
    """)
    db.execute("INSERT INTO metadata VALUES(?,?)", ("version", VERSION))
    for v in m.variables:
        lo, hi = m.num(v[3]), m.num(v[4])
        if lo is not None and lo == hi:
            db.execute(
                "INSERT INTO facts(kind,variable,lower,upper,source) VALUES(?,?,?,?,?)",
                ("fixed_variable", v[0], text(lo), text(hi), "variable_domain"),
            )
    for meta in m.rows:
        r = meta[0]
        nnz = int(m.row_ptr[r + 1] - m.row_ptr[r])
        lo, hi = m.num(meta[2]), m.num(meta[3])
        # Account for rational bit sizes as well as term count, before retaining a row.
        row_budget = int(memory_mb * 1024**2 / 8)
        original = []
        used = 0
        omitted_reason = None
        if nnz > 50000 or nnz * 192 > row_budget:
            omitted_reason = "row exceeds discovery working-set budget"
        else:
            for v, c in m.iter_terms(r):
                used += sys.getsizeof(c.numerator) + sys.getsizeof(c.denominator) + 192
                if used > row_budget:
                    omitted_reason = "exact coefficient bit sizes exceed discovery working-set budget"
                    break
                original.append((v, c))
        if omitted_reason:
            db.execute("INSERT INTO omitted VALUES(?,?)", (r, omitted_reason))
            db.execute(
                "INSERT INTO features VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    r,
                    "general_linear",
                    '["general_linear"]',
                    nnz,
                    "range",
                    text(lo),
                    text(hi),
                    "1",
                    "0",
                    None,
                ),
            )
            continue
        kinds = {}
        for v, c in original:
            var = m.variables[v]
            kinds[v] = var[2]
            if (
                var[2] == "I"
                and var[3] is not None
                and var[4] is not None
                and m.num(var[3]) >= 0
                and m.num(var[4]) <= 1
            ):
                kinds[v] = "B"
        denominator = 1
        for v, c in original:
            denominator = math.lcm(denominator, c.denominator)
        numerator = 0
        for v, c in original:
            numerator = math.gcd(
                numerator, abs(c.numerator) * (denominator // c.denominator)
            )
        scale = F(numerator, denominator) if numerator else F(1)
        if original and all(c < 0 for v, c in original):
            scale = -scale
        a = [(v, c / scale, kinds[v]) for v, c in original]
        lower = lo / scale if lo is not None else None
        upper = hi / scale if hi is not None else None
        if scale < 0:
            lower, upper = upper, lower
        shift = F(0)
        normalized = []
        for v, c, k in a:
            comp = k == "B" and c < 0
            if comp:
                shift -= c
                c = -c
            normalized.append((v, c, k, int(comp)))
        lower = lower + shift if lower is not None else None
        upper = upper + shift if upper is not None else None
        # Exactly-one false is the same literal family as exactly-one true.
        if (
            normalized
            and lower == upper
            and lower == len(normalized) - 1
            and lower != 1
            and all(c == 1 and k == "B" for v, c, k, b in normalized)
        ):
            normalized = [(v, c, k, 1 - comp) for v, c, k, comp in normalized]
            lower = upper = F(1)
            scale = -scale
            shift = F(len(normalized)) - shift
        cls, tags = classification(
            [(v, c, k) for v, c, k, b in normalized], lower, upper
        )
        sense = (
            "="
            if lower is not None and lower == upper
            else "<="
            if lower is None
            else ">="
            if upper is None
            else "range"
        )
        groups = defaultdict(list)
        for v, c, k, b in normalized:
            groups[k, c].append((v, b))
        ordered = sorted(groups, key=lambda p: (p[0], p[1]))
        center = None
        if (
            len(ordered) == 2
            and all(k == "B" for k, c in ordered)
            and upper is not None
            and lower is None
        ):
            candidates = [
                key
                for key in ordered
                if len(groups[key]) == 1 and key[1] > min(c for k, c in ordered)
            ]
            if len(candidates) == 1 and not any(
                b for g in groups.values() for v, b in g
            ):
                center = candidates[0]
        roles = []
        for i, key in enumerate(ordered):
            role = "center" if key == center else "neighbors" if center else f"g{i}"
            roles.append((role, key))
        kind = "center_neighborhood" if center else cls
        signature = (kind, sense, [(role, k) for role, (k, c) in roles])
        fid = "f_" + hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:20]
        if center:
            formula = "a[r]*x[center[r]] + b[r]*Sum(x[v], v in N[r]) <= upper[r]"
        elif cls == "set_partitioning":
            formula = "Sum(literal[v], v in S[r]) = 1"
        elif cls == "set_packing":
            formula = "Sum(literal[v], v in S[r]) <= 1"
        elif cls == "set_covering":
            formula = "Sum(literal[v], v in S[r]) >= 1"
        else:
            formula = "lower[r] <= Sum(coefficient[r,g] * Sum(literal[v], v in binding[r,g]), g in roles) <= upper[r]"
        ast = {
            "op": "range",
            "lower": {"parameter": "lower"},
            "upper": {"parameter": "upper"},
            "expression": {
                "op": "add",
                "args": [
                    {
                        "op": "mul",
                        "args": [
                            {"parameter": role},
                            {"op": "sum", "relation": role, "value": {"op": "literal"}},
                        ],
                    }
                    for role, key in roles
                ],
            },
        }
        # Keep generated sums within the affine interpreter's fan-out limit,
        # even when a row has thousands of distinct coefficient roles.
        terms = ast["expression"]["args"]
        while len(terms) > 1000:
            terms = [
                {"op": "add", "args": terms[i : i + 1000]}
                for i in range(0, len(terms), 1000)
            ]
        ast["expression"]["args"] = terms
        db.execute(
            "INSERT OR IGNORE INTO families VALUES(?,?,?,?)",
            (fid, kind, formula, json.dumps(ast)),
        )
        db.execute(
            "INSERT INTO features VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                r,
                cls,
                json.dumps(tags),
                nnz,
                sense,
                text(lower),
                text(upper),
                text(scale),
                text(shift),
                fid,
            ),
        )
        for role, key in roles:
            db.execute(
                "INSERT INTO roles VALUES(?,?,?,?,?)",
                (r, role, text(key[1]), key[0], len(groups[key])),
            )
            db.executemany(
                "INSERT INTO bindings VALUES(?,?,?,?)",
                ((r, role, v, b) for v, b in groups[key]),
            )
        if center:
            other = next(key for key in ordered if key != center)
            cv = groups[center][0][0]
            db.execute(
                "INSERT INTO graphs VALUES(?,?,?,?,?)",
                (r, cv, text(center[1]), text(other[1]), text(upper)),
            )
            db.executemany(
                "INSERT OR IGNORE INTO edges VALUES(?,?)",
                ((cv, v) for v, b in groups[other]),
            )
        if nnz == 1:
            v, c = original[0]
            l = lo / c if lo is not None else None
            u = hi / c if hi is not None else None
            if c < 0:
                l, u = u, l
            db.execute(
                "INSERT INTO facts(kind,row_id,variable,lower,upper,source) VALUES(?,?,?,?,?,?)",
                ("singleton_bound", r, v, text(l), text(u), "row"),
            )
        # This recognizer is a consumer of numerical roles, not a separate storage model.
        if nnz == 4 and (
            (hi is None and lo is not None) or (lo is None and hi is not None)
        ):
            cont = [(v, c) for v, c in original if kinds[v] == "C"]
            if len(cont) == 2 and abs(cont[0][1]) == abs(cont[1][1]):
                anchor = abs(cont[0][1]) * (1 if hi is None else -1)
                oriented = [(v, c / anchor) for v, c in original]
                rhs = (lo if hi is None else hi) / anchor
                neg = [v for v, c in oriented if kinds[v] == "C" and c == -1]
                pos = [v for v, c in oriented if kinds[v] == "C" and c == 1]
                qs = [(v, c) for v, c in oriented if kinds[v] == "B" and c > 0]
                ms = [(v, c) for v, c in oriented if kinds[v] == "B" and c < 0]
                if (
                    len(neg) == len(pos) == len(qs) == len(ms) == 1
                    and rhs == qs[0][1] + ms[0][1]
                ):
                    db.execute(
                        "INSERT INTO temporal VALUES(?,?,?,?,?,?,?,?)",
                        (
                            r,
                            neg[0],
                            pos[0],
                            qs[0][0],
                            ms[0][0],
                            text(qs[0][1]),
                            text(-ms[0][1]),
                            None,
                        ),
                    )
    # Restrict time domains to variables actually occurring in verified difference rows.
    for (v,) in db.execute(
        "SELECT source FROM temporal UNION SELECT target FROM temporal"
    ):
        var = m.variables[v]
        lo, hi = m.num(var[3]), m.num(var[4])
        for a, b in db.execute(
            "SELECT lower,upper FROM facts WHERE kind='singleton_bound' AND variable=?",
            (v,),
        ):
            if a is not None:
                lo = max(lo, F(a)) if lo is not None else F(a)
            if b is not None:
                hi = min(hi, F(b)) if hi is not None else F(b)
        db.execute("INSERT INTO time_bounds VALUES(?,?,?)", (v, text(lo), text(hi)))
    db.execute("CREATE INDEX feature_family ON features(family,row_id)")
    db.execute("CREATE INDEX role_coefficient ON roles(coefficient,row_id)")
    db.execute("CREATE INDEX graph_center ON graphs(center)")
    db.commit()
    db.close()


def verify_views(m):
    db = connect(m, True)
    count = 0
    nnz = 0
    for r, cls, tags, n, sense, lo, hi, scale, shift, fid in db.execute(
        "SELECT * FROM features ORDER BY row_id"
    ):
        if fid is None:
            continue
        scale = F(scale)
        shift = F(shift)
        terms = {}
        offset = F(0)
        for v, comp, c in db.execute(
            "SELECT b.variable,b.complement,p.coefficient FROM bindings b JOIN roles p ON b.row_id=p.row_id AND b.role=p.role WHERE b.row_id=?",
            (r,),
        ):
            a = F(c)
            terms[v] = (-a if comp else a) * scale
            if comp:
                offset += a
        if offset != shift:
            raise LPIRError(f"View offset mismatch at row {r}")
        lower = (F(lo) - shift) * scale if lo is not None else None
        upper = (F(hi) - shift) * scale if hi is not None else None
        if scale < 0:
            lower, upper = upper, lower
        meta = m.rows[r]
        if terms != m.row(r) or lower != m.num(meta[2]) or upper != m.num(meta[3]):
            raise LPIRError(f"View expansion mismatch at row {r}")
        count += 1
        nnz += len(terms)
    if db.execute("SELECT count(*) FROM features").fetchone()[0] != len(m.rows):
        raise LPIRError("Missing row classification")
    omitted = db.execute("SELECT count(*) FROM omitted").fetchone()[0]
    db.close()
    return {
        "views_verified": count,
        "view_nonzeros_verified": nnz,
        "omitted_rows": omitted,
    }


IMMUTABLE_TABLES = (
    "metadata",
    "features",
    "families",
    "roles",
    "bindings",
    "facts",
    "temporal",
    "time_bounds",
    "graphs",
    "edges",
    "omitted",
)


def view_digests(m):
    db = connect(m, True)
    result = {}
    for table in IMMUTABLE_TABLES:
        h = hashlib.sha256()
        # Stable rowid order; mutable analyses live in separate tables.
        for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            h.update(json.dumps(row, separators=(",", ":")).encode())
            h.update(b"\n")
        result[table] = h.hexdigest()
    db.close()
    return result
