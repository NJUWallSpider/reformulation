"""Human-facing projection of the persisted IR document; never serializes JSON."""


def short_number(value):
    if value is None:
        return "—"
    text = str(value)
    if len(text) > 16:
        from fractions import Fraction

        try:
            return "≈" + format(float(Fraction(text)), ".6g")
        except (ValueError, OverflowError):
            pass
    return text


def cell(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        return "；".join(f"{k}：{cell(v)}" for k, v in value.items()) or "无"
    if isinstance(value, (list, tuple)):
        return "、".join(cell(v) for v in value) or "无"
    return str(value).replace("|", r"\|").replace("\n", " ")


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(cell(v) for v in row) + " |" for row in rows],
        "",
    ]


def bound(v, lower=False):
    return ("−∞" if lower else "+∞") if v is None else short_number(v)


def relation(expression, lo, hi):
    if lo == hi and lo is not None:
        return f"{expression} = {lo}"
    if lo is None:
        return f"{expression} ≤ {bound(hi)}"
    if hi is None:
        return f"{expression} ≥ {lo}"
    return f"{lo} ≤ {expression} ≤ {hi}"


def entry(h):
    if not h:
        return "无"
    return " / ".join(
        [str(h.get("table", "")), *[f"{k}={v}" for k, v in h.items() if k != "table"]]
    )


def render(doc):
    lines = ["# 模型结构摘要", "", "<!-- lpir:human-summary-v1 -->", ""]
    if doc.get("completeness") == "partial" and doc.get("analyses"):
        lines += [
            "公式编译尚未完成；此页仅提供完整数值模型的入口，不代表未发现结构。",
            "",
            "通过 `analyze('explanations')` 继续编译，再用 `describe()` / `render()` 获取当前摘要。",
            "",
        ]
    types = {"B": "二元", "C": "连续", "I": "整数"}
    domains = "，".join(
        f"{n:,} 个{types.get(k, k)}变量"
        for k, n in doc.get("variable_types", {}).items()
    )
    lines += [
        f"**{doc.get('model_version', '模型')}** · {doc.get('variables', 0):,} 个变量 · {doc.get('rows', 0):,} 条约束 · {doc.get('nonzeros', 0):,} 个非零系数",
        "",
        domains,
        "",
    ]
    objective = doc.get("objective", {})
    if objective:
        direction = "最小化" if objective.get("sense") == "min" else "最大化"
        lines += [
            "## 目标函数",
            "",
            f"{direction}；{objective.get('nonzeros', 0):,} 个非零成本，系数范围 {short_number(objective.get('min'))} 至 {short_number(objective.get('max'))}，常数项 {objective.get('constant')}。",
            "",
        ]
        if objective.get("interpretation") not in (None, "linear_objective"):
            lines += [
                "已记录的目标解释：" + str(objective["interpretation"]) + "。",
                "",
            ]
    structure = doc.get("structure_views")
    if structure:
        lines += [
            "## 分块与覆盖概览",
            "",
            f"已识别 {structure.get('block_count', len(structure['blocks']))} 个变量块、{structure.get('family_count', len(structure['families']))} 个约束族；其中 {structure.get('coupling_family_count', 0)} 个约束族关联多个变量块。",
            f"已验证结构覆盖 {structure['covered_rows']} / {structure['scope_rows']} 行（去重）；其余行保留数值证据。",
            "",
        ]
        if structure.get("family_count", len(structure["families"])) == 0:
            lines += [
                "当前搜索方法在给定作用域内未发现可接受的结构家族；这不代表模型没有结构。搜索完成仅表示当前方法执行完毕。",
                "",
            ]
        lines += [
            "分块及其关系是已验证的局部结构，可以重叠；不据此声称模型可独立分解。",
            "",
        ]
    if structure:
        links = [f for f in structure["families"] if f.get("cross_block")]
        if links:
            lines += table(
                ["跨块约束族", "关联变量块", "覆盖行", "证据入口"],
                [
                    [f["name"], f["blocks"], f["rows"], entry(f["handle"])]
                    for f in links
                ],
            )
        lines += [
            "<details>",
            "<summary>展开结构公式、参数与映射证据</summary>",
            "",
            "## 共享坐标与结构约束族",
            "",
            f"搜索状态：{structure['search_completeness']}；结构覆盖 {structure['covered_rows']} / {structure['scope_rows']} 行；其余行保留本地公式。",
            "",
            "坐标及求和均限制在各变量块的有效域；缺失位置不补零。",
            "",
        ]
        block_rows = []
        for b in structure["blocks"]:
            mask = b["domain"]
            domain = (
                ", ".join(f"axis{a} ≠ axis{c}" for a, c in mask["not_equal"])
                if mask["kind"] == "inequalities"
                else "显式稀疏域"
            )
            profiles = "; ".join(
                f"{x['kind']} [{bound(x['lower'], True)}, {bound(x['upper'])}]"
                for x in b["metadata"]
            )
            costs = " 至 ".join(
                short_number(x) for x in b.get("objective_range", [])
            ) or ", ".join(sorted({x.get("objective", "") for x in b["metadata"]}))
            block_rows.append(
                (
                    b["name"],
                    " × ".join(map(str, b["shape"])) or "标量",
                    b["variables"],
                    domain or "完整域",
                    profiles,
                    costs,
                )
            )
        lines += table(
            ["变量块", "形状", "变量数", "有效域", "类型 / 界", "目标系数"], block_rows
        )
        for b in structure["blocks"]:
            lines += [f"- {b['name']}：`{entry(b['handle'])}`", ""]
        if structure.get("axis_subsets"):
            lines += [
                "### 轴子集与映射",
                "",
                "子集相同或目标轮廓相同不构成整个模型的对称性证明。",
                "",
            ]
            lines += table(
                ["子集", "位置", "大小", "证据"],
                [
                    (
                        x["name"],
                        f"{x['block']}.axis{x['axis']}",
                        x["size"],
                        x["evidence"],
                    )
                    for x in structure["axis_subsets"]
                ],
            )
            lines += table(
                ["映射", "定义域", "像集", "精确入口"],
                [
                    (
                        x["name"],
                        x.get("source_subset") or x["size"],
                        x["image_subset"],
                        entry(x["handle"]),
                    )
                    for x in structure.get("index_maps", [])
                ],
            )
        if structure.get("objective_tiles"):
            lines += [
                "### 目标系数分块",
                "",
                "目标系数按下列有效域分块；完整值见 `structure_objective`。长数值的 ≈ 仅用于显示。",
                "",
            ]
            lines += table(
                ["变量块", "域", "单元数", "系数依赖"],
                [
                    (
                        x["block"],
                        " × ".join(x["domain"]) or "标量",
                        x["cells"],
                        short_number(x["value"])
                        if x["value"] is not None
                        else "C["
                        + ",".join("axis" + str(a["axis"]) for a in x["inputs"])
                        + "]",
                    )
                    for x in structure["objective_tiles"]
                ],
            )
        for f in structure["families"]:
            lines += [
                f"### {f['name']} · {f['rows']} 行",
                "",
                "> " + f["formula"],
                "",
                ("精确线性定义；不消元。" if f["definition"] else "精确展开已验证。"),
                "",
                f"查询：`{entry(f['handle'])}`",
                "",
            ]
            domain = f.get("domain")
            if domain:
                exclusions = ", ".join(
                    chr(97 + a) + " ≠ " + chr(97 + b)
                    for a, b in domain.get("not_equal", [])
                )
                lines += [
                    f"自由索引：{len(domain['axes'])}；域大小："
                    + " × ".join(str(len(x)) for x in domain["axes"])
                    + ("；" + exclusions if exclusions else "")
                    + (
                        "；其余限制见精确行域。"
                        if domain["kind"] == "explicit"
                        else "。"
                    ),
                    "",
                ]
            if f.get("subsets"):
                lines += [
                    "求和子集："
                    + ", ".join(
                        f"{x['name']}（{x['size']} 个值）" for x in f["subsets"]
                    )
                    + "；见 `structure_subsets`。",
                    "",
                ]
            if f.get("parameters"):
                parameter_rows = []
                for p in f["parameters"]:
                    invariant = ", ".join(
                        chr(97 + x["row"]) if "row" in x else "s" + str(x["axis"])
                        for x in p["invariant_inputs"]
                    )
                    parameter_rows.append(
                        (
                            p["name"],
                            " × ".join(map(str, p["shape"])),
                            p["cells"],
                            f"{short_number(p['min'])} 至 {short_number(p['max'])}",
                            invariant or "—",
                        )
                    )
                lines += table(
                    ["参数", "形状", "有效单元", "范围", "不依赖的索引"], parameter_rows
                )
        if structure.get("omitted_cards"):
            lines += [
                f"另有 {structure['omitted_cards']} 张卡片未展示；通过 structure_blocks / structure_families 分页查询。",
                "",
            ]
        dl = structure["description_length"]
        lines += [
            f"规范序列化描述长度：{dl['flat_bytes']} → {dl['indexed_bytes']} 字节（仅比较已接受约束族，不含原始追溯映射）。",
            "",
            "原粗粒度算子保留为诊断查询：`index_operators`。",
            "",
        ]
        lines += ["</details>", ""]
    indexed = doc.get("indexed_views", {})
    if not doc.get("presentation", {}).get("show_indexed_diagnostics", True):
        indexed = {}
    if indexed:
        status = (
            "已完成"
            if indexed.get("search_completeness") == "complete"
            else "尚未完成（可续跑）"
        )
        lines += [
            "## 跨行结构",
            "",
            f"搜索状态：**{status}**。已验证的局部结构如下；它们可以重叠。",
            "",
        ]
        for t in indexed.get("tensors", []):
            name = t["id"]
            shape = " × ".join(map(str, t["shape"]))
            lines += [
                f"### {name}：{shape} 变量张量",
                "",
                f"{t['variables']:,} 个变量；完整笛卡尔积：{cell(t['complete_product'])}；缺失单元：{t['missing_cells']}。",
                "",
            ]
            for c in t.get("constraints", []):
                axis = c["axis"]
                expr = f"∑_b {name}[a,b]" if axis == 0 else f"∑_b {name}[b,a]"
                bounds = c.get("bounds", [])
                if len(bounds) == 1 and bounds[0][2] == t["shape"][axis]:
                    formula = relation(expr, *bounds[0][:2])
                elif (
                    bounds
                    and sum(x[2] for x in bounds) == t["shape"][axis]
                    and all(x[0] == x[1] and x[0] is not None for x in bounds)
                ):
                    formula = expr + " = rhs[a]（各原行的右端值见下表）"
                else:
                    # Display the stored formula when the bound profile is not uniform.
                    formula = (
                        c["formula"].replace("X[", name + "[").replace("Sum_b", "∑_b")
                    )
                lines += [f"**轴 {axis} 的约束**", "", f"> {formula}", ""]
                if len(bounds) > 1:
                    lines += table(
                        ["下界", "上界", "行数"],
                        [(bound(lo, True), bound(hi), n) for lo, hi, n in bounds],
                    )
                    if c.get("distinct_bounds", 0) > len(bounds):
                        lines += [
                            f"另有 {c['distinct_bounds'] - len(bounds)} 种界；见实例入口。",
                            "",
                        ]
            lines += [f"坐标查询：`{entry(t['coordinates'])}`", ""]
        for op in indexed.get("operators", []):
            lines += [f"### {op['id']}：{op['rows']:,} 条约束共享一个算子", ""]
            formula = (
                op["formula"]
                .replace("X[", op["tensor"] + "[")
                .replace("Sum_j", "∑_j")
                .replace("<=", "≤")
            )
            bounds = op.get("bound_profile", [])
            if len(bounds) == 1 and bounds[0][2] == op["rows"]:
                middle = formula.split(" ≤ ")
                if len(middle) == 3:
                    formula = relation(middle[1], *bounds[0][:2])
            lines += [f"> {formula}", ""]
            stats = op.get("kernel_statistics", {})
            rows = [
                ("核 A 的形状", " × ".join(map(str, op["kernel_shape"]))),
                ("沿共享轴的系数完全相同", op["axis_invariant"]),
                ("完整行索引域", op["complete_row_product"]),
                ("核元素范围", f"{stats.get('min')} 至 {stats.get('max')}"),
                ("零元素", stats.get("zeros")),
                ("非零元素", stats.get("stored_nonzeros")),
                ("精确整数核", stats.get("integer")),
                ("核验状态", op["verification"]),
            ]
            align = stats.get("alignment")
            if align:
                rows += [
                    ("轴对齐后对称", align["symmetric_after_mapping"]),
                    ("轴对齐后对角常数", align["diagonal_constant"]),
                ]
            lines += table(["性质", "结果"], rows)
            if len(bounds) > 1:
                lines += table(
                    ["行下界", "行上界", "行数"],
                    [(bound(lo, True), bound(hi), n) for lo, hi, n in bounds],
                )
            profile = op.get("anchor_profile", {})
            if profile:
                lines += ["**输出变量 y**", "", profile.get("axis", ""), ""]
                lines += table(["下界", "上界", "变量数"], profile.get("bounds", []))
                lines += table(["目标系数", "变量数"], profile.get("costs", []))
                lines += [
                    f"变量类型：{cell(profile.get('domains', {}))}；不同界组合 {profile.get('distinct_bounds', 0)} 种，不同成本 {profile.get('distinct_costs', 0)} 种。",
                    "",
                ]
            lines += [
                f"- 核参数：`{entry(op['kernel'])}`",
                f"- 原行与变量绑定：`{entry(op['bindings'])}`",
            ]
            if align:
                lines += [
                    f"- 轴对齐证据：`{entry(align['mapping'])}`",
                    "",
                    "轴对齐不代表模型具有置换对称性。",
                ]
            lines += [""]
        if indexed.get("omitted_cards"):
            lines += [
                f"另有 {indexed['omitted_cards']} 张结构卡片未在本页展示；使用 inspect 分页查看。",
                "",
            ]
        if indexed.get("continuation"):
            c = indexed["continuation"]
            lines += [
                f"续跑：analyze `{c.get('kind')}`，句柄 `{c.get('handle')}`；完整作用域见机器文档。",
                "",
            ]
    if structure and not doc.get("presentation", {}).get(
        "show_local_diagnostics", True
    ):
        lines += [
            "## 覆盖与追溯",
            "",
            f"{structure['covered_rows']} / {structure['scope_rows']} 条原始约束由结构族覆盖，逐行精确展开已验证；覆盖数不重复计算。",
            "",
            (
                f"其余 {structure['scope_rows'] - structure['covered_rows']} 行使用数值回退：`structure_residuals`；搜索未完成时该表尚未完整，完整行始终可由 `rows` / `terms` 查询。"
                if structure["covered_rows"] < structure["scope_rows"]
                else "无残余行。"
            ),
            "",
            "原本地公式仍可查询：`explanation_families`；原行绑定：`explanation_rows`。",
            "",
            "完整 AST、参数表及诊断视图保留在 `describe()` / `summary.json`。",
            "",
        ]
        return "\n".join(lines)
    lines += [
        "## 本地约束公式",
        "",
        "以下保留原始行家族，便于追溯；与上面的跨行视图可能重叠。",
        "",
    ]
    lines += table(
        ["引用", "类别", "行数"],
        [(f["id"], f["kind"], f["rows"]) for f in doc.get("families", [])],
    )
    for f in doc.get("families", []):
        lines += [
            "<details>",
            f"<summary>{f['id']} · {f['kind']} · {f['rows']} 行</summary>",
            "",
            f"> {f['formula']}",
            "",
        ]
        if f.get("roles"):
            lines += table(
                ["角色", "变量类型", "每行最少", "每行最多"],
                [
                    (
                        r["name"],
                        types.get(r["kind"], r["kind"]),
                        r["min_count"],
                        r["max_count"],
                    )
                    for r in f["roles"]
                ],
            )
        if f.get("parameters"):
            lines += table(
                ["参数", "常量或范围", "不同取值数", "例外行数"],
                [
                    (
                        p["name"],
                        p.get("constant", f"{p.get('min')} 至 {p.get('max')}"),
                        p.get("distinct"),
                        p.get("exception_rows", 0),
                    )
                    for p in f["parameters"]
                ],
            )
            for p in f["parameters"]:
                if p.get("exception_rows"):
                    lines += [
                        f"参数 {p['name']}：通常值 {p.get('usual')}；例外 {p['exception_rows']} 行。",
                        "",
                    ]
                    if p.get("exceptions"):
                        lines += table(
                            ["原行", "参数值"],
                            [(e["row"], e["value"]) for e in p["exceptions"]],
                        )
        lines += [
            f"核验：{cell(f.get('evidence', {}))}",
            "",
            f"- 原行：`{entry(f['instances_handle'])}`",
            f"- 变量绑定：`{entry(f['bindings_handle'])}`",
            f"- 精确公式定义：`{entry(f['ast_handle'])}`",
            "",
            "</details>",
            "",
        ]
    isolated = doc.get("isolated_variables_by_type", {})
    if isolated:
        lines += ["## 孤立变量", "", cell(isolated), ""]
    residuals = doc.get("large_or_unstructured_rows", [])
    if residuals:
        lines += ["## 大型或未归纳行", ""]
        lines += table(
            ["原行", "项数", "正系数", "负系数", "下界", "上界"],
            [
                (
                    r["row"],
                    r["terms"],
                    r["positive"],
                    r["negative"],
                    bound(r["lower"], True),
                    bound(r["upper"]),
                )
                for r in residuals
            ],
        )
        for r in residuals:
            lines += [
                f"行 {r['row']}：类型 {cell(r['types'])}；系数范围 {r['coefficient_min']} 至 {r['coefficient_max']}；查询 `{entry(r['handle'])}`。",
                "",
            ]
    coverage = doc.get("coverage", {})
    page = doc.get("page", {})
    lines += [
        "## 覆盖与展开",
        "",
        f"本页展示 {page.get('shown', len(doc.get('families', [])))} / {coverage.get('total_families', 0)} 个本地公式家族；直接引用数值行 {coverage.get('numerical_reference_rows', 0)} 条。",
        "",
    ]
    if page.get("truncated"):
        lines += [
            f"下一页 offset：{page.get('next_offset')}。{page.get('reason', '')}",
            "",
        ]
    lines += [
        f"残余行入口：`{entry(doc.get('residuals_handle'))}`",
        "",
        "完整参数、AST 和机器字段保留在 `describe()` / `summary.json`；本 Markdown 是同一 IR 的阅读视图，不是机器序列化格式。",
        "",
    ]
    return "\n".join(lines)
