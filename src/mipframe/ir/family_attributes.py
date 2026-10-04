"""Exact numerical observations in a bounded, short-lived scratch database."""
from contextlib import contextmanager
from fractions import Fraction
from math import inf
import sqlite3
import numpy as np
from mipframe.store.numbers import compare_numeric_text

@contextmanager
def observations(result, kind, depth, members, basis):
    if basis not in ('raw', 'canonical', 'scale_normalized'):
        raise ValueError('Unknown numeric basis')
    result._store.guard()
    db = sqlite3.connect('')
    try:
        db.create_collation('rational', compare_numeric_text)
        db.execute(f'PRAGMA cache_size=-{max(1,min(512,result._store.memory//4096))}')
        db.executescript("""
            CREATE TABLE populations(orientation INTEGER,member INTEGER,PRIMARY KEY(orientation,member));
            CREATE TABLE terms(orientation INTEGER,member INTEGER,sign INTEGER,neighbor INTEGER,value TEXT,n INTEGER,PRIMARY KEY(orientation,member,sign,neighbor,value));
            CREATE INDEX term_roles ON terms(orientation,sign,neighbor,value,member);
            CREATE TABLE bounds(orientation INTEGER,member INTEGER,lo TEXT COLLATE rational,hi TEXT COLLATE rational,objective TEXT COLLATE rational,PRIMARY KEY(orientation,member));
            CREATE TABLE sources(orientation INTEGER,member INTEGER,row INTEGER,factor TEXT,scale TEXT,PRIMARY KEY(orientation,member,row));
            CREATE TABLE row_directions(row INTEGER PRIMARY KEY,first INTEGER);
            CREATE TABLE direction_terms(direction INTEGER,neighbor INTEGER,value TEXT,n INTEGER,PRIMARY KEY(direction,neighbor,value));
        """)
        _build(db, result, kind, depth, members, basis)
        yield db
    finally:
        db.close()

def _build(db, result, kind, depth, members, basis):
    store, m = result._store, result._store.owner.model
    config = result._config
    neighbor_depth = max(0, depth-1)
    vc = store.array(config, "var", neighbor_depth)
    pos = store.array(config, "pos", depth)
    neg = store.array(config, "neg", depth)
    rp = store.array(config, "pos", neighbor_depth)
    rn = store.array(config, "neg", neighbor_depth)
    members = members.ids

    def row_info(row):
        scale, lo, hi, dual = store.db.execute("SELECT scale,lo,hi,dual FROM row_data WHERE config=? AND id=?", (config, row)).fetchone()
        scale = Fraction(scale)
        directions = [(0, 1)]
        if dual:
            cached = db.execute("SELECT first FROM row_directions WHERE row=?", (row,)).fetchone()
            if cached:
                first = cached[0]
            else:
                first = 1 if pos[row] <= neg[row] else -1
                if pos[row] == neg[row]:
                    # Numeric tie-breaking is *only* for presentation, never
                    # refinement. External aggregation also handles huge rows.
                    db.execute("DELETE FROM direction_terms")
                    for position in range(int(m.row_ptr[row]), int(m.row_ptr[row+1])):
                        value = m.num(int(m.coeff_id[position]))
                        neighbor = int(vc[int(m.col_idx[position])])
                        for direction in (1, -1):
                            db.execute("""INSERT INTO direction_terms VALUES(?,?,?,1)
                                ON CONFLICT(direction,neighbor,value) DO UPDATE SET n=n+1""",
                                (direction, neighbor, str(direction*value)))
                    positive = db.execute("SELECT neighbor,value,n FROM direction_terms WHERE direction=1 ORDER BY neighbor,value COLLATE rational")
                    negative = db.execute("SELECT neighbor,value,n FROM direction_terms WHERE direction=-1 ORDER BY neighbor,value COLLATE rational")
                    for a, b in zip(positive, negative):
                        ka, kb = (a[0], Fraction(a[1]), a[2]), (b[0], Fraction(b[1]), b[2])
                        if ka != kb:
                            first = 1 if ka < kb else -1
                            break
                db.execute("INSERT INTO row_directions VALUES(?,?)", (row, first))
            directions = [(0, first), (1, -first)]
        return scale, lo, hi, directions

    def observe(member, row, positions, info):
        scale, lo, hi, directions = info
        canonical = Fraction(1 if scale > 0 else -1)
        for orientation, reverse in directions:
            factor = Fraction(1) if basis == "raw" else reverse/scale if basis == "scale_normalized" else reverse*canonical
            db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?)", (orientation, member, row, str(factor), str(scale)))
            db.execute("INSERT OR IGNORE INTO populations VALUES(?,?)", (orientation, member))
            for position in positions():
                variable = int(m.col_idx[position])
                value = m.num(int(m.coeff_id[position]))
                sign = 1 if value*canonical*reverse > 0 else -1
                neighbor = int(vc[variable]) if kind == "row" else int(rp[row] if reverse == 1 else rn[row])
                db.execute("""INSERT INTO terms VALUES(?,?,?,?,?,1)
                    ON CONFLICT(orientation,member,sign,neighbor,value) DO UPDATE SET n=n+1""",
                    (orientation, member, sign, neighbor, str(value*factor)))
            if kind == "row":
                if basis == "raw":
                    lo_id, hi_id = m.db.execute("SELECT lo,hi FROM rows WHERE id=?", (row,)).fetchone()
                    lower, upper = m.num(lo_id), m.num(hi_id)
                else:
                    multiplier = abs(scale) if basis == "canonical" else 1
                    lower = None if lo is None else Fraction(lo)*multiplier
                    upper = None if hi is None else Fraction(hi)*multiplier
                db.execute("INSERT INTO bounds VALUES(?,?,?,?,NULL)", (orientation, member, str(-inf if lower is None else lower), str(inf if upper is None else upper)))

    for member in members:
        db.execute("INSERT INTO populations VALUES(0,?)", (member,))
        if kind == "row":
            begin, end = int(m.row_ptr[member]), int(m.row_ptr[member+1])
            observe(member, member, lambda: range(begin, end), row_info(member))
        else:
            lo_id, hi_id, obj_id = m.db.execute("SELECT lo,hi,obj FROM variables WHERE id=?", (member,)).fetchone()
            lower, upper, objective = m.num(lo_id), m.num(hi_id), m.num(obj_id)
            db.execute("INSERT INTO bounds VALUES(0,?,?,?,?)", (member, str(-inf if lower is None else lower), str(inf if upper is None else upper), str(objective)))
            for offset in range(int(m.col_ptr[member]), int(m.col_ptr[member+1])):
                position = int(m.col_positions[offset])
                # CSC contains sparse positions; binary search avoids a
                # graph-sized Python row lookup table.
                row = int(np.searchsorted(m.row_ptr, position, side="right"))-1
                observe(member, row, lambda: (position,), row_info(row))
    if db.execute("SELECT 1 FROM populations WHERE orientation=1 LIMIT 1").fetchone():
        db.execute("INSERT OR IGNORE INTO populations SELECT 1,member FROM populations WHERE orientation=0")
    # Variable bounds do not depend on incident-row orientations.
    if kind == "var":
        db.execute("INSERT INTO bounds SELECT 1,member,lo,hi,objective FROM bounds WHERE orientation=0 AND EXISTS(SELECT 1 FROM populations WHERE orientation=1)")
