"""The saved outline owns page identity/order. Only independent experiment configurations go to the model."""
import json
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import worksheet_evidence as evidence
from .worksheet_compile import qualify_columns, usable_page
from .storage import canonical


def apply_draft_calculations(page, method, linked=()):
    """Use the page layout to place the quantities; the draft supplies their physics."""
    from .measurement_plan import planned_calculations, target_outputs
    from .worksheet_units import convert, quantity_names
    from .worksheet_math import expression_tree, CONSTANTS
    steps = planned_calculations(method)
    if not steps:
        return
    observations = {v["id"]: v for v in method["observables"]}
    targets = {v["id"]: v for target in method["targets"] for v in target_outputs(target)}
    raw_inputs = {v["id"]: v for v in page["fields"]}
    derived = {}
    for table in page["tables"]:
        raw_inputs.update((v["id"], v) for v in table["columns"])
        derived.update((v["id"], v) for v in table.get("derived", []))
    results = {v["id"]: v for v in page["results"]}
    for key, (step, equation) in steps.items():
        if key in raw_inputs:
            raise ValueError("计算量被放入手填读数栏 " + key)
        spec = targets.get(key) or observations[key]
        item = derived.get(key) or results.get(key)
        if item is None:
            if key not in targets and spec.get("form") == "series":
                raise ValueError("请把逐点计算量放在对应测量表的 derived 中 " + key)
            item = {"id": key}
            page["results"].append(item)
        if key in targets and key in derived:
            raise ValueError("课程结果需要标量结果栏 " + key)
        item.update(label=spec["label"], unit=spec["unit"], expr=step["expr"])
        if step.get("symbol") or spec.get("symbol"):item["symbol"]=step.get("symbol") or spec["symbol"]
        if key not in derived:
            if (item.get("basis") or {}).get("page") != equation["source"]:
                item["basis"] = {"page": equation["source"]}
            item["note"] = step.get("analysis_change") or (spec.get("meaning", "") + " " + equation.get("conditions", ""))
            if step.get("uncertainty"):
                item["uncertainty"] = step["uncertainty"]
            else:
                item.pop("uncertainty", None)
        for expr in (step["expr"], step.get("uncertainty")):
            if not expr:
                continue
            for name in quantity_names(expression_tree(expr)) - CONSTANTS.keys() - steps.keys():
                obs = observations.get(name)
                if name not in raw_inputs or obs is None or (obs.get("derived_from") and name not in linked):
                    raise ValueError("请将草稿中的实测量按原标识放入输入字段或表列 " + name)
                field = raw_inputs[name]
                convert(1, field["unit"], obs["unit"])
                field.update(label=obs["label"], hint=obs["meaning"])
                if obs.get("symbol"):field["symbol"]=obs["symbol"]


def bind_interval_requirements(page, method):
    """A course-defined common interval must cover the same endpoints on both instruments."""
    import ast
    import re
    from .measurement_plan import planned_calculations
    from .worksheet_identity import FormulaGraph
    from .worksheet_math import expression_tree
    from .worksheet_units import equivalent_form
    graph = FormulaGraph(page)
    fields = {f["id"]: f for f in page["fields"]}
    steps = planned_calculations(method)
    for result in page["results"]:
        entry = steps.get(result["id"])
        if not entry or not re.search(r"(?:同一|相同).{0,8}(?:时段|区间|时间段)", entry[1].get("conditions", "")):
            continue
        root = expression_tree(result["expr"])
        if not isinstance(root, ast.BinOp) or not isinstance(root.op, ast.Div):
            continue
        endpoint_branch, tables = False, set()
        for branch in (root.left, root.right):
            expr = ast.unparse(branch)
            deps = graph.dependencies(expr)
            channels = {graph.columns[key] for key in deps if key in graph.columns}
            if channels:
                tables.update(channels)
                continue
            for node in ast.walk(graph.expand(expr)):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub) and all(isinstance(x, ast.Name) and x.id in fields for x in (node.left, node.right)):
                    left, right = fields[node.left.id], fields[node.right.id]
                    start, end = left.get("hint",""), right.get("hint","")
                    earlier = r"开始|初始|起始|之前|通电前|接通.{0,6}前"
                    later = r"结束|终态|终点|停止|暂停"
                    if (re.search(earlier, start) and re.search(later, end) or re.search(earlier, end) and re.search(later, start)) and equivalent_form(left["unit"]) == equivalent_form(right["unit"]):
                        endpoint_branch = True
        if endpoint_branch and tables:
            result["endpoint_tables"] = sorted(tables)


def observation_layout(method):
    targets = method.get("targets", [])
    inputs = method.get("observables", [])
    if not targets or not inputs or any(t.get("kind") != "observation" for t in targets):
        return None
    if any(v.get("form") not in ("choice","observation") for v in inputs):
        return None
    fields = [{"id":v["id"], "label":v["label"], "unit":"文字", "type":"text", "hint":v["meaning"]} for v in inputs]
    return {"sources":method["material_pages"], "fields":fields, "tables":[], "results":[], "plots":[]}


def method_layout(raw, method, methods):
    observation = observation_layout(method)
    if observation is not None:
        return observation
    own = {item["id"] for item in method.get("observables", []) + method.get("targets", [])}
    other = {item["id"] for m in methods if m["id"] != method["id"]
             for item in m.get("observables", []) + m.get("targets", [])} - own
    if not other:
        return raw
    plotted = {p.get("table") for p in raw.get("plots", [])}
    kept = []
    for table in raw.get("tables", []):
        columns = table.get("columns", [])
        ids = {col.get("id") for col in columns}
        text_only = all(col.get("type") == "text" or col.get("unit") in ("文字","文本","类别","描述","名称")
                        for col in columns)
        if text_only and ids & other and not ids & own and not table.get("derived") and table.get("id") not in plotted:
            continue
        kept.append(table)
    return {**raw, "tables":kept}


def page_from_receipt(raw,part,catalog,linked=()):
    if isinstance(raw,dict) and isinstance(raw.get("pages"),list) and len(raw["pages"])==1:
        raw=raw["pages"][0]
    if not isinstance(raw,dict):
        raise ValueError("没有收到此实验的数据页定义")
    page={"id":part["id"],"title":part["title"],"unit_system":"SI","check_dimensions":True}
    page["sources"]=[k for k in raw.get("sources",[]) if k in catalog] or list(part["material_pages"])
    if raw.get("entry_steps"):
        page["entry_steps"]=copy.deepcopy(raw["entry_steps"])
    dropped=[]
    for group,required in {"fields":("id","label","unit"),"tables":("id","title","columns"),
                           "results":("id","label","unit","expr"),"plots":("title","table","x","ys")}.items():
        page[group]=[]
        for item in raw.get(group,[]):
            if not isinstance(item,dict) or any(key not in item for key in required):
                dropped.append({"label":item.get("label","未定义的项目") if isinstance(item,dict) else "未定义的项目","reason":"模型未给出完整定义","definition":item})
                continue
            if group=="tables" and (not item["columns"] or any(not isinstance(c,dict) or not all(k in c for k in ("id","label","unit")) for c in item["columns"])):
                dropped.append({"label":item["title"],"reason":"记录列定义不完整","definition":item});continue
            page[group].append(item)
    if not page["fields"] and not page["tables"]:
        raise ValueError("未返回可录入的测量量")
    page=copy.deepcopy(page)
    from .worksheet_conversions import bind_si_conversions
    part = copy.deepcopy(part)
    bind_si_conversions(part)
    apply_draft_calculations(page,part,linked)
    expects_results = any(t.get("kind") == "computed" for t in part.get("targets", []))
    if not page["results"] and (expects_results or dropped):
        dropped.append({"label":"本页定量结果","reason":"未返回草稿约定的结果式","definition":{}})
    page=qualify_columns(page)
    from .worksheet_literals import bind_literals
    units={v["id"]:v["unit"] for v in page["fields"]+page["results"]}
    for t in page["tables"]:units.update((v["id"],v["unit"]) for v in t["columns"]+t.get("derived",[]))
    formulas=page["results"]+[v for table in page["tables"] for v in table.get("derived",[])]
    for target in formulas:bind_literals(target,units,catalog)
    page=usable_page(page)
    bind_interval_requirements(page, part)
    if dropped:page["unresolved"]=page.get("unresolved",[])+dropped
    return page


def incomplete(part,reason):
    return {"id":part["id"],"title":part["title"],"unit_system":"SI","sources":part["material_pages"],
            "fields":[],"tables":[],"results":[],"plots":[],"generation_error":reason,
            "unresolved":[{"label":part["title"]+"的数据页","reason":reason,"definition":{}}]}


def design_pages(service,job,parts,catalog,pictures,options,prompt,pack,saved,keep,check,draft_content=None,knowledge_urls=None):
    from .knowledge_notes import for_worksheet
    note_text=for_worksheet(service.store,knowledge_urls if knowledge_urls is not None else saved("knowledge_references"))
    from .format_program import Program,check_schema
    from .tool_receipts import project_empty_extras
    from .worksheet_submission import submit_record
    from .worksheet_math import validate_page
    directory=Path(pack["directory"])
    contract=json.loads((directory/"page_contract.json").read_text(encoding="utf-8"))
    instructions=(directory/"authoring.md").read_text(encoding="utf-8")
    tool={"type":"function","function":{"name":"submit_measurement_page",
          "description":"提交当前单一测量配置的输入表、可执行的标量结果、曲线和所据原页。宿主负责页名和页序。","parameters":contract}}
    formal=Program(pack).schemas['ExperimentWorksheet']['properties']['pages']['items']
    methods=(draft_content or {}).get('measurement_methods') or parts
    from .measurement_plan import prior_result_links
    links=prior_result_links(methods)
    def produce(part):
        stage="page:"+part["id"]
        accepted={}
        def normalize(raw):
            cleaned,paths=project_empty_extras(raw,contract)
            if paths:service.agent.runtime.event(job,'worksheet_empty_fields_projected',{'step':stage,'paths':paths})
            return cleaned
        def accept(raw):
            page=page_from_receipt(method_layout(raw,part,methods),part,catalog,links.get(part["id"],{}))
            if not page.get('fields') and not page.get('tables'):
                raise ValueError('编译后的定义没有可录入的测量量')
            bound=copy.deepcopy(page)
            service.bind_page(bound,catalog)
            validate_page(bound)
            check_schema(bound,formal,service.store,part['title'])
            accepted['page']=page
        observation = observation_layout(part)
        if observation is not None:
            check()
            accept(observation)
            return accepted["page"]
        cached=saved(stage)
        if cached is not None:
            try:
                cached=normalize(cached)
                check_schema(cached,contract,None,part['title']);accept(cached)
                return accepted['page']
            except (ValueError,KeyError,TypeError) as err:
                service.agent.runtime.event(job,'worksheet_invalid_checkpoint',{'step':stage,'value':cached,'error':str(err)})
        subset={k:catalog[k] for k in part.get("material_pages",[]) if k in catalog} or catalog
        instruction="只负责这一个草稿方法。调用 submit_measurement_page 编制该方法从原始读数到全部必交量的执行定义，宿主将它写回底稿之后才建立界面。结果使用单个目标或 components 中各输出已有的 id，读数沿用 observables 的 id；保留每一套实际可用接法和课内观察。学生希望先打开另一页不改变你这页的物理目标；不要把另一实验的测量条件用在这一页。"
        part_text={k:v for k,v in part.items() if k not in ("source_index","page")}
        from .worksheet_units import quantity_context
        part_text["observables"]=[quantity_context(v) for v in part.get("observables",[])]
        siblings = [p["title"] for p in methods if p["id"] != part["id"]]
        material="本方法的正式测量草稿："+canonical(part_text)+"\n其它方法单独成页，仅供区分："+canonical(siblings)+"\n课程规则："+service.agent.settings.read().get("course_rules","")+"\n本页依据的完整原页：\n"+evidence.texts(subset)
        material+=note_text
        formula_pages=list(dict.fromkeys([*(k for k in pictures if k in subset),
            *(k for e in part.get("equations",[]) if isinstance(e,dict) for k in [e.get("source"),*e.get("evidence_pages",[])] if k in subset)]))
        imgs=evidence.image_blocks(service.app,subset,formula_pages) if options.get("native_vision") else []
        content=[{"type":"text","text":material},*imgs] if imgs else material
        try:
            check()
            with service.agent.client.scope(service.agent.runtime,job):
                submit_record(service,job,tool,[{"role":"system","content":instructions+"\n"+instruction},
                    {"role":"user","content":content}],{**options,"_purpose":"worksheet_page_"+part["id"],"_structured_output":True},
                    stage,saved,keep,normalize,accept,continuation="本次用户要求："+prompt)
            check()
            return accepted['page']
        except InterruptedError:raise
        except (KeyError,TypeError,ValueError,ArithmeticError,OSError) as err:
            return incomplete(part,service.agent.settings.redact(str(err)))
    if not parts:return []
    workers=min(len(parts),3) if options.get("worksheet_quick",True) else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(produce,parts))
