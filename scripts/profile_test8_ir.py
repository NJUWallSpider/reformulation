"""Break down warm native load and cold structural analysis on a copied snapshot."""
import argparse
import functools
import importlib
import json
import shutil
import time
from collections import defaultdict
from pathlib import Path

import mipframe as mf

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('native', type=Path)
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
phase = 'warm'
results = defaultdict(lambda:[0,0.0])

def wrap(obj,name,label):
    original=getattr(obj,name)
    @functools.wraps(original)
    def timed(*a,**kw):
        start=time.perf_counter()
        try:return original(*a,**kw)
        finally:
            item=results[phase+':'+label];item[0]+=1;item[1]+=time.perf_counter()-start
    setattr(obj,name,timed)

compressed=importlib.import_module('mipframe.ir.compressed')
refinement=importlib.import_module('mipframe.ir.refinement_store')
discovery=importlib.import_module('mipframe.ir.indexing.discovery')
operators=importlib.import_module('mipframe.ir.indexing.operators')
indexstore=importlib.import_module('mipframe.ir.indexing.store')
wrap(compressed.CompressedIR,'__init__','compressed_IR_inclusive')
wrap(refinement.RefinementStore,'compute','refinement_compute')
for name in ('prepare','discover'):wrap(discovery,name,'indexed_'+name)
wrap(operators,'recover','indexed_recover')
wrap(indexstore.Store,'publish','indexed_publish')
wrap(mf.Model,'_build_ir','all_analyses_inclusive')

start=time.perf_counter()
with mf.load(args.native) as m:
    expected=m.version
results['warm:load']=[1,time.perf_counter()-start]

# Copy only immutable base files. Excluding analysis caches forces real discovery.
cold=args.output/'cold.native'
cold.mkdir()
manifest=json.loads((args.native/'manifest.json').read_text())
for name in ['manifest.json',*manifest['base_sha256'],manifest['edit_artifact']['file']]:
    shutil.copy2(args.native/name,cold/name)
phase='cold'
start=time.perf_counter()
with mf.load(cold) as m:assert m.version==expected
results['cold:load']=[1,time.perf_counter()-start]
(args.output/'timings.json').write_text(json.dumps(dict(results),indent=2))
print(json.dumps(dict(results),indent=2))
