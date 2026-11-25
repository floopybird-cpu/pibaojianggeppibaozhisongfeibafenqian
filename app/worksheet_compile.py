"""Keep independently valid measurements and formula branches usable after a localized authoring error."""
import copy
import ast
from collections import Counter
import re
from .worksheet_math import CONSTANTS, expression_tree, normalize_factors, pair_observation_means, quantity_names, row_tables, validate_expression, validate_page


class ValueRewriter(ast.NodeTransformer):
    """Field renaming never rewrites a function, a unit string, or part of an identifier."""
    def __init__(self,qualified,local):
        self.qualified=qualified
        self.local=local
        self.changed=False

    def visit_Call(self,node):
        node.args=[self.visit(a) for a in node.args]
        return node

    def visit_Attribute(self,node):
        if isinstance(node.value,ast.Name) and node.attr in self.qualified.get(node.value.id,{}):
            self.changed=True
            return ast.copy_location(ast.Name(id=self.qualified[node.value.id][node.attr],ctx=ast.Load()),node)
        return node

    def visit_Name(self,node):
        new=self.local.get(node.id,node.id)
        if new!=node.id:
            self.changed=True
            node.id=new
        return node


def qualify_columns(page):
    """Give identical headings in different measurement configurations distinct identities."""
    tables=page.get("tables",[])
    ids=Counter(f["id"] for f in page.get("fields",[]))
    from .worksheet_units import equivalent_form
    for t in tables:ids.update(c["id"] for c in t["columns"]+t.get("derived",[]))
    occupied=set(ids)|{v["id"] for v in page.get("results",[])}
    maps={}
    for t in tables:
        m=maps[t["id"]]={}
        for c in t["columns"]+t.get("derived",[]):
            old=c["id"]
            if old in m:continue
            name=old
            if ids[old]>1:
                name=t["id"]+"_"+old
                while name in occupied:name+="_col"
                occupied.add(name)
            m[old]=name
    def rewrite(expr,table=None):
        try:tree=expression_tree(expr)
        except (ValueError,SyntaxError,TypeError):return expr
        if table not in maps:
            names=quantity_names(tree)
            hints=[tid for tid,m in maps.items() if any(ids[k]==1 and k in names for k in m)]
            table=hints[0] if len(hints)==1 else None
        visitor=ValueRewriter(maps,maps.get(table,{}))
        value=visitor.visit(tree)
        return ast.unparse(value) if visitor.changed else expr
    for t in tables:
        original_units={v["id"]:equivalent_form(v.get("unit","")) for v in t["columns"]+page.get("fields",[])}
        for col in t.get("derived",[]):
            # The data engine stores absolute temperatures in kelvin and handles the display offset.
            match=re.fullmatch(r"\s*([A-Za-z]\w*)\s*([-+])\s*273\.15\s*",col.get("expr",""))
            if match and (original_units.get(match[1]),equivalent_form(col["unit"]),match[2]) in (("K","degC","-"),("degC","K","+")):
                col["expr"]=match[1]
        m=maps[t["id"]]
        for col in t["columns"]+t.get("derived",[]):
            col["id"]=m[col["id"]]
            if col.get("expr"):col["expr"]=rewrite(col["expr"],t["id"])
        if t.get("grouping"):
            for role in ("condition","variant"):
                key=t["grouping"][role];t["grouping"][role]=m.get(key,key)
        if t.get("selection"):t["selection"]["column"]=m.get(t["selection"]["column"],t["selection"]["column"])
    for plot in page.get("plots",[]):
        m=maps.get(plot["table"],{})
        def name(v):
            raw=v.removeprefix(plot["table"]+".")
            return m.get(raw,raw)
        plot["x"]=name(plot["x"]);plot["ys"]=[name(y) for y in plot["ys"]]
        if plot.get("sigma"):plot["sigma"]=name(plot["sigma"])
    for r in page.get("results",[]):
        for key in ("expr","uncertainty"):
            if r.get(key):r[key]=rewrite(r[key],r.get("table"))
    return page


def dependency_order(items):
    members={r["id"] for r in items}
    remaining=list(items);ordered=[];known=set()
    while remaining:
        ready=[]
        for r in remaining:
            try:
                refs=quantity_names(expression_tree(r["expr"]))&members
                if r.get("uncertainty"):
                    refs.update((quantity_names(expression_tree(r["uncertainty"]))&members)-{r["id"]})
            except (SyntaxError,ValueError,TypeError,KeyError):refs=set()
            if refs<=known:ready.append(r)
        if not ready:break
        ordered.extend(ready);known.update(r["id"] for r in ready)
        remaining=[r for r in remaining if r["id"] not in known]
    return ordered+remaining


def expand_dependencies(expression, definitions, inputs, trail=()):
    """Expand value aliases without replacing callable names or crossing raw inputs."""
    class Expand(ast.NodeTransformer):
        def visit_Call(self,node):
            node.args=[self.visit(a) for a in node.args]
            return node
        def visit_Name(self,node):
            if node.id in inputs or node.id not in definitions:return node
            if node.id in trail:raise ValueError("计算关系循环引用 "+node.id)
            return expand_dependencies(definitions[node.id]["expr"],definitions,inputs,trail+(node.id,))
    return Expand().visit(expression_tree(expression))


def prepare_measurements(page):
    """Compile a known parameter, a same-row observation and a scalar result into their own roles."""
    from .worksheet_units import convert
    for table in page.get("tables",[]):
        parameters=[v for v in table.get("derived",[]) if v.get("_course_parameter")]
        page.setdefault("results",[]).extend(parameters)
        table["derived"]=[v for v in table.get("derived",[]) if not v.get("_course_parameter")]
    fields={f["id"]:f for f in page.get("fields",[])}
    tables={t["id"]:t for t in page.get("tables",[])}
    columns={v["id"]:tid for tid,t in tables.items() for v in t["columns"]+t.get("derived",[])}
    declared={v["id"]:v for t in tables.values() for v in t.get("derived",[])}
    pending=[];results=[]
    for result in page.get("results",[]):
        key=result["id"]
        ref=result.pop("_course_parameter",None)
        if ref and key not in columns:
            field=fields.get(key)
            if field is None:
                field={k:result[k] for k in ("id","label","unit","hint") if k in result}
                page["fields"].append(field);fields[key]=field
            try:convert(ref["value"],ref["unit"],field["unit"])
            except (ValueError,KeyError,TypeError):pass
            else:
                field["reference"]=ref
                continue
        if key in columns:
            prior=declared.get(key)
            if prior is not None:
                try:
                    same=ast.dump(expression_tree(prior["expr"]))==ast.dump(expression_tree(result["expr"]))
                    convert(1,prior["unit"],result["unit"])
                except (ValueError,KeyError,TypeError,SyntaxError):same=False
                if same and not result.get("literal_error"):
                    prior.update({k:v for k,v in result.items() if k in ("label","basis","note")})
                    continue
                table=tables[columns[key]]
                table["derived"].remove(prior)
                del declared[key],columns[key]
                pending.append({"label":result["label"],"reason":"同一派生列收到不一致的定义，请按原件核对后再计算",
                                "definition":result,"earlier_definition":prior})
            else:
                pending.append({"label":result["label"],"reason":"此名称已用于原始读数，计算项需要独立的名称","definition":result})
            continue
        results.append(result)
    # A provider's table/scalar label is not a shape proof. Infer from the complete
    # dependency graph, where only actual recorded columns carry rows.
    items=[*(v for t in tables.values() for v in t.get("derived",[])),*results]
    definitions={v["id"]:v for v in items}
    raw_columns={v["id"]:tid for tid,t in tables.items() for v in t["columns"]}
    inputs=set(fields)|set(raw_columns)
    all_units={v["id"]:v["unit"] for v in [*fields.values(),*items,
               *(c for t in tables.values() for c in t["columns"])]}
    checked=set()
    def check_dependencies(item,trail=()):
        key=item["id"]
        if key in trail:raise ValueError("计算关系循环引用 "+key)
        if key in checked:return
        tree=normalize_factors(expression_tree(item["expr"]))
        validate_expression(tree,all_units,item,page)
        for name in quantity_names(tree)-inputs:
            if name in definitions:check_dependencies(definitions[name],trail+(key,))
        checked.add(key)
    original_tables={v["id"]:tid for tid,t in tables.items() for v in t.get("derived",[])}
    for table in tables.values():table["derived"]=[]
    kept=[]
    for item in dependency_order(items):
        try:
            tree=expression_tree(item["expr"])
            expanded=expand_dependencies(item["expr"],definitions,inputs,(item["id"],))
            owners=row_tables(expanded,raw_columns)
            tid=next(iter(owners)) if len(owners)==1 else None
            # Row expressions execute before scalar cards. Expand scalar aliases used
            # by them, and disallow hidden row dependencies on a different table.
            if tid is not None:
                available=set(fields)|{k for k,v in raw_columns.items() if v==tid}|CONSTANTS.keys()
                if not quantity_names(expanded)<=available:
                    raise ValueError("逐行计算引用了另一张表的读数，需明确对齐依据")
                if item.get("uncertainty"):
                    raise ValueError("逐行不确定度需声明为对应记录表的独立派生列")
                check_dependencies(item)
                expanded,_=pair_observation_means(expanded,raw_columns)
                item["expr"]=ast.unparse(normalize_factors(expanded))
            else:
                paired,changed=pair_observation_means(expanded,raw_columns)
                item["expr"]=ast.unparse(normalize_factors(paired if changed else tree))
        except (ValueError,TypeError,SyntaxError,KeyError) as err:
            tid=original_tables.get(item["id"])
            item["literal_error"]=str(err)
        if tid is not None:
            tables[tid].setdefault("derived",[]).append(item)
        else:kept.append(item)
    page["results"]=dependency_order(kept)
    columns={**raw_columns,**{v["id"]:tid for tid,t in tables.items() for v in t.get("derived",[])}}
    for table in tables.values():
        if table.get("derived"):table["derived"]=dependency_order(table["derived"])

    for item in page["results"]:
        for k in ("expr","uncertainty"):
            if not item.get(k):continue
            try:tree,changed=pair_observation_means(expression_tree(item[k]),columns)
            except (ValueError,TypeError,SyntaxError):continue
            if changed:item[k]=ast.unparse(tree)
    return pending


def usable_page(page):
    pending=prepare_measurements(page)
    if pending:page.setdefault("unresolved",[]).extend(pending)
    try:
        validate_page(page)
        return page
    except (ValueError,KeyError,TypeError):
        pass
    base=copy.deepcopy(page)
    base["results"]=[];base["plots"]=[]
    pending=list(base.get("unresolved",[]))
    def try_part(items,part,description):
        items.append(part)
        try:validate_page(base)
        except (KeyError,TypeError,ValueError) as err:
            items.pop()
            if items is base["results"] and part.get("uncertainty"):
                value=copy.deepcopy(part)
                value.pop("uncertainty")
                items.append(value)
                try:validate_page(base)
                except (KeyError,TypeError,ValueError):items.pop()
                else:
                    value["uncertainty_pending"]=str(err)
                    pending.append({"label":part["label"]+"的标准不确定度","reason":str(err),"definition":{"uncertainty":part["uncertainty"]}})
                    return
            pending.append({"label":part.get("label") or part.get("title") or description,
                            "reason":str(err),"definition":part})
    derived=[t.pop("derived",[]) for t in base["tables"]]
    windows=[t.pop("selection",None) for t in base["tables"]]
    validate_page(base)
    for table,calculated,window in zip(base["tables"],derived,windows):
        table["derived"]=[]
        for part in calculated:try_part(table["derived"],part,"派生列")
    for r in page.get("results",[]):try_part(base["results"],r,"计算项")
    for table,window in zip(base["tables"],windows):
        if window:
            table["selection"]=window
            try:validate_page(base)
            except (KeyError,ValueError,TypeError) as err:
                # An invalid window must never silently become a whole-run calculation.
                table.pop("selection")
                table["selection_pending"]=str(err)
                pending.append({"label":table["title"]+"的选区","definition":window,"reason":str(err)})
    for g in page.get("plots",[]):try_part(base["plots"],g,"曲线")
    base["unresolved"]=pending
    return base
