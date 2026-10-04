"""Correctness and bounded-resource regressions from the whole-library review."""
import json
from pathlib import Path

import pytest
import mipframe as mf


@pytest.fixture
def model():
    with mf.read(Path(__file__).parents[1] / 'examples/data/tiny.lp') as value:
        yield value


def records(rows, schema=None, key=None):
    return mf.Frame.from_records(rows, schema=schema or {'x': 'int'}, key=key)


@pytest.mark.parametrize('rows', [[], [{'x': 1}], [{'x': 1}, {'x': 2}]])
def test_global_aggregate_keeps_one_row_after_column_pruning(rows):
    f = records(rows)
    total = f.group_by().agg(total=mf.col('x').sum())
    assert total.to_records() == [{'total': sum(r['x'] for r in rows)}]
    assert total.count() == 1
    assert total.select(v=1).to_records() == [{'v': 1}]
    assert f.group_by().agg(n=mf.count()).to_records() == [{'n': len(rows)}]
    assert f.group_by().agg(n=mf.count()).count() == 1
    assert f.group_by('x').agg(n=mf.count()).count() == len(rows)


def test_tuple_expression_dependencies_survive_projection():
    f = records([{'a': 1, 'b': 1}, {'a': 2, 'b': 3}], {'a': 'int', 'b': 'int'})
    selected = f.where(mf.col('a').isin([mf.col('b')])).select('a')
    assert selected.to_records() == [{'a': 1}]


@pytest.mark.parametrize('grouped', [False, True])
def test_membership_expression_owns_snapshot_even_after_iteration_starts(model, grouped):
    f = records([{'x': 1}, {'x': 2}])
    domain = model.vars.select(x=mf.col('nnz')).distinct()
    expr = mf.col('x').isin(domain)
    query = f.group_by('x').agg(n=expr.count()) if grouped else f.select('x', hit=expr)
    assert query.models == frozenset([model.uid])
    iterator = iter(query)
    first = next(iterator)
    model.close()
    with pytest.raises(mf.MIPFrameError):
        first.to_record()
    with pytest.raises(mf.MIPFrameError):
        next(iterator)


def test_nullable_membership_matches_declared_schema():
    result = records([{'x': 1}, {'x': 2}]).select(hit=mf.col('x').isin([1, None]))
    assert result.schema['hit'].nullable
    assert records(result.to_records(), result.schema).to_records() == [{'hit': True}, {'hit': None}]


def test_batch_reuses_blocking_certificate_without_losing_rows(model):
    x = model.vars.by_name('x')
    using = model.vars.head(1).select('var', side='lb', multiplier=1).sort_by('var')
    assert model.check.linear(x >= 0, using=using).status == 'PROVED'
    with model.check.linear_batch() as batch:
        for i in range(3):
            batch.add(key=str(i), target=x >= 0, using=using)
        result = batch.run()
    assert [r['status'] for r in result.evidence['results'].to_records()] == ['PROVED'] * 3


def test_workspace_reset_invalidates_staging_and_membership():
    from mipframe.check.workspace import ProofWorkspace
    domain = records([{'x': 1}], key=('x',))
    s = ProofWorkspace()
    try:
        for _ in range(3):
            s.begin()
            assert s.contains(domain, 1)
            assert not s.contains(domain, 2)
            s.reset()
            assert not s.staged and not s.members
            assert not getattr(s, '_staged_frames', {})
        assert s.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 1
    finally:
        s.close()


def test_bound_updates_do_not_accumulate_scratch_tables(model):
    edit = model.edit()
    def tables():
        return edit._store.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    before = tables()
    var = model.vars.head(1)
    aligned = var.select('var', value=0)
    for value in [0, aligned] * 10:
        edit.set_var_bounds(var, lb=value)
        assert tables() == before
    with pytest.raises(mf.SchemaError):
        edit.set_var_bounds(var, lb=aligned.head(0))
    assert tables() == before
    with edit.build() as built:
        assert built.vars.head(1).to_records()[0]['lb'] == 0


def test_build_forwards_memory_budget(tmp_path, monkeypatch):
    from mipframe.store import model_store
    real = model_store.Writer
    budgets = []
    def writer(*args, **kwargs):
        result = real(*args, **kwargs)
        budgets.append(result.memory)
        return result
    monkeypatch.setattr(model_store, 'Writer', writer)
    with mf.read(Path(__file__).parents[1] / 'examples/data/tiny.lp', memory_mb=32):
        pass
    assert budgets == [32 * 1024**2]


def test_streaming_does_not_open_spill_storage(model, monkeypatch):
    from mipframe.frame.executor import Context
    original = Context._open
    opened = []
    def track(context):
        opened.append(context)
        return original(context)
    monkeypatch.setattr(Context, '_open', track)
    assert model.rows.head(1).select('name').to_records()
    assert opened == []
    assert model.rows.group_by().agg(n=mf.count()).to_records() == [{'n': model.shape[0]}]
    assert len(opened) == 1
    context = Context()
    context.close()
    with pytest.raises(mf.MIPFrameError):
        context.execute('SELECT 1')
    assert len(opened) == 1


def test_shared_plan_serialization_and_explain_are_linear(model):
    query = model.vars.select('var')
    for _ in range(14):
        query = query.join(query, on='var')
    spec = query.to_spec()
    assert len(json.dumps(spec)) < 10000
    assert len(str(query.explain())) < 10000
    restored = mf.frame_from_spec(json.loads(json.dumps(spec)), models={model.uid: model})
    assert restored.to_records() == query.to_records()


def test_shared_plan_memberships_roundtrip(model):
    domain = model.vars.select('var')
    query = domain.select('var', hit=mf.col('var').isin(domain)).where(mf.col('var').isin(domain))
    spec = json.loads(json.dumps(query.to_spec()))
    assert mf.frame_from_spec(spec, models={model.uid: model}).to_records() == query.to_records()


@pytest.mark.parametrize('plan', [
    {'op': 'ref', 'id': 0},
    {'op': 'select', '$id': 0, 'inputs': [{'op': 'ref', 'id': 0}], 'expressions': {}},
])
def test_invalid_plan_references_are_rejected(plan):
    with pytest.raises(mf.SchemaError):
        mf.frame_from_spec({'version': 1, 'plan': plan}, models={})


def test_algorithm_input_shared_with_later_query_roundtrips():
    edges = records([{'u': 0, 'v': 1}], {'u': 'int', 'v': 'int'})
    query = mf.ops.graph.components(edges).join(
        edges.select(vertex=mf.col('u')).distinct(), on='vertex',
    )
    spec = json.loads(json.dumps(query.to_spec()))
    bindings = {edges.to_spec()['plan']['relation']: edges}
    restored = mf.frame_from_spec(spec, models={}, relations=bindings)
    assert restored.to_records() == query.to_records()
