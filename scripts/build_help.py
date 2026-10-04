"""Compile canonical help, source docstrings and reference pages; --check is read-only."""
import argparse
import ast
import copy
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import mipframe as mf
from mipframe._help.content import TOPICS, EXCEPTIONS, topic
from mipframe._help.search import index
from mipframe._help.render import card
from mipframe._help.maintenance import source_hash, content_hash, docstring_hash


def resolve(binding):
    module, path = binding.split(':')
    obj = importlib.import_module(module)
    for part in path.split('.'):
        obj = inspect.getattr_static(obj,part)
        if isinstance(obj,(classmethod,staticmethod)):
            obj = obj.__func__
    return obj


def signature(obj):
    if isinstance(obj,property):
        return ' [property]'
    if inspect.isclass(obj):
        return ' [type]'
    sig = inspect.signature(obj)
    params = [p for n,p in sig.parameters.items() if n not in ('self','cls','ctx')]
    result = str(sig.replace(parameters=params))
    return re.sub(r'<object object at 0x[0-9a-f]+>', '<unchanged>',result)


def documentation(t):
    # Full contracts live in the searchable catalog and generated API guide.
    # Source docstrings retain the summary and an exact lookup target.
    text = t['summary'] + '\n\nFull contract: mf.help("' + t['id'] + '")'
    return 'MIPFRAME HELP BEGIN\n'+text+'\nMIPFRAME HELP END'


def source_docs(updates, check):
    changes=[]
    for path, entries in updates.items():
        source=path.read_text(); lines=source.splitlines(keepends=True); tree=ast.parse(source)
        nodes={}
        def walk(body,prefix=''):
            for n in body:
                if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    name=prefix+n.name;nodes[name]=n
                    if isinstance(n,ast.ClassDef): walk(n.body,name+'.')
        walk(tree.body)
        edits=[]
        for qual,doc in entries.items():
            n=nodes[qual]; old=ast.get_docstring(n,clean=True) or ''
            old=re.sub(r'\n?MIPFRAME HELP BEGIN.*?MIPFRAME HELP END','',old,flags=re.S).strip()
            combined=(old+'\n\n' if old else '')+doc
            first=n.body[0]
            isdoc=isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str)
            indent=' '*(n.col_offset+4)
            replacement=indent+'"""\n'+'\n'.join(indent+line if line else '' for line in combined.splitlines())+'\n'+indent+'"""\n'
            if isdoc:
                # Only overwrite the marked generated region; retain preceding human prose.
                if first.lineno==n.lineno: raise RuntimeError('Inline documented definition: '+qual)
                edits.append((first.lineno-1,first.end_lineno,replacement))
            elif first.lineno == n.lineno:
                line = lines[n.lineno-1]
                replacement = line[:first.col_offset].rstrip() + '\n' + replacement + indent + line[first.col_offset:]
                edits.append((n.lineno-1,n.lineno,replacement))
            else:
                start = min([first.lineno] + [d.lineno for d in getattr(first, 'decorator_list', ())]) - 1
                edits.append((start,start,replacement))
        for start,end,replacement in sorted(edits,reverse=True):lines[start:end]=[replacement]
        result=''.join(lines)
        if result!=source:
            changes.append(str(path.relative_to(ROOT)))
            if not check:path.write_text(result)
    return changes


def build(check=False):
    topics=copy.deepcopy(TOPICS)
    skill=(ROOT/'src/mipframe/_help/skill.md').read_text().replace('{{version}}',mf.__version__)
    topics.append(dict(id='skill',kind='concept',title='Portable agent onboarding',summary='Read this short workflow before analyzing a model.',aliases=['SKILL.md','onboarding'],keywords=['agent','workflow'],symbols=[],bindings={},body=skill,related=['home'],non_guarantees='This guide does not authorize external actions or provide optimization proofs.'))
    ids=[t['id'] for t in topics]
    assert len(ids)==len(set(ids)),'Duplicate topic IDs'
    exact={};objects={};updates={};api_names=[]
    for t in topics:
        t['signatures']={}
        for related in t.get('related',[]):
            assert related in ids,(t['id'],related)
        for symbol,binding in t.get('bindings',{}).items():
            try:obj=resolve(binding)
            except Exception as e:raise RuntimeError(binding) from e
            t['signatures'][symbol]=signature(obj)
            raw=obj.fget if isinstance(obj,property) else obj
            # Same inherited function can have several documented receiver paths.
            objects.setdefault(raw.__module__+':'+raw.__qualname__,t['id'])
            api_names.append(symbol.lower())
            for name in (symbol,'mf.'+symbol,'mipframe.'+symbol):
                exact.setdefault(name.lower().removeprefix('mf.').removeprefix('mipframe.'),[]).append(t['id'])
            if '<locals>' not in raw.__qualname__ and raw.__module__.startswith('mipframe.'):
                original=inspect.unwrap(raw)
                path=Path(inspect.getsourcefile(original))
                updates.setdefault(path,{}).setdefault(original.__qualname__,documentation(t))
        for name in [t['id'],*t.get('aliases',[])]:
            exact.setdefault(name.strip().lower().removeprefix('mf.').removeprefix('mipframe.'),[]).append(t['id'])
    objects['mipframe.options:Options'] = 'concept.options'
    for binding,ident in EXCEPTIONS.items():
        objects.setdefault(binding, ident)
        resolve(binding)
        assert ident in ids
    changes=source_docs(updates,check)
    data=dict(schema_version=1,version=mf.__version__,topics=topics,
        objects=objects,exceptions=EXCEPTIONS,exact={k:list(dict.fromkeys(v)) for k,v in sorted(exact.items())},
        api_names=sorted(set(api_names)),index=index(topics),
        source_hash=source_hash(ROOT/'src/mipframe'),content_hash=content_hash(ROOT/'src/mipframe'),
        docstring_hash=docstring_hash(ROOT/'src/mipframe'))
    data['checksum']=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    outputs={ROOT/'src/mipframe/_help/catalog.json':json.dumps(data,indent=2,ensure_ascii=False)+'\n',ROOT/'docs/agent/SKILL.md':skill}
    reference=['# Public API contracts\n\nGenerated from the installed help content source. Do not edit this file.\n']
    for t in topics:
        text=card(t,True)
        if t['kind']=='api':reference.append('## '+t['id']+'\n\n'+text)
        if t['kind']=='recipe':outputs[ROOT/'docs/agent/recipes'/ (t['id'].split('.',1)[1]+'.md')]= '# '+t['title']+'\n\n'+t['body']+'\n'
    outputs[ROOT/'docs/agent/API.md']='\n\n'.join(reference)+'\n'
    outputs[ROOT/'docs/agent/CONCEPTS.md'] = '# Concepts and capability boundaries\n\n' + '\n\n'.join('## '+t['title']+'\n\n'+card(t,True) for t in topics if t['kind'] in ('concept','limitation','troubleshooting') and t['id']!='skill')+'\n'
    for path,value in outputs.items():
        if not path.exists() or path.read_text()!=value:
            changes.append(str(path.relative_to(ROOT)))
            if not check:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
    if check and changes:raise SystemExit('Stale documentation:\n'+'\n'.join(changes))
    print(f'{len(topics)} topics, {len(api_names)} public bindings; '+('checked' if check else 'generated'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true')
    build(p.parse_args().check)
