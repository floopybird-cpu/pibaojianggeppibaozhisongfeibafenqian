"""Unit conversion at the boundary of SI-based worksheet calculations."""
import ast
import functools
import math
import re
import unicodedata
from fractions import Fraction

UNITS={
    "kg":1,"g":1e-3,"mg":1e-6,"ug":1e-9,"m":1,"km":1e3,"cm":1e-2,"mm":1e-3,"um":1e-6,"nm":1e-9,
    "s":1,"ms":1e-3,"us":1e-6,"ns":1e-9,"min":60,"h":3600,
    "L":1e-3,"mL":1e-6,"uL":1e-9,"K":1,"degC":1,"A":1,"mA":1e-3,"uA":1e-6,
    "V":1,"mV":1e-3,"uV":1e-6,"Ohm":1,"kOhm":1e3,"MOhm":1e6,"mOhm":1e-3,
    "N":1,"mN":1e-3,"Pa":1,"hPa":100,"kPa":1e3,"MPa":1e6,"bar":1e5,
    "J":1,"mJ":1e-3,"kJ":1e3,"W":1,"mW":1e-3,"uW":1e-6,"kW":1e3,
    "Hz":1,"kHz":1e3,"MHz":1e6,"GHz":1e9,"F":1,"uF":1e-6,"nF":1e-9,"pF":1e-12,
    "H":1,"mH":1e-3,"uH":1e-6,"T":1,"mT":1e-3,"C":1,"mol":1,"cd":1,
    "rad":1,"deg":math.pi/180,"percent":.01,"dB":1,"Wb":1,"S":1,"lx":1}
ALIASES={"℃":"degC","°C":"degC","摄氏度":"degC","摄氏":"degC","Ω":"Ohm","Ω":"Ohm","ohm":"Ohm","欧姆":"Ohm",
         "·":"*","⋅":"*","μ":"u","µ":"u","²":"^2","³":"^3","⁻¹":"^-1","⁻²":"^-2","⁻³":"^-3",
         "°":"deg","%":"percent","毫升":"mL","毫米":"mm","千克":"kg","秒":"s","克":"g","分钟":"min","度":"deg"}


def unit_expression(unit):
    u=str(unit).strip()
    if u in ("","1","无量纲","—","-","文字","文本","描述","次","组","个","圈","条"):
        return "1"
    for a,b in ALIASES.items():u=u.replace(a,b)
    # PDF text layers often flatten a superscript: "J kg-1 K-1".
    u=re.sub(r"(?<=[A-Za-z)])([−-]\d+)\b",r"^\1",u)
    u=re.sub(r"(?<=[A-Za-z)])(?=degC)","*",u)
    u=re.sub(r"\b(km|cm|mm|um|nm|m)([23])\b",r"\1^\2",u)
    u=u.replace("−","-").replace("^","**").replace("（","(").replace("）",")")
    u=re.sub(r"(?<=[A-Za-z\d)])\s+(?=[A-Za-z(])","*",u)
    u=re.sub(r"\s+","",u)
    return u


@functools.lru_cache(maxsize=2048)
def transform(unit):
    u=unit_expression(unit)
    if u=="degC":return 1.,273.15
    def scale(n):
        if isinstance(n,ast.Name) and n.id in UNITS:return UNITS[n.id]
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):return n.value
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub):return -scale(n.operand)
        if isinstance(n,ast.BinOp):
            a,b=scale(n.left),scale(n.right)
            if isinstance(n.op,ast.Mult):return a*b
            if isinstance(n.op,ast.Div):return a/b
            if isinstance(n.op,ast.Pow):return a**b
        raise ValueError()
    try:
        result=scale(ast.parse(u,mode="eval").body)
        if not math.isfinite(result) or result<=0:raise ValueError()
        return result,0.
    except (ValueError,SyntaxError,ArithmeticError,TypeError):
        raise ValueError("未识别单位 "+str(unit)) from None


FAMILIES=(("m","km","cm","mm","um","nm"),("s","ms","us","ns","min","h"),("kg","g","mg","ug"),
          ("K","degC"),("V","mV","uV"),("A","mA","uA"),("Ohm","kOhm","MOhm","mOhm"),
          ("L","mL","uL","m^3","cm^3","mm^3"),("rad","deg"),("Pa","hPa","kPa","MPa","bar"),
          ("F","uF","nF","pF"),("H","mH","uH"),("Hz","kHz","MHz","GHz"),("J","mJ","kJ"),("W","mW","uW","kW"),
          ("1","percent"))

# Exponents of the SI base units, in the order kg, m, s, A, K, mol, cd.
BASE_UNITS=("kg","m","s","A","K","mol","cd")
UNITLESS=(0,0,0,0,0,0,0)
_DIMENSIONS={}
for _symbols,_powers in (
    ("kg g mg ug",(1,0,0,0,0,0,0)),("m km cm mm um nm",(0,1,0,0,0,0,0)),
    ("s ms us ns min h",(0,0,1,0,0,0,0)),("A mA uA",(0,0,0,1,0,0,0)),
    ("K degC",(0,0,0,0,1,0,0)),("mol",(0,0,0,0,0,1,0)),("cd",(0,0,0,0,0,0,1)),
    ("L mL uL",(0,3,0,0,0,0,0)),("N mN",(1,1,-2,0,0,0,0)),
    ("Pa hPa kPa MPa bar",(1,-1,-2,0,0,0,0)),("J mJ kJ",(1,2,-2,0,0,0,0)),
    ("W mW uW kW",(1,2,-3,0,0,0,0)),("V mV uV",(1,2,-3,-1,0,0,0)),
    ("Ohm kOhm MOhm mOhm",(1,2,-3,-2,0,0,0)),("S",(-1,-2,3,2,0,0,0)),
    ("Hz kHz MHz GHz",(0,0,-1,0,0,0,0)),("F uF nF pF",(-1,-2,4,2,0,0,0)),
    ("H mH uH",(1,2,-2,-2,0,0,0)),("T mT",(1,0,-2,-1,0,0,0)),
    ("Wb",(1,2,-2,-1,0,0,0)),("C",(0,0,1,1,0,0,0)),("lx",(0,-2,0,0,0,0,1)),
    ("rad deg percent dB",UNITLESS)):
    _DIMENSIONS.update(dict.fromkeys(_symbols.split(),_powers))


def product_dimensions(left,right,sign=1):
    return tuple(a+sign*b for a,b in zip(left,right))


def power_dimension(dim,exponent):
    return tuple(a*exponent for a in dim)


def unit_label(dim):
    return "·".join(s if n==1 else f"{s}^{n}" for s,n in zip(BASE_UNITS,dim) if n) or "无量纲"


def quantity_context(item):
    """Describe the same quantity at the recording and calculation boundaries."""
    result = dict(item)
    unit = item.get("unit")
    if not isinstance(unit, str):
        return result
    try:
        scale, offset = transform(unit)
        formula_unit = unit_label(dimension(unit))
    except (ValueError, KeyError, SyntaxError, TypeError):
        return result
    result.update(recorded_unit=unit, formula_unit=formula_unit,
                  host_input_scale=scale, host_input_offset=offset)
    return result


@functools.lru_cache(maxsize=2048)
def dimension(unit):
    transform(unit)
    def visit(n):
        if isinstance(n,ast.Name):return _DIMENSIONS[n.id]
        if isinstance(n,ast.Constant):return UNITLESS
        if isinstance(n,ast.BinOp) and isinstance(n.op,ast.Pow):
            return power_dimension(visit(n.left),Fraction(str(ast.literal_eval(n.right))))
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Mult,ast.Div)):
            return product_dimensions(visit(n.left),visit(n.right),-1 if isinstance(n.op,ast.Div) else 1)
        raise ValueError("单位的幂必须是一个数："+unit)
    return visit(ast.parse(unit_expression(unit),mode="eval").body)


def quantity_names(node):
    """Only value uses are dependencies. In 'slope + slope(x,y)', the call is an operator."""
    if isinstance(node,ast.Name):
        return {node.id}
    children=node.args if isinstance(node,ast.Call) else ast.iter_child_nodes(node)
    return set().union(*(quantity_names(child) for child in children))


def check_formula_dimensions(node, units, output_unit, label):
    """Infer a parsed expression's SI dimensions before it can be shown as a result."""
    def mismatch(expected,actual):
        raise ValueError(f"{label} 的公式量纲为 {unit_label(actual)}，此处应为 {unit_label(expected)}")

    def same(a,b,left,right):
        if a==b:return a
        # Zero may be compared with or substituted for a quantity of any unit.
        if isinstance(left,ast.Constant) and left.value==0:return b
        if isinstance(right,ast.Constant) and right.value==0:return a
        mismatch(a,b)

    def require(a,b):
        if a!=b:mismatch(b,a)

    def exponent(n):
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):
            return Fraction(str(n.value))
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.USub,ast.UAdd)):
            return exponent(n.operand)*(-1 if isinstance(n.op,ast.USub) else 1)
        if isinstance(n,ast.BinOp) and isinstance(n.op,ast.Div):
            den=exponent(n.right)
            if den:return exponent(n.left)/den
        raise ValueError(f"{label} 的有量纲底数需要确定的数值指数")

    def infer(n):
        if isinstance(n,ast.Subscript):
            require(infer(n.slice),UNITLESS)
            return infer(n.value)
        if isinstance(n,ast.Name):return dimension(units[n.id]) if n.id in units else UNITLESS
        if isinstance(n,ast.Constant):return UNITLESS
        if isinstance(n,ast.UnaryOp):return infer(n.operand)
        if isinstance(n,ast.Compare):
            same(infer(n.left),infer(n.comparators[0]),n.left,n.comparators[0])
            return UNITLESS
        if isinstance(n,ast.IfExp) or isinstance(n,ast.Call) and n.func.id=="choose":
            cond,yes,no=(n.test,n.body,n.orelse) if isinstance(n,ast.IfExp) else n.args
            require(infer(cond),UNITLESS)
            return same(infer(yes),infer(no),yes,no)
        if isinstance(n,(ast.Tuple,ast.List)):
            value=infer(n.elts[0])
            for entry in n.elts[1:]:value=same(value,infer(entry),n.elts[0],entry)
            return value
        if isinstance(n,ast.BinOp):
            a,b=infer(n.left),infer(n.right)
            if isinstance(n.op,(ast.Add,ast.Sub)):return same(a,b,n.left,n.right)
            if isinstance(n.op,ast.Mult):return product_dimensions(a,b)
            if isinstance(n.op,ast.Div):return product_dimensions(a,b,-1)
            if isinstance(n.op,(ast.BitAnd,ast.BitOr)):
                require(a,UNITLESS);require(b,UNITLESS);return UNITLESS
            require(b,UNITLESS)
            return a if a==UNITLESS else power_dimension(a,exponent(n.right))
        args=[infer(arg) for arg in n.args]
        name=n.func.id
        if name=="quantity":return dimension(n.args[1].value)
        if name=="coalesce":
            for alternative in args[1:]:require(alternative,args[0])
            return args[0]
        if name in ("slope","intercept","r2","slope_se","intercept_se","diffslope"):
            x,y=args[:2]
            if len(args)==3:require(args[2],UNITLESS if name=="diffslope" else y)
            if name=="r2":return UNITLESS
            if name in ("intercept","intercept_se"):return y
            return product_dimensions(y,x,-1)
        if name=="integral":return product_dimensions(*args)
        if name=="sqrt":return power_dimension(args[0],Fraction(1,2))
        if name=="count":return UNITLESS
        if name in ("hypot","atan2"):
            require(args[0],args[1])
            return args[0] if name=="hypot" else UNITLESS
        if name in ("exp","log","log10","sin","cos","tan","atan"):
            require(args[0],UNITLESS)
            return UNITLESS
        if name=="lag" and len(args)>1:require(args[1],UNITLESS)
        return args[0]

    require(infer(node),dimension(output_unit))
    if not quantity_names(node).intersection(units):
        raise ValueError(f"{label} 未使用任何读数或参数。讲义常数可作为带出处的输入")


def equivalent_form(unit):
    return unit_expression(unit).replace("**","^")


def convert(value,source,target):
    a,b=equivalent_form(source),equivalent_form(target)
    if a==b:return value
    compatible=any(a in family and b in family for family in FAMILIES)
    if not compatible and (dimension(source)!=dimension(target) or dimension(source)==UNITLESS):
        raise ValueError(f"无法把 {source} 换算为 {target}，请核对量与单位")
    sa,za=transform(source);sb,zb=transform(target)
    return (value*sa+za-zb)/sb


def numeric_text(raw):
    """Read visible quantity text without rewriting the original record or joining tokens."""
    return "".join(c for c in str(raw) if c.isspace() or unicodedata.category(c) not in ("Cc","Cf"))


def read_quantity(raw,unit):
    text=numeric_text(raw)
    try:return float(text.strip().replace("−","-"))
    except (ValueError,TypeError):pass
    match=re.fullmatch(r"\s*([-+−]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([^\s].*)",text)
    if not match:return math.nan
    try:return convert(float(match[1].replace("−","-")),match[2].strip(),unit)
    except ValueError:return math.nan
