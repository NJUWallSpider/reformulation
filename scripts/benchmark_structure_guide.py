"""Run the structure guide's assertions and report per-cell wall times.

Run from the repository root. Optional cProfile instrumentation is deliberately
separate from ordinary wall-time measurements, since it adds substantial overhead.
"""

import argparse
import ast
import contextlib
import cProfile
import io
import json
from pathlib import Path
import resource
import sys
import time


def run_cell(source, namespace, filename):
    tree = ast.parse(source, filename)
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, filename, 'exec'), namespace)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), filename, 'eval'), namespace)
        if value is not None:
            repr(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--notebook', type=Path,
                        default=Path('examples/test10_structure_guide.ipynb'))
    parser.add_argument('--out', type=Path, required=True, help='JSON timing report')
    parser.add_argument('--profile', type=Path, help='Optional cProfile output')
    args = parser.parse_args()
    cells = json.loads(args.notebook.read_text())['cells']
    namespace = {'__name__': '__main__', 'display': lambda *items: [repr(x) for x in items]}
    timings = []
    model_info = None
    profiler = cProfile.Profile() if args.profile else None
    start = time.perf_counter()
    try:
        for index, cell in enumerate(cells):
            if cell['cell_type'] != 'code':
                continue
            code = ''.join(cell['source'])
            cell_start = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                if profiler:
                    profiler.enable()
                try:
                    run_cell(code, namespace, f'{args.notebook}:cell_{index}')
                finally:
                    if profiler:
                        profiler.disable()
            elapsed = time.perf_counter() - cell_start
            timings.append({'cell': index, 'seconds': elapsed})
            print(f'cell {index:2}: {elapsed:.3f}s', flush=True)
            if model_info is None and 'm' in namespace:
                model = namespace['m']
                indexed = model.structure.indexed()
                model_info = {
                    'model_version': model.version,
                    'rows': model.shape[0], 'variables': model.shape[1], 'nonzeros': model.nnz,
                    'tensors': len(indexed.tensors), 'operators': len(indexed.operators),
                }
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report = {
            'notebook': str(args.notebook),
            'python': sys.version,
            'profiled': profiler is not None,
            'seconds': time.perf_counter() - start,
            'cells': timings,
            **model_info,
            'assertions': 'passed',
            'peak_rss_bytes': rss if sys.platform == 'darwin' else rss * 1024,
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + '\n')
    finally:
        if profiler:
            args.profile.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(args.profile))
        if 'm' in namespace:
            namespace['m'].close()


if __name__ == '__main__':
    main()
