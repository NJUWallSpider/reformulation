"""Public format dispatch; proof checking belongs to Edit, not to writers."""
from pathlib import Path
import os
import hashlib
import time
from tempfile import NamedTemporaryFile
from mipframe.store.patch import PatchNotSupportedError


class WriteReceipt(dict):
    """
    Read-only serialization result; JSON-compatible and without proof authority.

    MIPFRAME HELP BEGIN
    Immutable numerical artifact readback receipt with writer, digest and phase timings.

    Full contract: mf.help("api.WriteReceipt")
    MIPFRAME HELP END
    """
    def __setitem__(self,*args):raise TypeError('Write receipts are immutable')
    __delitem__=clear=pop=popitem=setdefault=update=__setitem__
    def __ior__(self,other):raise TypeError('Write receipts are immutable')
    @property
    def writer(self):return self['writer']
    @property
    def verified(self):return self.get('status')=='PROVED'


def write(owner,path,*,format=None,strategy='auto',audit='readback',memory_mb=128,disk_mb=None,**options):
    path=Path(path);fmt=(format or path.suffix.lstrip('.')).lower()
    if strategy not in ('auto','patch','canonical'):raise ValueError('strategy must be auto, patch or canonical')
    if audit not in ('readback',None):raise ValueError('audit must be readback or None')
    if fmt not in ('mps','lp'):raise ValueError('Expected LP or MPS output format')
    if fmt=='mps':
        if options:raise TypeError('Unexpected MPS output options: '+', '.join(options))
        from mipframe.store.mps_output import write_mps
        return write_mps(owner,path,strategy=strategy,audit=audit,memory_mb=memory_mb,disk_mb=disk_mb)
    if strategy=='patch':raise PatchNotSupportedError('Source patching supports MPS only')
    if path.exists():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    from mipframe.store.exporter import _export
    with NamedTemporaryFile(dir=path.parent,delete=False) as stream:tmp=Path(stream.name)
    from mipframe.data.runtime import WorkContext,DiskReservation
    work=WorkContext(memory_mb,disk_mb);charge=DiskReservation(work)
    start=time.perf_counter()
    try:
        _export(owner.model,tmp,_charge=charge,**options);written=time.perf_counter()
        if audit=='readback':
            from mipframe.store.readback import compare_lp
            from mipframe.check.kernel import facts
            compare_lp(facts(owner),tmp,work=work,**options)
        from mipframe.store.artifacts import sha256
        receipt=dict(status='PROVED' if audit else 'UNVERIFIED',kind='artifact_readback',writer='canonical',
                     sha256=sha256(tmp),copy_bytes=0,written_bytes=charge.size,peak_working_bytes=work.ledger.peak_resident,peak_disk_bytes=work.ledger.peak_disk,timings=dict(write=written-start,readback=time.perf_counter()-written))
        os.link(tmp,path);return WriteReceipt(receipt)
    finally:tmp.unlink(missing_ok=True);charge.close();work.close()
