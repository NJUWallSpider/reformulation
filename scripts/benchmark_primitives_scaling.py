"""Independent-process Data Plane scale checks; fixture construction is timed separately.

Synthetic CSR/CSC inputs are already immutable numerical inputs. This does not
measure import speed; frozen test32/test33 separately measure the full I/O path.
"""
import argparse,json,resource,time,uuid
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import mipframe as mf
from mipframe.check.kernel import Facts
from mipframe.data.runtime import WorkContext
from mipframe.frame.array_source import frame_from_columns


class Names:
    def __init__(self,prefix):self.prefix=prefix
    def __getitem__(self,i):return self.prefix+str(i)


class Numbers:
    def __init__(self,zero):self.zero=zero;self.reads=0
    def __getitem__(self,i):
        if self.zero:raise AssertionError('Zero substitution decoded a coefficient')
        self.reads+=1;return 1


class MatrixFacts(Facts):
    def __init__(self,nnz,fixed,value,storage,degree=10):
        self.degree=degree;n=nnz//degree
        if n<degree or n%degree:raise ValueError('nnz must be divisible by degree squared')
        self.nrows=self.nvars=self.row_capacity=self.var_capacity=n
        self.fixed,self.value=fixed,value;self.uid=self.revision=str(uuid.uuid4());self.parent=None
        self.sense='min';self.constant=0;self.provenance=dict(format='synthetic',source='synthetic',sense='min')
        self.numbers=Numbers(value==0);self._nnz=nnz;self.degree_reads=0;self.bound_reads=0
        self.row_ptr=storage.array(n+1,'<u8');self.row_ptr[:]=np.arange(n+1)*degree
        self.col_idx=storage.array(nnz,'<u4');rows=storage.array(nnz,'<u4')
        self.coeff_id=np.broadcast_to(np.array([0],dtype='<u4'),(nnz,))
        chunk=max(1,min(8192,65536//degree))
        for start in range(0,n,chunk):
            stop=min(n,start+chunk);ids=np.arange(start,stop)
            self.col_idx[start*degree:stop*degree]=(ids[:,None]//degree+np.arange(degree)*(n//degree)).ravel()
            rows[start*degree:stop*degree]=((ids%(n//degree))[:,None]*degree+np.arange(degree)).ravel()
        self.data=SimpleNamespace(arrays=dict(col_ptr=self.row_ptr,csc_row_ids=rows,csc_value_ids=self.coeff_id),
            names=dict(row=Names('r'),var=Names('x')),release=lambda:None)
    @property
    def nnz(self):return self._nnz
    def var_bounds(self,v):
        self.bound_reads+=1
        return (self.value,self.value) if v<self.fixed else (0,10)
    def var_kind(self,v):return 'C'
    def row_bounds(self,r):return -float('inf'),100
    def row_degree(self,r):self.degree_reads+=1;return self.degree
    def numeric_var(self,v):return v,None,'C',*self.var_bounds(v),1
    def column(self,v):raise AssertionError('Substitution must use selected CSC incidence')
    def terms(self,r):raise AssertionError('Substitution must not scan the matrix')
    def name(self,axis,i):return ('r' if axis=='row' else 'x')+str(i)


def matrix(args):
    start=time.perf_counter()
    with WorkContext(64) as inputs:
        f=MatrixFacts(args.nnz,args.selected,args.value,inputs,args.degree)
        m=mf.Model._from_facts(f,{})
        preparation=time.perf_counter()-start;t=time.perf_counter()
        edit=m.edit(memory_mb=args.memory)
        edit.substitute_fixed(None if args.auto else range(args.selected))
        selected=time.perf_counter();delta=edit._compiled();compiled=time.perf_counter()
        child=edit.commit();built=time.perf_counter()
        assert child.nnz==args.nnz-args.selected*args.degree
        assert len(delta.degrees)<=args.selected*args.degree
        result=dict(scenario='matrix',nnz=args.nnz,selected=args.selected,value=args.value,auto=args.auto,
            memory_mb=args.memory,degree=args.degree,preparation=preparation,selection=selected-t,compilation=compiled-selected,
            substitution=compiled-t,build=built-compiled,coefficient_decodes=f.numbers.reads,
            row_degree_reads=f.degree_reads,bound_reads=f.bound_reads,selected_incidence=args.selected*args.degree,
            peak_working_bytes=edit._work.ledger.peak_resident,peak_disk_bytes=edit._work.ledger.peak_disk,
            persistent_input_bytes=f.row_ptr.nbytes+f.col_idx.nbytes+f.data.arrays['csc_row_ids'].nbytes,
            compiled_bytes=delta.degrees.ids.nbytes+sum(c.nbytes for c in delta.degrees.columns))
        assert result['peak_working_bytes']<=args.memory*1024**2
        child.close();edit.close();m.close()
        return result


def graph(args):
    start=time.perf_counter();groups=args.edges//3
    # Independent triangles give short, complete paths at million-edge scale.
    base=np.arange(groups,dtype='<i8')*3
    u=np.empty(groups*3,dtype='<i8');v=np.empty_like(u)
    u[0::3]=base;u[1::3]=base+1;u[2::3]=base
    v[0::3]=base+1;v[1::3]=base+2;v[2::3]=base+2
    if args.topology=='chain':
        n=args.vertices
        u=np.concatenate((np.arange(n,dtype='<i8'),np.zeros(n-1,dtype='<i8')))
        v=np.concatenate((np.arange(1,n+1,dtype='<i8'),np.arange(2,n+1,dtype='<i8')))
    frame=frame_from_columns(dict(u=u,v=v),dict(u='int',v='int'))
    preparation=time.perf_counter()-start;t=time.perf_counter()
    result=mf.ops.graph.transitive_reduction(frame,witnesses=True,memory_mb=args.memory,max_output_rows=None)
    elapsed=time.perf_counter()-t
    w=result.witnesses
    if args.topology=='triangles':
        assert result.kept.count()==2*groups and result.removed.count()==groups
        assert result.stats['path_edges']==2*groups
        assert np.all(np.diff(w.path_offsets)==2)
        ids=w.removed_edge_ids;paths=w.path_edge_ids.reshape(-1,2)
        assert np.all(u[ids]==u[paths[:,0]]) and np.all(v[paths[:,0]]==u[paths[:,1]]) and np.all(v[ids]==v[paths[:,1]])
    else:
        assert result.kept.count()==n and result.removed.count()==n-1
        assert result.stats['path_edges']==n*(n+1)//2-1
        for i,edge in enumerate(w.removed_edge_ids):
            path=w.path_edge_ids[int(w.path_offsets[i]):int(w.path_offsets[i+1])]
            assert np.all(path<n) and np.all(v[path[:-1]]==u[path[1:]])
            assert u[path[0]]==u[edge] and v[path[-1]]==v[edge]
    stats=dict(result.stats);result.close()
    assert stats['peak_working_bytes']<=args.memory*1024**2
    return dict(scenario='graph',topology=args.topology,memory_mb=args.memory,preparation=preparation,reduction_and_witness=elapsed,
                persistent_input_bytes=u.nbytes+v.nbytes,**stats)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('scenario',choices=('matrix','graph'))
    p.add_argument('--topology',choices=('triangles','chain'),default='triangles');p.add_argument('--vertices',type=int,default=1000)
    p.add_argument('--nnz',type=int,default=10_000_000);p.add_argument('--edges',type=int,default=1_000_002)
    p.add_argument('--degree',type=int,default=10);p.add_argument('--selected',type=int,default=4);p.add_argument('--value',type=int,default=0)
    p.add_argument('--auto',action='store_true');p.add_argument('--memory',type=float,default=128)
    p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    result=matrix(args) if args.scenario=='matrix' else graph(args)
    result['maxrss']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)


if __name__=='__main__':main()
