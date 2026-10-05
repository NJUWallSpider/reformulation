"""Explicit numerical comparison; numerical similarity is not a proof."""
from mipframe.check.kernel import facts


class NumericDiff:
    """
    MIPFRAME HELP BEGIN
    Numerical comparison of unrelated model snapshots returned by Model.diff().

    Full contract: mf.help("api.NumericDiff")
    MIPFRAME HELP END
    """
    def __init__(self,target,source):
        a,b=facts(source),facts(target)
        ar={a.row(r)[1]:r for r in a.row_ids()};br={b.row(r)[1]:r for r in b.row_ids()}
        av={a.var(v)[1]:v for v in a.var_ids()};bv={b.var(v)[1]:v for v in b.var_ids()}
        self.rows_dropped=tuple(ar[n] for n in ar.keys()-br.keys())
        self.rows_added=tuple(br[n] for n in br.keys()-ar.keys())
        self.vars_dropped=tuple(av[n] for n in av.keys()-bv.keys())
        self.vars_added=tuple(bv[n] for n in bv.keys()-av.keys())
        def row(f,r):return f.row(r)[2:],{f.var(v)[1]:c for v,c in f.terms(r)}
        self.rows_changed=tuple(br[n] for n in ar.keys()&br.keys() if row(a,ar[n])!=row(b,br[n]))
        self.vars_changed=tuple(bv[n] for n in av.keys()&bv.keys() if a.var(av[n])[2:]!=b.var(bv[n])[2:])
        self.numerically_equal=not any((self.rows_dropped,self.rows_added,self.vars_dropped,self.vars_added,self.rows_changed,self.vars_changed)) and (a.sense,a.constant)==(b.sense,b.constant)
        self.semantic=dict(coordinates='UNKNOWN',lp='UNKNOWN',mip='UNKNOWN',objective=dict(kind='UNKNOWN'),optimal_value='UNKNOWN')
