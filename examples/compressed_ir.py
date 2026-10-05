"""Read a cheap scan, then explicitly query exact local shapes."""
from pathlib import Path
import mipframe as mf

with mf.read(Path(__file__).parent / 'data' / 'tiny.lp') as model:
    print(model.ir.show())
    snapshot = model.ir.to_dict()
    shapes = model.ir.row_shapes()
    rows = shapes.head(1).rows
    print(rows.terms.head(5))
    print(model.ir.name_templates('var').related('slots'))
    # These explicit accesses are lazy and do not alter the default snapshot.
    model.ir.structure
    assert model.ir.to_dict() == snapshot

assert snapshot['schema_version'] == '0.4'
