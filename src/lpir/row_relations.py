"""Resumable exact comparisons over affine row pairs in the raw CSR model."""

import json
import hashlib
from contextlib import closing
from .budget import Budget, BudgetExceeded
from .evidence import begin, emit, save
from .numbers import LPIRError
from .results import result_page
from .semantic import connect

OPS = {"row_pairs", "compare_rows", "row_signatures"}


def pair_scope(m, spec):
    allowed = {"op", "start", "stop", "step", "left", "right", "mode"}
    if set(spec) - allowed or spec.get("op") not in OPS:
        raise LPIRError("Invalid row relation")
    start, stop, step = spec.get("start", 0), spec.get("stop"), spec.get("step", 1)
    if (
        any(type(x) is not int for x in (start, stop, step))
        or step <= 0
        or stop < start
    ):
        raise LPIRError(
            "Expected integer start <= stop and positive step; stop is exclusive"
        )
    domain = range(start, stop, step)
    for side in ("left", "right"):
        expr = spec.get(side)
        if (
            not isinstance(expr, dict)
            or set(expr) != {"offset", "stride"}
            or any(type(v) is not int for v in expr.values())
        ):
            raise LPIRError("Row mapping requires integer offset and stride")
        if domain:
            ids = [expr["offset"] + expr["stride"] * j for j in (domain[0], domain[-1])]
            if min(ids) < 0 or max(ids) >= len(m.rows):
                raise LPIRError("Row pair outside model; row IDs are zero-based")
    if spec.get("mode", "vector") not in ("support", "vector", "constraint"):
        raise LPIRError("Comparison mode must be support, vector, or constraint")
    return domain


def query(m, spec, budget=None, offset=0, limit=20):
    if spec.get("op") == "row_signatures":
        return signatures(m, spec, budget, offset, limit)
    domain = pair_scope(m, spec)
    b = Budget(**(budget or {}))
    with closing(connect(m)) as db:
        run = begin(m, db, "row_relation", spec)
        status, ordinal, summary = db.execute(
            "SELECT status,cursor,summary FROM runs WHERE id=?", (run,)
        ).fetchone()
        if status == "complete":
            return result_page(m, db, run, offset, limit)
        state = json.loads(summary)
        state["operation"] = spec["op"]
        state.setdefault("checked_entities", 0)
        state.setdefault("equal_pairs", 0)
        state.setdefault("unequal_pairs", 0)
        state.update(
            scope_total=len(domain), phase="pairs", mode=spec.get("mode", "vector")
        )
        try:
            while ordinal < len(domain):
                j = domain[ordinal]
                left, right = [
                    spec[s]["offset"] + spec[s]["stride"] * j for s in ("left", "right")
                ]
                rec = {"j": j, "left_row": left, "right_row": right}
                if spec["op"] == "compare_rows":
                    cur = state.setdefault(
                        "pair",
                        {
                            "a": int(m.row_ptr[left]),
                            "b": int(m.row_ptr[right]),
                            "differences": 0,
                            "first_difference": None,
                        },
                    )
                    ae, be = int(m.row_ptr[left + 1]), int(m.row_ptr[right + 1])
                    while cur["a"] < ae or cur["b"] < be:
                        b.tick()
                        state["work_done"] = state.get("work_done", 0) + 1
                        ai, bi = cur["a"], cur["b"]
                        av = int(m.col_idx[ai]) if ai < ae else len(m.variables)
                        bv = int(m.col_idx[bi]) if bi < be else len(m.variables)
                        v = min(av, bv)
                        ac = m.num(int(m.coeff_id[ai])) if av == v else 0
                        bc = m.num(int(m.coeff_id[bi])) if bv == v else 0
                        b.require(
                            2048
                            + sum(
                                (x.numerator.bit_length() + x.denominator.bit_length())
                                // 8
                                for x in (ac, bc)
                            )
                        )
                        different = (
                            (av != bv) if state["mode"] == "support" else ac != bc
                        )
                        if different:
                            cur["differences"] += 1
                            if cur["first_difference"] is None:
                                cur["first_difference"] = {
                                    "variable": v,
                                    "left": str(ac),
                                    "right": str(bc),
                                }
                        cur["a"] += av == v
                        cur["b"] += bv == v
                    b.tick()
                    state["work_done"] = state.get("work_done", 0) + 1
                    bounds = [
                        [
                            str(m.num(x)) if x is not None else None
                            for x in m.rows[r][2:4]
                        ]
                        for r in (left, right)
                    ]
                    bounds_equal = bounds[0] == bounds[1]
                    equal = cur["differences"] == 0 and (
                        state["mode"] != "constraint" or bounds_equal
                    )
                    rec.update(
                        equal=equal,
                        coefficient_differences=cur["differences"],
                        first_difference=cur["first_difference"],
                        bounds_equal=bounds_equal,
                        left_bounds=bounds[0],
                        right_bounds=bounds[1],
                        mode=state["mode"],
                    )
                    state["equal_pairs" if equal else "unequal_pairs"] += 1
                    if not equal:
                        state.setdefault("counterexample", rec)
                    state.pop("pair")
                else:
                    b.tick()
                    state["work_done"] = state.get("work_done", 0) + 1
                ordinal = emit(db, run, ordinal, rec)
                state["checked_entities"] = ordinal
            state["phase"] = "complete"
        except BudgetExceeded:
            pass
        except BaseException:
            db.rollback()
            raise
        complete = ordinal == len(domain)
        state["valid"] = (
            (False if state["unequal_pairs"] else True if complete else None)
            if spec["op"] == "compare_rows"
            else None
        )
        state["empty_scope"] = len(domain) == 0
        save(db, run, state, ordinal, complete)
        page = result_page(m, db, run, offset, limit)
        if not complete:
            page["continuation"] = {"spec": spec}
        return page


def validate(m, spec, budget=None):
    query_spec = {k: v for k, v in spec.items() if k != "kind"}
    page = query(m, {**query_spec, "op": "compare_rows"}, budget, limit=3)
    return {
        **page,
        "valid": page["summary"]["valid"],
        "counterexample": page["summary"].get("counterexample"),
    }


def signatures(m, spec, budget=None, offset=0, limit=20):
    if set(spec) - {"op", "start", "stop", "step", "mode"}:
        raise LPIRError("Invalid row signature fields")
    scope = {
        **spec,
        "op": "row_pairs",
        "stop": spec.get("stop", len(m.rows)),
        "left": {"offset": 0, "stride": 1},
        "right": {"offset": 0, "stride": 1},
    }
    domain = pair_scope(m, scope)
    b = Budget(**(budget or {}))
    mode = spec.get("mode", "vector")
    with closing(connect(m)) as db:
        run = begin(m, db, "row_signatures", spec)
        status, ordinal, summary = db.execute(
            "SELECT status,cursor,summary FROM runs WHERE id=?", (run,)
        ).fetchone()
        if status == "complete":
            return result_page(m, db, run, offset, limit)
        state = json.loads(summary)
        state.update(scope_total=len(domain), phase="signatures", mode=mode)
        try:
            while ordinal < len(domain):
                row = domain[ordinal]
                cur = state.setdefault(
                    "row",
                    {
                        "position": int(m.row_ptr[row]),
                        "digest": hashlib.sha256(
                            ("lpir-row-v1:" + mode).encode()
                        ).hexdigest(),
                    },
                )
                end = int(m.row_ptr[row + 1])
                while cur["position"] < end:
                    b.tick()
                    state["work_done"] = state.get("work_done", 0) + 1
                    p = cur["position"]
                    term = [int(m.col_idx[p])]
                    if mode != "support":
                        term.append(str(m.num(int(m.coeff_id[p]))))
                    payload = json.dumps(term, separators=(",", ":")).encode()
                    b.require(2048 + len(payload) * 4)
                    cur["digest"] = hashlib.sha256(
                        bytes.fromhex(cur["digest"]) + payload
                    ).hexdigest()
                    cur["position"] += 1
                b.tick()
                state["work_done"] = state.get("work_done", 0) + 1
                digest = cur["digest"]
                if mode == "constraint":
                    bounds = [
                        str(m.num(x)) if x is not None else None
                        for x in m.rows[row][2:4]
                    ]
                    digest = hashlib.sha256(
                        bytes.fromhex(digest) + json.dumps(bounds).encode()
                    ).hexdigest()
                ordinal = emit(
                    db,
                    run,
                    ordinal,
                    {
                        "row_id": row,
                        "signature": digest,
                        "mode": mode,
                        "algorithm": "sha256-chain-v1",
                        "proof": False,
                    },
                )
                state.pop("row")
                state["checked_entities"] = ordinal
            state["phase"] = "complete"
        except BudgetExceeded:
            pass
        except BaseException:
            db.rollback()
            raise
        save(db, run, state, ordinal, ordinal == len(domain))
        return result_page(m, db, run, offset, limit)
