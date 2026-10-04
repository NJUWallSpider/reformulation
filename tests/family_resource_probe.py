"""Family view FD stress, isolated from pytest's process limits."""
import gc
import json
import os
from pathlib import Path
import resource
import sys
from tempfile import TemporaryDirectory
import mipframe as mf


def fds():
    return len(os.listdir('/proc/self/fd' if os.path.isdir('/proc/self/fd') else '/dev/fd'))


def run(count):
    errors=[]
    sys.unraisablehook=lambda error: errors.append(str(error.exc_value))
    with TemporaryDirectory() as temp:
        p=Path(temp)/'families.lp'
        p.write_text('Minimize\n o:0\nSubject To\n a:2x<=4\n b:3y<=9\nEnd\n')
        with mf.read(p) as m:
            m.family('F0').related('parameters').where(basis='canonical').to_records()
            gc.collect();gc.disable()
            soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
            resource.setrlimit(resource.RLIMIT_NOFILE,(min(256,soft),hard))
            baseline=peak=fds();retained=[]
            for i in range(count):
                f=m.family('F0')
                params=f.related('parameters').where(basis='canonical')
                assert params.head(1).one().count>0
                assert f.rows.count()==2
                retained.append((f,params))
                peak=max(peak,fds())
            assert peak-baseline<=8,(baseline,peak)
            retained.clear();del f,params
            remaining=fds()
            assert remaining<=baseline+2,(baseline,remaining)
        assert not errors,errors
        print(json.dumps(dict(count=count,baseline=baseline,peak=peak,remaining=remaining,automatic_gc=gc.isenabled())))

if __name__=='__main__': run(int(sys.argv[1]))
