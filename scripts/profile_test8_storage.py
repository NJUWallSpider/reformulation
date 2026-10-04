"""Isolated, digest-preserving storage ablations for the test8 investigation."""
import argparse
import inspect
import json
import statistics
import time
from pathlib import Path
from fractions import Fraction

import numpy as np
from mipframe.store import model_store as ms

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('native', type=Path)
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
manifest = json.loads((args.native/'manifest.json').read_text())
results = {}

def bench(name, f, repetitions=3):
    values = []
    for _ in range(repetitions):
        start = time.perf_counter()
        value = f()
        values.append(time.perf_counter()-start)
    results[name] = {'seconds': values, 'median_seconds': statistics.median(values)}
    return value

source = inspect.getsource(ms.content_digest)
# Compare the production bounded pool with the original point-lookup oracle.
start = source.index("        pool, used =")
end = source.index("        emit((sense, number(constant)))", start)
original_lookup = '        def number(ident):\n            return None if ident is None else db.execute("SELECT numerator,denominator FROM numbers WHERE id=?", (ident,)).fetchone()\n'
namespace = dict(ms.__dict__)
exec(compile(source[:start]+original_lookup+source[end:], '<point lookup digest oracle>', 'exec'), namespace)
params = (args.native, manifest['sense'],manifest['objective_constant'])
baseline = bench('content_digest_point_lookup',lambda:namespace['content_digest'](*params))
pooled = bench('content_digest_bounded_pool',lambda:ms.content_digest(*params))
assert baseline == pooled == manifest['model_version']
results['digest_equal'] = True

store = ms.NumericalStore(args.native)
try:
    nums = {i:Fraction(int(n),int(d)) for i,n,d in store.db.execute('SELECT id,numerator,denominator FROM numbers')}
    ids = np.asarray(store.coeff_id).tolist()
    bench('cached_num_scan', lambda:[store.num(i) for i in ids])
    bench('pooled_num_scan', lambda:[nums[i] for i in ids])
    results['distinct_numbers'] = len(nums)
    results['nnz'] = len(ids)
    # Both variants scan every column once, using the existing vectorized CSC path.
    def searchsorted_rows():
        return [np.searchsorted(store.row_ptr,store.col_positions[int(store.col_ptr[v]):int(store.col_ptr[v+1])],side='right')-1 for v in range(len(store.variables))]
    row_index = np.searchsorted(store.row_ptr,store.col_positions,side='right')-1
    def cached_rows():
        return [row_index[int(store.col_ptr[v]):int(store.col_ptr[v+1])] for v in range(len(store.variables))]
    a=bench('csc_searchsorted_all_columns',searchsorted_rows)
    b=bench('csc_cached_row_index',cached_rows)
    assert all(np.array_equal(x,y) for x,y in zip(a,b))
    results['row_index_bytes_int64']=row_index.nbytes
finally:
    store.close()
args.output.write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
