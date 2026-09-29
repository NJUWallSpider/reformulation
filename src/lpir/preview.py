"""Compact human projection of an auditable query envelope."""

from .markdown import table
from .numbers import LPIRError


def preview(result, limit=3):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise LPIRError("Preview limit must be 1..20")
    state = result.get("summary", {})
    lines = [f"状态：{result.get('completeness', 'unknown')}", ""]
    if state.get("operation") == "compare_rows":
        verdict = {True: "全部相等", False: "发现反例", None: "尚未完成"}[
            state.get("valid")
        ]
        lines += [
            f"{verdict}；已检查 {state['checked_entities']} / {state['scope_total']} 对；相等 {state['equal_pairs']}，不等 {state['unequal_pairs']}。",
            "",
        ]
        lines += [f"比较模式：{state.get('mode')}；系数向量相等不代表上下界相等。", ""]
        if state.get("empty_scope"):
            lines += ["作用域为空，没有检查任何行对。", ""]
        if state.get("counterexample"):
            lines += ["首个反例：", ""]
            rec = state["counterexample"]
            lines += table(list(rec), [list(rec.values())])
    available = result.get("items", [])
    items = available[:limit]
    if items:
        fields = (
            ["j", "left_row", "right_row", "equal", "bounds_equal"]
            if state.get("operation") == "compare_rows"
            else list(dict.fromkeys(k for item in items for k in item))
        )
        lines += table(fields, [[item.get(k) for k in fields] for item in items])
    if result.get("truncated") or len(available) > limit:
        lines += ["此处仅为结果预览；完整证据可通过句柄分页读取。", ""]
    if result.get("handle"):
        lines += [f"证据句柄：`{result['handle']}`"]
    if result.get("continuation"):
        lines += ["核验未完成；使用原始查询或验证 spec 继续运行。"]
    return "\n".join(lines)
