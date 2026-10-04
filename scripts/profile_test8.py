"""Reproduce the test8 proof/edit workload, with phase timings and profiles.

Run with PYTHONPATH=src python scripts/profile_test8.py --output DIR.
Original public-API proof cells are copied from the specified evaluation log.
No production source or original run artifacts are modified.
"""
import argparse
import cProfile
import contextlib
import functools
import importlib
import json
import platform
import resource
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--input', type=Path, default=Path('/Users/bhz/Code/reform_test/runs/test8-20261003-213957/input/model.lp'))
parser.add_argument('--cells', type=Path, default=Path('benchmarks/test8-profile-20261004/original_cells.json'))
parser.add_argument('--profile', action='store_true')
parser.add_argument('--indexes', action='store_true', help='Experimental missing-index ablation; process-local only')
parser.add_argument('--index-ablation', action='store_true', help='After baseline summary, rerun semantic closure with only extra SQL indexes')
parser.add_argument('--sql-profile', action='store_true', help='Time semantic SELECT statements and capture query plans')
parser.add_argument('--sql-counts', action='store_true', help='Count actual SQLite statements separately from timing-only runs')
parser.add_argument('--audit-replay', action='store_true', help='After the workload, independently replay both complete edit lineages')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
import mipframe as mf

phase_name = 'startup'
measurements = defaultdict(lambda: [0, 0.0])
results = {'python': sys.version, 'platform': platform.platform(), 'mipframe': mf.__file__, 'phases': {}, 'experimental_indexes': args.indexes, 'completed': False}
sql_stats = {}
statement_counts = defaultdict(Counter)
if args.sql_counts:
    import sqlite3
    connect = sqlite3.connect
    def counted_connect(*a, **kw):
        connection = connect(*a, **kw)
        def count(sql):
            statement_counts[phase_name]['statements'] += 1
            if sql.lstrip().upper().startswith('CREATE TABLE'):
                statement_counts[phase_name]['create_table'] += 1
        connection.set_trace_callback(count)
        return connection
    sqlite3.connect = counted_connect

def instrument(obj, name, label=None):
    original = getattr(obj, name)
    @functools.wraps(original)
    def timed(*a, **kw):
        start = time.perf_counter()
        try:
            return original(*a, **kw)
        finally:
            item = measurements[(phase_name, label or name)]
            item[0] += 1
            item[1] += time.perf_counter() - start
    setattr(obj, name, timed)

def flush():
    results['sql_counts'] = dict(statement_counts)
    results['components'] = [{'phase': p, 'name': n, 'calls': v[0], 'seconds': v[1]} for (p,n),v in measurements.items()]
    results['maxrss_bytes_macos'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    (args.output/'timings.json').write_text(json.dumps(results, indent=2, default=str))
    if sql_stats:
        (args.output/'sql.json').write_text(json.dumps(list(sql_stats.values()), indent=2, default=str))

@contextlib.contextmanager
def phase(name):
    global phase_name
    phase_name = name
    print('START', name, flush=True)
    p = cProfile.Profile() if args.profile else None
    if p: p.enable()
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter()-start
        if p:
            p.disable()
            p.dump_stats(str(args.output/(name+'.prof')))
        results['phases'][name] = duration
        flush()
        print('END', name, round(duration,3), flush=True)

buildmod = importlib.import_module('mipframe.edit.build')
storemod = importlib.import_module('mipframe.store.model_store')
preparemod = importlib.import_module('mipframe.edit.prepare')
semanticsmod = importlib.import_module('mipframe.edit.semantics')
batchmod = importlib.import_module('mipframe.check.batch')
instrument(mf.Model, '_build_ir', 'IR_and_indexed')
instrument(buildmod, 'write_lineage')
instrument(buildmod, 'build', 'native_build_inclusive')
instrument(buildmod, 'content_digest', 'build_content_digest')
instrument(storemod, 'content_digest', 'open_content_digest')
instrument(storemod.NumericalStore, '_open', 'native_open_inclusive')
instrument(storemod.Writer, 'arrays', 'matrix_arrays')
instrument(preparemod.Prepared, '_metadata', 'prepare_metadata')
instrument(preparemod.Prepared, '_structure', 'prepare_structure')
instrument(semanticsmod, 'semantic', 'semantic_inclusive')
instrument(batchmod.LinearBatch, 'run', 'batch_run')
instrument(batchmod.LinearBatch, 'add', 'batch_add')
lineagemod = importlib.import_module('mipframe.edit.lineage')
diffmod = importlib.import_module('mipframe.edit.diff')
instrument(lineagemod.Lineage, '_restore', 'lineage_restore_inclusive')
instrument(diffmod.ModelDiff, '_verify_target', 'diff_verify_target')
if args.sql_profile:
    from mipframe.frame.executor import Context
    original_execute = Context.execute
    def sql_execute(self, sql, params=()):
        if phase_name not in ('middle_summary','middle_semantic_index_ablation','middle_diff','final_summary','final_diff') or not any(token in sql for token in ('LEFT JOIN','SELECT o.proof,o.claim','JOIN proofs b')):
            return original_execute(self, sql, params)
        start = time.perf_counter()
        result = original_execute(self, sql, params)
        elapsed = time.perf_counter()-start
        key = (phase_name,sql)
        if key not in sql_stats:
            sql_stats[key] = {'phase':phase_name,'sql':sql,'count':0,'seconds':0,'plan':self.db.execute('EXPLAIN QUERY PLAN '+sql,params).fetchall()}
        sql_stats[key]['count'] += 1
        sql_stats[key]['seconds'] += elapsed
        return result
    Context.execute = sql_execute

original_table = preparemod.Prepared.table
def indexed_table(self, definition):
    t = original_table(self, definition)
    columns = [s.strip().split()[0] for s in definition.split(',')]
    for col in ('proof','claim','fact','binding','op'):
        if col in columns and f'{col} INTEGER PRIMARY KEY' not in definition:
            self.s.execute(f'CREATE INDEX {t}_profile_{col} ON {t}({col})')
    return t
if args.indexes:
    preparemod.Prepared.table = indexed_table

cells = json.loads(args.cells.read_text())
by_title = {c['title']: c['code'].replace(".check.batch('linear',", ".check.linear_batch(") for c in cells}
def display(*a): pass
def replay(title):
    exec(compile(by_title[title], '<original:'+title+'>', 'exec'), globals())

with phase('read'):
    m = mf.read(args.input)
    assert m.shape == (13206, 4914) and m.nnz == 131859
    results['number_count'] = m.model.db.execute('SELECT count(*) FROM numbers').fetchone()[0]

with phase('setup'):
    rowdata={r['row'].id:r for r in m.rows.to_records()}
    vardata={v['var'].id:v for v in m.vars.to_records()}
    terms_by_row=defaultdict(dict)
    for t in m.terms().to_records(): terms_by_row[t['row'].id][t['var'].id]=t['coef']
    fids={k:set() for k in ('F0','F1','F2','F3','F4')}
    for r,d in rowdata.items():
        ts=terms_by_row[r]
        if all(vardata[v]['kind']=='I' for v in ts): f='F0'
        elif d['ub']==1200: f='F2'
        elif d['ub']==0: f='F3'
        elif d['ub']==3: f='F1'
        elif d['ub']==8: f='F4'
        else: raise AssertionError(d)
        fids[f].add(r)
    cont_support={r:{v for v in terms_by_row[r] if vardata[v]['kind']=='C'} for r in fids['F1']|fids['F2']|fids['F4']}
    x_resources=defaultdict(set)
    for r in sorted(fids['F1']):
        for x in cont_support[r]: x_resources[x].add(r)
    cover_plan={}; red_gate_source={}
    for r in sorted(fids['F2']):
        opts=set().union(*(x_resources[x] for x in cont_support[r]))
        contained=[c for c in sorted(opts) if cont_support[r]<=cont_support[c]]
        if contained: red_gate_source[r]=contained[-1]
        uncovered=set(cont_support[r]);chosen=[]
        while uncovered:
            best=min(opts,key=lambda c:(-len(uncovered&cont_support[c]),c))
            assert uncovered&cont_support[best]
            chosen.append(best);uncovered-=cont_support[best]
        cover_plan[r]=chosen
    red_gate_ids=sorted(red_gate_source)
    edges={r:tuple(terms_by_row[r]) for r in fids['F0'] if rowdata[r]['ub']==1}
    local_edges={r:[e for e in sorted(edges) if set(edges[e])<=set(terms_by_row[r])] for r in sorted(fids['F0']) if rowdata[r]['ub']==3}
    card_linear_plan={}
    for r,es in local_edges.items():
        uncovered=set(terms_by_row[r]);chosen=[]
        for e in es:
            if set(edges[e])<=uncovered: chosen.append(e);uncovered-=set(edges[e])
        if len(chosen)+len(uncovered)<=3:card_linear_plan[r]=(chosen,sorted(uncovered))
    activity_switch={}
    for r in fids['F3']:
        ts=terms_by_row[r]
        activity_switch[next(v for v in ts if vardata[v]['kind']=='C')]=next(v for v in ts if vardata[v]['kind']=='I')
    rhandles={r:m.rows.loc[r] for r in rowdata};vhandles={v:m.vars.loc[v] for v in vardata}
    x_resource={x:min(rs) for x,rs in x_resources.items()}
    assert len(red_gate_ids)==441 and len(card_linear_plan)==315 and len(activity_switch)==2457
    print('COVERS',Counter(3*len(c) for r,c in cover_plan.items() if r not in red_gate_ids),flush=True)

with phase('resource_batch'):
    code=by_title['Completing exact batch certificate verification']
    exec(compile(code[code.index('with m.check.linear_batch'):], '<resource>', 'exec'),globals())

code=by_title['Proving tightened gates and activity links']
branch, cases=code.split('case_reports={}\n',1)
with phase('branch_batch'):
    exec(compile(branch,'<branches>','exec'),globals())
with phase('cases'):
    case_reports={}
    exec(compile(cases,'<cases>','exec'),globals())

code=by_title['Building the justified intermediate snapshot']
staging, rest=code.split('display(edit.summary())',1)
with phase('middle_staging'):
    exec(compile(staging,'<middle staging>','exec'),globals())
with phase('middle_summary'):
    print(edit.summary(),flush=True)
if args.index_ablation:
    with phase('middle_semantic_index_ablation'):
        preparemod.Prepared.table = indexed_table
        try:
            assert semanticsmod.semantic(edit._prepared) == edit._prepared.semantic
        finally:
            preparemod.Prepared.table = original_table
with phase('middle_build'):
    middle=edit.build()
with phase('middle_diff'):
    diff_add=middle.diff(m)
    assert diff_add.semantic['mip']=='SAME' and diff_add.semantic['lp']=='SUBSET'
    assert middle.shape==(16923,4914) and middle.nnz==176274

code=by_title['Replacing obsolete gates and links with audited rows']
reverse,rest=code.split('final_edit=middle.edit()',1)
staging,rest=rest.split('display(final_edit.summary())',1)
with phase('reverse_batch'):
    exec(compile(reverse,'<reverse>','exec'),globals())
with phase('final_staging'):
    final_edit=middle.edit()
    exec(compile(staging,'<final staging>','exec'),globals())
with phase('final_summary'):
    print(final_edit.summary(),flush=True)
with phase('final_build'):
    final=final_edit.build()
with phase('final_diff'):
    diff_remove=final.diff(middle)
    assert diff_remove.semantic['lp']=='SAME' and diff_remove.semantic['mip']=='SAME'
    assert final.shape==(12450,4914) and final.nnz==125055
    results['final_semantics']=diff_remove.semantic
    results['final_version']=final.version
    assert final.version == 'dad1643fda059c357f4a9892f42446483130c1350398eb0d0e31545e07c905fd'
with phase('save'):
    final.save(args.output/'final.native')
with phase('native_load'):
    reloaded=mf.load(args.output/'final.native')
    assert reloaded.version==final.version
    reloaded.close()
results['workload_seconds'] = sum(results['phases'].values())
if args.audit_replay:
    with phase('audit_middle_replay'):
        assert middle.diff(m, verify='replay').semantic == diff_add.semantic
    with phase('audit_final_replay'):
        assert final.diff(middle, verify='replay').semantic == diff_remove.semantic
print('SUCCESS',flush=True)
results['completed'] = True
flush()
