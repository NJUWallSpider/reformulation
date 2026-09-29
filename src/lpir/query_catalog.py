"""Whitelisted relational scans and their physical SQLite representations."""

QUERY_VERSION = "3.5"

TABLES = {
    "rows": "SELECT json_object('row_id',row_id,'class',class,'nnz',nnz,'sense',sense,'lower',lower,'upper',upper,'family',family,'scale',scale,'shift',shift,'view_layer','normalized') rec FROM features",
    "raw_rows": "SELECT json_object('row_id',id,'name',name,'lower',exact_num(lo),'upper',exact_num(hi),'source_line',line,'view_layer','raw') rec FROM numerical.rows",
    "normalized_rows": "SELECT json_object('row_id',row_id,'class',class,'nnz',nnz,'lower',lower,'upper',upper,'scale',scale,'shift',shift,'family',family,'view_layer','normalized') rec FROM features",
    "roles": "SELECT json_object('row_id',row_id,'role',role,'coefficient',coefficient,'kind',kind,'count',count,'view_layer','normalized') rec FROM roles",
    "bindings": "SELECT json_object('row_id',row_id,'role',role,'variable',variable,'complement',complement,'view_layer','normalized') rec FROM bindings",
    "temporal": "SELECT json_object('row_id',row_id,'source',source,'target',target,'switch',switch,'arc',arc,'q',q,'M',M) rec FROM temporal",
    "assignment_edges": "SELECT json_object('block',block,'variable',variable,'left_index',left_idx,'right_index',right_idx) rec FROM assignment_edges",
    "assignment_members": "SELECT json_object('block',block,'side',side,'index',idx,'row_id',row_id) rec FROM assignment_members",
    "time_bounds": "SELECT json_object('variable',variable,'lower',lower,'upper',upper) rec FROM time_bounds",
    "facts": "SELECT json_object('kind',kind,'row_id',row_id,'variable',variable,'lower',lower,'upper',upper,'source',source) rec FROM facts",
    "edges": "SELECT json_object('source',source,'target',target) rec FROM edges",
    "variables": "SELECT json_object('variable',id,'name',name,'kind',kind,'lower',exact_num(lo),'upper',exact_num(hi),'cost',exact_num(obj),'degree',column_degree(id),'view_layer','raw') rec FROM numerical.variables",
    "terms": "SELECT json_object('row_id',row_id,'variable',variable,'coefficient',coefficient,'view_layer','raw') rec FROM numerical_terms",
    "explanation_families": "SELECT json_object('id',id,'kind',kind,'ast',json(ast),'formula',formula,'roles',json(roles),'parameters',json(parameters),'rows',rows) rec FROM explained.families",
    "explanation_rows": "SELECT json_object('row_id',row_id,'family',family,'scale',scale,'source_family',source_family,'view_layer','explanation') rec FROM explained.instances",
    "explanation_parameters": "SELECT json_object('family',family,'row_id',row_id,'name',name,'value',value) rec FROM explained.parameters",
    "explanation_bindings": "SELECT json_object('family',i.family,'row_id',b.row_id,'role',b.role,'variable',b.variable,'view_layer','explanation') rec FROM explained.bindings b JOIN explained.instances i USING(row_id)",
    "explanation_residuals": "SELECT json_object('row_id',row_id,'nnz',nnz,'positive',positive,'negative',negative,'types',json(types),'min_coefficient',min_coefficient,'max_coefficient',max_coefficient,'lower',lower_bound,'upper',upper_bound) rec FROM explained.residuals",
}


from .indexed_store import RELATIONS as INDEXED_RELATIONS
from .structural_store import RELATIONS as STRUCTURE_RELATIONS

EXPLANATION_RELATIONS = tuple(
    name for name in TABLES if name.startswith("explanation_")
)

for _relation in STRUCTURE_RELATIONS:
    TABLES[_relation] = (
        "SELECT record rec FROM structured.records WHERE relation='" + _relation + "'"
    )

for _relation in INDEXED_RELATIONS:
    TABLES[_relation] = (
        "SELECT record rec FROM indexed.records WHERE relation='" + _relation + "'"
    )

TABLES["index_operators"] += " AND json_extract(record,'$.verification')='verified'"
for _relation in ("index_rows", "index_kernel", "index_alignment"):
    TABLES[_relation] += (
        " AND EXISTS(SELECT 1 FROM indexed.records p WHERE p.relation='index_operators' AND p.key=json_extract(records.record,'$.id') AND json_extract(p.record,'$.verification')='verified')"
    )
