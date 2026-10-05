"""Exact numerical I/O audits. These reports are not linear certificates."""

from pathlib import Path
from tempfile import TemporaryDirectory
from fractions import Fraction
from mipframe.store.model_store import build_numerical
from mipframe.frame.schema import DType, SchemaError
from mipframe.check.report import Report


def roundtrip(
    model, path, *, rename_vars=None, rename_rows=None, memory_mb=128, disk_mb=None
):
    """
    MIPFRAME HELP BEGIN
    Numerically reparse an LP and compare exact model facts.

    Full contract: mf.help("api.audit.roundtrip")
    MIPFRAME HELP END
    """
    if Path(path).suffix.lower() != ".lp":
        raise ValueError("The initial roundtrip auditor supports LP files")
    from mipframe.execution.algorithm import ArrayAlgorithmStore
    from mipframe.check.algorithm import DiskBudgetExceeded
    s=ArrayAlgorithmStore((model,),memory_mb=memory_mb,disk_mb=disk_mb,max_output_rows=None)
    temp=TemporaryDirectory(prefix='mipframe-audit-');read=None
    try:
        read=build_numerical(path,Path(temp.name)/'model',memory_mb,format='lp')
        if disk_mb is not None and sum(p.stat().st_size for p in Path(temp.name).rglob('*') if p.is_file())>s.work.disk_limit:
            raise DiskBudgetExceeded('LP audit artifact exceeds disk budget')
        base=model.model;differences=[];maps={}
        def issue(category,expected,observed,row=None,var=None):
            differences.append(dict(category=category,row=row,var=var,expected=str(expected),observed=str(observed)))
        for axis,old,new,mapping in (
            ('var',base.variables,read.variables,rename_vars),('row',base.rows,read.rows,rename_rows)):
            names={rec[1]:rec[0] for rec in new};mapped={};used=set()
            for rec in old:
                ident,name=rec[:2];output=mapping[ident] if mapping is not None else name
                dest=names.get(output)
                if dest is None:issue('missing_'+axis,output,None);continue
                if dest in used:raise SchemaError('Rename mapping is not injective')
                mapped[ident]=dest;used.add(dest);actual=new[dest]
                expected=(rec[2],*(base.num(x) for x in rec[3:6])) if axis=='var' else tuple(base.num(x) for x in rec[2:4])
                observed=(actual[2],*(read.num(x) for x in actual[3:6])) if axis=='var' else tuple(read.num(x) for x in actual[2:4])
                if expected!=observed:issue(axis+'_metadata',expected,observed,**{axis:output})
            for rec in new:
                if rec[0] not in used:issue('extra_'+axis,None,rec[1])
            maps[axis]=mapped
        if base.manifest['sense']!=read.manifest['sense']:issue('objective_sense',base.manifest['sense'],read.manifest['sense'])
        before=base.num(base.manifest['objective_constant']);after=read.num(read.manifest['objective_constant'])
        if before!=after:issue('objective_constant',before,after)
        for r,rr in maps['row'].items():
            expected={maps['var'][v]:c for v,c in base.iter_terms(r) if v in maps['var']}
            for v,c in read.iter_terms(rr):
                old=expected.pop(v,None)
                if old!=c:issue('coefficient',old,c,row=read.name('row',rr),var=read.name('var',v))
            for v,c in expected.items():issue('coefficient',c,0,row=read.name('row',rr),var=read.name('var',v))
        schema=dict(category=DType('str'),row=DType('str',True),var=DType('str',True),expected=DType('str'),observed=DType('str'))
        violations=s.publish(schema,differences)
        return Report(s,not differences,dict(operation='LP roundtrip',differences=len(differences),scope='all numerical model facts'),
            violations,conclusion=dict(kind='io_consistency'),scope=dict(kind='io_consistency',arithmetic='exact',domain=None,complete=True))
    except BaseException:
        s.close();raise
    finally:
        if read is not None:read.close()
        temp.cleanup()
