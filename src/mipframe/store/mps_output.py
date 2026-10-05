"""Delta-aware MPS output and numerical-only artifact readback."""
from pathlib import Path
from tempfile import NamedTemporaryFile
from fractions import Fraction
from math import inf
import hashlib
import json
import os
import time
from mipframe.check.kernel import facts, exact
from mipframe.store.numbers import decimal


def atomic_text(path,text):
    path=Path(path)
    if path.exists():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile('w',dir=path.parent,delete=False,encoding='utf-8') as f:
        tmp=Path(f.name)
        try:
            f.write(text);f.close()
            os.link(tmp,path)
        finally:tmp.unlink(missing_ok=True)


def number(token):
    try:return int(token)
    except ValueError:return exact(token.replace('D','E').replace('d','e'))


def parse_numeric(path,*,mps_format='auto',rhs=None,bounds=None,ranges=None,coefficient_sink=None,metadata_limits=None):
    """Independent numerical reader; no store creation, IR, or certificate replay."""
    from mipframe.store.mps import _fixed_fields
    rows={};variables={};matrix={};rhsvals={};rangevals={};objective=None
    section=None;intmode=False;lastcol=None;lastset={};sense='min';ended=False
    selections={'RHS':rhs,'BOUNDS':bounds,'RANGES':ranges};sets={k:set() for k in selections}
    def variable(name):
        if not name:raise ValueError('Missing variable name')
        if metadata_limits is not None and name not in variables and len(variables)>=metadata_limits[0]:raise ValueError('Artifact variable limit exceeded')
        return variables.setdefault(name,['C',0,inf,0])
    def selected(section,name):
        if metadata_limits is not None and name not in sets[section] and len(sets[section])>=8:
            from mipframe.store.readback import CacheOverflow
            raise CacheOverflow()
        sets[section].add(name)
        choice=selections[section]
        if choice is None and len(sets[section])>1:raise ValueError('Multiple MPS vectors require explicit selection')
        return choice is None or name==choice
    with open(path,encoding='utf-8') as stream:
        for line in stream:
            if metadata_limits is not None and len(line)>128:
                from mipframe.store.readback import CacheOverflow
                raise CacheOverflow()
            if not line.strip() or line.startswith('*'):continue
            if ended:raise ValueError('Data after ENDATA')
            p=line.split()
            if not line[0].isspace():
                section=p[0]
                if section not in ('NAME','OBJSENSE','OBJNAME','ROWS','COLUMNS','RHS','RANGES','BOUNDS','ENDATA'):raise ValueError('Unsupported MPS section')
                if section=='ENDATA':ended=True
                elif section=='OBJSENSE' and len(p)>1:sense=p[1].lower()
                elif section=='OBJNAME' and len(p)>1:objective=p[1]
                continue
            if section=='OBJSENSE':sense=p[0].lower();continue
            if section=='OBJNAME':objective=p[0];continue
            if section=='COLUMNS' and len(p)>1 and p[1].strip("'")=='MARKER':
                token=p[-1].strip("'")
                if token not in ('INTORG','INTEND') or (token=='INTORG')==intmode:raise ValueError('Unbalanced integer markers')
                intmode=token=='INTORG';continue
            if mps_format!='free':
                fixed=_fixed_fields(line,section)
                if fixed is not None:p=fixed
                elif mps_format=='fixed':raise ValueError('Invalid fixed MPS')
            if section=='ROWS':
                if len(p)!=2 or p[0] not in ('E','L','G','N') or p[1] in rows:raise ValueError('Invalid/duplicate row')
                kind,name=p
                if metadata_limits is not None and len(rows)>=metadata_limits[1]+1:raise ValueError('Artifact row limit exceeded')
                rows[name]=kind
                if coefficient_sink is None or kind=='N':matrix[name]={}
                if kind=='N' and objective is None:objective=name
            elif section=='COLUMNS':
                if not p[0]:p[0]=lastcol
                if len(p) not in (3,5) or not p[0]:raise ValueError('Invalid column record')
                lastcol=p[0];v=variable(lastcol)
                if intmode and v[0]=='C':v[0]='I';v[2]=1
                for k in range(1,len(p),2):
                    row=p[k]
                    if row not in rows:raise ValueError('Unknown coefficient row')
                    c=number(p[k+1])
                    if coefficient_sink is not None and row!=objective:coefficient_sink(row,lastcol,c)
                    else:matrix[row][lastcol]=matrix[row].get(lastcol,0)+c
            elif section in ('RHS','RANGES'):
                if len(p) not in (3,5):raise ValueError('Invalid RHS/range record')
                if not p[0]:p[0]=lastset.get(section,'')
                lastset[section]=p[0]
                if not selected(section,p[0]):continue
                dest=rhsvals if section=='RHS' else rangevals
                for k in range(1,len(p),2):
                    if p[k] not in rows:raise ValueError('Unknown RHS/range row')
                    dest[p[k]]=number(p[k+1])
            elif section=='BOUNDS':
                if len(p) not in (3,4):raise ValueError('Invalid bound record')
                kind,bset,name=p[:3]
                if not selected(section,bset):continue
                v=variable(name);a=number(p[3]) if len(p)==4 else None
                if kind in ('LO','UP','FX','LI','UI') and a is None:raise ValueError('Missing bound value')
                if kind in ('LO','LI'):v[1]=a
                elif kind in ('UP','UI'):v[2]=a
                elif kind=='FX':v[1]=v[2]=a
                elif kind=='FR':v[1:3]=[-inf,inf]
                elif kind=='MI':v[1]=-inf
                elif kind=='PL':v[2]=inf
                elif kind=='BV':v[:3]=['I',0,1]
                else:raise ValueError('Unsupported bound type')
                if kind in ('LI','UI'):v[0]='I'
            else:raise ValueError('Unexpected MPS data')
    if not ended or intmode or objective not in rows or sense not in ('min','max'):raise ValueError('Incomplete MPS')
    for k,choice in selections.items():
        if choice is not None and choice not in sets[k]:raise ValueError('Selected MPS vector missing')
    if objective in rangevals:raise ValueError('Range on objective')
    for v,c in matrix.pop(objective).items():variables[v][3]=c
    rows.pop(objective)
    out={}
    for r,kind in rows.items():
        b=rhsvals.get(r,0);lo=b if kind in ('E','G') else -inf;hi=b if kind in ('E','L') else inf
        if r in rangevals:
            a=rangevals[r]
            if kind=='L':lo=b-abs(a)
            elif kind=='G':hi=b+abs(a)
            elif kind=='E':lo,hi=(b,b+a) if a>=0 else (b+a,b)
            else:raise ValueError('Range on free row')
        out[r]=(lo,hi)
        if coefficient_sink is None:matrix[r]={v:c for v,c in matrix[r].items() if c}
    return dict(rows=out,variables={v:tuple(x) for v,x in variables.items()},
                matrix=matrix,sense=sense,constant=-rhsvals.get(objective,0))


def compare_readback(f,path,**options):
    from mipframe.store.readback import compare
    return compare(f,path,**options)


def _canonical(f,path,*,charge=None):
    def name(axis,i):
        value=f.name(axis,i)
        if not value or any(c.isspace() for c in value):raise ValueError('Free MPS names must not contain whitespace')
        return value
    objective='__mf_objective'
    while any(f.name('row',r)==objective for r in f.row_ids()):objective+='_'
    fmt=lambda x:str(x) if isinstance(x,int) else decimal(x)
    digest=hashlib.sha256();written=0
    with open(path,'wb') as out:
        def emit(text):
            nonlocal written
            raw=text.encode('utf8')
            if charge is not None:charge.add(len(raw))
            out.write(raw);digest.update(raw);written+=len(raw)
        emit('NAME          mipframe\n')
        if f.sense!='min':emit(f'OBJSENSE\n {f.sense.upper()}\n')
        emit(f'ROWS\n N  {objective}\n')
        for r in f.row_ids():
            lo,hi=f.row_bounds(r)
            kind='N' if lo==-inf and hi==inf else 'G' if hi==inf else 'L' if lo==-inf else 'E'
            emit(f' {kind}  {name("row",r)}\n')
        emit('COLUMNS\n');integer=False;marker=0
        for v in f.var_ids():
            label=name('var',v);_,_,kind,lo,hi,cost=f.numeric_var(v)
            if (kind=='I')!=integer:
                integer=kind=='I';emit(f"    MARK{marker:06d}  'MARKER'  '{'INTORG' if integer else 'INTEND'}'\n");marker+=1
            column=iter(f.column(v));first=next(column,None)
            if cost or first is None:emit(f'    {label}  {objective}  {fmt(cost)}\n')
            if first is not None:
                from itertools import chain
                for r,c in chain((first,),column):emit(f'    {label}  {name("row",r)}  {fmt(c)}\n')
        if integer:emit(f"    MARK{marker:06d}  'MARKER'  'INTEND'\n")
        emit('RHS\n')
        if f.constant:emit(f'    rhs  {objective}  {fmt(-f.constant)}\n')
        ranged=False
        for r in f.row_ids():
            lo,hi=f.row_bounds(r);value=lo if lo!=-inf else hi if hi!=inf else 0
            if value:emit(f'    rhs  {name("row",r)}  {fmt(value)}\n')
            ranged|=lo!=-inf and hi!=inf and lo!=hi
        if ranged:
            emit('RANGES\n')
            for r in f.row_ids():
                lo,hi=f.row_bounds(r)
                if lo!=-inf and hi!=inf and lo!=hi:emit(f'    rng  {name("row",r)}  {fmt(hi-lo)}\n')
        emit('BOUNDS\n')
        for v in f.var_ids():
            label=name('var',v);lo,hi=f.var_bounds(v)
            if lo==hi:emit(f' FX bnd  {label}  {fmt(lo)}\n');continue
            emit(f' MI bnd  {label}\n' if lo==-inf else f' LO bnd  {label}  {fmt(lo)}\n')
            emit(f' PL bnd  {label}\n' if hi==inf else f' UP bnd  {label}  {fmt(hi)}\n')
        emit('ENDATA\n')
    return dict(copy_bytes=0,written_bytes=written,sha256=digest.hexdigest())


def write_mps(owner,path,*,strategy='auto',audit='readback',memory_mb=128,disk_mb=None):
    from mipframe.store.patch import patch_plan,execute_patch,PatchNotSupportedError
    if audit not in ('readback',None):raise ValueError('audit must be readback or None')
    if strategy not in ('auto','patch','canonical'):raise ValueError('Unknown write strategy')
    f=facts(owner);path=Path(path)
    if path.exists():raise FileExistsError(path)
    start=time.perf_counter()
    plan=patch_plan(f,memory_mb=memory_mb,disk_mb=disk_mb) if strategy!='canonical' else None
    if strategy=='patch' and plan.mode!='STREAM_PATCH':raise PatchNotSupportedError('; '.join(plan.reasons))
    patched=plan is not None and plan.mode=='STREAM_PATCH'
    planned=time.perf_counter()
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(dir=path.parent,delete=False) as stream:tmp=Path(stream.name)
    from mipframe.data.runtime import WorkContext,DiskReservation
    work=plan.resource if patched else WorkContext(memory_mb,disk_mb)
    charge=DiskReservation(work)
    try:
        counts=execute_patch(plan,tmp,charge=charge) if patched else (_canonical(f,tmp,charge=charge) or {})
        written=time.perf_counter()
        receipt=compare_readback(f,tmp,mps_format=plan.info['readback_format'] if patched else 'free',memory_mb=memory_mb,disk_mb=disk_mb,work=work) if audit=='readback' else dict(status='UNVERIFIED',kind='artifact_readback')
        receipt.update(writer='stream_patch' if patched else 'canonical',sha256=counts.pop('sha256',None) or _file_hash(tmp),
                       timings=dict(write=written-start,planning=planned-start,serialization=written-planned,readback=time.perf_counter()-written),**counts)
        receipt.update(peak_working_bytes=work.ledger.peak_resident,peak_disk_bytes=work.ledger.peak_disk,
                       source_bytes=plan.counts.get('source_bytes',0) if patched else 0,
                       source_index_bytes=plan.counts.get('source_index_bytes',0) if patched else 0)
        os.link(tmp,path)
        from mipframe.store.output import WriteReceipt
        return WriteReceipt(receipt)
    finally:
        tmp.unlink(missing_ok=True);charge.close()
        if not patched:work.close()


def _deltas(f):
    while f.parent is not None:
        yield f.delta;f=f.parent


def _file_hash(path):
    from mipframe.store.artifacts import sha256
    return sha256(path)
