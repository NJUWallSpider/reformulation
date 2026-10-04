"""Exact numerical I/O audits. These reports are not linear certificates."""

from pathlib import Path
from tempfile import TemporaryDirectory
from fractions import Fraction
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.store.model_store import build_numerical
from mipframe.frame.schema import DType, SchemaError
from mipframe.check.report import Report


def roundtrip(
    model, path, *, rename_vars=None, rename_rows=None, memory_mb=128, disk_mb=None
):
    """
    MIPFRAME HELP BEGIN
    Numerically reparse an LP and compare exact model facts.

    Full contract: mf.help("api.audit.roundtrip")
    MIPFRAME HELP END
    """
    if Path(path).suffix.lower() != ".lp":
        raise ValueError("The initial roundtrip auditor supports LP files")
    s = AlgorithmStore(
        (model,), memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None
    )
    temp = TemporaryDirectory(prefix="mipframe-audit-")
    read = None
    s.paths.add(Path(temp.name))
    try:
        read = build_numerical(
            path, Path(temp.name) / "model", max(32, memory_mb), format="lp"
        )
        s.disk_check()
        base = model.model
        diffs = s.raw_table(
            "category TEXT,row TEXT,var TEXT,expected TEXT,observed TEXT"
        )

        def issue(category, expected, observed, row=None, var=None):
            s.execute(
                f"INSERT INTO {diffs} VALUES(?,?,?,?,?)",
                (category, row, var, str(expected), str(observed)),
            )

        vm = s.raw_table("old INTEGER PRIMARY KEY,new INTEGER UNIQUE")
        rm = s.raw_table("old INTEGER PRIMARY KEY,new INTEGER UNIQUE")
        for axis, old, new, mapping, t in (
            ("var", base.variables, read.variables, rename_vars, vm),
            ("row", base.rows, read.rows, rename_rows, rm),
        ):
            names = read.var_names if axis == "var" else read.row_names
            for rec in old:
                ident, name = rec[:2]
                output_name = mapping[ident] if mapping is not None else name
                try:
                    dest = names[output_name]
                except KeyError:
                    issue("missing_" + axis, output_name, None)
                    continue
                s.execute(f"INSERT INTO {t} VALUES(?,?)", (ident, dest))
                actual = new[dest]
                if axis == "var":
                    expected = (rec[2], *(base.num(x) for x in rec[3:6]))
                    observed = (actual[2], *(read.num(x) for x in actual[3:6]))
                else:
                    expected = tuple(base.num(x) for x in rec[2:4])
                    observed = tuple(read.num(x) for x in actual[2:4])
                if expected != observed:
                    issue(axis + "_metadata", expected, observed, **{axis: output_name})
            for rec in new:
                if not s.execute(
                    f"SELECT 1 FROM {t} WHERE new=?", (rec[0],)
                ).fetchone():
                    issue("extra_" + axis, None, rec[1])
        if base.manifest["sense"] != read.manifest["sense"]:
            issue("objective_sense", base.manifest["sense"], read.manifest["sense"])
        if base.num(base.manifest["objective_constant"]) != read.num(
            read.manifest["objective_constant"]
        ):
            issue(
                "objective_constant",
                base.num(base.manifest["objective_constant"]),
                read.num(read.manifest["objective_constant"]),
            )
        work = s.raw_table("v INTEGER PRIMARY KEY,c TEXT")
        for r, rr in s.execute(f"SELECT old,new FROM {rm} ORDER BY old"):
            s.execute(f"DELETE FROM {work}")
            for v, c in base.iter_terms(r):
                dest = s.execute(f"SELECT new FROM {vm} WHERE old=?", (v,)).fetchone()
                if dest is not None:
                    s.execute(f"INSERT INTO {work} VALUES(?,?)", (dest[0], str(c)))
            for v, c in read.iter_terms(rr):
                expected = s.execute(f"SELECT c FROM {work} WHERE v=?", (v,)).fetchone()
                if expected is None or Fraction(expected[0]) != c:
                    issue(
                        "coefficient",
                        None if expected is None else expected[0],
                        c,
                        row=read.rows[rr][1],
                        var=read.variables[v][1],
                    )
                s.execute(f"DELETE FROM {work} WHERE v=?", (v,))
            for v, c in s.execute(f"SELECT v,c FROM {work} ORDER BY v"):
                issue(
                    "coefficient", c, 0, row=read.rows[rr][1], var=read.variables[v][1]
                )
        schema = {
            "category": DType("str"),
            "row": DType("str", True),
            "var": DType("str", True),
            "expected": DType("str"),
            "observed": DType("str"),
        }
        violations = s.publish(
            schema,
            (
                dict(zip(schema, row))
                for row in s.execute(f"SELECT * FROM {diffs} ORDER BY rowid")
            ),
        )
        return Report(
            s,
            len(violations) == 0,
            dict(
                operation="LP roundtrip",
                differences=len(violations),
                scope="all numerical model facts",
            ),
            violations,
            conclusion=dict(kind="io_consistency"),
            scope=dict(
                kind="io_consistency", arithmetic="exact", domain=None, complete=True
            ),
        )
    except BaseException:
        s.close()
        raise
    finally:
        if read is not None:
            read.close()
        temp.cleanup()
