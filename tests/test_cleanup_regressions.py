"""Public correctness and bounded-work regressions for the cleanup."""
from fractions import Fraction
from math import inf
from pathlib import Path

import pytest
import mipframe as mf
from mipframe.frame.executor import Context


@pytest.fixture
def m():
    with mf.read(Path(__file__).resolve().parents[1] / 'examples/data/tiny.lp') as model:
        yield model


def test_join_evidence_works_in_scalar_and_reused_batch_context(m):
    x = m.vars.by_name('x')
    using = m.vars.head(1).select('var', side='lb').join(
        m.vars.head(1).select('var', multiplier=1), on='var')
    assert m.check.linear(x >= 0, using=using).ok
    with m.check.linear_batch(memory_mb=0.015625) as batch:
        for i in range(8):
            batch.add(key=str(i), target=x >= 0, using=using)
        result = batch.run()
    assert result.ok
    assert mf.Report.replay(result.for_target('7').to_spec(), models=[m]).ok


@pytest.mark.parametrize('a,b,op,expected', [
    (1, 3, 'div', Fraction(1, 3)), (1, inf, 'div', Fraction(0)),
    (-inf, -3, 'div', inf), (10**400, inf, 'add', inf),
    (10**400, -inf, 'mul', -inf), (inf, -inf, 'sub', inf),
])
def test_extended_arithmetic_stays_exact_and_roundtrips(a, b, op, expected):
    frame = mf.Frame.from_records([dict(a=a, b=b)], schema={'a':'number', 'b':'number'})
    expr = getattr(mf.col('a'), '__' + {'div':'truediv'}.get(op, op) + '__')(mf.col('b'))
    result = frame.select(value=expr)
    records = result.to_records()
    assert records == [{'value': expected}]
    assert type(records[0]['value']) is type(expected)
    assert mf.Frame.from_records(records, schema=result.schema).to_records() == records


@pytest.mark.parametrize('expr', [
    mf.col('x') - mf.col('x'), mf.col('x') + -inf,
    mf.col('x') * 0, mf.col('x') / mf.col('x'),
])
def test_indeterminate_arithmetic_never_leaks_nan(expr):
    frame = mf.Frame.from_records([{'x':inf}], schema={'x':'number'})
    with pytest.raises(mf.ExactArithmeticError, match='Indeterminate'):
        frame.select(value=expr).to_records()


def test_exact_sum_rejects_indeterminate_infinities():
    frame = mf.Frame.from_records([{'x':inf}, {'x':-inf}], schema={'x':'number'})
    with pytest.raises(mf.ExactArithmeticError, match='Indeterminate'):
        frame.group_by().agg(total=mf.col('x').sum()).to_records()
    with pytest.raises(mf.ExactArithmeticError, match='Indeterminate'):
        frame.column('x').sum()


def test_workspace_reuses_indexes_across_joins_and_membership():
    frame=mf.Frame.from_records(({'x':i} for i in range(5)),schema={'x':'int'}).distinct()
    query=frame.join(frame,on='x',cardinality='1:1')
    with Context(memory_mb=.015625) as ctx:
        first,_=ctx.stage(query)
        for _ in range(25):
            assert ctx.stage(query)[0] is first
            assert len(first)==5 and ctx.contains(frame,3) and not ctx.contains(frame,8)
        assert ctx.work.ledger.peak_resident<=16384


@pytest.mark.parametrize('count', [0, 1, 3])
def test_zero_column_distinct(count):
    frame = mf.Frame.from_records([{} for _ in range(count)], schema={})
    assert frame.distinct().to_records() == ([{}] if count else [])


def test_membership_preserves_candidate_provenance_and_plan():
    edges = mf.Frame.from_records([dict(u=0, v=1), dict(u=1, v=2)], schema={'u':'int', 'v':'int'})
    candidate = mf.alg.graph.clique_candidates(edges, top=1)
    domain = candidate.select('vertex').distinct()
    frame = mf.Frame.from_records([{'x':i} for i in range(4)], schema={'x':'int'})
    result = frame.where(mf.col('x').isin(domain))
    assert result._search_runs() == candidate._search_runs()
    assert 'clique_candidates' in str(result.explain())
    assert {r['x'] for r in result.to_records()} == set(domain.column('vertex'))
    restored = mf.frame_from_spec(result.to_spec(), models={}, relations={
        f._query.spec["relation"]: f for f in (frame, edges)})
    assert restored.to_records() == result.to_records()
    assert len(restored._search_runs()) == len(result._search_runs()) == 1
    assert restored._search_runs()[0]['mode'] == result._search_runs()[0]['mode']


@pytest.mark.parametrize('memory', [128, 0.015625])
def test_multiple_sparse_columns_avoid_full_csr_scan(tmp_path, monkeypatch, memory):
    path = tmp_path / 'diagonal.lp'
    path.write_text('Minimize\n obj: x0\nSubject To\n' +
                    ''.join(f' r{i}: x{i} <= 1\n' for i in range(100)) + 'End\n')
    with mf.read(path) as model:
        selected = model.vars.iloc[98:1:-96]
        expected = model.terms(vars=selected).to_records()
        array = model.model.col_idx
        class UnreadableColumns:
            def __len__(self):
                return len(array)
            def __getitem__(self, key):
                raise AssertionError('Sparse selection scanned CSR columns')
        monkeypatch.setattr(model.model, 'col_idx', UnreadableColumns())
        assert model.terms(vars=selected).to_records(memory_mb=memory) == expected
        assert [(r['row'].id, r['var'].id) for r in expected] == [(2, 2), (98, 98)]


def test_verified_receipt_only_keeps_coordinate_maps(m):
    edit = m.edit()
    edit.add_row(m.vars.by_name('x') <= 3)
    with edit.build() as child:
        from mipframe.check.kernel import facts
        assert facts(child)._base is facts(m)
        assert child.model._arrays is None and child.model._db is None
        assert child.lineage.mapping('var',source=m).count()==m.shape[1]
        assert sum(len(d.add_rows) for d in child.diff(m).deltas) == 1
    edit.close()
