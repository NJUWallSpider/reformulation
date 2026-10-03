"""Stored edit history is auditable data; proofs require explicit source binding."""

from contextlib import closing
import json
import sqlite3
from tempfile import NamedTemporaryFile
from mipframe.frame.schema import DType, ref_type, decode, SchemaError
from mipframe.store.algorithm_store import AlgorithmStore

TABLES = (
    "operations",
    "fields",
    "subjects",
    "proofs",
    "changes",
    "dropped",
    "new_vars",
    "new_rows",
    "new_terms",
    "objective",
    "settings",
    "replaces",
)


def decoded(x):
    if isinstance(x, dict):
        if set(x) in ({"rational"}, {"infinity"}, {"ref"}):
            return decode(x)
        return {k: decoded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [decoded(v) for v in x]
    return x


class Lineage:
    """
    MIPFRAME HELP BEGIN
    Lineage [api.Lineage]

    Read-only Lineage domain facade.

    Lineage [type]

    When to use: Read-only Lineage domain facade.

    Returns: Obtain from the corresponding Model analysis or lineage property.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, owner):
        self._owner = owner

    def _db(self):
        m = self._owner.model
        if "edit_artifact" not in m.manifest:
            raise SchemaError("This snapshot has no Edit parent")
        return sqlite3.connect(
            (m.path / "lineage.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )

    @property
    def parent(self):
        """
        MIPFRAME HELP BEGIN
        Lineage.parent [api.Lineage.parent]

        Read Lineage parent.

        Lineage.parent [property]

        When to use: Read Lineage parent.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self._owner.parent

    def _meta(self, key):
        with closing(self._db()) as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return decoded(json.loads(row[0]))

    @property
    def semantic(self):
        """
        MIPFRAME HELP BEGIN
        Lineage.semantic [api.Lineage.semantic]

        Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        Lineage.semantic [property]

        When to use: Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return dict(
            verification="recorded at build; bind parent through diff to recheck",
            **self._meta("semantic"),
        )

    @property
    def validation(self):
        """
        Detached build-time diagnostics; never a reusable mathematical proof.

        MIPFRAME HELP BEGIN
        Lineage.validation [api.Lineage.validation]

        Read Lineage validation.

        Lineage.validation [property]

        When to use: Read Lineage validation.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self._meta("validation")

    @property
    def findings(self):
        """
        MIPFRAME HELP BEGIN
        Lineage.findings [api.Lineage.findings]

        Read Lineage findings.

        Lineage.findings [property]

        When to use: Read Lineage findings.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self._frame(
            "SELECT id,category,axis,entity,reason FROM validation_findings ORDER BY id",
            {
                "id": DType("int"),
                "category": DType("str"),
                "axis": DType("str"),
                "entity": DType("int"),
                "reason": DType("str"),
            },
            key=("id",),
        )

    def _frame(self, query, schema, params=(), key=None):
        owner = self._owner

        def source(fields, ctx):
            with closing(self._db()) as db:
                for row in db.execute(query, params):
                    owner.model
                    yield {n: row[list(schema).index(n)] for n in fields}

        from mipframe.frame.base import Frame

        return Frame(schema, models=(owner,), source=source, key=key)

    @property
    def operations(self):
        """
        MIPFRAME HELP BEGIN
        Lineage.operations [api.Lineage.operations]

        Read Lineage operations.

        Lineage.operations [property]

        When to use: Read Lineage operations.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self._frame(
            "SELECT id,kind FROM operations ORDER BY id",
            {"operation": DType("int"), "kind": DType("str")},
            key=("operation",),
        )

    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Lineage.evidence [api.Lineage.evidence]

        Read Lineage evidence.

        Lineage.evidence [property]

        When to use: Read Lineage evidence.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self._frame(
            "SELECT DISTINCT root FROM proof_events ORDER BY root",
            {"proof": DType("int")},
            key=("proof",),
        )

    @property
    def changes(self):
        """
        MIPFRAME HELP BEGIN
        Lineage.changes [api.Lineage.changes]

        Read Lineage changes.

        Lineage.changes [property]

        When to use: Read Lineage changes.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self._frame(
            "SELECT ?,?,axis,id,field,value FROM changes ORDER BY axis,id,field",
            {
                "source_uid": DType("str"),
                "source_version": DType("str"),
                "axis": DType("str"),
                "source_id": DType("int"),
                "field": DType("str"),
                "exact_value": DType("str"),
            },
            params=(self.parent["uid"], self.parent["version"]),
        )

    def mapping(self, axis, *, source=None):
        """
        MIPFRAME HELP BEGIN
        Lineage.mapping [api.Lineage.mapping]

        Read explicit source/target entity correspondence, optionally binding the parent.

        Lineage.mapping(axis, *, source=None)

        When to use: Read explicit source/target entity correspondence, optionally binding the parent.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        if axis not in ("row", "var"):
            raise ValueError("axis must be row or var")
        schema = {
            "source_uid": DType("str"),
            "source_version": DType("str"),
            "source_id": DType("int"),
            axis: ref_type(self._owner, axis),
        }
        f = self._frame(
            f"SELECT ?,?,source_id,id FROM {axis}_map WHERE ns=0 ORDER BY source_id",
            schema,
            params=(self.parent["uid"], self.parent["version"]),
            key=("source_uid", "source_version", "source_id"),
        )
        if source is None:
            return f
        if dict(uid=source.uid, version=source.version) != self.parent:
            raise SchemaError("Wrong parent snapshot")
        schema = {
            f"source_{axis}": ref_type(source, axis),
            axis: ref_type(self._owner, axis),
        }
        from mipframe.frame.base import Frame

        def records(fields, ctx):
            for r in f._raw(("source_id", axis), ctx):
                yield {
                    n: r["source_id"] if n.startswith("source_") else r[axis]
                    for n in fields
                }

        return Frame(
            schema,
            models=(source, self._owner),
            source=records,
            key=(f"source_{axis}",),
        )

    def resolve(self, ref):
        """
        MIPFRAME HELP BEGIN
        Lineage.resolve [api.Lineage.resolve]

        Read Lineage resolve.

        Lineage.resolve(ref)

        When to use: Read Lineage resolve.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.edit.draft import DraftRef, DraftVar

        if isinstance(ref, DraftVar):
            ref = ref.ref
        if not isinstance(ref, DraftRef) or ref.edit_uid != self._meta("edit_uid"):
            raise SchemaError("Expected a draft reference from this Edit")
        with closing(self._db()) as db:
            r = db.execute(
                f"SELECT id FROM {ref.kind}_map WHERE ns=1 AND source_id=?", (ref.id,)
            ).fetchone()
        if r is None:
            raise KeyError(ref)
        return (self._owner.rows if ref.kind == "row" else self._owner.vars).by_id(r[0])

    def proof(self, ident, *, models, memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Lineage.proof [api.Lineage.proof]

        Replay stored lineage evidence with explicitly bound source snapshots.

        Lineage.proof(ident, *, models, memory_mb=128, disk_mb=None)

        When to use: Replay stored lineage evidence with explicitly bound source snapshots.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.check.report import Report

        with (
            closing(self._db()) as db,
            NamedTemporaryFile(mode="w+", suffix=".jsonl") as f,
        ):
            found = False
            for (event,) in db.execute(
                "SELECT event FROM proof_events WHERE root=? ORDER BY n", (ident,)
            ):
                f.write(event + "\n")
                found = True
            if not found:
                raise KeyError(ident)
            f.flush()
            return Report.replay(
                f.name, models=models, memory_mb=memory_mb, disk_mb=disk_mb
            )

    def _restore(self, source, *, memory_mb=128, disk_mb=None):
        from mipframe.edit.plan import Edit

        if self.parent != dict(uid=source.uid, version=source.version):
            raise SchemaError("diff requires a direct parent snapshot")
        e = Edit(source, memory_mb=memory_mb, disk_mb=disk_mb)
        try:
            with closing(self._db()) as db:
                for table in TABLES:
                    cursor = db.execute(f"SELECT * FROM {table}")
                    marks = ",".join("?" for _ in cursor.description)
                    for row in cursor:
                        e._store.execute(f"INSERT INTO {table} VALUES({marks})", row)
                roots = [
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT root FROM proof_events ORDER BY root"
                    )
                ]
            for root in roots:
                if root != len(e._proofs):
                    raise SchemaError("Invalid lineage proof numbering")
                p = self.proof(
                    root, models=(source,), memory_mb=memory_mb, disk_mb=disk_mb
                )
                e._check_proof(p)
                e._proofs.append(p)
            return e
        except BaseException:
            e.close()
            raise

    def __repr__(self):
        return f"<Lineage: parent={self.parent}; proofs require replay>"


def validate_artifact(path, manifest):
    from mipframe.store.numbers import MIPFrameError
    from mipframe.store.artifacts import sha256

    artifact = manifest["edit_artifact"]
    try:
        if (
            artifact.get("version") != 1
            or artifact.get("file") != "lineage.sqlite"
            or sha256(path / "lineage.sqlite") != artifact.get("sha256")
        ):
            raise ValueError("checksum or format")
        with closing(
            sqlite3.connect(
                (path / "lineage.sqlite").resolve().as_uri() + "?mode=ro", uri=True
            )
        ) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("database integrity")

            def meta(key):
                return json.loads(
                    db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[
                        0
                    ]
                )

            if (
                meta("version") != 1
                or meta("parent") != manifest["parent"]
                or meta("target")
                != dict(uid=manifest["uid"], version=manifest["model_version"])
            ):
                raise ValueError("snapshot identity")
            for table in TABLES + (
                "row_map",
                "var_map",
                "proof_events",
                "validation_findings",
            ):
                db.execute(f"SELECT * FROM {table} LIMIT 0")
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise MIPFrameError(
            "Corrupt or incompatible Edit lineage: " + str(exc)
        ) from exc
