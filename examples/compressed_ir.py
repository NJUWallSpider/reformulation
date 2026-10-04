"""Read a factual model map, then drill down into one structural family."""
from pathlib import Path

import mipframe as mf

with mf.read(Path(__file__).parent / 'data' / 'tiny.lp') as model:
    print(model.ir.show())
    snapshot = model.ir.to_dict()
    families = snapshot['structure']['row_families']
    if families:
        record = families[0]
        rows = model.family(f"{record['id']}@{record['depth']}").rows
        view = model.matrix.view(rows=rows, vars=rows.vars)
        print(view.entries.head(5))

# This exact machine snapshot is independent of the closed model.
assert snapshot['schema_version'] == '0.3'
