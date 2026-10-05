# Read, prove, commit and export a small model

## When to use
Run a complete first example without an existing model or prepared witnesses.
Inputs: Only mipframe and the Python standard library. All files are temporary and removed at context exit.

## Primitive pattern
Import → select explicit rows → verify retained witness → commit → audited MPS write.

## Minimal code
```python
from pathlib import Path
from tempfile import TemporaryDirectory
import mipframe as mf

with TemporaryDirectory() as directory:
    source = Path(directory) / "input.lp"
    source.write_text("\n".join([
        "Minimize", " obj: x + y", "Subject To",
        " keep: x + y <= 4", " redundant: x + y <= 4",
        "Bounds", " 0 <= x <= 5", " 0 <= y <= 5", "End",
    ]))
    with mf.read(source) as m:
        kept = m.rows.by_name("keep")
        dropped = m.rows.by_name("redundant")
        proof = m.check.linear_rows(
            targets=[dropped.id], witnesses=[[kept.id]],
        ).require()
        with m.edit() as edit:
            edit.drop_rows([dropped.id], because=proof)
            with edit.commit() as final:
                assert final.shape == (1, 2)
                receipt = final.write(Path(directory) / "output.mps", audit="readback")
                assert receipt.verified
                print(receipt.writer, receipt.verified)
```

## What it establishes
The retained row implies the deleted row; commit checks grounded equivalence and readback checks the numerical output.

## What it does not establish
This example supplies its own witness. It does not discover redundant rows or solve the model.
