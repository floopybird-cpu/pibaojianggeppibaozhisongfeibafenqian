"""Trace the reported results of one measurement method to its recorded inputs."""
import ast
import copy

from .worksheet_math import CONSTANTS, parse
from .worksheet_units import convert


def opening_page(pages, requested=None):
    if any(p["id"] == requested for p in pages):
        return requested
    available = [p for p in pages if not p.get("generation_error") and (p.get("fields") or p.get("tables"))]
    first = next((p for p in available if p.get("results")), next(iter(available or pages), {}))
    return first.get("id", "")


def target_outputs(target):
    """A scalar goal retains its identity; a grouped goal names its actual outputs."""
    return target.get("components") or [target]


def prior_result_links(methods):
    """Only explicit one-to-one links to an unambiguous earlier scalar output."""
    sources,links={},{}
    for method in methods:
        for obs in method.get("observables",[]):
            deps=obs.get("derived_from",[])
            if obs.get("form") not in ("constant","scalar") or len(deps)!=1:
                continue
            source=sources.get(deps[0],[])
            if len(source)==1:
                pid,unit=source[0]
                convert(1,unit,obs["unit"])
                links.setdefault(method["id"],{})[obs["id"]]=[pid,deps[0]]
        for target in method.get("targets",[]):
            if target.get("kind")=="computed":
                for result in target_outputs(target):
                    sources.setdefault(result["id"],[]).append((method["id"],result["unit"]))
    return links


def planned_calculations(method):
    """Executable course definitions pinned to this revision of the method."""
    definitions = {}
    for equation in method.get("equations", []):
        if isinstance(equation, dict):
            for step in equation.get("calculations", []):
                key = step["id"]
                if key in definitions:
                    raise ValueError("草稿重复定义计算量 " + key)
                definitions[key] = (step, equation)
    return definitions


def bind_equation_outputs(method):
    """Resolve a private result name only when its equation declares one unique output."""
    from collections import Counter
    from .worksheet_compile import ValueRewriter
    from .worksheet_math import expression_tree, FUNCTIONS
    equations = [eq for eq in method.get("equations", []) if isinstance(eq, dict)]
    steps = [step for eq in equations for step in eq.get("calculations", [])]
    ids = Counter(step["id"] for step in steps)
    owners = Counter(key for eq in equations for key in eq.get("targets", []))
    targets = {v["id"] for t in method["targets"] if t["kind"] == "computed" for v in target_outputs(t)}
    declared = targets | {v["id"] for v in method["observables"]} | CONSTANTS.keys() | FUNCTIONS.keys()
    aliases = {}
    for eq in equations:
        outputs, assigned = eq.get("targets", []), eq.get("calculations", [])
        if len(outputs) != 1 or len(assigned) != 1:
            continue
        target, local = outputs[0], assigned[0]["id"]
        if target in targets and target not in ids and owners[target] == 1 and local not in declared and ids[local] == 1:
            aliases[local] = target
    if aliases:
        for step in steps:
            step["id"] = aliases.get(step["id"], step["id"])
            for field in ("expr","uncertainty"):
                if step.get(field):
                    rewrite = ValueRewriter({}, aliases)
                    tree = rewrite.visit(expression_tree(step[field]))
                    if rewrite.changed:
                        step[field] = ast.unparse(tree)
    return aliases


def validate_calculations(method, required=False):
    computed = planned_calculations(method)
    if not required and not computed and not any(isinstance(eq, dict) and "calculations" in eq
                                for eq in method.get("equations", [])):
        return  # Drafts saved before executable course definitions.
    targets = {output["id"] for target in method["targets"] if target["kind"] == "computed"
               for output in target_outputs(target)}
    missing = targets - computed.keys()
    if missing:
        raise ValueError("草稿缺少可执行目标 " + "、".join(sorted(missing)))
    observations = {item["id"] for item in method["observables"]}
    for key, (step, _) in computed.items():
        if key not in targets | observations:
            raise ValueError("中间量须先声明单位和含义 " + key)
        for expr in (step["expr"], step.get("uncertainty")):
            if expr:
                parse(expr, observations | computed.keys())


def source_keys(source):
    if isinstance(source, list):
        return [key for entry in source for key in source_keys(entry)]
    if isinstance(source, dict):
        return source_keys(source.get("page") or source.get("source_anchor"))
    if not isinstance(source, str):
        return []
    return [key.strip() for key in source.replace("，", ",").split(",") if key.strip()]


def check_required_targets(page, part):
    """Check each draft target's executable connection to actual measurement inputs."""
    fields = {field["id"]: field for field in page.get("fields", [])}
    raw = set(fields)
    calculated = {}
    window_errors = {}
    for table in page.get("tables", []):
        raw.update(column["id"] for column in table["columns"])
        calculated.update((column["id"], column) for column in table.get("derived", []))
        if table.get("selection_pending"):
            for column in table["columns"] + table.get("derived", []):
                window_errors[column["id"]] = table["selection_pending"]
    results = {item["id"]: item for item in page.get("results", [])}
    calculated.update(results)
    declared = {item["id"]: item for item in part.get("observables", [])}
    pending = {entry["definition"].get("id"): entry.get("reason", "")
               for entry in page.get("unresolved", [])
               if isinstance(entry.get("definition"), dict) and not entry.get("required_target")}
    index = part.get("source_index", {})
    sources = set(page.get("sources", []))
    resolved_pages = {index.get(key, key) for key in sources}

    def check_source(source):
        keys = source_keys(source)
        if not keys:
            raise ValueError("尚未指出课程原式、操作或仪器出处")
        for key in keys:
            if index.get(key, key) not in resolved_pages:
                raise ValueError("本页缺少对应的课程出处 " + key)

    def input_leaves(key, ancestors):
        if key in window_errors:
            raise ValueError("取数区间未接通：" + window_errors[key])
        if key in raw:
            if key in declared:
                check_source(declared[key].get("source"))
            field = fields.get(key, {})
            form = declared.get(key, {}).get("form")
            if field.get("reference") or form in ("choice", "constant", "observation"):
                return set()
            return {key}
        if key not in calculated:
            raise ValueError(pending.get(key) or "找不到输入或结果 " + key)
        if key in ancestors:
            raise ValueError("计算关系循环引用 " + key)
        tree = parse(calculated[key]["expr"], raw | calculated.keys())
        calls = {node.func for node in ast.walk(tree) if isinstance(node, ast.Call)}
        names = {node.id for node in ast.walk(tree)
                 if isinstance(node, ast.Name) and node not in calls and node.id not in CONSTANTS}
        leaves = set()
        for name in sorted(names):
            leaves.update(input_leaves(name, ancestors | {key}))
        return leaves

    def result_inputs(target):
        key = target["id"]
        if key not in results:
            raise ValueError(pending.get(key) or page.get("generation_error")
                             or "页面未定义草稿要求的计算结果 " + key)
        check_source(target.get("source"))
        result = results[key]
        check_source(result.get("source_anchor") or result.get("basis"))
        convert(1, result["unit"], target["unit"])
        leaves = input_leaves(key, set())
        if not leaves:
            raise ValueError("结果仅引用常量，未连接本次实测读数")
        return leaves

    coverage = []
    for target in part.get("targets", []):
        if target.get("kind", "computed") != "computed":
            continue
        key = target["id"]
        item = {"id": key, "label": target["label"], "status": "missing"}
        try:
            check_source(target.get("source"))
            leaves = set()
            failures = []
            for output in target_outputs(target):
                try:
                    leaves.update(result_inputs(output))
                except (ValueError, TypeError, KeyError) as error:
                    failures.append(output["label"] + " " + str(error))
            if failures:
                raise ValueError("；".join(failures))
            item.update(status="actual", inputs=sorted(leaves))
        except (ValueError, TypeError, KeyError) as error:
            item["reason"] = str(error)
        coverage.append(item)
    return coverage


def attach_required_targets(page, part):
    page = copy.deepcopy(page)
    if not part.get("targets"):
        return page
    coverage = check_required_targets(page, part)
    page["target_coverage"] = coverage
    pending = [entry for entry in page.get("unresolved", []) if not entry.get("required_target")]
    reported = {(entry.get("definition") or {}).get("id") for entry in pending
                if isinstance(entry.get("definition"), dict)}
    for item in coverage:
        if item["status"] == "missing" and item["id"] not in reported:
            pending.append({"label": item["label"], "reason": item["reason"],
                            "definition": {"id": item["id"]}, "required_target": True})
    if pending or "unresolved" in page:
        page["unresolved"] = pending
    return page


def worksheets_from_draft(exact_revision_content):
    return [attach_required_targets(method["page"], method)
            for method in exact_revision_content.get("measurement_methods", []) if method.get("page")]


def _description(value):
    if isinstance(value, list):
        return "、".join(_description(item) for item in value)
    if isinstance(value, dict):
        return "，".join(str(key) + "：" + _description(item) for key, item in value.items())
    return str(value or "")


def measurement_markdown(part):
    lines = ["### " + part["title"], "", part["purpose"], ""]
    page = part.get("page", {})
    coverage = {v["id"]: v for v in check_required_targets(page, part)} if page else {}
    definitions = {entry["id"]:entry for entry in page.get("results", [])}
    for item in part["targets"]:
        state = coverage.get(item["id"], {})
        if item["kind"] == "observation":
            configured = any(entry.get("id") == item["id"] for entry in page.get("observations", []))
            status = "可记录。已设记录栏，实测填写后才有现象记录" if configured else "定性记录栏尚未配置"
        elif state.get("status") == "actual":
            status = "已连接输入 " + "、".join(state["inputs"])
        else:
            status = state.get("reason") or "尚未形成可执行定义"
        lines.append(f"- {item['label']}（{item['id']}，{item['unit']}）　{item['meaning']}。{status}。出处 {_description(item['source'])}")
        for output in target_outputs(item):
            if output['id'] in definitions:
                lines.append("  - " + output['label'] + "（" + output['unit'] + "）　" + definitions[output['id']]['expr'])
    lines += ["", "观测与记录", ""]
    forms = {"reading":"单次读数", "series":"连续或重复读数", "constant":"有出处的参数",
             "choice":"操作配置", "observation":"现场观察"}
    for item in part.get("observables", []):
        form = forms.get(item['form'], item['form'])
        if item.get("derived_from"):
            form = "由 " + "、".join(item["derived_from"]) + " 计算"
        lines.append(f"- {item['label']}（{item['id']}，{item['unit']}，{form}）　{item['meaning']}。出处 {_description(item['source'])}")
    lines += ["", "原式、操作及适用条件", ""]
    for entry in part.get("equations", []):
        if isinstance(entry, str):
            lines.append("- " + entry)
            continue
        text = entry.get("relation") or entry.get("text") or entry.get("expression") or entry.get("expr") or entry.get("formula") or ""
        lines.append("- " + str(text))
        for step in entry.get("calculations", []):
            lines.append("  - " + step["id"] + " = " + step["expr"])
            if step.get("uncertainty"):
                lines.append("    标准不确定度　" + step["uncertainty"])
            if step.get("analysis_change"):
                lines.append("    本次分析　" + step["analysis_change"])
        for field, label in (("symbols","符号"),("device","装置"),("condition","条件"),("conditions","条件"),
                             ("bindings","装置与读数"),("targets","使用此式的目标"),("window","选区"),
                             ("sign_convention","方向"),("source","出处"),("evidence_pages","原件页")):
            if entry.get(field):
                lines.append("  - " + label + "　" + _description(entry[field]))
    return lines + [""]


def validate_methods(content):
    """Method identity and verified source membership form part of a saved revision."""
    known_sources = set(content.get("sources", []))
    methods = content.get("measurement_methods", [])
    for group in [methods] + [items for method in methods for items in (method["targets"], method["observables"])]:
        ids = [item["id"] for item in group]
        if len(set(ids)) != len(ids):
            raise ValueError("同一组测量方法、目标或观测的标识重复")
    for method in methods:
        for target in method["targets"]:
            components = target.get("components", [])
            ids = [part["id"] for part in components]
            if components and target["kind"] != "computed":
                raise ValueError("组合输出用于计算目标，现场现象按实际观察逐项记录")
            if len(ids) != len(set(ids)):
                raise ValueError("同一组合目标的输出标识重复")
        anchors = method.get("source_index", {}).values()
        if any(not isinstance(value, str) or value not in known_sources for value in anchors):
            raise ValueError(method["title"] + "的原件索引须引用本草稿保存的真实出处")
        page = method.get("page")
        if page and page["id"] != method["id"]:
            raise ValueError(method["title"] + "的可执行页与方法标识不符")
