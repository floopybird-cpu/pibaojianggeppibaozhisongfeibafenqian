"""Measurement steps for both newly authored worksheets and earlier saved experiments."""
import ast
import re

from .worksheet_identity import FormulaGraph
from .worksheet_math import expression_tree
from .worksheet_units import equivalent_form


def reading_hint(item):
    hint = (item.get("reading_hint") or "").strip()
    if hint:
        return hint
    if item.get("source_headers"):
        return "取原始文件 " + " / ".join(item["source_headers"]) + " 列"
    text = (item.get("hint") or item.get("meaning") or "").strip()
    if text:
        clauses = [v.strip() for v in re.split(r"[；;。，\n]", text) if v.strip()]
        if clauses:
            first=clauses[0]
            action=re.match(r"^(.+?(?:量取|读出|读取|测得))的",first)
            return action[1] if action else first
    if item.get("reference"):
        return "取讲义给定值"
    return "填写现场观察" if item.get("type")=="text" or item.get("unit")=="文字" else "填实验记录中的读数"


def difference_checks(page):
    """Expose a signed subtraction already present in an equation, with the same units and order."""
    fields = {f["id"]: f for f in page["fields"]}
    seen = set()
    taken = set(fields) | {r["id"] for r in page["results"]}
    taken.update(c["id"] for t in page["tables"] for c in t["columns"]+t.get("derived",[]))
    trees = [expression_tree(r["expr"]) for r in page["results"]]
    existing = {ast.dump(tree) for tree in trees}
    checks = []
    for tree in trees:
        if any(isinstance(n,ast.IfExp) or isinstance(n,ast.Call) and n.func.id in ("choose","coalesce") for n in ast.walk(tree)):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node,ast.BinOp) and isinstance(node.op,ast.Sub) and
                    isinstance(node.left,ast.Name) and isinstance(node.right,ast.Name)):
                continue
            key = ast.dump(node)
            a,b = fields.get(node.left.id),fields.get(node.right.id)
            if not a or not b or a is b or key in existing or key in seen:
                continue
            if equivalent_form(a["unit"]) != equivalent_form(b["unit"]) or a.get("type")=="text" or b.get("type")=="text":
                continue
            seen.add(key)
            unit = "K" if equivalent_form(a["unit"])=="degC" else a["unit"]
            identity = "step_difference_"+str(len(checks)+1)
            while identity in taken:
                identity += "_"
            taken.add(identity)
            checks.append({"id":identity, "label":(a.get("symbol") or a["label"])+" − "+(b.get("symbol") or b["label"]),
                           "unit":unit, "expr":ast.unparse(node), "note":a["label"]+" 减 "+b["label"]})
    return checks


def measurement_steps(page):
    """Keep author order and put every input and result in exactly one reachable step."""
    fields = {v["id"]:v for v in page["fields"]}
    tables = {v["id"]:v for v in page["tables"]}
    checks = difference_checks(page)
    results = {v["id"]:v for v in checks+page["results"]}
    graph = FormulaGraph(page)
    dependencies = {key:graph.dependencies(r["expr"]) for key,r in results.items()}
    steps, owners, requests = [], {}, {}
    def add(title, inputs=(), records=(), outputs=()):
        inputs = list(dict.fromkeys(k for k in inputs if k in fields and ("field",k) not in owners))
        records = list(dict.fromkeys(k for k in records if k in tables and ("table",k) not in owners))
        outputs = [k for k in outputs if k in results and k not in requests]
        if not (inputs or records or outputs):
            return
        i = len(steps)
        steps.append(dict(title=title, fields=inputs, tables=records, results=[]))
        for key in inputs:owners["field",key] = i
        for key in records:owners["table",key] = i
        for key in outputs:requests[key] = i

    authored=page.get("entry_steps",[])
    scheduled={k for row in authored for k in row.get("fields",[])}
    add("讲义参数", [k for k,f in fields.items() if f.get("reference") and k not in scheduled])
    for row in authored:
        add(row["title"],row.get("fields",[]),row.get("tables",[]),row.get("results",[]))

    for result in page["results"]:
        deps = dependencies[result["id"]]
        if not any(k in graph.columns for k in deps):
            add(result["label"], [k for k in fields if k in deps])
    add("测量条件与读数", fields)
    for key,table in tables.items():
        add(table["title"], records=[key])

    for key in results:
        deps = dependencies[key]
        locations = [owners["field",f] for f in fields if f in deps]
        locations.extend(owners["table",graph.columns[c]] for c in deps if c in graph.columns)
        if not locations and key in requests:
            locations.append(requests[key])
        if not steps:steps.append(dict(title="计算结果",fields=[],tables=[],results=[]))
        steps[max(locations,default=0)]["results"].append(key)
    return [s for s in steps if s["fields"] or s["tables"] or s["results"]], checks


def difference_display(item, row, fields, readings):
    """Report a check at the coarser decimal place of its two original readings."""
    from decimal import Decimal, InvalidOperation, localcontext
    from .worksheet_units import numeric_text, transform
    if "value" not in row:
        return
    node=expression_tree(item["expr"])
    places=[]
    for name in (node.left.id,node.right.id):
        raw=numeric_text(readings.get(name,"")).strip().replace("−","-")
        match=re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*)",raw)
        if not match:return
        unit=match[2] or fields[name]["unit"]
        factor=Decimal(str(transform(unit)[0]))/Decimal(str(transform(item["unit"])[0]))
        if factor.normalize().as_tuple().digits!=(1,):
            row["display"]=format(row["value"],".12g")
            return
        places.append(Decimal(match[1]).as_tuple().exponent+factor.adjusted())
    with localcontext() as context:
        value=Decimal(str(row["value"]))
        context.prec=max(context.prec, len(value.as_tuple().digits))
        try:
            rounded=value.quantize(Decimal(1).scaleb(max(places)))
        except InvalidOperation:
            return
        row["display"]=str(rounded.copy_abs() if not rounded else rounded)
