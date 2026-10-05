"""Immutable MPS patch planning; serialization has no proof authority."""
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from math import inf
import hashlib
import json
import numpy as np
from mipframe.data.ids import IDSet
from mipframe.data.runtime import WorkContext
from mipframe.store.source_mps import SECTIONS,HEADER,FIXED,SELECTED,MARKER,CONTINUATION
from mipframe.store.numbers import MIPFrameError,decimal


class PatchNotSupportedError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Strict patch output cannot represent this edit or source format.

    Full contract: mf.help("api.PatchNotSupportedError")
    MIPFRAME HELP END
    """
    pass
class SourceChangedError(MIPFrameError): pass


def row_kind(lo,hi):
    return 'N' if lo==-inf and hi==inf else 'L' if lo==-inf else 'G' if hi==inf else 'E'


def pair_record(name,pairs,fmt):
    if fmt=='fixed':
        fields=[name,*[x for pair in pairs for x in pair]]
        if len(name)>8 or any(len(r)>8 or len(str(v))>12 for r,v in pairs):
            raise PatchNotSupportedError('Edited values do not fit fixed MPS fields')
        text='    '+f'{name:<8}  '+f'{pairs[0][0]:<8}  {str(pairs[0][1]):>12}'
        if len(pairs)>1:text+='   '+f'{pairs[1][0]:<8}  {str(pairs[1][1]):>12}'
        return (text+'\n').encode()
    if any(any(c.isspace() for c in token) for token in [name,*[r for r,v in pairs]]):
        raise PatchNotSupportedError('Free MPS names cannot contain whitespace')
    return ('    '+name+'  '+'  '.join(str(x) for p in pairs for x in p)+'\n').encode()


def bound_record(kind,vector,name,value,fmt):
    token='' if value is None else decimal(value)
    if fmt=='fixed':
        if len(vector)>8 or len(name)>8 or len(token)>12:raise PatchNotSupportedError('Edited bound does not fit fixed MPS')
        return (f' {kind:<2} {vector:<8}  {name:<8}'+('  '+f'{token:>12}' if token else '')+'\n').encode()
    if any(any(c.isspace() for c in x) for x in (vector,name)):raise PatchNotSupportedError('Free MPS names cannot contain whitespace')
    return (f' {kind} {vector}  {name}'+('  '+token if token else '')+'\n').encode()


@dataclass(frozen=True)
class PatchPlan:
    """
    MIPFRAME HELP BEGIN
    Immutable MPS serialization plan with support reasons and physical record actions.

    Full contract: mf.help("api.PatchPlan")
    MIPFRAME HELP END
    """
    source: object
    source_sha256: str | None
    target_revision: str
    mode: str
    reasons: tuple
    counts: object
    output_format: str
    actions: object = None
    records: object = None
    changed_rows: object = None
    changed_vars: object = None
    rebuild_domains: bool = False
    objective_changed: bool = False
    info: object = None
    resource: object = None
    target: object = None
    def __repr__(self):
        return f'<PatchPlan {self.mode}: {dict(self.counts)}'+(f'; {"; ".join(self.reasons)}' if self.reasons else '')+'>'
    @property
    def ranges(self):
        """Compact action runs; ranges address source physical records."""
        if self.actions is None:return ()
        def runs():
            start=0
            for i in range(1,len(self.actions)+1):
                if i==len(self.actions) or self.actions[i]!=self.actions[start]:
                    yield (start,i,('COPY','SKIP','REWRITE')[int(self.actions[start])]);start=i
        return runs()


def patch_plan(owner,*,format='mps',memory_mb=128,disk_mb=None):
    from mipframe.check.kernel import Facts,facts
    f=owner if isinstance(owner,Facts) else facts(owner)
    root=f;chain=[]
    while root.parent is not None:chain.append(root.delta);root=root.parent
    def unsupported(reason):
        return PatchPlan(None,root.provenance.get('source_sha256'),f.revision,'CANONICAL_REQUIRED',
            (reason,),MappingProxyType({}),'free',target=f)
    if format!='mps':return unsupported('Source patching supports MPS only')
    if any(d.add_rows or d.add_vars or d.objective is not None or d.sense for d in chain):
        return unsupported('Added entities or an objective replacement require canonical output')
    directory=root.data.path
    source=directory/'source.mps'
    if not source.is_file():source=Path(root.provenance.get('source',''))
    if root.provenance.get('format')!='mps' or not source.is_file():return unsupported('MPS source bytes are unavailable')
    if not (directory/'source_info.json').is_file() or 'source_records' not in root.data.arrays:
        return unsupported('MPS source index is unavailable; re-import for patch support')
    info=json.loads((directory/'source_info.json').read_text())
    from mipframe.store.source_mps import SourceRecords
    records=SourceRecords(root.data.arrays['source_records'],root.data.arrays.get('source_contexts'))
    fmt='fixed'
    from itertools import chain as iterchain
    objective=f.constant!=root.constant
    def live(axis,i):
        if i<0:return i==-1
        mask=getattr(f,'_'+axis+'_live',None)
        return True if mask is None else bool(mask[i])
    work=WorkContext(memory_mb,disk_mb)
    rows=IDSet((r for r in iterchain.from_iterable(iterchain(d.row_bounds,d.substitution.shifts if d.substitution else ()) for d in chain) if live('row',r)),work=work)
    variables=IDSet((v for v in iterchain.from_iterable(iterchain(d.var_bounds,d.kinds) for d in chain) if live('var',v)),work=work)
    domains=bool(variables)  # Only surviving bounds affect emitted defaults and markers.
    actions=work.array(len(records),'u1',zero=True)
    copied=skipped=rewritten=0
    # Continuations must refer to the same column after preceding deletions.
    last_column=-2
    try:
        def member(ids,keys):
            if not len(ids):return np.zeros(len(keys),dtype=bool)
            pos=np.searchsorted(ids,keys)
            return (pos<len(ids))&(ids[np.minimum(pos,len(ids)-1)]==keys)
        def live_many(axis,ids):
            mask=getattr(f,'_'+axis+'_live',None)
            valid=ids>=0
            result=ids==-1
            result[valid]=True if mask is None else mask[ids[valid]]
            return result
        block=max(1,work.memory//512);numeric_found=False;needs_auto=False
        for start in range(0,len(records),block):
            chunk=records[start:start+block];sec=chunk['section'];flags=chunk['flags']
            v=chunk['var'];a=chunk['row1'];b=chunk['row2'];act=actions[start:start+len(chunk)]
            ordinary=(flags&(HEADER|MARKER))==0
            needs_auto |= bool(np.any((flags&CONTINUATION)!=0))
            numeric=ordinary&(sec>=4)&(sec<=8)&((a!=-2)|(v!=-2))
            if np.any(numeric):
                numeric_found=True
                if np.any((flags[numeric]&FIXED)==0):fmt='free'
            row=ordinary&(sec==4)&(a>=0)
            alive=live_many('row',a);changed=member(rows.array,a)
            act[row&~alive]=1;act[row&alive&changed]=2
            column=ordinary&(sec==5)&(v>=0)
            av=live_many('var',v);ab=live_many('row',b)
            survive=alive|ab
            act[column&(~av|~survive)]=1
            partial=((a!=-2)&~alive)|((b!=-2)&~ab)
            act[column&av&survive&partial]=2
            # Resolve only actual continuations sequentially within column runs.
            for local_index in np.flatnonzero(column&((flags&CONTINUATION)!=0)):
                i=int(local_index);previous=np.flatnonzero(column[:i]&(act[:i]!=1))
                previous_column=int(v[previous[-1]]) if len(previous) else last_column
                if act[i]!=1 and previous_column!=v[i]:act[i]=2
            previous=np.flatnonzero(column&(act!=1))
            if len(previous):last_column=int(v[previous[-1]])
            vector=ordinary&((sec==6)|(sec==7))&(a!=-2)
            keep_a=alive&~changed&~((a==-1)&objective)
            keep_b=ab&~member(rows.array,b)&~((b==-1)&objective)
            act[vector&~(keep_a|keep_b)]=1
            partial=((a!=-2)&~keep_a)|((b!=-2)&~keep_b)|((flags&CONTINUATION)!=0)
            act[vector&(keep_a|keep_b)&partial]=2
            bounds=ordinary&(sec==8)&(v>=0)
            act[bounds&(~av|domains|member(variables.array,v))]=1
            act[ordinary&(sec>=6)&(sec<=8)&((flags&SELECTED)==0)]=1
            if domains:act[(flags&MARKER)!=0]=1
            lengths=chunk['length']
            copied+=int(lengths[act==0].sum());skipped+=int(lengths[act==1].sum());rewritten+=int(lengths[act==2].sum())
        if info['mps_format']=='fixed':fmt='fixed'
        elif not numeric_found:fmt='free'
        actions=work.seal(actions)
        info=MappingProxyType(dict(info,selected=MappingProxyType(dict(info['selected'])),readback_format='auto' if needs_auto and fmt=='free' else fmt))
        counts=dict(rows_dropped=root.nrows-f.nrows,vars_dropped=root.nvars-f.nvars,
                    row_bounds=len(rows),var_bounds=len(variables),copy_bytes=copied,
                    skip_bytes=skipped,rewrite_source_bytes=rewritten,source_bytes=source.stat().st_size,
                    source_index_bytes=records.records.nbytes+(0 if records.contexts is None else records.contexts.nbytes))
        plan=PatchPlan(source,root.provenance.get('source_sha256'),f.revision,'STREAM_PATCH',(),
            MappingProxyType(counts),fmt,actions,records,rows,variables,domains,objective,info,work,f)
        # Preflight exact representation before opening an output file.
        for section in ('RHS','RANGES','BOUNDS'):
            for _ in insertions(plan,section):pass
        return plan
    except PatchNotSupportedError as exc:
        work.close();return unsupported(str(exc))
    except BaseException:work.close();raise


def insertions(plan, section):
    f=plan.target;fmt=plan.output_format
    if section in ('RHS','RANGES'):
        vector=plan.info['selected'][section] or ('rhs' if section=='RHS' else 'rng')
        if section=='RHS' and plan.objective_changed:
            yield pair_record(vector,[(plan.info['objective'],decimal(-f.constant))],fmt)
        for r in plan.changed_rows:
            try:lo,hi=f.row_bounds(r)
            except ValueError:continue
            if section=='RHS':
                b=lo if lo!=-inf else hi if hi!=inf else 0
                # Emit explicit zeros to override an earlier source assignment.
                yield pair_record(vector,[(f.name('row',r),decimal(b))],fmt)
            elif lo!=-inf and hi!=inf and lo!=hi:
                yield pair_record(vector,[(f.name('row',r),decimal(hi-lo))],fmt)
    elif section=='BOUNDS':
        vector=plan.info['selected']['BOUNDS'] or 'bnd'
        ids=f.var_ids() if plan.rebuild_domains else plan.changed_vars
        for v in ids:
            try:_,_,kind,lo,hi,_=f.numeric_var(v)
            except ValueError:continue
            name=f.name('var',v)
            if lo==hi:yield bound_record('FX',vector,name,lo,fmt)
            else:
                yield bound_record('MI' if lo==-inf else 'LO',vector,name,None if lo==-inf else lo,fmt)
                yield bound_record('PL' if hi==inf else 'UP',vector,name,None if hi==inf else hi,fmt)


def execute_patch(plan, output, *, charge=None):
    if plan.mode!='STREAM_PATCH':raise PatchNotSupportedError('; '.join(plan.reasons))
    from mipframe.store.mps import _fixed_fields
    f=plan.target;fmt=plan.output_format;digest=hashlib.sha256();output_digest=hashlib.sha256()
    with plan.resource.fork() as scratch:
        seen=scratch.array(f.var_capacity,'?',zero=True)
        with Path(plan.source).open('rb') as source,open(output,'wb') as out:
            integer=False;marker=0;section='';written_sections=set();last_column=None
            bytes_out=0;raw_copy=0
            def emit(raw):
                nonlocal bytes_out
                if charge is not None:charge.add(len(raw))
                out.write(raw);output_digest.update(raw);bytes_out+=len(raw)
            def marker_for(kind):
                nonlocal integer,marker
                if (kind=='I')!=integer:
                    integer=kind=='I'
                    name=f'M{marker:07d}';marker+=1
                    emit((f"    {name:<8}  'MARKER'                 '{'INTORG' if integer else 'INTEND'}'\n").encode())
            def finish(sec):
                if sec=='COLUMNS':
                    for v in f.var_ids():
                        if not seen[v]:
                            marker_for(f.var_kind(v))
                            emit(pair_record(f.name('var',v),[(plan.info['objective'],'0')],fmt))
                    marker_for('C')
                else:
                    for raw in insertions(plan,sec):emit(raw)
            def missing(before):
                for sec in ('RHS','RANGES','BOUNDS'):
                    if SECTIONS.index(sec)<SECTIONS.index(before) and sec not in written_sections:
                        stream=iter(insertions(plan,sec));first=next(stream,None)
                        if first is not None:
                            emit((sec+'\n').encode());emit(first)
                            for raw in stream:emit(raw)
                        written_sections.add(sec)
            buffer_bytes=max(1,min(1024**2,scratch.memory//16))
            records=plan.records;actions=plan.actions
            boundaries=scratch.array(len(records)+1,'<u8');boundaries[0]=0;count=1
            block=max(1,scratch.memory//256)
            previous_action=previous_section=previous_var=None;previous_special=False
            for start in range(0,len(records),block):
                chunk=records[start:start+block];act=actions[start:start+len(chunk)]
                special=((chunk['flags']&(HEADER|MARKER))!=0)|(act==2)
                changed=np.ones(len(chunk),dtype=bool)
                if len(chunk)>1:changed[1:]=(act[1:]!=act[:-1])|(chunk['section'][1:]!=chunk['section'][:-1])|special[1:]|special[:-1]
                variables=chunk['var']
                if plan.rebuild_domains and len(chunk)>1:
                    changed[1:]|=(chunk['section'][1:]==5)&(act[1:]!=1)&(variables[1:]!=variables[:-1])
                changed[0]=start>0 and (previous_action!=act[0] or previous_section!=chunk['section'][0] or previous_special or special[0] or plan.rebuild_domains and chunk['section'][0]==5 and act[0]!=1 and variables[0]!=previous_var)
                previous_var=variables[-1]
                ids=np.flatnonzero(changed)+start;boundaries[count:count+len(ids)]=ids;count+=len(ids)
                previous_action=act[-1];previous_section=chunk['section'][-1];previous_special=special[-1]
                vs=variables;valid=(chunk['section']==5)&(act!=1)&(vs>=0);seen[vs[valid]]=True
            boundaries[count]=len(records)
            for run in range(count):
                begin,end=map(int,boundaries[run:run+2]);rec=records[begin];action=int(actions[begin])
                special=int(rec['flags'])&(HEADER|MARKER)
                if action in (0,1) and not special:
                    if action==0 and plan.rebuild_domains and int(rec['section'])==5 and int(rec['var'])>=0:marker_for(f.var_kind(int(rec['var'])))
                    length=int(records[end-1]['offset'])+int(records[end-1]['length'])-int(rec['offset'])
                    while length:
                        raw=source.read(min(length,buffer_bytes))
                        if not raw:raise SourceChangedError('Truncated MPS source')
                        digest.update(raw);length-=len(raw)
                        if action==0:emit(raw);raw_copy+=len(raw)
                    continue
                raw=source.read(int(rec['length']));digest.update(raw)
                flags=int(rec['flags']);sec=SECTIONS[int(rec['section'])]
                if flags&HEADER:
                    finish(section);missing(sec)
                    section=sec;written_sections.add(sec)
                    emit(raw);raw_copy+=len(raw);continue
                if sec=='COLUMNS' and flags&MARKER:
                    if not plan.rebuild_domains:
                        integer=b'INTORG' in raw;emit(raw);raw_copy+=len(raw)
                    continue
                v=int(rec['var'])
                if action==1:continue
                if sec=='COLUMNS' and v>=0:
                    seen[v]=True
                    if plan.rebuild_domains:marker_for(f.var_kind(v))
                if action==0:
                    emit(raw);raw_copy+=len(raw);continue
                text=raw.decode()
                fields=(_fixed_fields(text,sec) if flags&FIXED else None) or text.split()
                if sec=='ROWS':
                    r=int(rec['row1']);kind=row_kind(*f.row_bounds(r));name=f.name('row',r)
                    emit((f' {kind}  {name}\n').encode());continue
                if sec in ('COLUMNS','RHS','RANGES'):
                    if sec=='COLUMNS':name=f.name('var',v)
                    else:name=plan.info['selected'][sec] or ('rhs' if sec=='RHS' else 'rng')
                    pairs=[]
                    for k,r in enumerate((int(rec['row1']),int(rec['row2']))):
                        if r==-2:continue
                        try:
                            rowname=plan.info['objective'] if r==-1 else f.name('row',r)
                        except ValueError:continue
                        if sec!='COLUMNS' and (r in plan.changed_rows or r==-1 and plan.objective_changed):continue
                        pairs.append((rowname,fields[2+2*k]))
                    if pairs:emit(pair_record(name,pairs,fmt))
            if digest.hexdigest()!=plan.source_sha256:raise SourceChangedError('MPS source changed since import')
    return dict(copy_bytes=raw_copy,written_bytes=bytes_out,sha256=output_digest.hexdigest())
