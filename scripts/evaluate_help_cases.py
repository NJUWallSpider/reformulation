"""Read-only, opt-in documentation regressions against an explicit external corpus.

No historical script is imported or executed. A PASS covers only the scope in
the output record, never every conclusion in a historical analysis narrative.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import mipframe as mf
from mipframe import col


CASES = {
    9: ('data/test9.mps', 'Explicit coupling removal and crossing-row check'),
    10: ('data/test10.mps', 'Full indexed coordinates and exact coefficient join'),
    11: ('data/test11.mps', 'First 128 rows: exact template cross-check'),
    12: ('data/test12.mps', 'Named containment witness and exact zero certificate'),
    16: ('playground/test16/presolved.mps', 'One finite integer domain: local exhaustive verification'),
    17: ('playground/test17/presolved.mps', 'Declared seven-cycle: edge certificates, exact half-sum and integer rounding'),
    30: ('playground/test30/presolved.mps', 'One bounded variable: conditional contradiction'),
    31: ('playground/test31/presolved.mps', 'Full signed equality-incidence candidate submatrix'),
    32: ('playground/test32/presolved.mps', 'First 64 bounded rows: exact residual certificates'),
    33: ('playground/test33/presolved.mps', 'Full two-term precedence candidates; two-root closure union'),
    34: ('playground/test34/presolved.mps', 'First 128 rows: one-hot candidates and disjunction boundary'),
}


def check9(m):
    coupling = m.rows.where(col('name').isin([f'R{i:04d}' for i in range(101, 111)]))
    assert len(coupling) == 10
    local = m.rows - coupling
    components = mf.ops.sets.components(local.terms, set='row', member='var', vertices=m.vars)
    assert components.column('component').nunique() == 10
    crossing = m.terms().join(components, on='var', cardinality='m:1').group_by('row').agg(n=col('component').nunique()).where(col('n') > 1)
    assert len(crossing.refs('row') - coupling) == 0
    r = local.iloc[0]
    m.check.identity(2*r.expr, r.expr+r.expr).require()
    return {'coupling_names': list(coupling.column('name')),
            'components': 10, 'crossing_rows': len(crossing), 'local_rows': len(local)}


def check10(m):
    indexed = m.indexed()
    tensors = [indexed.tensor(r.id) for r in indexed.tensors]
    t = next(t for t in tensors if sorted(t.related("dimensions").where(component="shape").column("size")) == [200, 950] and len(t.vars) == 190000)
    a, b = (t.related("axes").where(axis=axis).rows for axis in (0,1))
    p = t.vars.project(i=a, j=b)
    p.check('product', ('i', 'j')).require()
    assert len(p) == p.nunique(('i', 'j')) == 190000
    distributions = []
    for rows in (a, b):
        coefficients = m.terms(rows=rows, vars=t.vars).select('var', a=col('coef'))
        q = p.join(coefficients, on='var', cardinality='1:1', require='both')
        assert len(q) == 190000
        distributions.append(q.column('a').nunique())
    indexed.verify().require()
    return {'variables': len(p), 'axis_rows': [len(a), len(b)], 'missing': t.one().missing_cells,
            'distinct_coefficients_by_axis': distributions,
            'scope': 'Full discovered tensor and all indexed verification obligations'}


def check11(m):
    rows = m.rows.head(128)
    terms = rows.terms
    a = mf.ops.equal_groups(terms, by='row', rows=('var', 'coef'))
    b = mf.ops.sets.equal_groups(terms, set='row', member='var', value='coef', sets=rows)
    # Empty rows have no observed group in ordinary group-by.
    q = a.select('row', a=col('class')).join(b.select('row', b=col('class')), on='row', cardinality='1:1', require='left')
    assert q.where(col('a') != col('b')).count() == 0
    return {'row_ids': list(rows.ids), 'observed_groups': len(a), 'not_established': 'Full-model symmetry or an inferred local coordinate mapping'}


def check12(m):
    a, b = m.rows.by_name('c22'), m.rows.by_name('c24')
    assert a.lb == a.ub == b.lb == b.ub == 1
    assert a.terms.column('coef').min() == a.terms.column('coef').max() == 1
    assert b.terms.column('coef').min() == b.terms.column('coef').max() == 1
    rows = m.rows.where(col('row').isin([a, b]))
    p = mf.ops.sets.pairs(rows.terms, set='row', member='var', sets=rows, relation='strict_subset')
    assert len(p) == 1
    d = mf.ops.sets.members(rows.terms, set='row', member='var', pairs=p, op='right_only')
    zero = m.check.linear((b.expr-a.expr).eq(0), using=[b.eq_claim, (-1, a.eq_claim)])
    zero.require()
    for x in d.refs('member'):
        m.check.linear(x <= 0, using=[zero], residual='bounds').require()
    return {'rows': [a.id, b.id], 'fixed_zero_vars': list(d.refs('member').ids), 'not_established': 'Historical multi-round propagation counts or optimum'}


def check16(m):
    for x in m.vars:
        if x.kind == 'I' and math.isfinite(x.lb) and math.isfinite(x.ub) and 0 <= math.floor(x.ub)-math.ceil(x.lb) <= 16:
            report = m.check.enumerate(x <= x.ub, vars=[x], max_states=1000)
            report.require()
            return {'var_id': x.id, 'scope': report.scope,
                    'not_established': 'A full-model assignment or historical multi-variable cuts'}
    raise ValueError('No small finite integer domain in the declared snapshot')


def check17(m):
    names = ('C21597', 'C21626', 'C57730', 'C52465', 'C52312', 'C24464', 'C24465')
    witnesses = ('R1095', 'R3304', 'R3683', 'R1338', 'R2065', 'R1110', 'R2066')
    variables = [m.vars.by_name(name) for name in names]
    sources = [m.rows.by_name(name) for name in witnesses]
    assert all(x.is_binary for x in variables)
    reports = []
    for i, row in enumerate(sources):
        target = variables[i] + variables[(i+1) % len(variables)] <= 1
        report = m.check.linear(target, using=[row.ub_claim], residual='bounds')
        report.require()
        reports.append(report)
    selected = m.vars.where(col('var').isin(variables))
    rows = m.rows.where(col('row').isin(sources))
    membership = m.terms(rows=rows, vars=selected).select(set=col('var'), member=col('row'))
    pairs = mf.ops.sets.pairs(membership, sets=selected, relation='overlap')
    edges = pairs.select(u=col('left'), v=col('right'))
    cycles = mf.alg.graph.odd_cycle_candidates(edges, vertices=selected, lengths=(7,), chordless=False)
    assert len(cycles.relations['summary']) > 0
    expression = m.linexpr(vars=selected)
    half = m.check.linear(expression <= mf.q('7/2'), using=[(mf.q('1/2'), r) for r in reports])
    half.require()
    cut = m.check.round(expression <= 3, from_=half)
    cut.require()
    return {'var_ids': [x.id for x in variables], 'source_row_ids': [r.id for r in sources],
            'verified_edges': 7, 'candidate_cycles': len(cycles.relations['summary']),
            'rounded_rhs': 3, 'domain': cut.scope['domain'],
            'not_established': 'Full cycle enumeration, LP violation, optimum, or solver speedup'}


def check30(m):
    x = next(x for x in m.vars if math.isfinite(x.ub))
    hypothesis = x >= x.ub+1
    report = m.check.infeasible(using=[x.ub_claim, hypothesis], assume=[hypothesis])
    report.require()
    assert report.scope['assumptions']
    return {'var_id': x.id, 'scope': report.scope, 'not_established': 'An unconditional fixing, infeasibility, or the historical objective bound'}


def check31(m):
    rows = m.rows.where(col('lb') == col('ub'))
    terms = rows.terms
    stats = terms.group_by('var').agg(n=col('coef').count(), lo=col('coef').min(), hi=col('coef').max())
    selected = stats.where((col('n') == 2) & (col('lo') == -1) & (col('hi') == 1)).refs('var')
    terms = m.terms(rows=rows, vars=selected)
    tail = terms.where(coef=1).select('var', u=col('row'))
    head = terms.where(coef=-1).select('var', v=col('row'))
    edges = tail.join(head, on='var', cardinality='1:1', require='both')
    assert len(edges) == len(selected) and len(edges) > 0
    vertices = edges.refs('u') | edges.refs('v')
    c = mf.ops.graph.components(edges, vertices=vertices)
    scc = mf.ops.graph.scc(edges, vertices=vertices)
    return {'rows_examined': len(rows), 'arcs': len(edges), 'active_vertices': len(vertices),
            'components': c.column('component').nunique(), 'sccs': scc.column('scc').nunique(),
            'not_established': 'Unit supplies, integrality, or separation from excluded constraints'}


def check32(m):
    proven = []
    for r in m.rows.head(64):
        if math.isfinite(r.ub):
            m.check.linear(r.expr <= r.ub, using=[r.ub_claim], residual='bounds').require()
            proven.append(r.id)
    assert proven
    return {'row_ids': proven, 'scope': 'Exact original-side residual replay; no bound improvement is asserted',
            'not_established': 'The 43/39 bound from a different transformed snapshot'}


def check33(m):
    # Preserve all intermediate vertices; only query endpoints are restricted.
    rows = m.rows.where((col('ub') == 0) & (col('nnz') == 2))
    t = rows.terms
    stats = t.group_by('row').agg(lo=col('coef').min(), hi=col('coef').max())
    rows = stats.where((col('lo') == -1) & (col('hi') == 1)).refs('row')
    t = rows.terms
    edges = t.where(coef=1).select('row', u=col('var')).join(t.where(coef=-1).select('row', v=col('var')), on='row', cardinality='1:1', require='both')
    vertices = edges.refs('u') | edges.refs('v')
    roots = vertices.head(2)
    assert len(roots) == 2
    closure = mf.ops.graph.reachability(edges, vertices=vertices, sources=roots, include_self=True)
    membership = closure.select(set=col('source'), member=col('target'))
    pairs = mf.ops.sets.pairs(membership, sets=roots, relation='overlap')
    resources = (m.rows-rows).head(8).terms.select(member=col('var'), resource=col('row'), value=col('coef'))
    usage = mf.ops.sets.pair_aggregate(membership, pairs, resources, by='resource')
    return {'precedence_rows': len(rows), 'root_ids': list(roots.ids), 'reachable_pairs': len(closure),
            'resource_rows': list((m.rows-rows).head(8).ids), 'usage_records': len(usage),
            'not_established': 'Historical named-root capacity value or unconditional conflicts without domain and sign checks'}


def check34(m):
    rows = m.rows.head(128)
    one_hot = []
    for r in rows:
        if r.lb == r.ub == 1 and len(r.vars) and all(t.coef == 1 and t.var.is_binary for t in r.terms):
            one_hot.append(r.id)
    assert mf.help('limitation.disjunction', format='json')['results']
    return {'row_ids': list(rows.ids), 'verified_one_hot_rows': one_hot,
            'not_established': 'General continuous disjunctive hull, variable elimination, or objective preservation'}


CHECKS = {n: globals()[f'check{n}'] for n in CASES}


def run(root, cases):
    results = []
    for n in cases:
        relative, scope = CASES[n]
        path = root / relative
        record = dict(case=n, input=str(path.resolve()), scope=scope, status='NOT_RUN', phase='input')
        results.append(record)
        if not path.is_file():
            record['reason'] = 'Required snapshot is missing; no fallback or historical script execution'
            continue
        print(f'Reading case {n}: {path}', flush=True)
        with path.open('rb') as f:
            record['sha256'] = hashlib.file_digest(f, 'sha256').hexdigest()
        try:
            record['phase'] = 'load'
            with mf.read(path) as m:
                record.update(uid=m.uid, version=m.version, shape=list(m.shape))
                record['phase'] = 'check'
                record['checks'] = CHECKS[n](m)
            record['status'] = 'PASS'
        except Exception as error:
            record.update(status='FAIL', error=type(error).__name__, diagnostic=str(error))
            print(f'{type(error).__name__}: {error}', flush=True)
        print(f"Case {n}: {record['status']}", flush=True)
    return {'format': 1, 'package_version': mf.__version__, 'online_agent_evaluation': 'NOT_RUN', 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', type=int, choices=sorted(CASES), default=sorted(CASES))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.data_root, args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=args.output.parent, prefix='.help-evaluation-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(result, f, indent=2, default=str)
            f.write('\n')
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return 0 if all(r['status'] == 'PASS' for r in result['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
