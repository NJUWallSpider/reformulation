import argparse,json,sys
from pathlib import Path
from .api import IR
from .numbers import LPIRError

def main(argv=None):
    p=argparse.ArgumentParser(prog='lpir');subs=p.add_subparsers(dest='command',required=True)
    b=subs.add_parser('build');b.add_argument('source');b.add_argument('--out',required=True)
    for name in ('overview','inspect','query','render','verify','export','generate'):
        q=subs.add_parser(name);q.add_argument('artifact')
        if name=='inspect':q.add_argument('kind',choices=['row','variable','template','parameter','assignment','residual','exceptions']);q.add_argument('--key')
        if name=='query':q.add_argument('kind',nargs='?',choices=['neighbors','external','filter','relation']);q.add_argument('--key');q.add_argument('--where');q.add_argument('--handle')
        if name in ('inspect','query'):q.add_argument('--offset',type=int,default=0);q.add_argument('--limit',type=int,default=20)
        if name=='render':q.add_argument('--format',choices=['markdown'],default='markdown');q.add_argument('--out')
        if name in ('export','generate'):q.add_argument('--out',required=True)
        if name=='generate':
            group=q.add_mutually_exclusive_group(required=True);group.add_argument('--copies',type=int);group.add_argument('--target-mb',type=float)
    args=p.parse_args(argv)
    try:
        if args.command=='build':result=IR.build(args.source,args.out).overview()
        else:
            ir=IR(args.artifact)
            if args.command=='overview':result=ir.overview()
            elif args.command=='inspect':result=ir.inspect(args.kind,args.key,args.offset,args.limit)
            elif args.command=='query':result=ir.query(args.kind,args.key,args.where,args.offset,args.limit,args.handle)
            elif args.command=='verify':result=ir.verify()
            elif args.command=='render':
                text=ir.render()
                if args.out:Path(args.out).write_text(text);result={'output':args.out,'bytes':len(text.encode())}
                else:print(text,end='');return
            elif args.command=='export':ir.export(args.out);result={'output':args.out}
            else:
                from .generate import generate
                result=generate(ir.model,args.out,args.copies,args.target_mb)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (LPIRError,OSError,KeyError,ValueError) as e:
        print(json.dumps({'error':str(e),'type':type(e).__name__},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
