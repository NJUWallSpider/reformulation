"""Retained indexed views and diagnostic Reports must not allocate per-item FDs."""
import gc
import json
import os
from pathlib import Path
import resource
import sys
from tempfile import TemporaryDirectory
import mipframe as mf


def fds():return len(os.listdir('/proc/self/fd' if os.path.isdir('/proc/self/fd') else '/dev/fd'))


def run(count):
    errors=[]
    sys.unraisablehook=lambda error:errors.append(str(error.exc_value))
    with TemporaryDirectory() as temp:
        path=Path(temp)/'test.lp'
        path.write_text('Minimize\n o:a+b+c+d+y+z\nSubject To\n r0:a+b=1\n r1:c+d=1\n r2:a+c=1\n r3:b+d=1\n r4:y-2a-c>=0\n r5:y-2b-d>=0\n r6:z-a-2c>=0\n r7:z-b-2d>=0\nEnd\n')
        with mf.read(path) as m:
            I=m.indexed();assert I.operators.count()
            ident=I.operators.one().id
            I.operator(ident).related('kernel').to_records();m.verify().require()
            gc.collect();gc.disable()
            soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
            resource.setrlimit(resource.RLIMIT_NOFILE,(min(256,soft),hard))
            baseline=peak=fds();views=[]
            for i in range(count):
                view=I.operator(ident);relation=view.related('kernel')
                assert relation.head(1).one().value
                assert view.rows.count()==4
                views.append((view,relation));peak=max(peak,fds())
            reports=[]
            for i in range(count):
                report=m.verify() if i%2 else I.verify(I.operators)
                report.require();assert report.evidence['checks'].count()>0
                reports.append(report);peak=max(peak,fds())
            assert peak-baseline<=8,(baseline,peak)
            del report,view,relation
            views.clear();reports.clear()
            assert m._diagnostic_store.db.execute('SELECT count(*) FROM diagnostic_records').fetchone()[0]==0
            remaining=fds();assert remaining<=baseline+2,(baseline,remaining)
        assert not errors,errors
        print(json.dumps(dict(count=count,baseline=baseline,peak=peak,remaining=remaining,automatic_gc=gc.isenabled())))

if __name__=='__main__':run(int(sys.argv[1]))
