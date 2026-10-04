"""Exact replay of public indexed hypotheses, independent of discovery labels."""

import sqlite3
from mipframe.ir.indexing.views import report
from mipframe.ir.indexing.operators import check_operator


def validate(result, spec):
    s = result._store
    s.guard()
    kind = spec.get("kind")
    errors = []
    if "analysis" in spec:
        raise ValueError("Legacy analysis handles are not accepted")
    if kind == "indexed_operator":
        candidate = spec.get("candidate")
        card = (
            candidate.operator
            if candidate
            else result._card("operators", spec["id"])
        )
        errors = check_operator(
            s,
            card,
            kernel=candidate.kernel if candidate else spec.get("kernel"),
            rows=candidate.rows if candidate else spec.get("rows"),
            coordinates=candidate.coordinates if candidate else None,
            ast=spec.get("ast"),
        )
        return report(s, errors, operation=kind, checked_rows=card["rows"])
    if kind == "cartesian" and "partitions" in spec:
        return explicit_cartesian(result, spec)
    if kind == "cartesian":
        t = result._card("tensors", spec["id"])
        if "coordinates" not in spec:
            p = result.tensor(t["id"]).related("coordinates")
            answer = p.check("unique", ("i", "j"))
            # Check each recorded coordinate is an actual edge in both declared axes.
            m = s.owner.model
            for v, i, j in s.execute(
                "SELECT v,i,j FROM coordinates WHERE t=?", (t["id"],)
            ):
                for r in (i, j):
                    import numpy as np

                    cols = m.col_idx[int(m.row_ptr[r]) : int(m.row_ptr[r + 1])]
                    pos = int(np.searchsorted(cols, v))
                    if pos == len(cols) or int(cols[pos]) != v:
                        errors.append(
                            dict(
                                reason="coordinate absent from raw row",
                                row_id=r,
                                variable=v,
                            )
                        )
                        break
                if errors:
                    break
            if not answer.ok:
                errors.append(dict(reason="duplicate coordinate"))
            return report(
                s,
                errors,
                operation=kind,
                complete_product=t["complete_product"],
                checked_variables=len(p),
                missing_cells=t["missing_cells"],
            )
        table = s.table("v INTEGER PRIMARY KEY,a INTEGER,b INTEGER,UNIQUE(a,b)")
        try:
            try:
                for x in spec["coordinates"]:
                    s.db.execute(
                        f"INSERT INTO {table} VALUES(?,?,?)",
                        (x["variable"], x["axis0"], x["axis1"]),
                    )
                for query in (
                    f"SELECT v,a,b FROM {table} EXCEPT SELECT v,a,b FROM coordinates WHERE t=?",
                    f"SELECT v,a,b FROM coordinates WHERE t=? EXCEPT SELECT v,a,b FROM {table}",
                ):
                    if s.db.execute(query + " LIMIT 1", (t["id"],)).fetchone():
                        errors.append(
                            dict(reason="coordinate universe or mapping mismatch")
                        )
                        break
            except (sqlite3.IntegrityError, KeyError):
                errors.append(dict(reason="duplicate or invalid coordinate"))
            return report(
                s, errors, operation=kind, complete_product=t["complete_product"]
            )
        finally:
            s.discard(table)
    if kind == "indexed_partition":
        if "sets" not in spec:
            card = result._card("partitions", spec["id"])
            from mipframe.ir.indexing.discovery import partition

            ok, _, _ = partition(
                s, result.id, card["g"], card["rows_domain"], card["vars_domain"]
            )
            return report(
                s, [] if ok else [dict(reason="invalid partition")], operation=kind
            )
        table = s.table("v INTEGER PRIMARY KEY")
        n = 0
        try:
            try:
                for row in spec["sets"]:
                    n += 1
                    count = 0
                    r = row["row_id"]
                    m = s.owner.model
                    if type(r) != int or not 0 <= r < len(m.rows):
                        errors.append(dict(reason="invalid row"))
                        break
                    for v in row["variables"]:
                        import numpy as np

                        cols = m.col_idx[int(m.row_ptr[r]) : int(m.row_ptr[r + 1])]
                        p = int(np.searchsorted(cols, v))
                        if p == len(cols) or int(cols[p]) != v:
                            errors.append(dict(reason="absent membership"))
                            break
                        s.db.execute(f"INSERT INTO {table} VALUES(?)", (v,))
                        count += 1
                    if not count:
                        errors.append(dict(reason="empty set"))
                if not n:
                    errors.append(dict(reason="empty partition"))
                if "variables" in spec:
                    u = s.table("v INTEGER PRIMARY KEY")
                    try:
                        s.db.executemany(
                            f"INSERT OR IGNORE INTO {u} VALUES(?)",
                            ((v,) for v in spec["variables"]),
                        )
                        if any(
                            s.db.execute(q + " LIMIT 1").fetchone()
                            for q in (
                                f"SELECT v FROM {u} EXCEPT SELECT v FROM {table}",
                                f"SELECT v FROM {table} EXCEPT SELECT v FROM {u}",
                            )
                        ):
                            errors.append(dict(reason="universe mismatch"))
                    finally:
                        s.discard(u)
            except sqlite3.IntegrityError:
                errors.append(dict(reason="overlapping membership"))
            return report(s, errors, operation=kind, checked_sets=n)
        finally:
            s.discard(table)
    if kind == "row_bundle":
        card = result._card("operators", spec["id"])
        errors = check_operator(s, card, rows=spec.get("rows"))
        return report(s, errors, operation=kind, checked_rows=card["rows"])
    if kind == "axis_alignment":
        op = result._card("operators", spec["id"])
        if "alignment" not in op["kernel_statistics"]:
            return report(
                s, [dict(reason="no unique maximum bijection")], operation=kind
            )
        if "mapping" in spec:
            table = s.table("p INTEGER PRIMARY KEY,j INTEGER UNIQUE")
            try:
                try:
                    s.db.executemany(
                        f"INSERT INTO {table} VALUES(?,?)",
                        ((x["p"], x["j"]) for x in spec["mapping"]),
                    )
                    for q in (
                        f"SELECT p,j FROM {table} EXCEPT SELECT p,j FROM alignment WHERE o=?",
                        f"SELECT p,j FROM alignment WHERE o=? EXCEPT SELECT p,j FROM {table}",
                    ):
                        if s.db.execute(q + " LIMIT 1", (op["id"],)).fetchone():
                            errors.append(dict(reason="alignment mismatch"))
                            break
                except sqlite3.IntegrityError:
                    errors.append(dict(reason="alignment is not bijective"))
            finally:
                s.discard(table)
        return report(s, errors, operation=kind)
    raise ValueError("Unknown indexed validation kind")


def explicit_cartesian(result, spec):
    """Validate caller-declared axes, including empty sets and sparse products."""
    import numpy as np

    s = result._store
    m = s.owner.model
    parts = spec["partitions"]
    if len(parts) != 2:
        raise ValueError("Cartesian validation requires exactly two partitions")
    axes = [s.table("v INTEGER PRIMARY KEY,r INTEGER,idx INTEGER") for _ in range(2)]
    coords = s.table("v INTEGER PRIMARY KEY,a INTEGER,b INTEGER,UNIQUE(a,b)")
    sizes = [0, 0]
    errors = []
    try:
        try:
            for side, part in enumerate(parts):
                for idx, record in enumerate(part):
                    sizes[side] += 1
                    r = record["row_id"]
                    if type(r) is not int or not 0 <= r < len(m.rows):
                        raise ValueError("row outside model")
                    n = 0
                    for v in record["variables"]:
                        if type(v) is not int or not 0 <= v < len(m.variables):
                            raise ValueError("variable outside model")
                        selected = m.col_idx[int(m.row_ptr[r]) : int(m.row_ptr[r + 1])]
                        p = int(np.searchsorted(selected, v))
                        if p == len(selected) or int(selected[p]) != v:
                            raise ValueError("member absent from original row")
                        s.db.execute(
                            f"INSERT INTO {axes[side]} VALUES(?,?,?)", (v, r, idx)
                        )
                        n += 1
                    if not n:
                        raise ValueError("empty declared axis member")
            if not all(sizes):
                raise ValueError("empty partition")
            for a, b in (axes, axes[::-1]):
                if s.db.execute(
                    f"SELECT v FROM {a} EXCEPT SELECT v FROM {b} LIMIT 1"
                ).fetchone():
                    raise ValueError("axis universes differ")
            for x in spec["coordinates"]:
                if "var" in x:
                    from mipframe.ir.indexing.operators import _ref_id
                    v = _ref_id(s, x['var'], 'var')
                    a = _ref_id(s, x['i'], 'row')
                    b = _ref_id(s, x['j'], 'row')
                    left=s.db.execute(f'SELECT idx FROM {axes[0]} WHERE v=? AND r=?',(v,a)).fetchone()
                    right=s.db.execute(f'SELECT idx FROM {axes[1]} WHERE v=? AND r=?',(v,b)).fetchone()
                    if left is None or right is None: raise ValueError('coordinate disagrees with declared membership')
                    a,b=left[0],right[0]
                elif "source_id" in x:
                    v = x["source_id"]
                    left = s.db.execute(
                        f"SELECT idx FROM {axes[0]} WHERE v=? AND r=?", (v, x["i"])
                    ).fetchone()
                    right = s.db.execute(
                        f"SELECT idx FROM {axes[1]} WHERE v=? AND r=?", (v, x["j"])
                    ).fetchone()
                    if left is None or right is None:
                        raise ValueError(
                            "coordinate disagrees with declared membership"
                        )
                    a, b = left[0], right[0]
                else:
                    v, a, b = x["variable"], x["axis0"], x["axis1"]
                s.db.execute(f"INSERT INTO {coords} VALUES(?,?,?)", (v, a, b))
            expected = (
                f"SELECT a.v,a.idx,b.idx FROM {axes[0]} a JOIN {axes[1]} b ON a.v=b.v"
            )
            for query in (
                f"SELECT * FROM {coords} EXCEPT {expected}",
                f"{expected} EXCEPT SELECT * FROM {coords}",
            ):
                if s.db.execute(query + " LIMIT 1").fetchone():
                    raise ValueError("coordinate universe or mapping mismatch")
        except (KeyError, ValueError, TypeError, sqlite3.IntegrityError) as error:
            errors.append(
                dict(reason="invalid Cartesian hypothesis", detail=str(error))
            )
        n = s.db.execute(f"SELECT count(*) FROM {coords}").fetchone()[0]
        return report(
            s,
            errors,
            operation="cartesian",
            complete_product=not errors and n == sizes[0] * sizes[1],
            checked_variables=n,
            missing_cells=sizes[0] * sizes[1] - n,
        )
    finally:
        for table in [*axes, coords]:
            s.discard(table)


def recipe_spec(value):
    from mipframe.frame import Frame, Record
    from mipframe.frame.schema import encode
    if isinstance(value,Frame):return {'$frame':value.to_spec()}
    if isinstance(value,Record):value=value.to_record()
    if isinstance(value,dict):return {k:recipe_spec(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [recipe_spec(v) for v in value]
    return encode(value)


def restore_spec(value, owner):
    from mipframe.frame.serialization import frame_from_spec
    from mipframe.frame.schema import decode_tree
    return decode_tree(value,frame_decoder=lambda spec:frame_from_spec(spec,models={owner.uid:owner}))
