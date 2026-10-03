"""Integer support discovery; coefficients are not involved in role candidates."""

import json
from mipframe.ir.indexing.store import ident
from mipframe.ir.indexing.store import js


def evidence(s, run, reason, **fields):
    rec = dict(reason=reason, **fields)
    key = ident("e_", rec)
    s.put(run, "evidence", key, dict(id=key, **rec))


def candidate(s, run, rg, vg, origin):
    if not s.size(rg) or not s.size(vg):
        return
    old = s.db.execute(
        "SELECT g,record FROM candidates WHERE run=? AND rg=? AND vg=?", (run, rg, vg)
    ).fetchone()
    if old:
        g = old[0]
        origins = json.loads(old[1])
        if origin not in origins:
            origins.append(origin)
            s.db.execute("UPDATE candidates SET record=? WHERE g=?", (js(origins), g))
    else:
        g = s.db.execute(
            "INSERT INTO candidates(run,rg,vg,record) VALUES(?,?,?,?)",
            (run, rg, vg, js([origin])),
        ).lastrowid
    return g


def prepare(s, run, rows, variables, result):
    m = s.owner.model
    rt = s.table("id INTEGER PRIMARY KEY")
    vt = s.table("id INTEGER PRIMARY KEY")
    s.db.executemany(f"INSERT OR IGNORE INTO {rt} VALUES(?)", ((int(i),) for i in rows))
    s.db.executemany(
        f"INSERT OR IGNORE INTO {vt} VALUES(?)", ((int(i),) for i in variables)
    )
    rg = s.domain("row", f"SELECT id FROM {rt}")
    vg = s.domain("var", f"SELECT id FROM {vt}")
    edges = s.table("r INTEGER,v INTEGER,pos INTEGER,PRIMARY KEY(r,v)")
    # The sparse position is preserved for deferred numeric decoding.
    block = max(1, s.memory // 256)
    for (r,) in s.db.execute(f"SELECT id FROM {rt} ORDER BY id"):
        for start in range(int(m.row_ptr[r]), int(m.row_ptr[r + 1]), block):
            stop = min(start + block, int(m.row_ptr[r + 1]))
            s.db.executemany(
                f"INSERT INTO {edges} SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM {vt} WHERE id=?)",
                (
                    (r, int(m.col_idx[p]), p, int(m.col_idx[p]))
                    for p in range(start, stop)
                ),
            )
    s.db.execute(f"CREATE INDEX {edges}_v ON {edges}(v,r)")
    rowgroups = s.table("g INTEGER PRIMARY KEY,origins TEXT")
    vargroups = s.table("g INTEGER PRIMARY KEY,origins TEXT")

    def add_group(table, g, origin):
        old = s.db.execute(f"SELECT origins FROM {table} WHERE g=?", (g,)).fetchone()
        values = json.loads(old[0]) if old else []
        if origin not in values:
            values.append(origin)
        s.db.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (g, js(values)))

    for axis, ids, groups in [("row", rt, rowgroups), ("var", vt, vargroups)]:
        color_table = s.table(
            "depth INTEGER,color INTEGER,id INTEGER,PRIMARY KEY(depth,color,id)"
        )
        for depth in range(result.depth + 1):
            colors = result._store.array(result._config, axis, depth)
            s.db.executemany(
                f"INSERT INTO {color_table} VALUES(?,?,?)",
                (
                    (depth, int(colors[i]), i)
                    for (i,) in s.db.execute(f"SELECT id FROM {ids}")
                ),
            )
        for depth, color in s.db.execute(
            f"SELECT DISTINCT depth,color FROM {color_table} ORDER BY depth,color"
        ):
            domain = s.domain(
                axis,
                f"SELECT id FROM {color_table} WHERE depth=? AND color=?",
                (depth, color),
            )
            add_group(
                groups,
                domain,
                dict(kind="signature", axis=axis, depth=depth, color=color),
            )
        s.discard(color_table)
    # Coarse row directions, and homogeneous type / unit-sum seeds.
    coarse = s.table("r INTEGER PRIMARY KEY,sense TEXT,unit TEXT,typ TEXT")
    kinds_table = s.table("id INTEGER PRIMARY KEY,kind TEXT")
    s.db.executemany(
        f"INSERT INTO {kinds_table} VALUES(?,?)",
        m.db.execute("SELECT id,kind FROM variables"),
    )
    for (r,) in s.db.execute(f"SELECT id FROM {rt}"):
        bounds = m.db.execute("SELECT lo,hi FROM rows WHERE id=?", (r,)).fetchone()
        lo, hi = m.num(bounds[0]), m.num(bounds[1])
        sense = (
            "eq"
            if lo == hi
            else "le"
            if lo is None
            else "ge"
            if hi is None
            else "range"
        )
        kinds = [
            x[0]
            for x in s.db.execute(
                f"SELECT DISTINCT k.kind FROM {edges} e JOIN {kinds_table} k ON k.id=e.v WHERE e.r=?",
                (r,),
            )
        ]
        typ = kinds[0] if len(kinds) == 1 else None
        n = s.db.execute(f"SELECT count(*) FROM {edges} WHERE r=?", (r,)).fetchone()[0]
        first = None
        uniform = True
        first_id = None
        if typ and n == int(m.row_ptr[r + 1] - m.row_ptr[r]):
            for (pos,) in s.db.execute(f"SELECT pos FROM {edges} WHERE r=?", (r,)):
                cid = int(m.coeff_id[pos])
                if first_id is None:
                    first_id = cid
                elif first_id != cid:
                    uniform = False
                    break
            if uniform and first_id is not None:
                first = m.num(first_id)
        else:
            uniform = False
        unit = None
        if typ and uniform and n == int(m.row_ptr[r + 1] - m.row_ptr[r]):
            low, high = (lo, hi) if first > 0 else (hi, lo)
            unit = js(
                [
                    typ,
                    "eq"
                    if low == high
                    else "le"
                    if low is None
                    else "ge"
                    if high is None
                    else "range",
                    "unit" if low == high == first else "other",
                ]
            )
        s.db.execute(f"INSERT INTO {coarse} VALUES(?,?,?,?)", (r, sense, unit, typ))
    for (sense,) in s.db.execute(f"SELECT DISTINCT sense FROM {coarse}"):
        d = s.domain("row", f"SELECT r FROM {coarse} WHERE sense=?", (sense,))
        add_group(rowgroups, d, dict(kind="coarse_direction", sense=sense))
    # Group candidate domains by actual adjacency, not a Cartesian list of colors.
    rm = s.table("g INTEGER,id INTEGER,PRIMARY KEY(g,id)")
    vm = s.table("g INTEGER,id INTEGER,PRIMARY KEY(g,id)")
    s.db.execute(
        f"INSERT INTO {rm} SELECT d.g,d.id FROM domain_members d JOIN {rowgroups} g ON g.g=d.g"
    )
    s.db.execute(
        f"INSERT INTO {vm} SELECT d.g,d.id FROM domain_members d JOIN {vargroups} g ON g.g=d.g"
    )
    s.db.execute(f"CREATE INDEX {rm}_id ON {rm}(id,g)")
    s.db.execute(f"CREATE INDEX {vm}_id ON {vm}(id,g)")
    for rdom, vdom in s.db.execute(
        f"SELECT DISTINCT rr.g,vv.g FROM {edges} e JOIN {rm} rr ON rr.id=e.r JOIN {vm} vv ON vv.id=e.v ORDER BY rr.g,vv.g"
    ):
        origins = lambda table, g: json.loads(
            s.db.execute(f"SELECT origins FROM {table} WHERE g=?", (g,)).fetchone()[0]
        )
        candidate(
            s,
            run,
            rdom,
            vdom,
            dict(
                kind="roles",
                rows=origins(rowgroups, rdom),
                vars=origins(vargroups, vdom),
            ),
        )
    for typ, sense in s.db.execute(
        f"SELECT DISTINCT typ,sense FROM {coarse} WHERE typ IS NOT NULL"
    ):
        d = s.domain(
            "row", f"SELECT r FROM {coarse} WHERE typ=? AND sense=?", (typ, sense)
        )
        u = s.domain(
            "var",
            f"SELECT e.v FROM {edges} e JOIN domain_members d ON d.id=e.r WHERE d.g=?",
            (d,),
        )
        candidate(s, run, d, u, dict(kind="homogeneous_type", type=typ, sense=sense))
    for (unit,) in s.db.execute(
        f"SELECT DISTINCT unit FROM {coarse} WHERE unit IS NOT NULL"
    ):
        d = s.domain("row", f"SELECT r FROM {coarse} WHERE unit=?", (unit,))
        u = s.domain(
            "var",
            f"SELECT e.v FROM {edges} e JOIN domain_members d ON d.id=e.r WHERE d.g=?",
            (d,),
        )
        candidate(s, run, d, u, dict(kind="unit_sum", seed=json.loads(unit)))
    for t in (rt, vt, coarse, rm, vm, rowgroups, vargroups, kinds_table):
        s.discard(t)
    return rg, vg, edges


def partition(s, run, g, rg, vg):
    n = s.size(vg)
    nr = s.size(rg)
    distinct, total = s.db.execute(
        "SELECT count(DISTINCT v),count(*) FROM members WHERE g=?", (g,)
    ).fetchone()
    nonempty = s.db.execute(
        "SELECT count(DISTINCT r) FROM members WHERE g=?", (g,)
    ).fetchone()[0]
    ok = bool(n and nr and total == distinct == n and nonempty == nr)
    if ok:
        key = ident("partition_", [rg, vg])
        origins = json.loads(
            s.db.execute("SELECT record FROM candidates WHERE g=?", (g,)).fetchone()[0]
        )

        s.put(
            run,
            "partitions",
            key,
            dict(
                id=key,
                g=g,
                rows_domain=rg,
                vars_domain=vg,
                sets=nr,
                variables=n,
                origins=origins,
                verification="verified",
            ),
        )
    else:
        evidence(
            s,
            run,
            "not a partition",
            candidate=g,
            empty_sets=nr - nonempty,
            uncovered_variables=n - distinct,
            extra_memberships=total - distinct,
        )
    return ok, distinct, total


def split_two(s, run, g, rg, vg):
    if s.db.execute(
        "SELECT v FROM members WHERE g=? GROUP BY v HAVING count(*)<>2 LIMIT 1", (g,)
    ).fetchone():
        return
    if s.db.execute("SELECT count(DISTINCT v) FROM members WHERE g=?", (g,)).fetchone()[
        0
    ] != s.size(vg):
        return
    graph = s.table("v INTEGER PRIMARY KEY,a INTEGER,b INTEGER")
    s.db.execute(
        f"INSERT INTO {graph} SELECT v,min(r),max(r) FROM members WHERE g=? GROUP BY v",
        (g,),
    )
    s.db.execute(f"CREATE INDEX {graph}_a ON {graph}(a)")
    s.db.execute(f"CREATE INDEX {graph}_b ON {graph}(b)")
    colors = s.table("r INTEGER PRIMARY KEY,color INTEGER,root INTEGER,done INTEGER")
    s.db.execute(f"CREATE INDEX {colors}_queue ON {colors}(root,done,r)")
    for (root,) in s.db.execute(
        "SELECT id FROM domain_members WHERE g=? ORDER BY id", (rg,)
    ):
        if s.db.execute(f"SELECT 1 FROM {colors} WHERE r=?", (root,)).fetchone():
            continue
        if not s.db.execute(
            f"SELECT 1 FROM {graph} WHERE a=? OR b=? LIMIT 1", (root, root)
        ).fetchone():
            continue
        s.db.execute(f"INSERT INTO {colors} VALUES(?,0,?,0)", (root, root))
        valid = True
        while True:
            item = s.db.execute(
                f"SELECT r,color FROM {colors} WHERE root=? AND done=0 ORDER BY r LIMIT 1",
                (root,),
            ).fetchone()
            if item is None:
                break
            r, c = item
            for (other,) in s.db.execute(
                f"SELECT b FROM {graph} WHERE a=? UNION SELECT a FROM {graph} WHERE b=?",
                (r, r),
            ):
                old = s.db.execute(
                    f"SELECT color FROM {colors} WHERE r=?", (other,)
                ).fetchone()
                if old is None:
                    s.db.execute(
                        f"INSERT INTO {colors} VALUES(?,?,?,0)", (other, 1 - c, root)
                    )
                elif old[0] == c:
                    if valid:
                        evidence(
                            s,
                            run,
                            "non-bipartite candidate",
                            candidate=g,
                            edge=[r, other],
                        )
                    valid = False
            s.db.execute(f"UPDATE {colors} SET done=1 WHERE r=?", (r,))
        if not valid:
            continue
        uv = s.domain(
            "var",
            f"SELECT e.v FROM {graph} e JOIN {colors} c ON c.r=e.a WHERE c.root=?",
            (root,),
        )
        for side in (0, 1):
            rr = s.domain(
                "row", f"SELECT r FROM {colors} WHERE root=? AND color=?", (root, side)
            )
            new = candidate(
                s,
                run,
                rr,
                uv,
                dict(kind="bipartite_component", parent=g, root=root, side=side),
            )
            s.db.execute(
                "INSERT OR IGNORE INTO members SELECT ?,m.r,m.v FROM members m JOIN domain_members d ON d.id=m.r WHERE m.g=? AND d.g=?",
                (new, g, rr),
            )
            partition(s, run, new, rr, uv)
    s.discard(graph)
    s.discard(colors)


def discover(s, run, edges):
    # candidates only belong to this run's staging database (prior members stay for cached runs).
    for g, rg, vg in s.db.execute(
        "SELECT g,rg,vg FROM candidates WHERE run=? ORDER BY g", (run,)
    ):
        s.db.execute(
            f"INSERT OR IGNORE INTO members SELECT ?,e.r,e.v FROM {edges} e JOIN domain_members r ON r.id=e.r AND r.g=? JOIN domain_members v ON v.id=e.v AND v.g=?",
            (g, rg, vg),
        )
        ok, _, _ = partition(s, run, g, rg, vg)
        if not ok:
            split_two(s, run, g, rg, vg)
    parts = s.table("id TEXT PRIMARY KEY,g INTEGER,rg INTEGER,vg INTEGER,n INTEGER")
    for key, txt in s.db.execute(
        "SELECT id,record FROM cards WHERE run=? AND kind='partitions' ORDER BY id",
        (run,),
    ):
        c = json.loads(txt)
        s.db.execute(
            f"INSERT INTO {parts} VALUES(?,?,?,?,?)",
            (key, c["g"], c["rows_domain"], c["vars_domain"], c["sets"]),
        )
    for a, b, ag, bg, ar, br, vg, na, nb in s.db.execute(
        f"SELECT a.id,b.id,a.g,b.g,a.rg,b.rg,a.vg,a.n,b.n FROM {parts} a JOIN {parts} b ON a.vg=b.vg AND a.id<b.id WHERE a.n>=2 AND b.n>=2 ORDER BY a.id,b.id"
    ):
        collision = s.db.execute(
            "SELECT l.r,r.r,count(*) FROM members l JOIN members r ON l.v=r.v WHERE l.g=? AND r.g=? GROUP BY l.r,r.r HAVING count(*)>1 LIMIT 1",
            (ag, bg),
        ).fetchone()
        if collision:
            duplicates, extras = s.db.execute(
                "SELECT count(*),sum(n-1) FROM (SELECT count(*) n FROM members l JOIN members r ON l.v=r.v WHERE l.g=? AND r.g=? GROUP BY l.r,r.r HAVING count(*)>1)",
                (ag, bg),
            ).fetchone()
            evidence(
                s,
                run,
                "multiple variables per cell",
                duplicate_coordinates=duplicates,
                extra_variables=extras,
                partitions=[a, b],
                counterexample=list(collision),
            )
            continue
        pa, pb = s.get(run, "partitions", a), s.get(run, "partitions", b)
        unit = lambda p: any(o.get("seed", [None])[-1] == "unit" for o in p["origins"])
        if (unit(pa), na, -ar) < (unit(pb), nb, -br):
            a, b, ag, bg, ar, br, na, nb = b, a, bg, ag, br, ar, nb, na
        t = ident("tensor_", [run, ar, br, vg])
        nv = s.size(vg)
        # Materialized primary keys avoid SQLite scanning a window-function
        # coroutine once per sparse entry (quadratic in the axis length).
        am = s.table("r INTEGER PRIMARY KEY,pos INTEGER")
        bm = s.table("r INTEGER PRIMARY KEY,pos INTEGER")
        s.db.execute(
            f"INSERT INTO {am} SELECT id,row_number() OVER(ORDER BY id)-1 FROM domain_members WHERE g=?",
            (ar,),
        )
        s.db.execute(
            f"INSERT INTO {bm} SELECT id,row_number() OVER(ORDER BY id)-1 FROM domain_members WHERE g=?",
            (br,),
        )
        s.db.execute(
            f"INSERT OR IGNORE INTO coordinates SELECT ?,l.v,l.r,r.r,aa.pos,bb.pos FROM members l JOIN members r ON l.v=r.v AND r.g=? JOIN {am} aa ON aa.r=l.r JOIN {bm} bb ON bb.r=r.r WHERE l.g=?",
            (t, bg, ag),
        )
        s.discard(am)
        s.discard(bm)
        s.put(
            run,
            "tensors",
            t,
            dict(
                id=t,
                shape=[na, nb],
                variables=nv,
                rows_domains=[ar, br],
                vars_domain=vg,
                partitions=[a, b],
                complete_product=nv == na * nb,
                missing_cells=na * nb - nv,
                verification="verified",
                meaning="incidence coordinates; not a symmetry certificate",
            ),
        )
    s.discard(parts)
