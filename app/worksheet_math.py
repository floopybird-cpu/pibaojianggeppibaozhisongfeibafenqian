"""Numerical language for worksheet definitions, also shipped with exported experiments."""
from __future__ import annotations

import ast
import json
import keyword
import math
import operator
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
from pathlib import Path

import numpy as np
try:
    from .worksheet_units import convert, check_formula_dimensions, numeric_text, quantity_names, read_quantity, transform
except ImportError:  # standalone export
    from worksheet_units import convert, check_formula_dimensions, numeric_text, quantity_names, read_quantity, transform


def finite(values):
    array = np.atleast_1d(np.asarray(values, dtype=float))
    valid = array[np.isfinite(array)]
    if not valid.size:
        raise ValueError("尚无有效读数")
    return valid


def paired(*series):
    arrays = np.broadcast_arrays(*(np.atleast_1d(np.asarray(s, dtype=float)) for s in series))
    keep = np.logical_and.reduce([np.isfinite(a) for a in arrays])
    if not np.any(keep):
        raise ValueError("尚无成对的有效读数")
    return [a[keep] for a in arrays]


def linear(x, y, sigma=None):
    if sigma is None:
        x, y = paired(x, y)
        weights = np.ones_like(x)
    else:
        x, y, sigma = paired(x, y, sigma)
        if np.any(sigma <= 0):
            raise ValueError("各点的标准不确定度须为正")
        weights = 1 / np.square(sigma)
    if len(x) < 2 or np.ptp(x) == 0:
        raise ValueError("拟合需要至少两个不同的横坐标")
    xm, ym = np.average(x, weights=weights), np.average(y, weights=weights)
    dx, dy = x - xm, y - ym
    sxx = np.sum(weights * dx**2)
    slope = np.sum(weights * dx * dy) / sxx
    intercept = ym - slope*xm
    ssr, sst = np.sum(weights * (dy-slope*dx)**2), np.sum(weights * dy**2)
    variance = 1.0 if sigma is not None else ssr / (len(x)-2) if len(x) > 2 else np.nan
    return dict(slope=slope, intercept=intercept, r2=1-ssr/sst if sst else np.nan,
                slope_se=np.sqrt(variance/sxx), intercept_se=np.sqrt(variance*(1/sum(weights)+xm*xm/sxx)))


def first_order(x, y):
    x, y = paired(x, y)
    if len(x) < 2 or np.any(np.diff(x) <= 0):
        raise ValueError("积分需要至少两个横坐标递增的有效点")
    return np.sum((y[1:]+y[:-1])*np.diff(x)/2)


def deviation(x):
    a = finite(x)
    if len(a) < 2:
        raise ValueError("样本标准差至少需要两次测量")
    return np.std(a, ddof=1)


def successive(x,y,lag):
    x,y=np.broadcast_arrays(np.asarray(x,dtype=float),np.asarray(y,dtype=float))
    k=int(lag)
    if lag!=k or k<=0 or x.ndim!=1 or k>=len(x):
        raise ValueError("逐差间隔须为小于测量组数的正整数")
    dx,dy=paired(x[k:]-x[:-k],y[k:]-y[:-k])
    if np.any(dx==0):raise ValueError("逐差横坐标差不能是零")
    return np.mean(dy/dx)


FUNCTIONS = {"sqrt": np.sqrt, "abs": np.abs, "log": np.log, "log10": np.log10,
             "exp": np.exp, "sin": np.sin, "cos": np.cos, "tan": np.tan,
             "mean": lambda x: np.mean(finite(x)), "std": deviation,
             "sem": lambda x: deviation(x)/math.sqrt(len(finite(x))),
             "min": lambda x: np.min(finite(x)), "max": lambda x: np.max(finite(x)),
             "sum": lambda x: np.sum(finite(x)), "count": lambda x: len(finite(x)),
             "first": lambda x: finite(x)[0], "last": lambda x: finite(x)[-1],
             "integral": first_order, "hypot": np.hypot}
FUNCTIONS["diffslope"]=successive
def lag(values,step=1):
    data=np.asarray(values,dtype=float)
    if int(step)!=step or step<1:raise ValueError("滞后量需是正整数")
    shifted=np.full_like(data,np.nan)
    if step<len(data):shifted[int(step):]=data[:-int(step)]
    return shifted
FUNCTIONS.update(diff=lambda x:np.asarray(x,dtype=float)-lag(x),lag=lag)
def sample_period(times):
    step=np.diff(finite(times))
    if not len(step) or np.any(step<=0) or not np.allclose(step,step[0],rtol=1e-4,atol=0):
        raise ValueError("需要至少两个按固定间隔依次采集的时间点")
    return float(np.mean(step))


def quantity(value,unit):
    scale,offset=transform(unit)
    return value*scale+offset


FUNCTIONS.update(quantity=quantity,sample_period=sample_period)
def coordinate_of_peak(x,y):
    x,y=paired(x,y)
    return float(x[int(np.argmax(y))])

FUNCTIONS.update(x_at_max=coordinate_of_peak, rms=lambda x:np.sqrt(np.mean(finite(x)**2)),
                 atan=np.arctan, atan2=np.arctan2)
for _key in ("slope", "intercept", "r2", "slope_se", "intercept_se"):
    FUNCTIONS[_key] = lambda x, y, sigma=None, key=_key: linear(x, y, sigma)[key]
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
             ast.Div: operator.truediv, ast.Pow: np.power}
CONSTANTS = {"pi": math.pi, "e": math.e}
REDUCTIONS=frozenset(("mean","min","max","sum","count","std","sem","first","last","integral","slope",
                      "intercept","r2","slope_se","intercept_se","diffslope","sample_period","rms","x_at_max"))


def row_tables(node, columns):
    """Infer the rows still present after an operation, validating alignment *before* reduction."""
    if isinstance(node,ast.Name):
        return {columns[node.id]} if node.id in columns else set()
    children=node.args if isinstance(node,ast.Call) else ast.iter_child_nodes(node)
    tables=set().union(*(row_tables(child,columns) for child in children))
    if len(tables)>1:
        raise ValueError("不同记录表的行不能直接配对；请分别求标量或明确对齐依据")
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in REDUCTIONS:return set()
    return tables


def pair_observation_means(node,columns):
    """Only complete pairs contribute to the mean difference/sum of two co-recorded channels."""
    class PairMeans(ast.NodeTransformer):
        changed=False
        def visit_BinOp(self,n):
            self.generic_visit(n)
            if not isinstance(n.op,(ast.Sub,ast.Add)):return n
            terms=[]
            for arg in (n.left,n.right):
                if not (isinstance(arg,ast.Call) and isinstance(arg.func,ast.Name) and arg.func.id=="mean"
                        and len(arg.args)==1 and not arg.keywords and isinstance(arg.args[0],ast.Name)):return n
                terms.append(arg.args[0])
            loc=columns.get(terms[0].id)
            if loc is None or loc!=columns.get(terms[1].id):return n
            self.changed=True
            return ast.Call(func=ast.Name(id="mean",ctx=ast.Load()),
                            args=[ast.BinOp(left=terms[0],op=n.op,right=terms[1])],keywords=[])
    visitor=PairMeans()
    return visitor.visit(node),visitor.changed


def expression_tree(expr):
    # Greek-letter transliterations such as lambda are valid quantity names.
    if not isinstance(expr,str):raise ValueError("计算式需要是含变量名的表达式")
    aliases={}
    def substitute(m):
        s=m.group(0)
        if keyword.iskeyword(s):
            alias="__quantity_"+s;aliases[alias]=s;return alias
        return s
    expr=expr.strip().replace("^","**").replace("×","*").replace("·","*").replace("−","-")
    ternary=re.fullmatch(r"([^?:]+)\?([^?:]+):([^?:]+)",expr)
    if ternary:expr=f"choose({ternary[1]}, {ternary[2]}, {ternary[3]})"
    renamed=re.sub(r"\b[A-Za-z][A-Za-z0-9_]*\b",lambda m:m[0] if m[0] in ("if","else") else substitute(m),expr)
    node=ast.parse(renamed,mode="eval").body
    for item in ast.walk(node):
        if isinstance(item,ast.Name) and item.id in aliases:item.id=aliases[item.id]
    return node


def parse(expr, available):
    try:
        node = expression_tree(expr)
    except (ValueError, TypeError, SyntaxError) as err:
        raise ValueError("计算式语法有误，使用变量名与标准函数") from err
    def check(n):
        if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Name):
            def mask(part):
                if isinstance(part,ast.BinOp) and isinstance(part.op,(ast.BitAnd,ast.BitOr)):
                    mask(part.left);mask(part.right)
                elif isinstance(part,ast.Compare):check(part)
                else:raise ValueError("选区须由同一张表的数值比较给出")
            check(n.value);mask(n.slice);return
        if isinstance(n,ast.Constant) and type(n.value) is str:return
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="coalesce" and len(n.args)>=2 and not n.keywords:
            for arg in n.args:check(arg)
            return
        if isinstance(n,ast.IfExp):
            for child in (n.test,n.body,n.orelse):check(child)
            return
        if isinstance(n,ast.Compare) and len(n.ops)==1 and type(n.ops[0]) in (ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE):
            check(n.left);check(n.comparators[0]);return
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="choose" and len(n.args)==3 and not n.keywords:
            for a in n.args:check(a)
            return
        if isinstance(n,(ast.List,ast.Tuple)) and n.elts:
            for element in n.elts:check(element)
            return
        if isinstance(n, ast.Name) and n.id in available | CONSTANTS.keys():
            return
        if isinstance(n, ast.Constant) and type(n.value) in (int, float) and math.isfinite(n.value):
            return
        if isinstance(n, ast.BinOp) and type(n.op) in OPERATORS:
            check(n.left); check(n.right); return
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            check(n.operand); return
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCTIONS and not n.keywords:
            f = n.func.id
            if f=="quantity":
                if (len(n.args)!=2 or not isinstance(n.args[0],ast.Constant) or type(n.args[0].value) not in (int,float)
                        or not math.isfinite(n.args[0].value) or not isinstance(n.args[1],ast.Constant) or not isinstance(n.args[1].value,str)):
                    raise ValueError("带单位常数需给出数值和单位")
                transform(n.args[1].value)
                return
            limits = (2, 3) if f in ("slope","intercept","r2","slope_se","intercept_se") else (2, 2) if f in ("integral","hypot") else (1, 1)
            if f=="diffslope":limits=(3,3)
            if f=="lag":limits=(1,2)
            if f in ("x_at_max","atan2"):limits=(2,2)
            if not limits[0] <= len(n.args) <= limits[1]:
                raise ValueError(f"{f} 的参数个数应为 {limits}")
            for arg in n.args:
                check(arg)
            return
        raise ValueError("未知变量或不支持的表达式：" + ast.unparse(n))
    check(node)
    return node


def evaluate(n, env):
    if isinstance(n,ast.Call) and n.func.id=="coalesce":
        for arg in n.args:
            try:value=evaluate(arg,env)
            except (ValueError,ZeroDivisionError,IndexError):continue
            if np.ndim(value)==0 and np.isfinite(value):return value
        raise ValueError("请完成一种测量配置所需的读数")
    if isinstance(n,ast.Subscript):
        values=np.asarray(evaluate(n.value,env))
        mask=np.asarray(evaluate(n.slice,env))
        if mask.dtype.kind!="b" or values.shape!=mask.shape:raise ValueError("选区与原读数行数不符")
        # Keep observation identities: distinct masks of equal length must never zip
        # point 1 from one channel to point 2 from another channel.
        return np.where(mask,values,np.nan)
    if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.BitAnd,ast.BitOr)):
        fn=np.logical_and if isinstance(n.op,ast.BitAnd) else np.logical_or
        return fn(evaluate(n.left,env),evaluate(n.right,env))
    if isinstance(n,ast.Compare):
        fn={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}[type(n.ops[0])]
        return fn(evaluate(n.left,env),evaluate(n.comparators[0],env))
    if isinstance(n,ast.IfExp) or isinstance(n,ast.Call) and n.func.id=="choose":
        cond,yes,no=(n.test,n.body,n.orelse) if isinstance(n,ast.IfExp) else n.args
        value=evaluate(cond,env)
        if np.ndim(value):return np.where(value,evaluate(yes,env),evaluate(no,env))
        return evaluate(yes if value else no,env)
    if isinstance(n,(ast.List,ast.Tuple)):
        values=[evaluate(a,env) for a in n.elts]
        if any(np.ndim(v)!=0 for v in values):raise ValueError("列表中只能放标量")
        return np.asarray(values,dtype=float)
    if isinstance(n, ast.Constant):
        return n.value if type(n.value) is str else float(n.value)
    if isinstance(n, ast.Name):
        if n.id in CONSTANTS:
            return CONSTANTS[n.id]
        if n.id not in env:
            raise ValueError("缺少 " + n.id)
        return env[n.id]
    if isinstance(n, ast.BinOp):
        return OPERATORS[type(n.op)](evaluate(n.left, env), evaluate(n.right, env))
    if isinstance(n, ast.UnaryOp):
        value = evaluate(n.operand, env)
        return -value if isinstance(n.op, ast.USub) else value
    return FUNCTIONS[n.func.id](*(evaluate(arg, env) for arg in n.args))


def check_id(key, seen):
    if not isinstance(key,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*",key) or key in seen or key in CONSTANTS or key in ("if","else"):
        raise ValueError(f"变量名无效、重复或占用数学常数：{key}")
    seen.add(key)


def normalize_factors(node):
    """Cancel exact numerical factors without changing measurements, sums, or function meanings."""
    def product(left,right):
        if left is None:return right
        if right is None:return left
        return ast.BinOp(left=left,op=ast.Mult(),right=right)
    def build(factor,body):
        if body is None:return ast.Constant(value=float(factor))
        if factor==1:return body
        if factor.denominator==1:
            return ast.BinOp(left=ast.Constant(value=int(factor)),op=ast.Mult(),right=body)
        if factor.numerator!=1:body=product(ast.Constant(value=factor.numerator),body)
        return ast.BinOp(left=body,op=ast.Div(),right=ast.Constant(value=factor.denominator))
    conversion_factors=(1e-9,1e-6,.001,.01,100,1000,1e6,1e9)
    def split(n):
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):
            factor=Fraction(str(n.value))
            return factor,None,factor if n.value in conversion_factors else Fraction(1)
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Mult,ast.Div)):
            a,x,sa=split(n.left);b,y,sb=split(n.right)
            if isinstance(n.op,ast.Mult):return a*b,product(x,y),sa*sb
            if not b:return Fraction(1),n,Fraction(1)
            return a/b,x if y is None else ast.BinOp(left=x or ast.Constant(value=1),op=ast.Div(),right=y),sa/sb
        if (isinstance(n,ast.BinOp) and isinstance(n.op,ast.Pow) and isinstance(n.right,ast.Constant)
                and type(n.right.value) is int and 1<=n.right.value<=8):
            a,x,scale=split(n.left)
            return a**n.right.value,None if x is None else ast.BinOp(left=x,op=n.op,right=n.right),scale**n.right.value
        return Fraction(1),n,Fraction(1)
    class Normalize(ast.NodeTransformer):
        def visit_Call(self,n):
            if isinstance(n.func,ast.Name) and n.func.id=="quantity":return n
            return self.generic_visit(n)
        def visit_BinOp(self,n):
            self.generic_visit(n)
            factor,body,scale=split(n)
            # Keep an uncancelled conversion visible to the shared SI gate. Folding
            # /1000/2 to /2000 must not hide an illicit second unit conversion.
            if scale!=1:return n
            return build(factor,body)
    return Normalize().visit(node)


def scalar_aliases(page):
    return {v["id"]:v for v in [*page.get("results",[]),
            *(c for t in page.get("tables",[]) for c in t.get("derived",[]))]}


def bound_tree(expr,field_units,axis_unit,physical,aliases=None):
    def expand(node,trail=()):
        class Expand(ast.NodeTransformer):
            def visit_Call(self,n):
                n.args=[self.visit(a) for a in n.args];return n
            def visit_Name(self,n):
                if n.id in field_units or n.id not in (aliases or {}):return n
                if n.id in trail:raise ValueError("选区边界循环引用："+n.id)
                return expand(expression_tree(aliases[n.id]["expr"]),trail+(n.id,))
        return Expand().visit(node)
    if aliases:expr=ast.unparse(expand(expression_tree(expr)))
    node=parse(expr,set(field_units))
    if not quantity_names(node).intersection(field_units) or any(
        isinstance(n,(ast.List,ast.Tuple,ast.Subscript,ast.Compare,ast.IfExp)) or
        isinstance(n,ast.Call) and n.func.id!="abs" for n in ast.walk(node)):
        raise ValueError("选区边界须由本页可填写的量给出")
    if physical:check_formula_dimensions(node,field_units,axis_unit,"选区界限")
    return node


def constant_value(node):
    """Fold a subtree of literals only; None when a name or call is involved."""
    if isinstance(node,ast.Constant):
        return node.value if type(node.value) in (int,float) else None
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
        value=constant_value(node.operand)
        return None if value is None else (-value if isinstance(node.op,ast.USub) else value)
    if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
        left,right=constant_value(node.left),constant_value(node.right)
        if left is None or right is None:return None
        operation={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,
                   ast.Div:operator.truediv,ast.Pow:operator.pow}[type(node.op)]
        try:value=operation(left,right)
        except (ArithmeticError,TypeError,ValueError):return None
        return value if math.isfinite(value) else None
    return None


def unit_factors(unit):
    """Conversion factors a formula must not bake in: its unit's scale and inverse."""
    from .worksheet_units import transform
    scale,_=transform(unit)
    if not math.isfinite(scale) or scale<=0 or scale==1:return ()
    return (scale,1/scale)


def validate_expression(node,units,item,page):
    if item.get("literal_error"):raise ValueError(item["literal_error"])
    if page.get("check_dimensions"):check_formula_dimensions(node,units,item["unit"],item["label"])
    if page.get("unit_system")=="SI":
        documented={a["value"] for a in item.get("numeric_factors",[]) if a.get("meaning") and a.get("source_anchor")}
        for n in ast.walk(node):
            if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Mult,ast.Div)):
                literals=[v.value for v in (n.left,n.right) if isinstance(v,ast.Constant) and type(v.value) in (int,float)]
                if any(v in (1e-9,1e-6,.001,.01,100,1000,1e6,1e9) and v not in documented for v in literals):
                    raise ValueError(item["label"]+" 含手写倍率。单位由宿主换算；真实标定或物性系数请使用注明意义和单位的量。")
        factors=unit_factors(item["unit"])
        for n in ast.walk(node):
            value=constant_value(n)
            if value is None or value in documented:continue
            if isinstance(n,ast.Constant) and type(n.value) is int and abs(n.value)>=10**12:
                raise ValueError(item["label"]+" 的算式写了手算的单位换算或圆周率的整数近似。本页在 SI 内求值，换算由单位字段完成；请只写物理关系，π 写 pi。")
            if any(math.isclose(abs(value),factor,rel_tol=1e-6) for factor in factors):
                raise ValueError(item["label"]+" 的算式已自带单位换算（倍率 "+format(value,".6g")+"）。本页在 SI 内求值，度与弧度、毫与微这类换算由 unit 完成；请只写物理关系。")


def validate_page(page):
    seen, ids, tables = set(), set(), {}
    units={}
    physical=bool(page.get("check_dimensions"))
    if not page.get("title") or not page.get("sources"):
        raise ValueError("每个实验页需要标题及讲义页出处")
    if page.get("generation_error") and page.get("unresolved") and not any(page.get(k) for k in ("fields","tables","results","plots")):
        return
    def variable(item):
        check_id(item["id"], seen)
        if not item.get("label") or not isinstance(item.get("unit"), str):
            raise ValueError("每个输入和结果需要名称和单位，无量纲写 1")
        units[item["id"]]=item["unit"]
    for field in page.get("fields", []):
        variable(field)
    scalars=seen.copy()
    for table in page.get("tables", []):
        check_id(table["id"], ids)
        if not table.get("columns") or not table.get("title") or type(table.get("rows",3)) != int or table.get("rows",3)<0:
            raise ValueError("表格需要标题、列和非负初始行数")
        for col in table["columns"]:
            variable(col)
        tables[table["id"]] = {col["id"] for col in table["columns"]}
        if table.get("entry_mode","groups") not in ("groups","samples"):
            raise ValueError("测量录入方式须为 groups 或 samples")
        if table.get("grouping"):
            group=table["grouping"]
            keys={group.get("condition"),group.get("variant")}
            tags=group.get("variants",[])
            if (table.get("entry_mode")=="samples" or len(keys)!=2 or not keys<=tables[table["id"]]
                    or not isinstance(tags,list) or len(tags)<2 or not all(isinstance(v,str) and v.strip() for v in tags)
                    or len(set(tags))!=len(tags)):
                raise ValueError("组内分支需要两个不同的有效原始列和至少两个不同的具名分支")
        for col in table.get("derived",[]):
            available=scalars|tables[table["id"]]
            variable(col);node=parse(col["expr"],available);tables[table["id"]].add(col["id"])
            validate_expression(node,units,col,page)
        w=table.get("selection")
        if w and (w.get("column") not in tables[table["id"]] or not any(w.get(k) for k in ("min","max"))):
            raise ValueError("选区须绑定实际表列，以及本页可填写的上下界")
        if w:
            for bound in ("min","max"):
                if w.get(bound):bound_tree(w[bound],{k:units[k] for k in scalars},units[w["column"]],physical,scalar_aliases(page))
    by_column={key:tid for tid,keys in tables.items() for key in keys}
    for item in page.get("results", []):
        available = seen.copy()
        variable(item)
        if item.get("literal_error"):raise ValueError(item["literal_error"])
        parse(item["expr"], available)
        if page.get("unit_system")=="SI" and isinstance(expression_tree(item["expr"]),ast.Constant) and item["unit"] not in ("","1") and not item.get("source_anchor"):
            raise ValueError("有单位的固定量应标明真实来源；实测结果需关联读数")
        for expression in (item["expr"],item.get("uncertainty","")):
            if not expression:continue
            tree=expression_tree(expression)
            validate_expression(tree,units,item,page)
            if row_tables(tree,by_column):
                raise ValueError("该式逐行产生一列读数，应在对应记录表中计算，或明确取平均、拟合等汇总方法")
        if item.get("uncertainty"):
            parse(item["uncertainty"], seen)
        if item.get("digits") is not None and not 1 <= item["digits"] <= 15:
            raise ValueError("有效数字须为 1–15")
        if item.get("u_digits", "course") not in ("course",1,2,3):
            raise ValueError("不确定度位数设置无效")
    for plot in page.get("plots", []):
        cols = tables.get(plot["table"], set())
        if plot["x"] not in cols or not plot.get("ys") or not set(plot["ys"]) <= cols or plot.get("sigma") and plot["sigma"] not in cols:
            raise ValueError("绘图的横纵坐标和权重须来自同一张表")
        if plot.get("fit","none") not in ("linear","none"):
            raise ValueError("当前拟合图支持直线或不拟合")
    if not page.get("fields") and not tables:
        raise ValueError("实验页需要可填写的测量项目")
    if page.get("unit_system")=="SI":
        for v in [*page["fields"],*(c for t in page["tables"] for c in t["columns"]+t.get("derived",[])),*page["results"]]:
            transform(v["unit"])


def number(raw):
    s = numeric_text(raw).strip().replace("−","-")
    if not s:
        return np.nan
    try:
        n = float(s)
        return n if math.isfinite(n) else np.nan
    except (ValueError, TypeError):
        return np.nan


def display_series(page,table,rows,fields,directions=None):
    si=page.get("unit_system")=="SI"
    values={}
    for f in page.get("fields",[]):
        raw=read_quantity(fields.get(f["id"],""),f["unit"])
        if math.isfinite(raw):
            scale,zero=transform(f["unit"]) if si else (1,0)
            values[f["id"]]=raw*scale+zero
    result=columns_for(table,rows,values,si,directions)
    if si:
        for c in table["columns"]+table.get("derived",[]):
            if np.asarray(result[c["id"]]).dtype.kind in ("U","S"):continue
            scale,zero=transform(c["unit"]);result[c["id"]]=(result[c["id"]]-zero)/scale
    return result


def effective_fields(page,state):
    values={f["id"]:f["reference"]["value"] for f in page.get("fields",[]) if f.get("reference",{}).get("source_anchor")}
    values.update(state.get("fields",{}))
    values.update(state.get("linked_fields",{}))
    return values


def resolved_window(table,state,fields,page):
    cols={c["id"]:c for c in table["columns"]+table.get("derived",[])}
    # "全区间" is an explicit user choice. Removing the override restores the course window.
    if "window" in state and state["window"] is None:return None
    if isinstance(state.get("window"),dict):
        manual=state["window"]
        w={"column":manual.get("column",""),"min":None,"max":None}
        if w["column"] not in cols:return {**w,"pending":"核对所选数据列"}
        for side in ("min","max"):
            if manual.get(side) is None:continue
            w[side]=read_quantity(manual[side],cols[w["column"]]["unit"])
            if not math.isfinite(w[side]):return {**w,side:None,"pending":"核对手选范围及单位"}
        if w["min"] is not None and w["max"] is not None and w["max"]<w["min"]:
            return {**w,"pending":"下限不能高于上限"}
        return w
    if table.get("selection_pending"):return {"column":"","min":None,"max":None,"pending":str(table["selection_pending"])}
    rule=table.get("selection")
    if not rule:return None
    defs={f["id"]:f for f in page.get("fields",[])}
    w={"column":rule.get("column",""),"min":None,"max":None}
    if w["column"] not in cols or not any(rule.get(k) for k in ("min","max")):return {**w,"pending":"核对拟合区间"}
    axis_unit=cols[w["column"]]["unit"]
    cs,cz=transform(axis_unit)
    for bound in ("min","max"):
        expr=rule.get(bound)
        if not expr:continue
        try:
            tree=bound_tree(expr,{k:f["unit"] for k,f in defs.items()},axis_unit,page.get("check_dimensions"),scalar_aliases(page))
            env={}
            for k in quantity_names(tree).intersection(defs):
                value=read_quantity(fields.get(k,""),defs[k]["unit"])
                if not math.isfinite(value):return {**w,"pending":defs[k]["label"]}
                fs,fz=transform(defs[k]["unit"]);env[k]=value*fs+fz
            w[bound]=(float(evaluate(tree,env))-cz)/cs
            if not math.isfinite(w[bound]):raise ValueError("取数界限不是有限值")
        except (TypeError,ValueError,ArithmeticError) as err:
            return {**w,bound:None,"pending":str(err)}
    if w["min"] is not None and w["max"] is not None and w["max"]<w["min"]:
        return {**w,"pending":"下限不能高于上限"}
    return w


def selected_rows(table, state, fields=None,page=None):
    rows = state.get("rows", [])
    window = resolved_window(table,state,fields or {},page or {})
    if window and window.get("pending"):return
    transformed=None
    if window and (state.get("directions") or window["column"] in {c["id"] for c in table.get("derived",[])}):
        transformed=display_series(page or {},table,rows,fields or {},state.get("directions"))[window["column"]]
    axis=next((c for c in table["columns"] if window and c["id"]==window["column"]),None)
    for index,row in enumerate(rows):
        if row.get("_excluded"):
            continue
        if window:
            v = transformed[index] if transformed is not None else read_quantity(row.get(window["column"], ""),axis["unit"])
            if not math.isfinite(v) or window.get("min") is not None and v < window["min"] or window.get("max") is not None and v > window["max"]:
                continue
        yield row


def columns_for(table,rows,fields,si=False,directions=None):
    env={k:number(v) for k,v in fields.items()}
    for c in table["columns"]:
        if c.get("type")=="text" or c.get("unit") in ("文字","文本","类别","名称","描述"):
            env[c["id"]]=np.asarray([str(r.get(c["id"],"")) for r in rows]);continue
        env[c["id"]]=np.array([read_quantity(r.get(c["id"],""),c["unit"]) for r in rows],dtype=float)
        env[c["id"]]*=(directions or {}).get(c["id"],1)
        if si:
            scale,zero=transform(c["unit"]);env[c["id"]]=env[c["id"]]*scale+zero
    for c in table.get("derived",[]):
        try:
            with np.errstate(all="ignore"):
                value=evaluate(parse(c["expr"],set(env)),env)
            env[c["id"]]=np.broadcast_to(value,(len(rows),))
        except (ValueError,TypeError,ArithmeticError):
            env[c["id"]]=np.full(len(rows),np.nan)
    return env


def rounding(value, u, digits=4, u_digits="course"):
    def dec(v):
        return Decimal(str(float(v)))
    with localcontext() as ctx:
        ctx.prec = 40
        v = dec(value)
        if u is None or u == 0:
            display = format(v, f".{digits}g")
            return display if u is None else display+" ± 0"
        uncert = dec(u)
        n = (1 if uncert.scaleb(-uncert.adjusted()) >= 3 else 2) if u_digits == "course" else int(u_digits)
        exponent = uncert.adjusted()-n+1
        quantum = Decimal(1).scaleb(exponent)
        rounded = uncert.quantize(quantum, rounding=ROUND_HALF_UP)
        if rounded.adjusted() != uncert.adjusted():
            quantum = Decimal(1).scaleb(rounded.adjusted()-n+1)
        return format(v.quantize(quantum, rounding=ROUND_HALF_UP),"f")+" ± "+format(uncert.quantize(quantum,rounding=ROUND_HALF_UP),"f")


def compute(page, state):
    env, names, output = {}, set(), []
    labels = {}
    window_issues={}
    partial_tables = set()
    by_column={}
    si=page.get("unit_system")=="SI"
    fields=effective_fields(page,state)
    for field in page.get("fields", []):
        key = field["id"]; names.add(key); labels[key] = field["label"]
        value = read_quantity(fields.get(key,""),field["unit"])
        if math.isfinite(value):
            scale,zero=transform(field["unit"]) if si else (1,0)
            env[key] = value*scale+zero
    constants=dict(env)
    for table in page.get("tables", []):
        table_state=state.get("tables", {}).get(table["id"], {})
        window=resolved_window(table,table_state,fields,page)
        rows = list(selected_rows(table,table_state,fields,page))
        chosen = {id(row) for row in rows}
        if any(id(row) not in chosen and any(str(row.get(c["id"], "")).strip() for c in table["columns"]) for row in table_state.get("rows", [])):
            partial_tables.add(table["id"])
        series=columns_for(table,rows,constants,si,table_state.get("directions"))
        for col in table["columns"]+table.get("derived",[]):
            key = col["id"]; names.add(key); labels[key] = col["label"]
            by_column[key]=table["id"]
            env[key] = series[key]
            if window and window.get("pending"):window_issues[key]=window["pending"]
    def scalar(expr, keys):
        tree,_=pair_observation_means(parse(expr,keys),by_column)
        try:
            with np.errstate(all="ignore"):
                result = evaluate(tree, env)
            if np.ndim(result) != 0 or not np.isfinite(result):
                raise ValueError("检查有效读数、分母及公式适用条件")
        except (ValueError,TypeError,ArithmeticError) as err:
            issues=list(dict.fromkeys(window_issues[k] for k in sorted(quantity_names(tree)) if k in window_issues))
            if issues:raise ValueError("请先确定取数范围："+"、".join(issues)) from err
            raise
        return float(result)
    def problem(err):
        msg=str(err)
        return "缺少"+labels.get(msg[3:],msg[3:]) if msg.startswith("缺少 ") else msg
    for item in page.get("results", []):
        key = item["id"]; record = dict(id=key, label=item["label"], unit=item["unit"])
        labels[key] = item["label"]
        try:
            if partial_tables.intersection(item.get("endpoint_tables", [])):
                record["interval_note"] = "按当前选区和所填起止读数计算，请核对它们是否对应。"
            v = scalar(item["expr"], names)
            domain=item.get("domain","signed")
            if domain=="positive" and v<=0 or domain in ("nonnegative","fraction") and v<0 or domain=="fraction" and v>1:
                raise ValueError("结果超出该物理量的定义范围，请核对信号极性、单位和所选数据段")
            env[key] = v
            scale,zero=transform(item["unit"]) if si else (1,0)
            record["value"]=(v-zero)/scale
            u = scalar(item["uncertainty"],names|{key}) if item.get("uncertainty") else None
            if u is not None and u < 0:
                raise ValueError("不确定度须为非负值")
            u_out=None if u is None else u/scale
            record.update(uncertainty=u_out,display=rounding(record["value"],u_out,item.get("digits",4),item.get("u_digits","course")))
            if item.get("uncertainty_pending"):
                record["pending"]=item["uncertainty_pending"]
                record["display"]+="（标准不确定度待核对）"
        except (ArithmeticError, ValueError, TypeError) as err:
            record["pending"] = problem(err)
            if "value" in record:
                record["display"]=rounding(record["value"],None,item.get("digits",4))+"（不确定度待补）"
        output.append(record); names.add(key)
    return output


def resolve_prior_results(pages, data, links):
    """Populate display-only fields in course order, including multi-page chains."""
    if not links:
        return
    required={source for fields in links.values() for source,_ in fields.values()}
    computed={}
    for page in pages:
        pid=page["id"]
        if pid in links:
            fields={f["id"]:f for f in page["fields"]}
            values={}
            for key,(source,result) in links[pid].items():
                if key not in fields:
                    continue
                row=computed.get(source,{}).get(result,{})
                values[key]=convert(row["value"],row["unit"],fields[key]["unit"]) if "value" in row else ""
            data.setdefault(pid,{})["linked_fields"]=values
        if pid in required:
            computed[pid]={r["id"]:r for r in compute(page,data.get(pid,{}))}


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    definition = json.loads((base/"definition.json").read_text(encoding="utf-8"))
    data = json.loads((base/"measurements.json").read_text(encoding="utf-8"))
    resolve_prior_results(definition["pages"],data,definition.get("prior_result_links",{}))
    results = {p["id"]:compute(p, data.get(p["id"], {})) for p in definition["pages"]}
    (base/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
