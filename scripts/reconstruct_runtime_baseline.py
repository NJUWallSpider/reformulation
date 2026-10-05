"""Reconstruct the synchronized baseline from the two archived source freezes.

Does not use git HEAD, overwrite a freeze, or touch the working source tree.
See benchmarks/runtime-v5/baseline-synchronized.json for exact source hashes.
"""
from pathlib import Path
import ast,shutil,json,hashlib,sys,platform
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path('benchmarks/runtime-v5')
old=root/'baseline-source';sync=root/'synchronized-source';dest=root/'baseline-synchronized'
if dest.exists():raise SystemExit('Baseline already frozen')
shutil.copytree(sync,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
restored=['store/model_store.py','store/mps.py','store/sparse_store.py','store/stream_lp.py','check/kernel.py','frame/base.py','frame/plan.py','frame/serialization.py','ops/_runtime.py']
for name in restored:shutil.copyfile(old/'src/mipframe'/name,dest/'src/mipframe'/name)
def restore_methods(name,methods):
 before=(old/'src/mipframe'/name).read_text();p=dest/'src/mipframe'/name;current=p.read_text()
 def nodes(text):
  out={}
  for cls in ast.parse(text).body:
   if isinstance(cls,ast.ClassDef):
    for n in cls.body:
     if isinstance(n,ast.FunctionDef):out[cls.name+'.'+n.name]=n
  return out
 original=nodes(before);latest=nodes(current);lines=current.splitlines(keepends=True);oldlines=before.splitlines(keepends=True)
 def start(n):return min([n.lineno,*[d.lineno for d in n.decorator_list]])-1
 for key in sorted(methods,key=lambda k:start(latest[k]),reverse=True):
  n=latest[key];o=original.get(key)
  lines[start(n):n.end_lineno]=[] if o is None else oldlines[start(o):o.end_lineno]
 p.write_text(''.join(lines))
restore_methods('edit/delta.py',['DeltaEdit._ids','DeltaEdit._semantics','DeltaEdit.build'])
restore_methods('model.py',['Model._from_store','Model._from_facts'])
for name in ('data','control'):
 shutil.rmtree(dest/'src/mipframe'/name)
for name in ('array_source.py','numpy_backend.py','planner.py'):(dest/'src/mipframe/frame'/name).unlink()
# The completed exporter uses the lifecycle guard. This is a compatibility
# precondition, not an optimization or change to numerical execution.
p=dest/'src/mipframe/store/model_store.py';s=p.read_text();s=s.replace('class NumericalStore:\n','class NumericalStore:\n    def guard(self):\n        if self.db is None:raise MIPFrameError("Model is closed")\n\n',1);p.write_text(s)
manifest=dict(note='Reconstructed synchronized pre-migration baseline: retain completed concurrent check/edit work; remove only individually identified changes from this runtime migration.',whole_files_restored=restored,methods_restored={'edit/delta.py':['DeltaEdit._ids','DeltaEdit._semantics','DeltaEdit.build'],'model.py':['Model._from_store','Model._from_facts']},compatibility_change='NumericalStore.guard required by completed exporter',python=sys.version,platform=platform.platform(),files={str(p.relative_to(dest)):hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.rglob('*') if p.is_file()})
(root/'baseline-synchronized.json').write_text(json.dumps(manifest,indent=2))
print(dest)
