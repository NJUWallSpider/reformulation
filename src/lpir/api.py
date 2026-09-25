"""Bounded JSON-ready query API. All relation expressions use a restricted AST."""
import ast,base64,json
from fractions import Fraction as F
from collections import Counter
from .numbers import LPIRError,decimal
from .store import Model,build
from .verify import verify,write_model

class Relations:
    def __init__(self,expression):
        if len(expression)>2000:raise LPIRError('Expression too long')
        try:self.tree=ast.parse(expression,mode='eval').body
        except (SyntaxError,RecursionError) as e:raise LPIRError(f'Invalid relation: {e}') from e
        if len(list(ast.walk(self.tree)))>200:raise LPIRError('Expression too complex')
        allowed=(ast.Expression,ast.Name,ast.Load,ast.Constant,ast.Subscript,ast.BinOp,ast.Add,ast.Sub,ast.UnaryOp,ast.UAdd,ast.USub,ast.Compare,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.BoolOp,ast.And,ast.Or,ast.Not)
        for node in ast.walk(self.tree):
            if not isinstance(node,allowed):raise LPIRError(f'Unsupported relation operation: {type(node).__name__}')
    def __call__(self,context):
        def evaluate(node):
            if isinstance(node,ast.Name):
                if node.id not in context:raise LPIRError(f'Unknown relation name: {node.id}')
                return context[node.id]
            if isinstance(node,ast.Constant):
                if type(node.value) is int:return F(node.value)
                if isinstance(node.value,str):
                    try:return F(node.value)
                    except ValueError as e:raise LPIRError('String literals must contain exact numbers') from e
                raise LPIRError('Use integers or quoted decimal strings, not floating literals')
            if isinstance(node,ast.Subscript):
                mapping=evaluate(node.value);key=evaluate(node.slice)
                if not isinstance(mapping,dict) or key not in mapping:raise LPIRError('Missing parameter index')
                return mapping[key]
            if isinstance(node,ast.BinOp):
                a,b=evaluate(node.left),evaluate(node.right)
                if a is None or b is None:raise LPIRError('Relation uses an infinite/missing bound')
                return a+b if isinstance(node.op,ast.Add) else a-b
            if isinstance(node,ast.UnaryOp):
                x=evaluate(node.operand)
                return -x if isinstance(node.op,ast.USub) else not x if isinstance(node.op,ast.Not) else x
            if isinstance(node,ast.BoolOp):
                return all(evaluate(v) for v in node.values) if isinstance(node.op,ast.And) else any(evaluate(v) for v in node.values)
            if isinstance(node,ast.Compare):
                left=evaluate(node.left)
                for op,rightnode in zip(node.ops,node.comparators):
                    right=evaluate(rightnode)
                    if left is None or right is None:raise LPIRError('Relation uses an infinite/missing bound')
                    ok=left==right if isinstance(op,ast.Eq) else left!=right if isinstance(op,ast.NotEq) else left<right if isinstance(op,ast.Lt) else left<=right if isinstance(op,ast.LtE) else left>right if isinstance(op,ast.Gt) else left>=right
                    if not ok:return False
                    left=right
                return True
            raise LPIRError('Unsupported relation node')
        try:return bool(evaluate(self.tree))
        except (TypeError,ValueError,KeyError,RecursionError) as e:raise LPIRError(f'Invalid relation evaluation: {e}') from e

class IR:
    def __init__(self,path):
        self.model=Model(path)
        self.structures=json.loads((self.model.path/'structures.json').read_text())
    @classmethod
    def build(cls,source,out):
        build(source,out);return cls(out)
    def envelope(self,items,total,offset=0,limit=20,scope=None,handle=None,**extra):
        return {'model_version':self.model.manifest['model_version'],'exactness':'exact','scope':scope,'total':total,'offset':offset,'limit':limit,'truncated':offset+len(items)<total,'next_offset':offset+len(items) if offset+len(items)<total else None,'handle':handle,'items':items,**extra}
    def overview(self):
        m=self.model;c=m.compressed;t=self.structures['temporal']
        fs=[{'id':f['id'],'kind':f['kind'],'formula':f['formula'],'rows':len(f['instances']),'estimated_encoding':f['encoding_estimate']} for f in c['families']]
        return {'model_version':m.manifest['model_version'],'exactness':'exact','scope':'model','variables':len(m.variables),'variable_types':dict(Counter(v[2] for v in m.variables)),'rows':len(m.rows),'nonzeros':len(m.col_idx),'objective_nonzeros':sum(bool(m.num(v[5])) for v in m.variables),'objective_sense':c['sense'],'objective_constant':decimal(m.num(c['objective_constant'])),'families':fs[:20],'family_count':len(fs),'family_list_truncated':len(fs)>20,'templated_rows':sum(len(f['instances']) for f in c['families']),'residual_rows':len(c['residual']),'reconstruction_coverage':(sum(len(f['instances']) for f in c['families'])+len(c['residual']))/len(m.rows) if m.rows else 1.0,'assignment_blocks':[{'id':b['id'],'shape':b['shape'],'complete':b['complete'],'missing_pairs':b['missing_pairs'],'duplicate_pair_count':len(b['duplicate_pairs'])} for b in self.structures['assignments'][:20]],'assignment_block_count':len(self.structures['assignments']),'temporal_rows':len(t['rows']),'temporal_exceptions':len(t['exceptions']),'time_variables':len(t['time_bounds']),'wl_rounds':self.structures['wl_rounds'],'metrics':{k:m.manifest[k] for k in ('timings','peak_rss_bytes','peak_rss_scope','artifact_bytes') if k in m.manifest}}
    @staticmethod
    def page(offset,limit):
        if offset<0 or limit<1 or limit>1000:raise LPIRError('offset >= 0 and 1 <= limit <= 1000 required')
    def resolve(self,kind,value):
        lookup=self.model.var_names if kind=='variable' else self.model.row_names
        if isinstance(value,str) and value in lookup:return lookup[value]
        try:i=int(value)
        except (TypeError,ValueError) as e:raise LPIRError(f'Unknown {kind}: {value}') from e
        n=len(self.model.variables) if kind=='variable' else len(self.model.rows)
        if not 0<=i<n:raise LPIRError(f'Invalid {kind} ID {i}')
        return i
    def inspect(self,kind,key=None,offset=0,limit=20):
        self.page(offset,limit);m=self.model
        fmt=lambda i:decimal(m.num(i)) if i is not None else None
        if kind=='row':
            r=self.resolve('row',key);meta=m.rows[r];terms=m.row_ids(r)
            return self.envelope([{'variable':v,'name':m.variables[v][1],'coefficient':fmt(c)} for v,c in terms[offset:offset+limit]],len(terms),offset,limit,scope={'row':r},name=meta[1],lower=fmt(meta[2]),upper=fmt(meta[3]),source_line=meta[4])
        if kind=='variable':
            v=m.variables[self.resolve('variable',key)]
            return self.envelope([{'id':v[0],'name':v[1],'kind':v[2],'lower':fmt(v[3]),'upper':fmt(v[4]),'objective':fmt(v[5]),'source_line':v[6]}],1,scope={'variable':v[0]})
        if kind=='template':
            families=m.compressed['families'];ff=[f for f in families if key is None or f['id']==key]
            if not ff:raise LPIRError(f'Unknown template {key}')
            if key is None:return self.envelope([{'id':f['id'],'kind':f['kind'],'formula':f['formula'],'instances':len(f['instances'])} for f in ff[offset:offset+limit]],len(ff),offset,limit,'templates')
            f=ff[0];records=[]
            for inst in f['instances'][offset:offset+limit]:
                rec=dict(inst);rec['variable_count']=len(rec['variables']);rec['variables']=rec['variables'][:20];rec['bindings_truncated']=rec['variable_count']>20
                if 'parameters' in rec:
                    rec['parameter_count']=len(rec['parameters']);rec['parameters']=[fmt(x) for x in rec['parameters'][:20]]
                for name in ('q','negative_M','lower','upper'):
                    if name in rec:rec[name]=fmt(rec[name])
                records.append(rec)
            return self.envelope(records,len(f['instances']),offset,limit,{'template':key},formula=f['formula'],definition={k:v for k,v in f.items() if k not in ('instances','exceptions','coefficients')},coefficient_slots=f.get('coefficients',[])[:20],coefficient_slot_count=len(f.get('coefficients',[])),exceptions=f['exceptions'][:20],exception_count=len(f['exceptions']))
        if kind in ('assignment','residual','exceptions'):
            if kind=='assignment':
                blocks=[b for b in self.structures['assignments'] if key is None or b['id']==key]
                if key is None:
                    records=[{k:v for k,v in b.items() if k not in ('bindings','left_rows','right_rows','duplicate_pairs')} for b in blocks]
                    return self.envelope(records[offset:offset+limit],len(records),offset,limit,'assignments')
                if not blocks:raise LPIRError(f'Unknown assignment {key}')
                b=blocks[0];records=[{'variable':v,'left_index':i,'right_index':j,'left_row':b['left_rows'][i],'right_row':b['right_rows'][j]} for v,i,j in b['bindings'][offset:offset+limit]]
                return self.envelope(records,len(b['bindings']),offset,limit,{'assignment':key},shape=b['shape'],missing_pairs=b['missing_pairs'],duplicate_pair_count=len(b['duplicate_pairs']))
            records=[{'row':r['row']} for r in m.compressed['residual']] if kind=='residual' else self.structures['temporal']['exceptions']
            return self.envelope(records[offset:offset+limit],len(records),offset,limit,kind)
        if kind=='parameter':
            if key not in ('q','M','L','U'):raise LPIRError('Parameter must be q, M, L, or U')
            records=[]
            if key in ('q','M'):
                data=self.structures['temporal']['rows']
                for r in data[offset:offset+limit]:records.append({'row':r['row'],'source':r['source'],'target':r['target'],'value':fmt(r['q']) if key=='q' else decimal(-m.num(r['negative_M']))})
            else:
                data=list(self.structures['temporal']['time_bounds'].items())
                for v,b in data[offset:offset+limit]:records.append({'variable':int(v),'value':b[0 if key=='L' else 1]})
            return self.envelope(records,len(data),offset,limit,{'parameter':key})
        raise LPIRError(f'Unknown inspection kind {kind}')
    def query(self,kind=None,key=None,where=None,offset=0,limit=20,handle=None):
        self.page(offset,limit);m=self.model
        if handle:
            try:
                if not handle.startswith('lpir1:') or len(handle)>8192:raise LPIRError('Invalid query handle')
                spec=json.loads(base64.urlsafe_b64decode(handle.removeprefix('lpir1:')))
                if spec['model_version']!=m.manifest['model_version']:raise LPIRError('Query handle belongs to another model version')
                kind,key,where=spec['kind'],spec.get('key'),spec.get('where')
            except (ValueError,KeyError) as e:raise LPIRError('Invalid query handle') from e
        spec={'model_version':m.manifest['model_version'],'kind':kind,'key':key,'where':where}
        query_handle='lpir1:'+base64.urlsafe_b64encode(json.dumps(spec).encode()).decode()
        if kind=='neighbors':
            if not key or ':' not in str(key):raise LPIRError('neighbors key must be row:ID or variable:ID')
            obj,ident=key.split(':',1)
            if obj not in ('row','variable'):raise LPIRError('neighbors requires row or variable')
            idx=self.resolve(obj,ident)
            values=m.incident(idx) if obj=='variable' else [v for v,c in m.row_ids(idx)]
            records=[{'row' if obj=='variable' else 'variable':v} for v in values[offset:offset+limit]]
            return self.envelope(records,len(values),offset,limit,spec,query_handle)
        if kind=='external':
            fs=[f for f in m.compressed['families'] if f['id']==key]
            bs=[b for b in self.structures['assignments'] if b['id']==key]
            if fs:rr={r['row'] for r in fs[0]['instances']}
            elif bs:rr=set(bs[0]['left_rows']+bs[0]['right_rows'])
            else:raise LPIRError('external key must name a template or assignment block')
            variables={v for r in rr for v,c in m.row_ids(r)}
            # Objective coupling is not a constraint but must remain visible.
            external=set()
            for v in variables:external.update(m.incident(v))
            external.difference_update(rr);ordered=sorted(external)
            records=[{'row':r,'name':m.rows[r][1]} for r in ordered[offset:offset+limit]]
            return self.envelope(records,len(ordered),offset,limit,spec,query_handle,objective_variable_count=sum(bool(m.num(m.variables[v][5])) for v in variables),block_variable_count=len(variables))
        if kind in ('filter','relation'):
            if key not in (None,'temporal'):raise LPIRError('Relations currently operate on temporal parameter records')
            if not where:raise LPIRError('A relation expression is required')
            predicate=Relations(where);t=self.structures['temporal'];L={int(v):F(b[0]) if b[0] is not None else None for v,b in t['time_bounds'].items()};U={int(v):F(b[1]) if b[1] is not None else None for v,b in t['time_bounds'].items()}
            records=[];total=0;checked=0;passed=0
            for rec in t['rows']:
                context={'L':L,'U':U,'source':rec['source'],'target':rec['target'],'q':m.num(rec['q']),'M':-m.num(rec['negative_M']),'cost_switch':m.num(m.variables[rec['switch']][5]),'cost_arc':m.num(m.variables[rec['arc']][5])}
                result=predicate(context);checked+=1;passed+=result
                if result if kind=='filter' else not result:
                    if offset<=total<offset+limit:records.append({'row':rec['row'],'source':rec['source'],'target':rec['target'],'arc':rec['arc'],'q':decimal(context['q']),'M':decimal(context['M']),'assignment_verified':rec['assignment'] is not None})
                    total+=1
            return self.envelope(records,total,offset,limit,spec,query_handle,checked=checked,passed=passed,all_satisfied=passed==checked,result_kind='matches' if kind=='filter' else 'counterexamples')
        raise LPIRError(f'Unknown query kind {kind}')
    def render(self):
        o=self.overview();s=self.structures;m=self.model
        text=['# LP compressed IR',f"Model version: `{o['model_version']}`",f"{o['variables']} variables; {o['rows']} rows; {o['nonzeros']} nonzeros.",f"Objective: {o['objective_sense']} sum(v, c[v]*x[v]) + {o['objective_constant']}. Exact coefficients remain queryable.",'## Assignment structure']
        for b in s['assignments'][:20]:
            description = f"y[i,j] binary, shape {b['shape']}; sum(j,y[i,j])=1 and sum(i,y[i,j])=1" if not b['duplicate_pairs'] else f"x[e] binary with endpoint sets {b['shape']}; sum(e incident to each endpoint,x[e])=1; parallel edges retained"
            text.extend([f"- {b['id']}: {description}.",f"  Complete: {b['complete']}; missing pairs: {b['missing_pairs']}; duplicate pairs: {len(b['duplicate_pairs'])}. External constraints: query external {b['id']}."])
        t=s['temporal'];ms=Counter(decimal(-m.num(r['negative_M'])) for r in t['rows'])
        text.extend(['## Time relationships',f"{len(t['rows'])} exact rows: t[j]-t[i] >= q[i,j]*(1-switch[i]) - M[i,j]*(1-arc[i,j]).",f"{len(t['time_bounds'])} continuous variables with explicit/derived singleton bounds L[i], U[i].",f"M value distribution (first 10): {dict(ms.most_common(10))}.",f"Verified assignment index joins: {sum(r['assignment'] is not None for r in t['rows'])}; exceptions: {len(t['exceptions'])}.",'Parameter handles: inspect parameter q / M / L / U. No approximate equality is substituted.','## Primary encoding'])
        for f in m.compressed['families'][:20]:text.append(f"- {f['id']}: {f['kind']}, {len(f['instances'])} rows; {f['formula']}.")
        text.extend([f"Residual rows: {len(m.compressed['residual'])}. Reconstruction coverage: {o['reconstruction_coverage']:.0%}.",'Every objective term, domain, row and exception is retained. Role colors do not prove symmetry.','Inspect row/variable/template for exact instances and provenance. Query external for all boundary constraints and objective involvement.','## Query examples','```text','query filter --key temporal --where "L[source] + q > U[target]"','query relation --key temporal --where "q == cost_switch + cost_arc"','inspect template --key family_0','```','No presolve or reformulation has been applied. This artifact adds indexes and views; its total disk size need not be smaller than the source LP.'])
        if len(s['assignments'])>20 or len(m.compressed['families'])>20:text.append('Lists truncated at 20; use paginated inspection for all instances.')
        result='\n\n'.join(text)+'\n'
        if len(result.encode())>20000:raise LPIRError('Summary exceeds 20KB budget')
        return result
    def verify(self):return verify(self.model)
    def export(self,path):write_model(self.model,path)
