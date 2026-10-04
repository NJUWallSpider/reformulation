# Native family compatibility fixture

`families-native-v3.zip` was generated with the original, pre-refactor
mipframe 0.8.0 wheel (SHA-256 `a23750638f5671f8c99cba75a8e6935fdd8cabc1d323c07e915c8c2e2eef1b04`).
It is a synthetic three-row, two-variable LP, with no external model data.

The archive contains the input LP, native v3 snapshot, and JSON query specifications
with expected row/variable membership labels. The original wheel prepared template
refinement through depth 3 and indexed coordinates at depth 3 before saving;
the membership queries cover row depth 2, variable depth 1, and exact row depth 2.

Keep the binary snapshot and query expectations fixed. The compatibility regression
must load an artifact produced by the old release, not generate one with the code
under test. Source distribution verification runs this regression too.
